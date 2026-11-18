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

**The legal reference books are built** (owner's scope, 2026-10-30; on `feat/reference-books` since 2026-11-03, integrated locally, not yet on the VM), starting with Circular 92 (Title 17 U.S.C.): their own collection with no `client_id`, their own parser and chunk model, and citations by `ref_id` that code checks and renders. The decisions are in [decisions/](decisions/README.md) (2026-10-30, *reference book* and *reference books*), and the design is [Reference books in RAG](superpowers/specs/2026-10-30-reference-books-design.md).

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

> Built on `feat/reference-books` (2026-11-03) and integrated locally after the owner checked the sample; the VM rollout is pending (see the [roadmap](roadmap.md) row *Index the legal reference books*). Design: [Reference books in RAG](superpowers/specs/2026-10-30-reference-books-design.md). Decisions: [RAG, not fine-tuning](decisions/2026-10-30-reference-books-rag-not-fine-tuning.md) · [parser](decisions/2026-10-30-reference-book-parser.md) · [chunk model](decisions/2026-10-30-reference-book-chunk-model.md) · [citations](decisions/2026-10-30-reference-book-citations.md) · [history notes](decisions/2026-10-30-reference-book-history-notes.md) · [keyword search in Qdrant](decisions/2026-10-30-keyword-search-in-qdrant.md).

The first book is the U.S. Copyright Office's **Circular 92 (Dec. 2025)**: Title 17 U.S.C. and related laws, a public-domain U.S. government work. The research agent can search it and look passages up. Code checks what the answer cites and renders the citation.

### The registry and the parser

- **Registry:** [`ingest/books/registry.py`](../ingest/books/registry.py) lists each book and edition: `circ92`, edition `2025-12` (Dec. 2025), the PDF's SHA-256, the licence. The ingest command refuses an unregistered id and a PDF whose hash differs. A new edition is a new entry, never an edit.
- **Parser:** a layout front end (PDF words to lines to a tree of labelled nodes with printed pages) and a back end (tree to chunks, cross-references, note links). Rules that can't be general are **hand overrides**, one file per book and edition ([`overrides-2025-12.json`](../ingest/books/circular92/overrides-2025-12.json)). Every entry carries its reason. Five kinds exist: `label_level`, `hyphen`, `xref_target` (one path, several, or `unparsed`), `text_owner` and `note_home`. The Dec. 2025 file holds 66 entries: 57 hyphens, 4 xref targets, 3 note homes, 2 text owners.
- **`ref_id`:** `<book>-<edition>:<path>`, for example `circ92-2025-12:usc17-s107`. It names a **node** of the book (a section, a subdivision, a definition, a note), never a chunk, so changing the chunk budget moves chunks and leaves every id valid. A chunk has its own key (`<ref_id>`, or `<ref_id>~lead`, `~tail`, `~p2`…), never cited. The point id is a uuid5 of that key.

### The collection

- **Name:** `legal_books` (`BOOK_COLLECTION`), written and read by [`rag/book_store.py`](../rag/book_store.py) and [`rag/book_search.py`](../rag/book_search.py). Those functions name only this collection and take neither a collection nor a `client_id`.
- **No `client_id`.** The collection holds no client data, so rule 3 in [CLAUDE.md](../CLAUDE.md#hard-rules-from-memory--past-incidents) is about the other collections ([decision](decisions/2026-10-30-reference-book-chunk-model.md)).
- **Vectors:** a dense `dense` (`QDRANT_VECTOR_DIM`, cosine) and a sparse `bm25` (IDF). **Qdrant computes the BM25 vector** from the chunk body (without the context header, so headers repeated across a section don't dominate), through a `QdrantClient(cloud_inference=True)`. That flag means "send the text to the server that embeds it", which is our own Qdrant, not a cloud. Without it the locked client (`qdrant-client` 1.18.0) computes the same vector itself with its built-in BM25: byte-identical, no fastembed. The flag only makes Qdrant do the work. With the flag, which is what the code sends, nothing is downloaded, so constraint 7 holds; that the no-flag path downloads nothing too is not verified. That client-side path is a possible fallback if Spark's Qdrant can't compute BM25, not yet verified offline ([roadmap](roadmap.md)).
- **Payload:** the chunk (`BookChunk`), with `kind` (`rule`, `definition`, `history_note`, `repealed`), `source_type` (`usc_title`, `uncodified_act`, `treaty`), `jurisdiction` (`US`; `international` for the treaty appendix), pages, `ancestor_ref_ids` and `sub_ref_ids`. Beside `ancestor_ref_ids`, index for index, `ancestor_labels` and `ancestor_pages`: each node's label and its own page span (its whole subtree). A node with no chunk of its own is cited and paged from them, so it keeps a page that only its own text is on, such as a heading line the chunker drops (`(d) Statutory License … Systems.—`); paged from the chunks below instead, §111(d) and five other nodes started a page late (final review, 2026-11-03). **Indexes** on `ref_id`, `book`, `edition`, `kind`, `source_type`, `jurisdiction`, `ancestor_ref_ids` and `sub_ref_ids`.
- **Created by the ingest command** when missing. An existing one is checked first: if its dense vector isn't `QDRANT_VECTOR_DIM` long, the ingest refuses (exit 1) before it embeds or deletes anything. A collection made with another vector size has to be recreated.
- **Never written by `POST /api/ingest`,** which answers 400 when its `collection` field names `BOOK_COLLECTION`: a client document can't land here.

### Ingest

```bash
uv run python -m scripts.ingest_book <book_id> <pdf_path> [--dry-run]
# locally, the PDF is in the repo:
uv run python -m scripts.ingest_book circ92 tests/fixtures/books/circular-92-2025-12.pdf --dry-run
```

- **Dry run first.** It parses and chunks the whole book (tokens are counted by Ollama on the embedding model, so Ollama must be up) and builds the report. It writes `<ref_prefix>-<UTC stamp>-{chunks.jsonl, chunks.tsv, report.json, sample20.tsv}` to `data/deploy-logs/` and never touches Qdrant; it exits 1 when the bar fails. `sample20.tsv` is 20 random chunks with their text: a person checks them against the printed page, text and page number, before the first ingest.
- **The bar.** A real ingest refuses (exit 1) unless the report passes all eight:
  1. 152 Title 17 sections are nodes;
  2. 192 footnote markers are linked to their notes, with no scope where markers and notes differ;
  3. 20 appendices are parsed, each with its `source_type`;
  4. no label line is left out of sequence;
  5. no chunk is over the token budget (a chunk past the embedding window counts as over);
  6. no override is unused;
  7. no line-end hyphen is undecided;
  8. every node of the tree, resolved over the chunk list by the function that resolves a citation (`resolve_nodes`, which `fetch_nodes` runs over what Qdrant returns), has the tree's label, kind, source type, appendix and pages. A node it gets wrong would reach the attorney as a verified citation with that wrong label or page.

  The cross-reference resolution rate is reported (Title 17 targets, and all targets), not gated: a Public Law target is never a node, so the all-targets rate is low by construction.
- **Local whole-book dry run:** the bar passes; 1,245 chunks (rule 974, definition 54, repealed 4, history_note 213); the largest is 800 tokens; about 80 s.
- **Real ingest order:** the bar, then the collection if missing (an existing one's dense size checked), then **every chunk embedded**, each vector's size checked against `QDRANT_VECTOR_DIM`, and only then the edition's old points deleted and the new ones upserted, followed by a count check. An Ollama failure, a chunk past the window or a wrong-size vector therefore leaves the previous ingest intact. A re-run replaces that book and edition and nothing else.
- **Exit codes:**

  | Code | Meaning |
  |---|---|
  | 0 | done |
  | 1 | the bar failed; a vector's size, or the existing collection's dense size, differs from `QDRANT_VECTOR_DIM`, or `QDRANT_VECTOR_DIM` is 0; an embedding was over the window; the point count after the ingest differs from the chunk count |
  | 2 | refused: unregistered book, no PDF at the path, or a hash that is not the registered edition |

- **On the VM** the command runs inside the backend container, whose image excludes `data/`: the PDF must be at `data/books/` and `data/` mounted for the dry-run files. See [deploy-vm.md](deploy-vm.md#reference-books-not-yet-rolled-out).

### Search and lookup

Two tools, in the research agent only (`_build_agent` in `skills/legal_research/legal_research.py`). Not in contract review, doc-chat or contract generation. A research turn from the Word pane is always doc-chat, so the Chat tab can't reach them (see *Known limits*). The agent now sends `num_ctx = ollama_num_ctx`, the server's resident window, so Ollama doesn't reload the model for it; thinking is as it was.

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
- **What counts as cited:** an id bare, in backticks, after a `ref_id:` marker, or in markdown italics (`_<id>_`). Labels glued to an id are part of it: `…:usc17-s122(f)(1)` is checked, paged and rendered as the sub-path `…:usc17-s122-f-1`, the labels replaced with the rest. When the labels don't make a valid id, the whole token is what was cited, and it renders unverified.
- **What counts as retrieved:** an id printed after a `ref_id:` marker in a book tool's result (`retrieved_book_refs`). Ids elsewhere in the output (a chunk's history notes, the rest of a long lookup) were not shown and don't count. The state holds them as `book_refs_retrieved` (the conversation's, oldest first, the last 500, in the checkpoint), `book_refs_turn` (this turn's) and `book_citations`.
- **The check** runs once per turn, in `risk_assessor` ([`rag/book_citations.py`](../rag/book_citations.py)). A cited id is valid when the conversation retrieved it, or it sits inside a chunk the conversation retrieved, **and** it exists in the stored book. Each invalid one adds a high flag, which routes the turn to attorney review:
  - *Cites a reference that was not retrieved: `<id>`*
  - *Could not verify a cited reference: `<id>`* (Qdrant failed, the stored node can't be rendered, or a retrieved id is gone from the store after a re-ingest)
  - *The answer states a page itself* (`p. 12`, `pp. 12–13`, `page 12` in the text, flagged and left in place)

  A turn that cites only the book, validly, passes the "must cite a source" rule.
- **A turn with an attached document skips the check.** The book isn't wired into doc-chat, and a `ref_id`-shaped token in a contract mustn't flag the turn. "Attached" is one test, `has_attached_document` in `graph/state.py` (`uploaded_docs` is non-empty, whatever its text), read by `legal_research` to choose doc-chat and by `risk_assessor` to skip the check.
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
| `BOOK_MIN_SCORE` | `0` (off) | drops hits scoring below it. It compares the rerank score when the reranker ran, else the fused score, which are not on one scale; off until it is calibrated on the test set's `none` questions (*Measuring retrieval*) |
| `BOOK_LOOKUP_MAX_TOKENS` | `6000` | the text one `lookup_book` call returns |

### Measuring retrieval

`BOOK_SEARCH_TOP_K=10 uv run python -m evals.books.run_retrieval <questions file>` scores `search_books` and `lookup_book` against a question set: Recall@5, Recall@10, MRR, nDCG@10, the citation hit rate and the not-found rate. A line is `question | gold ids | type`, with type `search`, `history`, `citation` or `none` ([`questions.example.txt`](../evals/books/questions.example.txt) shows the shape). `history` is a search with the history notes included, for an answer that lives only in a note, such as an effective date; it is scored like `search` and reported in its own row. The runner refuses to run below `BOOK_SEARCH_TOP_K=10`, because the @10 numbers would then describe eight results. It needs Ollama and Qdrant, so it is not in `scripts/check.sh`.

**The test set** ([design](superpowers/specs/2026-11-18-book-retrieval-test-set-design.md)) is four files in `evals/books/sets/`, one per source, so a score always says where its questions came from. No attorney wrote or checked it.

| File | Questions | Where they come from |
|---|---|---|
| [`lse-search.txt`](../evals/books/sets/lse-search.txt) | 60 `search`: 18 `answers`, 42 `governs` | Law Stack Exchange, word for word (Hugging Face `ymoslem/Law-StackExchange`, revision `ab2dbaad`, CC BY-SA): copyright questions with no foreign-jurisdiction tag and an answer citing 17 U.S.C., walked in a seeded random order. Gold chosen by Claude from the book's text |
| [`lse-none.txt`](../evals/books/sets/lse-none.txt) | 15 `none` | Law Stack Exchange, word for word: trademark and patent questions that no node of the book answers |
| [`citations.txt`](../evals/books/sets/citations.txt) | 15 `citation` | Claude, from the book's structure, in mixed citation forms; the gold is the node the citation names |
| [`written.txt`](../evals/books/sets/written.txt) | 15: 7 `history`, 8 `search` | Claude, for what the site never asks: effective dates in notes, TRIPs arts. 35–38, Titles 18 and 28, definitions in plain words. Scored apart from `lse-search.txt` |

- **Provenance, per file.** The two `lse-*` headers state the source, its revision and licence, the selection rule, the pool size and the seed. In them, a comment line before each question gives its link and licence, then, in `lse-search.txt`, what the answerers cited (a hint, often wrong) and a one-line reason for the gold, and in `lse-none.txt`, the question's tags and why no node of the book answers it. `written.txt`'s header states its source and its coverage and wording rules; it has no seed, as nothing was sampled. Each of its questions has a comment line (`# written by Claude · <type> · gold: …`) with the reason for the gold, quoting the book, and no link, licence or answerers' citations, since there are none. `citations.txt` has a three-line header and no comment per question: each gold is the node its citation names. A gold is the smallest node that holds the answer, read from the parsed book, never from search. `answers` means the gold's text settles the question by itself; `governs` means the gold is the provision the question turns on, while facts or case law decide it.
- **Frozen** at commit `01d2969`, before the first scored run. After a scored run a gold changes only to fix a labelling error, with the reason written down, never because search missed it.
- **How far the gold can be trusted.** A second agent labelled the 90 questions of `lse-search.txt`, `lse-none.txt` and `written.txt` again from the book's text alone, without the first labels or the answerers' citations, and each disagreement was settled by reading the book. `lse-search.txt`: the two golds were identical on 5 of 60 (8%) and shared at least one node on 48 of 60, mean Jaccard 0.39 (a question's Jaccard is the overlap of its two golds: the nodes both named ÷ the nodes either named); of the 55 settled, the first reading was kept on 41, the second taken on 3, and a mix of both on 11. `lse-none.txt`: 15 of 15. `written.txt`: identical on 10 of 15, mean Jaccard 0.82. The second reader saw only the question text. For the `lse-*` files the first reading also read the question's body, and the owner decided (2026-11-18) that a gold may rely on facts in the body that the scored text doesn't carry; for `written.txt` the first reader was the questions' author, writing from the book. The owner then checked 10 entries against the book (5 from `lse-search.txt`, 1 from `lse-none.txt`, 4 from `written.txt`) and corrected none. `citations.txt` had no second reading: its gold is what `parse_reference` returns for the citation, and a test checks that.

Run one file at a time, from the repo root:

```bash
BOOK_SEARCH_TOP_K=10 uv run python -m evals.books.run_retrieval evals/books/sets/lse-search.txt
# answers and governs apart: keep the question under each `answers` provenance line, then score that file
awk '/^# lse:.* · answers · /{k=1;next} /^#/{next} k&&NF{print;k=0}' evals/books/sets/lse-search.txt > "${TMPDIR:-/tmp}/answers.txt"
BOOK_SEARCH_TOP_K=10 uv run python -m evals.books.run_retrieval "${TMPDIR:-/tmp}/answers.txt"   # and the same with `governs`
```

**Baseline: 2026-11-18, commit `01d2969`.** Settings: `BOOK_SEARCH_TOP_K=10`, `BOOK_CHUNK_MAX_TOKENS=800`, embeddinggemma (`embeddinggemma:latest`, digest `82f094e4c0e19bc4208c692996d4f1ecf14e82a8dee579ee1d2ca1175260058e`, on the local Ollama), `BOOK_DENSE_CANDIDATES` and `BOOK_SPARSE_CANDIDATES` 30 each, `BOOK_MIN_SCORE=0` (off). The reranker was enabled with `RERANKER_URL` empty, so every call failed and the fused order stood (`Reranker error: Request URL is missing an 'http://' or 'https://' protocol`, once per searched question): these numbers are un-reranked. Local Qdrant, `legal_books` with 1,245 points. Each run took 1 to 4 s of wall time (`lse-search.txt` 4 s).

Each cell is the baseline run. In brackets, where the value moved, its range over 20 repeat runs on the same settings (see the last note below); a cell without a range did not move in any of them.

| File | Type | n | Recall@5 | Recall@10 | MRR | nDCG@10 | Hit rate | Not found |
|---|---|---|---|---|---|---|---|---|
| `lse-search.txt` | search | 60 | 0.348 (0.312–0.356) | 0.416 | 0.297 (0.259–0.304) | 0.273 (0.245–0.275) | – | – |
| `lse-search.txt`, `answers` only | search | 18 | 0.407 (0.352–0.435) | 0.454 | 0.317 (0.315–0.346) | 0.287 (0.286–0.309) | – | – |
| `lse-search.txt`, `governs` only | search | 42 | 0.322 (0.296–0.322) | 0.399 | 0.263 (0.239–0.288) | 0.249 (0.231–0.266) | – | – |
| `lse-none.txt` | none | 15 | – | – | – | – | – | 0.000 |
| `citations.txt` | citation | 15 | – | – | – | – | 1.000 | – |
| `written.txt` | search | 8 | 0.562 (0.562–0.688) | 0.938 | 0.335 (0.276–0.412) | 0.432 (0.407–0.490) | – | – |
| `written.txt` | history | 7 | 0.429 | 0.571 | 0.202 | 0.293 | – | – |

The `answers` and `governs` rows are separate runs of the two split files, so their ties fell differently from the main row's. Weighted by n they recompose its Recall@10, which ties don't move, but not its MRR or nDCG@10.

- **`lse-search.txt` is the main number.** 18 of its 60 questions had every gold node in the top 10, 15 had some, and 27 had none. For `history`, the gold note was in the top 10 for 4 of 7 questions and absent for 3.
- **`answers` and `governs`.** The spec's warning sign was `governs` scoring far above `answers`: broad nodes easy to find, propping up the main number. It scored below. The `answers` subset is small, though: one question is worth up to 0.056 of its Recall@10, so the split is noisy.
- **Not-found is 0 by construction while the floor is off.** With no floor, search returns its top 10 whatever they score, so no `none` question can come back empty. `lse-none.txt` is there to calibrate `BOOK_MIN_SCORE`. Its hardest negative is lse:88930 (when combining integrated circuits yields something patentable), which sits near the mask-work text, §902(b)(2) on originality: a floor has to drop that one without dropping real answers. The book lacks 18 U.S.C. §2320 (counterfeit marks) and 28 U.S.C. §1498(a) (patents used by the government; its §1498 has only (b), (c) and (e)), so a question on either is a `none` although the book has text next to it.
- **Order inside a tie changes from run to run.** RRF gives a chunk only the dense list found and a chunk only BM25 found at the same rank the same fused score (0.5 for each list's first), and Qdrant returns tied points in a different order on different calls; the query embedding is the same each time. Over the 20 repeat runs (the ranges in the table), Recall@5, MRR and nDCG@10 moved on every `search` row. Recall@10, the hit rate, the not-found rate and all four `history` metrics stayed fixed. Until ties break the same way every time, a settings change that moves a metric by less than its range is not a finding.

### Known limits

- **The check proves that a citation exists, not that it supports the claim.** A real, retrieved passage cited for something it doesn't say passes.
- **Reachable from one path only:** research with no document attached. Locally that is Chainlit and `POST /api/query`; on the VM only the API, because Chainlit isn't deployed there. The Word Chat tab (doc-chat) and contract review can't reach it; precedence and access are deferred (roadmap).
- **A Word research turn is always doc-chat, even on a blank document.** The pane sends the document's text, `""` for a blank one, and the query route attaches it whatever it is when the request carries the pane's headers (`X-Pane-Build` or `X-Office-Platform`; `ClientInfo.from_pane`). Without that, a blank document took the KB path with the book, and a flagged citation there interrupted a turn the pane can't resume, which ChatTab shows as raw text. Requests without those headers (Chainlit, direct API calls) attach only text they send.
- **Chainlit's review screen shows raw `ref_id`s.** During attorney review it displays the raw `llm_response`, and so does an interrupt payload. When the attorney submits a revised answer, Chainlit's revision path renders it with the earlier citation list. All local only.
- **Contract ingest now fails loudly** on a chunk over the embedding window (see Ingest pipeline above).
- **The BM25 tokenizer on legal tokens** (`§`, `(a)`, `104A`) is unmeasured. Direct lookup covers citations.
- **A looked-up split node larger than `BOOK_LOOKUP_MAX_TOKENS` shows only its leading parts.** The reply says so and points to a narrower search for the rest.
- **The research agent's calls are one condition away from a known Spark crash.** It sends tool calls at `num_ctx` 131072 with thinking on; the Ollama crash (ollama#17434, see [CLAUDE.md](../CLAUDE.md#backend)) needs thinking off as well. Not tried on Spark: it is another team's host.
- **Short chunks** (under 60 tokens; 31 in chapters 1 and 5) stay as they are; whether to merge them is open.
