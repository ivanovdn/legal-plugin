# Legal Plugin — RAG

> Part of the [project wiki](wiki.md).

Retrieval over the firm's documents and the legal reference books in Qdrant. This page covers which model runs where, the search pipeline for client documents as the code runs it today, where it is going, the collections, ingest, and the reference books (their collection, ingest, search, citations and known limits).

## Where each piece runs

| Piece | Model or engine | Local (Mac) | VM (production) | Settings |
|---|---|---|---|---|
| Embeddings | `embeddinggemma:latest`, 768 dimensions, cosine | Ollama on the Mac (`localhost:11434`) | Ollama on Spark (`172.20.0.22:11434`) | `OLLAMA_BASE_URL`, `EMBEDDING_MODEL`, `QDRANT_VECTOR_DIM=768` |
| Vector store | Qdrant | Docker, `qdrant/qdrant:v1.17.0` (`localhost:6333`) | Spark's Qdrant 1.17.1 (`QDRANT_REMOTE_URL`), beside compliance-bot's collections | `QDRANT_URL` |
| Reranker | Qwen3-Reranker-4B | llama.cpp's `llama-server`, `POST /v1/rerank` | vLLM, `POST /v1/rerank` (or `/v1/score`) | `RERANKER_ENABLED`, `RERANKER_BACKEND`, `RERANKER_URL`, `RERANKER_MODEL` |
| Keyword search, client documents | BM25 in pure Python ([`rag/bm25_index.py`](../rag/bm25_index.py)) | off | off | `BM25_ENABLED=false`, the default |
| Keyword search, reference books | Qdrant's own BM25: a sparse vector the server computes at upsert and at query | on, Qdrant 1.17.0 (checked 2026-10-30) | on once the book is ingested; Spark's Qdrant 1.17.1 is **not checked yet** | none; it is part of the collection ([Reference books](#reference-books)) |

- **The reranker is not wired up yet.** The local `.env` has `RERANKER_ENABLED=true` with `RERANKER_URL` empty, and `.env.remote.example` sets no reranker keys; the VM's live `.env` hasn't been checked. With the URL empty, every rerank call fails, and the search silently falls back to the fused order. The fix is the [roadmap](roadmap.md) row *The reranker has never run*.
- **`RERANKER_BACKEND`** picks the request format. `vllm` and `vllm-score` wrap the query and the documents in Qwen3's chat template; `vllm-score` also calls `/v1/score` instead of `/v1/rerank`. Any other value, empty included, sends llama-server's plain `{model, query, documents, top_n}`.
- **Keyword search is off in both places for client documents**, so retrieval there today is dense search, then the reranker. It is moving into Qdrant; see [Where it is going](#where-it-is-going). The reference books' collection has it from the start, and the reranker (still unwired) sits behind their search too.

## Search pipeline (today)

`hybrid_search()` in [`rag/hybrid_search.py`](../rag/hybrid_search.py):

```
query → embed_query() → Qdrant dense search ──────────────────┐   20 candidates, payload filters
                                                              ├─→ RRF (k=60) → reranker → top_k
        BM25 search (only if BM25_ENABLED, no filters) ───────┘   20 candidates
```

1. **Dense search.** The query is embedded on Ollama, with `EMBEDDING_QUERY_PREFIX` in front. Qdrant returns `HYBRID_VECTOR_CANDIDATES` (20) points, filtered on the payload.
2. **BM25**, only with `BM25_ENABLED=true`. It returns `HYBRID_BM25_CANDIDATES` (20) chunks from an in-process index.
3. **Fusion.** Reciprocal rank fusion: each chunk scores `1/(60 + rank)`, summed over the lists it appears in. With BM25 off there is only one list, so the dense order passes through unchanged.
4. **Rerank.** Every fused candidate goes to the reranker, which keeps the best `top_k`. Any error (unreachable, timeout, empty URL) logs one warning and returns the fused order cut to `top_k`. A reranker failure never fails the search.

**Who calls it:**
- **The `rag_retriever` graph node:** `legal_docs`, `top_k=10`, with the state's filters. `intake` always puts `client_id` in them.
- **The agents' `search_legal` tool:** `top_k=10` by default. `client_id` is an optional argument the agent fills in from its prompt; nothing in the tool enforces it, and `get_document` is the same. See the [roadmap](roadmap.md) row *Bind `client_id` to the agents' RAG tools*.
- **No ranking:** `get_document`, `extract_clauses` and `get_parent_msa` scroll Qdrant by payload filter.
- **The reference books** have their own search and lookup, and are not reached through `hybrid_search()`; see [Reference books](#reference-books).

### Settings

| Setting | Default | What it does |
|---|---|---|
| `HYBRID_VECTOR_CANDIDATES` | `20` | dense candidates |
| `HYBRID_BM25_CANDIDATES` | `20` | BM25 candidates |
| `EMBEDDING_QUERY_PREFIX` / `EMBEDDING_PASSAGE_PREFIX` | empty | put in front of queries and of chunks before embedding |
| `RERANKER_INSTRUCTION` | empty | the task line in the vLLM chat template |
| `RERANKER_QUERY_TEMPLATE` | empty | llama-server only: formats `{instruction}` and `{query}`; empty sends the bare query |

**Declared in `config.py` but never read:** `RETRIEVAL_TOP_K` (10), `MIN_CONFIDENCE_SCORE` (0.45) and `RERANKER_CANDIDATES` (25).

**Read but never used:** `RERANKER_TOP_N` (6) applies only when a caller passes no `top_k`, and every caller passes one.

So no score threshold drops weak matches: every search returns its full `top_k`.

### Why keyword search is off

Read from the code on 2026-10-30, today's BM25 can't simply be switched on:
- **It ignores filters.** `search_bm25` takes none, so with BM25 on, chunks from any client reach the fusion. That breaks rule 3 in [CLAUDE.md](../CLAUDE.md#hard-rules-from-memory--past-incidents).
- **Its index is never saved.** Ingest adds chunks to an in-memory index in whichever process runs the ingest, and only the tests call `save()`. The backend loads `.bm25_index.json` from its working directory, a file nothing writes. An index built by `scripts/ingest_all.py` is therefore lost when the script exits. Only `POST /api/ingest`, which runs inside the backend, fills the backend's index, and only until the next restart.

## Where it is going

**Decided 2026-10-30** ([decision note](decisions/2026-10-30-keyword-search-in-qdrant.md)): keyword search moves into Qdrant's built-in hybrid search, both locally and on the VM. Embeddings and the reranker stay as they are.
- **Points:** each point gets a sparse BM25 vector beside its dense one.
- **Query:** one Query API call runs the dense and the sparse search under the same payload filter, and Qdrant fuses the results (RRF).
- **Rerank:** the fused list goes to the reranker, as it does today.
- **Removed:** `rag/bm25_index.py`, `BM25_ENABLED` and `HYBRID_BM25_CANDIDATES` go.

The work is tracked as the [roadmap](roadmap.md) row *Move keyword search into Qdrant's hybrid search*. The legal reference books were the first corpus that needed it, and their collection is the first built that way ([Reference books](#reference-books)); `legal_docs` and `case_history` are still dense-only, and what stays on that row is moving them.

**The legal reference books are built** (owner's scope, 2026-10-30; on `feat/reference-books` since 2026-11-03, not yet merged or on the VM), starting with Circular 92 (Title 17 U.S.C.): their own collection with no `client_id`, their own parser and chunk model, and citations by `ref_id` that code checks and renders. The decisions are in [decisions/](decisions/README.md) (2026-10-30, *reference book* and *reference books*), and the design is [Reference books in RAG](superpowers/specs/2026-10-30-reference-books-design.md).

## Qdrant collections

| Collection | Content | Chunking |
|---|---|---|
| `legal_docs` | Contracts, legislation, templates, policies | Varies by doc_type |
| `case_history` | Past signed contracts (5 demo + CUAD) | Clause-level (one chunk per clause) |
| `memory` | Unused stub (attorney preferences shipped as per-attorney `USER.md` files, not here) | Small entries |
| `legal_books` | Reference books: Circular 92 (Title 17 U.S.C. and related laws). No client data, no `client_id` | One chunk per rule, definition or history note, at most 800 tokens |

`scripts/create_collections.py` creates the first three, each with one dense vector (`QDRANT_VECTOR_DIM`, cosine) and **no payload indexes**. `legal_books` is not one of them: `scripts.ingest_book` creates it, with a sparse vector and payload indexes ([Reference books](#reference-books)).
- **Unindexed filters.** Filters such as `client_id` work without an index, but Qdrant has to check each point's payload. On 2026-10-30 the local collections had no indexes (`legal_docs` 154 points, `case_history` 43, `memory` 0). Only `legal_books` is created with them.
- **Indexing code exists but never runs.** `init_collection` in [`rag/vector_store.py`](../rag/vector_store.py) would also add keyword indexes on `doc_id`, `doc_type`, `client_id`, `jurisdiction`, `sensitivity`, `clause_type`, `section`, `section_number`, `clause_number` and `section_display`, but nothing calls it.
- **Changing a collection means rebuilding it.** Both paths skip a collection that already exists. To change its vector setup, such as adding a sparse vector, you must recreate it and re-ingest its documents.

## Ingest pipeline

```
Document (PDF/DOCX) → Parser → Chunks (LegalChunk) → Embed (Ollama, passage prefix) → Upsert (Qdrant, 100 at a time)
                                                                                   (+ BM25 index, if BM25_ENABLED)
```

- **DOCX parser** — heading detection, clause numbering, Word auto-numbering, table-to-text, chunk splitting/merging, noise filtering
- **PDF parser** — section-based chunking by heading regex
- **Batch ingest**: `python scripts/ingest_all.py <dir> --collection legal_docs --client-id <id>`; one file through the backend: `POST /api/ingest`
- **A chunk past the embedding window fails the ingest.** `rag/embeddings.py` sends `truncate: false` on every request. Left unset, Ollama silently cut input past the model's 2,048-token window and returned a vector for the front of the text: a chunk indexed on text nobody reads back. Now Ollama answers HTTP 400, and `embed_texts` raises `EmbeddingInputTooLong`, naming the chunk and its length, so the ingest stops. This covers **contract ingest too**: the PDF parser has made chunks of up to 1,911 words, so a long contract can now fail where it used to be cut. No client documents are ingested in production today.
- **A query past the window is retried once, truncated.** `embed_query` does it and records the silent degradation `embedding_query_truncated`: a long pasted question must not fail the turn, nor pass unnoticed.
- **The book collection is not fed by any of these.** The upload and `POST /api/ingest` route by extension to the client-document parsers and cannot reach it.

## Reference books

> Built on `feat/reference-books` (2026-11-03). Not merged, and not on the VM: the owner's local integration and the VM rollout come first (see the [roadmap](roadmap.md) row *Index the legal reference books*). Design: [Reference books in RAG](superpowers/specs/2026-10-30-reference-books-design.md). Decisions: [RAG, not fine-tuning](decisions/2026-10-30-reference-books-rag-not-fine-tuning.md) · [parser](decisions/2026-10-30-reference-book-parser.md) · [chunk model](decisions/2026-10-30-reference-book-chunk-model.md) · [citations](decisions/2026-10-30-reference-book-citations.md) · [history notes](decisions/2026-10-30-reference-book-history-notes.md) · [keyword search in Qdrant](decisions/2026-10-30-keyword-search-in-qdrant.md).

The first book is the U.S. Copyright Office's **Circular 92 (Dec. 2025)**: Title 17 U.S.C. and related laws, a public-domain U.S. government work. The research agent can search it and look passages up. Code checks what the answer cites and renders the citation.

### The registry and the parser

- **Registry:** [`ingest/books/registry.py`](../ingest/books/registry.py) lists each book and edition: `circ92`, edition `2025-12` (Dec. 2025), the PDF's SHA-256, the licence. The ingest command refuses an unregistered id and a PDF whose hash differs. A new edition is a new entry, never an edit.
- **Parser:** a layout front end (PDF words to lines to a tree of labelled nodes with printed pages) and a back end (tree to chunks, cross-references, note links). Rules that can't be general are **hand overrides**, one file per book and edition ([`overrides-2025-12.json`](../ingest/books/circular92/overrides-2025-12.json)). Every entry carries its reason. Five kinds exist: `label_level`, `hyphen`, `xref_target` (one path, several, or `unparsed`), `text_owner` and `note_home`. The Dec. 2025 file holds 66 entries: 57 hyphens, 4 xref targets, 3 note homes, 2 text owners.
- **`ref_id`:** `<book>-<edition>:<path>`, for example `circ92-2025-12:usc17-s107`. It names a **node** of the book (a section, a subdivision, a definition, a note), never a chunk, so changing the chunk budget moves chunks and leaves every id valid. A chunk has its own key (`<ref_id>`, or `<ref_id>~lead`, `~tail`, `~p2`…), never cited. The point id is a uuid5 of that key.

### The collection

- **Name:** `legal_books` (`BOOK_COLLECTION`), written and read by [`rag/book_store.py`](../rag/book_store.py) and [`rag/book_search.py`](../rag/book_search.py). Those functions name only this collection and take neither a collection nor a `client_id`.
- **No `client_id`.** The collection holds no client data, so rule 3 in [CLAUDE.md](../CLAUDE.md#hard-rules-from-memory--past-incidents) is about the other collections ([decision](decisions/2026-10-30-reference-book-chunk-model.md)).
- **Vectors:** a dense `dense` (`QDRANT_VECTOR_DIM`, cosine) and a sparse `bm25` (IDF). **Qdrant computes the BM25 vector** from the chunk body (without the context header, so headers repeated across a section don't dominate), through a `QdrantClient(cloud_inference=True)`. That flag means "send the text to the server that embeds it", which is our own Qdrant, not a cloud. Without it the locked client (`qdrant-client` 1.18.0) computes the same vector itself with its built-in BM25: byte-identical, no fastembed. The flag only makes Qdrant do the work. Nothing is downloaded either way, so constraint 7 holds. That built-in path is also the fallback if Spark's Qdrant can't do it ([roadmap](roadmap.md)).
- **Payload:** the chunk (`BookChunk`), with `kind` (`rule`, `definition`, `history_note`, `repealed`), `source_type` (`usc_title`, `uncodified_act`, `treaty`), `jurisdiction` (`US`; `international` for the treaty appendix), pages, `ancestor_ref_ids` and `sub_ref_ids`. **Indexes** on `ref_id`, `book`, `edition`, `kind`, `source_type`, `jurisdiction`, `ancestor_ref_ids` and `sub_ref_ids`.
- **Created by the ingest command** when missing. A collection made with another vector size has to be recreated.

### Ingest

```bash
uv run python -m scripts.ingest_book <book_id> <pdf_path> [--dry-run]
# locally, the PDF is in the repo:
uv run python -m scripts.ingest_book circ92 tests/fixtures/books/circular-92-2025-12.pdf --dry-run
```

- **Dry run first.** It parses and chunks the whole book (tokens are counted by Ollama on the embedding model, so Ollama must be up) and builds the report. It writes `<ref_prefix>-<UTC stamp>-{chunks.jsonl, chunks.tsv, report.json, sample20.tsv}` to `data/deploy-logs/` and never touches Qdrant; it exits 1 when the bar fails. `sample20.tsv` is 20 random chunks with their text: a person checks them against the printed page, text and page number, before the first ingest.
- **The bar.** A real ingest refuses (exit 1) unless the report passes all seven:
  1. 152 Title 17 sections are nodes;
  2. 192 footnote markers are linked to their notes, with no scope where markers and notes differ;
  3. 20 appendices are parsed, each with its `source_type`;
  4. no label line is left out of sequence;
  5. no chunk is over the token budget (a chunk past the embedding window counts as over);
  6. no override is unused;
  7. no line-end hyphen is undecided.

  The cross-reference resolution rate is reported (Title 17 targets, and all targets), not gated: a Public Law target is never a node, so the all-targets rate is low by construction.
- **Local whole-book dry run:** the bar passes; 1,245 chunks (rule 974, definition 54, repealed 4, history_note 213); the largest is 800 tokens; about 80 s.
- **Real ingest order:** the bar, then the collection if missing, then **every chunk embedded**, each vector's size checked against `QDRANT_VECTOR_DIM`, and only then the edition's old points deleted and the new ones upserted, followed by a count check. An Ollama failure, a chunk past the window or a wrong-size vector therefore leaves the previous ingest intact. A re-run replaces that book and edition and nothing else.
- **Exit codes:**

  | Code | Meaning |
  |---|---|
  | 0 | done |
  | 1 | the bar failed; a vector's size differs from `QDRANT_VECTOR_DIM`, or `QDRANT_VECTOR_DIM` is 0; an embedding was over the window; the point count after the ingest differs from the chunk count |
  | 2 | refused: unregistered book, no PDF at the path, or a hash that is not the registered edition |

- **On the VM** the command runs inside the backend container, whose image excludes `data/`: the PDF must be at `data/books/` and `data/` mounted for the dry-run files. See [deploy-vm.md](deploy-vm.md#reference-books-not-yet-rolled-out).

### Search and lookup

Two tools, in the research agent only (`_build_agent` in `skills/legal_research/legal_research.py`). Not in contract review, doc-chat or contract generation. The agent now sends `num_ctx = ollama_num_ctx`, the server's resident window, so Ollama doesn't reload the model for it; thinking is as it was.

- **`search_books(query, include_history_notes=False)`:**
  1. The query is embedded (`embed_query`).
  2. Qdrant runs a dense and a BM25 prefetch under one `kind` filter, `BOOK_DENSE_CANDIDATES` and `BOOK_SPARSE_CANDIDATES` each, and fuses them (RRF). The filter sits inside each prefetch, so the candidate cut counts only searchable chunks.
  3. The reranker (the same one as above; with it down or unset, the fused order stands).
  4. The top `BOOK_SEARCH_TOP_K`, then `BOOK_MIN_SCORE`.

  The default filter is `rule` and `definition`. History notes come only when asked for, and `repealed` stubs only by lookup. **Treaties stay in** (they are `rule`s by kind), and every result prints its `source_type`.
- **`lookup_book(reference)`:** a `ref_id` or a citation: `§ 107`, `17 U.S.C. §122(f)(1)`, `section 101 'joint work'`. It returns the node's chunks in document order up to `BOOK_LOOKUP_MAX_TOKENS`, then lists the ids of the rest (§115 alone is 22,023 tokens). A history note's id returns the note. This is the only way to reach a `repealed` stub.
- **Output** is one block per passage: `[n] ref_id: <id>`, then label, kind and source type, the heading, the body, and the ids of its history notes and effective-date note. **No page and no edition is printed**, so the model has none to copy.
- **A failed read** (Qdrant or Ollama down) returns "Book search is unavailable right now" to the agent, which may answer without the book, and records `book_search_failed` (silent). Both tools have a span (`tool.search_books`, `tool.lookup_book`) carrying the ids returned.

### Citations

- **The model cites `ref_id: <id>`,** exactly as the tool printed it. The prompt also says never to write a page or an edition, and that a history note is amendment history and a treaty is not U.S. law.
- **What counts as retrieved:** an id printed after a `ref_id:` marker in a book tool's result (`retrieved_book_refs`). Ids elsewhere in the output (a chunk's history notes, the rest of a long lookup) were not shown and don't count. The state holds them as `book_refs_retrieved` (the conversation's, oldest first, the last 500, in the checkpoint), `book_refs_turn` (this turn's) and `book_citations`.
- **The check** runs once per turn, in `risk_assessor` ([`rag/book_citations.py`](../rag/book_citations.py)). A cited id is valid when the conversation retrieved it, or it sits inside a chunk the conversation retrieved, **and** it exists in the stored book. Each invalid one adds a high flag, which routes the turn to attorney review:
  - *Cites a reference that was not retrieved: `<id>`*
  - *Could not verify a cited reference: `<id>`* (Qdrant failed, the stored node can't be rendered, or a retrieved id is gone from the store after a re-ingest)
  - *The answer states a page itself* (`p. 12`, `pp. 12–13`, `page 12` in the text, flagged and left in place)

  A turn that cites only the book, validly, passes the "must cite a source" rule.
- **A turn with an attached document skips the check.** The book isn't wired into doc-chat, and a `ref_id`-shaped token in a contract mustn't flag the turn.
- **Rendering:** `output_formatter` replaces each cited id with the citation built **from the stored node** (label, kind, pages), never from the model's text, e.g. `17 U.S.C. §122(f)(1) — Circular 92 (Dec. 2025), p. 143`. An invalid one renders `[unverified reference: <id>]`. There are six formats: statute, definition, another title, an uncodified Act, a history note, a treaty (`… (treaty, not U.S. law)`). `report.sources` gains `{ref_id, citation}` for each valid one. `llm_response` keeps the raw ids, for history and the audit.
- **Audit:** `audit_log.sources` is JSONB, `{doc_ids, ref_ids_retrieved, ref_ids_cited}`, this turn's only; `{}` on rows from before ([operations.md](operations.md#reference-books)).

### Book settings

All are on `RECORDED` in `api/runtime_facts.py`, so every `turn_log` row keeps the values it ran with. `@lru_cache`'d: restart after a change.

| Setting | Default | What it does |
|---|---|---|
| `BOOK_COLLECTION` | `legal_books` | the Qdrant collection |
| `BOOK_CHUNK_MAX_TOKENS` | `800` | chunk budget in embedding-model tokens, context header included; changing it moves chunk boundaries, never `ref_id`s |
| `BOOK_SEARCH_TOP_K` | `8` | passages `search_books` returns |
| `BOOK_DENSE_CANDIDATES` / `BOOK_SPARSE_CANDIDATES` | `30` / `30` | candidates each prefetch fetches before fusion |
| `BOOK_MIN_SCORE` | `0` (off) | drops hits scoring below it. It compares the rerank score when the reranker ran, else the fused score, which are not on one scale; off until a test set calibrates it |
| `BOOK_LOOKUP_MAX_TOKENS` | `6000` | the text one `lookup_book` call returns |

### Measuring retrieval

`BOOK_SEARCH_TOP_K=10 uv run python -m evals.books.run_retrieval <questions file>` scores `search_books` and `lookup_book` against a question set: Recall@5, Recall@10, MRR, nDCG@10 and the not-found rate. A line is `question | gold ids | type`, with type `search`, `citation` or `none` ([`questions.example.txt`](../evals/books/questions.example.txt) shows the shape). It refuses to run below `BOOK_SEARCH_TOP_K=10`, because the @10 numbers would then describe eight results. It needs Ollama and Qdrant, so it is not in `scripts/check.sh`. **No test set exists yet**; the chunk budget, the embedding model and `BOOK_MIN_SCORE` wait for one (roadmap).

### Known limits

- **The check proves that a citation exists, not that it supports the claim.** A real, retrieved passage cited for something it doesn't say passes.
- **Reachable from one path only:** research with no document attached. Locally that is Chainlit and `POST /api/query`; on the VM only the API, because Chainlit isn't deployed there. The Word Chat tab (doc-chat) and contract review can't reach it; precedence and access are deferred (roadmap).
- **Chainlit's review screen shows raw `ref_id`s.** During attorney review it displays the raw `llm_response`, and so does an interrupt payload. When the attorney submits a revised answer, Chainlit's revision path renders it with the earlier citation list. All local only.
- **Contract ingest now fails loudly** on a chunk over the embedding window (see Ingest pipeline above).
- **The BM25 tokenizer on legal tokens** (`§`, `(a)`, `104A`) is unmeasured. Direct lookup covers citations.
- **A looked-up split node larger than `BOOK_LOOKUP_MAX_TOKENS` shows only its leading parts.** The reply says so and points to a narrower search for the rest.
- **The research agent's calls are one condition away from a known Spark crash.** It sends tool calls at `num_ctx` 131072 with thinking on; the Ollama crash (ollama#17434, see [CLAUDE.md](../CLAUDE.md#backend)) needs thinking off as well. Not tried on Spark: it is another team's host.
- **Short chunks** (under 60 tokens; 31 in chapters 1 and 5) stay as they are; whether to merge them is open.
