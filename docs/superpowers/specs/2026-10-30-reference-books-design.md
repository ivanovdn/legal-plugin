# Reference books in RAG: Circular 92 (Title 17) — design

**Related:** [roadmap.md](../../roadmap.md) — *Index the legal reference books for retrieval-augmented checking* · decisions (2026-10-30): [RAG, not fine-tuning](../../decisions/2026-10-30-reference-books-rag-not-fine-tuning.md) · [parser](../../decisions/2026-10-30-reference-book-parser.md) · [chunk model](../../decisions/2026-10-30-reference-book-chunk-model.md) · [citations](../../decisions/2026-10-30-reference-book-citations.md) · [history notes](../../decisions/2026-10-30-reference-book-history-notes.md) · [keyword search in Qdrant](../../decisions/2026-10-30-keyword-search-in-qdrant.md)

- **Date:** 2026-10-30
- **Status:** Built on `feat/reference-books` (2026-11-03) and integrated locally with the owner; the VM rollout is pending. Approved by the owner 2026-11-02.
- **Branch:** `feat/reference-books`, cut from `main` once this spec was approved.
- **Scope:** the first slice of the RAG work, which the owner made the next scope on 2026-10-30. One book is ingested, searchable and citable from the research path. Who else may reach it (contract review, the Word Chat tab) is deferred.

---

## Context & motivation

### 1. RAG was never properly set up
Retrieval today covers client documents only. It runs as dense search with a reranker that has never run, and keyword search is off ([rag.md](../../rag.md)). The owner made RAG the next scope, starting with the legal reference books. The first book is the U.S. Copyright Office's **Circular 92 (Dec. 2025)**: the text of Title 17 U.S.C. with appendices A–T, 486 pages, a public-domain U.S. government work.

### 2. The decisions this spec carries out
- **Retrieval, not fine-tuning or a memory layer.**
- **A new parser behind an admin command:** a layout front end and a layout-free back end, with an overrides file per book and edition.
- **A `BookChunk` with no `client_id`,** in its own collection, written only from a book registry. It has `jurisdiction` and `source_type` fields, and the edition is part of the `ref_id`.
- **The model cites `ref_id`s;** code checks each one against what the conversation retrieved and renders the readable citation.
- **History notes are their own kind,** out of default search. Rules carry their notes' ids and flag effective-date notes; treaties stay in default search, labelled.
- **Keyword search runs in Qdrant** (BM25 sparse vectors, fused by Qdrant).

### 3. What was measured
From the Circular 92 exploration, 2026-10-30. The report, the prototype scripts and the ch.1 + ch.5 chunk list are in the research vault, under `research/reference-books/claude-code/`.
- **Tokens:** embeddinggemma's tokenizer averages 4.46 characters per token. The section median is 442 tokens; §115 runs to 22,023; 18 sections exceed 2,048; §101 is 3,801 tokens holding 54 definitions.
- **Chunks at an 800-token budget:** ch.1 + ch.5 give 568 chunks: 436 rules, 54 definitions, 77 history notes and 1 repealed stub. Seven notes exceed 800 tokens and two exceed 2,048.
- **The parser rules:** footnote markers by font, 192 of 192; labels at their level, 99.4%; printed page = PDF page − 22.
- **Cross-references:** in ch.1 + ch.5, 98% of 1,068 resolve to a node that exists.
- **Embedding truncation.** `rag/embeddings.py` sends no `truncate` flag, so Ollama silently cuts input past 2,048 tokens. With `truncate: false`, Ollama answers HTTP 400 instead.
- **BM25 in Qdrant.** Local Qdrant 1.17.0 computes BM25 sparse vectors itself, from text, at upsert and at query. Verified 2026-10-30 through REST and through `qdrant-client` 1.18.0 with `cloud_inference=True`. Without that flag the client tries to embed locally with fastembed, which isn't installed. Spark's Qdrant 1.17.1 is not yet checked. (2026-11-03: on `qdrant-client` 1.18.0 the client computes the same vector itself when the flag is off, with its built-in BM25: byte-identical, no fastembed. The flag only makes Qdrant do the work.)

---

## Goals
1. **Ingest:** Circular 92 goes into a `legal_books` collection through an admin command, and only when the book is registered and the file's SHA-256 matches.
2. **Parse:** the book becomes a tree, then chunks, with:
   - printed pages, down to each line;
   - the fields `kind`, `source_type` and `jurisdiction`;
   - cross-references, note links and effective-date flags.
3. **Search:** a dense and a sparse (BM25) search under one filter, fused by Qdrant, then the reranker. History notes are out unless asked for.
4. **Look up:** a passage directly by `ref_id`, or by a U.S.C. citation ("§107", "17 U.S.C. 122(f)(1)").
5. **Cite:** research answers cite `ref_id`s. Code checks each against the conversation's retrievals and the parsed tree, flags the rest, and renders the readable citation.
6. **Stop silent truncation** in `rag/embeddings.py`, for every caller.
7. **Make the open decisions measurable:** the chunk budget becomes a setting, and a retrieval eval runner will take the test set when it exists.

## Non-goals
- **No other surfaces for the book.** It is not wired into contract review, the Word Chat tab (the doc-chat path) or contract generation; precedence and access are deferred. In this build the book is reachable from the research path without a document. Locally that means Chainlit and `POST /api/query`; on the VM only the API, because Chainlit isn't deployed there.
- **No migration of `legal_docs` and `case_history` to hybrid search.** That is its own roadmap row; this build creates only the new collection. It does build the Query API search code that migration can reuse.
- **No verbatim-quote check or verifier** (citations decision: later).
- **No choice of chunk budget or embedding model.** Both wait for the test set.
- **No test set.** It is separate work, written in the attorneys' style and checked by an attorney. The runner is in scope.
- **No second book.** The registry and the back end allow one; the front end is Circular 92's.
- **No `client_id` binding in the client tools** (deferred row).

## Anti-goals
Any of these would make the build a failure even if everything else works:
- A citation with a wrong page or edition reaching the attorney unflagged.
- A client document in the book collection, or a book search reaching a client collection.
- A history note or a treaty presented as U.S. statutory text.
- Silent truncation anywhere on the ingest path.
- A network call to the internet from the VM at runtime (constraint 7), for example a model download.

---

## Decisions (spec level)

### D1 — The `ref_id` names a node of the book, not a chunk
`<book>-<edition>:<path>`, for example `circ92-2025-12:usc17-s122-f-1`.
- **A `ref_id` is a node of the parsed tree:** a section, a subdivision, a definition or a note. It never depends on the chunk budget or the tokenizer. Citations, `book_refs_retrieved` and the audit always hold node ids, so ids already written to history and the audit stay valid when the budget changes.
- **The path grammar** follows the prototype, with the book-and-edition prefix in front:
  - a section is `usc17-s107`; a subdivision is `usc17-s122-f-1`, keeping each label's case, as in `usc17-s122-a-4-B`;
  - a definition is `usc17-s101-def-joint-work`;
  - a history note is `ch1-n3`; an appendix note is `appB-n2`;
  - another title in an appendix is `usc18-s2319`;
  - an uncodified Act is its Public Law number plus its section, for example `pl105-304-s101`;
  - TRIPs is its article and paragraph, for example `trips-art38-1`. Circular 92 prints only Part II, Section 6 of TRIPs (Articles 35–38, app. S); the Agreement's copyright articles are not in the book.

  *(My call, 2026-10-30: the Act and treaty forms.)*
- **Characters** are only `[A-Za-z0-9_:-]`, so the id is one token in prose and can't absorb a trailing period.
- **Chunks have their own key.** A chunk that holds a whole node uses the node's `ref_id` as its key. A piece of a node that had to be split gets a suffix: `<ref_id>~lead` for the text before its first child, `<ref_id>~tail` for text after its last child, and `<ref_id>~p2`, `~p3`… for parts of a split note. Point ids are `uuid5(<fixed namespace>, chunk key)`. Keys are never cited.
- **Citing a split node:** citing `ch1-n56` is valid when any of its parts was retrieved. The rendered citation then gives the node's whole page span.
- **Paging a node with no chunk of its own** (2026-11-03, final review C1): each chunk carries `ancestor_pages`, index for index with its `ancestor_ref_ids`: every node above it with that node's own span, its whole subtree. Such a node is paged from there, never from the span of the chunks below it, which loses a page that only its own text is on: a heading line the chunker drops, as in §111(d), pp. 33–38, not 34–38.

### D2 — The collection
- **Name:** `legal_books` (`BOOK_COLLECTION`).
- **Vectors:** a named dense vector `dense` (768, cosine) and a named sparse vector `bm25` (modifier `idf`).
- **BM25 is computed by Qdrant** from the chunk text: `models.Document(text=…, model="qdrant/bm25")`, through a `QdrantClient(…, cloud_inference=True)`. That flag sends the text to our own Qdrant, not to a cloud. No fastembed and no model download, so constraint 7 holds.
- **Payload indexes** at creation: `ref_id`, `book`, `edition`, `kind`, `source_type`, `jurisdiction`.
- **The text BM25 sees** is the chunk body without the context header, so headers repeated across a section don't dominate term frequencies. The builder checks this against the eval runner.

### D3 — Embedding truncation is an error, except at query time
`_ollama_embed` sends `"truncate": false`.
- **Ingest** (`embed_texts`, contracts included): an over-length input raises an error naming the item and its length. The ingest stops, and nothing is silently cut.
- **Queries** (`embed_query`): on the over-length error, retry once with truncation allowed, and record a silent degradation, `embedding_query_truncated`. The turn still answers, and the trace says the query was cut. A long pasted question shouldn't fail the turn, but it must not pass unnoticed either.

### D4 — Chunking
- **Budget:** `BOOK_CHUNK_MAX_TOKENS` (default 800, header included).
- **Counting:** tokens are counted with the embedding model itself (`/api/embed`'s `prompt_eval_count`), cached by text. The 4.46 figure is never used as an estimate.
- **Splitting:** section → subsection → paragraph → …, only when a unit is over budget.
- **§101** gives one chunk per definition, plus one for its opening sentence.
- **History notes** give one chunk each, split by paragraph when over budget.
- **The context header** gives the citation, the section's title and the parents' headings. Chunks under 60 tokens (31 in ch.1 + ch.5) stay as they are for now; whether to merge them is open (test set).
- **Sub-paths:** each chunk stores the node `ref_id`s it contains, each with its page, so a citation below the chunk can be checked and given its own page. It stores the nodes above it the same way (`ancestor_ref_ids`, `ancestor_labels`, `ancestor_pages`), so a node with no chunk of its own can be too (D1).
- **Changing the budget or the tokenizer moves chunk boundaries, never `ref_id`s** (D1). A Mac and Spark running different Ollama builds may cut chunks differently, but they produce the same nodes.

### D5 — Classification
- **`kind`:** `rule`, `definition`, `history_note` or `repealed`.
- **`source_type`:** `usc_title`, `uncodified_act` or `treaty`.
- **`jurisdiction`:** taken from the registry entry (`US`); the treaty appendix (S, TRIPs) is `international`.
- **The front matter** (PDF pages 1–22) and the back matter (PDF pages 482–486: a folio page, blanks and the back cover) are not ingested. The registry takes the edition from the front matter once, by hand.
- **Effective-date notes:** a note is one if it matches `tak(e|es|en) effect|took effect|effective (date|upon|on)` (case-insensitive). That is 17 of 77 in ch.1 + ch.5, and the list is reviewed in the golden file.
- **Rule chunks carry two lists:** `history_notes` (the `ref_id`s of every note whose marker they hold) and `effective_date_notes` (the subset that are effective-date notes).

### D6 — Default search
- **The default filter:** `kind` ∈ {`rule`, `definition`} (`MatchAny`).
- **History notes** are searched only when the tool is asked to include them.
- **`repealed`** stubs are reached only by lookup.
- **Treaties are included,** with their `source_type` printed on every result.

The book search builds its own Qdrant filter. `vector_store`'s helpers can only test equality, and they aren't changed.

### D7 — Two tools, research path only
- **`search_books(query, include_history_notes=False)`:** dense and sparse prefetches (`BOOK_DENSE_CANDIDATES`, `BOOK_SPARSE_CANDIDATES`) under the D6 filter, RRF fusion in Qdrant, then the existing reranker (`rag/reranker.py`, which falls back to the fused order as today), then the top `BOOK_SEARCH_TOP_K`. `BOOK_MIN_SCORE` drops weaker results; its default is 0, meaning off until the test set calibrates it.
- **`lookup_book(reference)`:** accepts a `ref_id` or a U.S.C. citation such as "§ 107", "17 U.S.C. §122(f)(1)" or "section 101 'joint work'". It returns that node's chunks in order, up to `BOOK_LOOKUP_MAX_TOKENS`, then lists the remaining chunks' `ref_id`s (§115 alone is 22,023 tokens). A note's `ref_id` returns the note.
- **Fixed scope:** neither tool takes `client_id` or a collection argument. Both name `BOOK_COLLECTION` in code.
- **Where they're wired:** both go only into the research agent's tool list (`_build_agent` in `skills/legal_research/legal_research.py`). They are not added to contract review, doc-chat or contract generation.
- **Output:** one block per result. It names the passage but **prints no page and no edition**, so the model has nothing to copy. Those appear only after rendering. *(My call.)*

```
[1] ref_id: circ92-2025-12:usc17-s107
    17 U.S.C. §107 · rule · usc_title
    § 107 · Limitations on exclusive rights: Fair use
    <chunk text>
    history notes: circ92-2025-12:ch1-n40              (ids only)
    effective-date note: <ref_id>                      (only when one is linked; §107 has none)
```

### D8 — The citation check and rendering
- **Retrieved:** `ref_id`s are read only from the book tools' `ToolMessage`s, as whole tokens (`ref_id:\s*([A-Za-z0-9_:-]+)`). This is the same rule `retrieved_doc_ids` follows.
- **Conversation scope:** a new state field, `book_refs_retrieved`, accumulates across the session, with a union reducer capped at the 500 most recent ids. The checkpointer persists it the same way it persists `chat_history`. Without Redis (the stateless fallback), only this turn's retrievals count, so an earlier-turn citation is then flagged. That is rare, and it errs toward review.
- **Valid citation:** a cited `ref_id` is valid when it was retrieved in the conversation, or when it is a sub-path that lies under a retrieved chunk and exists in that chunk's sub-path list.
- **`_check_citations`:**
  - The answer counts as cited if it names a retrieved `doc_id` or title, as today, or at least one valid `ref_id`.
  - Each invalid `ref_id` adds a high flag: "Cites a reference that was not retrieved: <id>". High risk on a research turn routes to attorney review, as it does today.
  - A turn that used only the book, with valid citations, passes. Today it would hit "No citation possible".
- **Rendering:** `output_formatter` replaces each cited `ref_id`, along with its `ref_id:` marker and any backticks around it, with the readable citation in `report["response"]`. The citation is built from the stored payload, never from the model's text, using the sub-path's own page. An invalid one renders as `[unverified reference: <id>]`. `report["sources"]` gains the cited book passages.
- **The six citation formats:** the five of the citations decision plus one for treaties, e.g. `TRIPs Agreement, art. 38(1) — Circular 92 (Dec. 2025), app. S, p. <page> (treaty, not U.S. law)`. *(My call: the treaty format.)*
- **A page the model writes itself is flagged.** After rendering, any page reference in the response that the renderer didn't produce (`p. 12`, `pp. 12–13`, `page 12`) adds a high flag: "The answer states a page itself". The text is flagged, not stripped, so the attorney sees what the model wrote. A prompt rule cuts how often this happens, never the worst case (CLAUDE.md, *Chat edit-block parsing*). *(My call.)*
- **Raw ids stay raw:** `llm_response`, and therefore `chat_history` and the audit row, keep the raw ids.
- **Prompt:** `RESEARCH_SYSTEM_PROMPT` gains three principles:
  - cite book passages as `ref_id: <id>`, exactly as a tool printed it;
  - never write a page or an edition;
  - a history note is amendment history, and a treaty is not U.S. law.

  The `client_id` rule in the prompt narrows to client collections. No worked example (CLAUDE.md, *Chat edit-block parsing*).

### D9 — Audit (constraint 6)
`audit_log` gains `sources JSONB NOT NULL DEFAULT '{}'`, added by `ALTER TABLE … ADD COLUMN IF NOT EXISTS` as the table's other added columns were. `memory_writer` writes `{"doc_ids": […], "ref_ids_retrieved": […], "ref_ids_cited": […]}`, each for **this turn only**. That needs a per-turn state field, `book_refs_turn`, which the research skill sets every turn, beside the cumulative `book_refs_retrieved` that the citation check reads. *(My call.)*

### D10 — The registry and the ingest command
- **The registry** is `ingest/books/registry.py`, tracked in git. Each entry holds:
  - `id` (`circ92`), `title` and `short_title` ("Circular 92");
  - `edition_id` (`2025-12`) and `edition_label` ("Dec. 2025");
  - `jurisdiction`, `source` and `licence` ("public domain, U.S. government work");
  - `pdf_sha256`, `front_end` (`circular92`) and the path of its overrides file.
- **The command** is `python -m scripts.ingest_book <book_id> <pdf_path> [--dry-run]`. It runs these steps:
  1. Refuse an unregistered id or a hash mismatch.
  2. Parse and chunk.
  3. On `--dry-run`, write the chunk list (JSONL and TSV) to `data/deploy-logs/` and stop.
  4. Otherwise create the collection if it is missing (D2), and delete the points of this book and edition.
  5. Embed with `truncate: false`, upsert, and check the point count.
  6. Log a summary: book, edition, chunks by kind, the largest chunk in tokens, and the time taken.
- **The add-in's upload and `POST /api/ingest` can't reach it:** they keep routing by extension to the client parsers.

### D11 — The golden file and the PDF
- **The PDF goes into the repo** at `tests/fixtures/books/circular-92-2025-12.pdf` (6.2 MB, public domain), so the golden test runs in `scripts/check.sh` on any machine. `tests/` is excluded from the image.
- **The golden file tests the tree, not the chunks** *(my call)*. It is `tests/fixtures/books/circ92-ch1-ch5.jsonl`: one row per node of ch.1 and ch.5, holding the `ref_id`, kind, source type, pages, cross-references, note links and effective-date flag. Chunk boundaries depend on the tokenizer, so they can't be fixed without Ollama.
- **Chunking is tested separately** with a deterministic stand-in token counter. The real chunk list, counted by the model, is checked in the dry run.
- **It starts from the prototype's chunk list,** converted to the new names (`history_note`, `repealed`, prefixed `ref_id`s), **with the known bugs fixed first:**
  - the 4 labels in §506(a)(3), set one level too deep: fixed by the label rule (characters and sequence first, indent only as tie-break), or through the overrides file if the rule can't;
  - ch.5 n.8's "section 2323", which is 18 U.S.C.;
  - §504(c)'s Act reference.
- **The bar for the rest of the book** *(my call)*. The golden file covers ch.1 and ch.5; the dry run's report must show, for the whole book:
  - all 152 section headings present as nodes;
  - all 192 markers linked to their notes;
  - all 20 appendices parsed, each with its `source_type`;
  - every label either in sequence or covered by an override;
  - the cross-reference resolution rate, with the misses listed.

  A person then checks 20 randomly chosen chunks against the printed page, text and page number, before the first ingest.

### D12 — Settings
All go on `RECORDED` in `api/runtime_facts.py`:

| Setting | Default |
|---|---|
| `BOOK_COLLECTION` | `legal_books` |
| `BOOK_CHUNK_MAX_TOKENS` | `800` |
| `BOOK_SEARCH_TOP_K` | `8` |
| `BOOK_DENSE_CANDIDATES` | `30` |
| `BOOK_SPARSE_CANDIDATES` | `30` |
| `BOOK_MIN_SCORE` | `0` (off) |
| `BOOK_LOOKUP_MAX_TOKENS` | `6000` |

### D13 — Observability
- **Spans:** both tools are `@traced`, and their spans carry the `ref_id`s returned.
- **New degradation codes** in the closed vocabulary, both silent:
  - `book_search_failed`: the tool returns an error text, and the agent may answer without the book;
  - `embedding_query_truncated`: D3.
- **An unverified citation is a risk flag, not a degradation.** The attorney sees it through review.

---

## Design

### Modules (the layout is the builder's call; these are the boundaries)
- **`ingest/books/`:**
  - the registry;
  - `BookChunk`;
  - the Circular 92 front end: PDF words → lines → a tree of labelled nodes with pages, using the overrides;
  - the back end: tree → chunks, cross-references, note links and sub-paths;
  - the `ref_id` grammar and the citation renderer.

  The prototype's rules move here from the vault's `parse_book.py`, `labels.py`, `notes.py`, `xref.py`, `defs101.py` and `text.py`. They are rewritten as tested code, not copied.
- **`rag/book_search.py`:** the Query API search (D6, D7) and the lookup.
- **`rag/tools/search_books.py` and `rag/tools/lookup_book.py`:** the two tools and their output format.
- **`rag/embeddings.py`:** D3.
- **`graph/state.py`:** `book_refs_retrieved`. **`graph/nodes/risk_assessor.py`:** D8's check. **`graph/nodes/output_formatter.py`:** D8's rendering. **`graph/nodes/memory_writer.py` and `memory/db.py`:** D9.
- **`scripts/ingest_book.py`:** D10.
- **`evals/books/run_retrieval.py`:** reads a questions file in the format `question | gold ref_ids | type`, runs `search_books` and `lookup_book`, and reports Recall@5, Recall@10, MRR, nDCG@10 and the not-found rate. It needs Ollama and Qdrant, so it isn't part of `scripts/check.sh`.

### Text cleaning (the parser decision's rules)
- **Footnote markers** are lifted out of the text and linked.
- **Running heads and footers** are dropped.
- **Line-end hyphens** are joined when safe. The 50 undecided cases go to the overrides file.
- **Number breaks are re-joined** ("105-|304" → "105-304").
- **Small caps** lose their stray spaces.
- **TOC labels** are matched case-insensitively against body labels ("104a" against "104A").

---

## Verification

### Red first, then mutation-checked
Each test fails before its code exists, and fails again when the line it guards is reverted:
- **Parser:** markers by font, with the size ignored (including Appendix G's 10.2pt marker); a label's kind from its characters and sequence, with the indent as tie-break; page = PDF page − 22; the overrides applied with their reasons.
- **Golden file:** ch.1 + ch.5 parse to `circ92-ch1-ch5.jsonl` exactly.
- **`ref_id`:** the grammar round-trips, it is one token in prose, and a sub-path is accepted only under a retrieved chunk.
- **Registry:** an unregistered id is refused, and so is a hash mismatch.
- **Truncation:** an over-length input raises at ingest; at query time it retries once and records `embedding_query_truncated`.
- **The citation check:**
  - a book-only turn with valid citations passes;
  - an earlier-turn `ref_id` passes;
  - an id that was never retrieved is flagged;
  - an id appearing only in the answer is not counted as retrieved.
- **Rendering:** each of the five formats; a page span gets `pp.`; an invalid id renders `[unverified reference: …]`; `llm_response` stays raw.
- **Search filter:** notes are excluded by default and included on request; treaties are in default results, labelled; `repealed` stubs come only through lookup.
- **Tool scope:** the book tools appear only in the research agent's tool list.

### Integration (local, before any VM step)
- **Dry run:** read the summary (chunks by kind, the largest chunk at 800 tokens or under) and diff it against the prototype's counts.
- **Ingest, then five questions** through the research path: "What does §107 say?" (lookup); "What is a joint work?" (definition); "When did paragraph (5) of 'international agreement' take effect?" (effective-date flag, then the note); a TRIPs question (treaty label); a Ukrainian-law question (not found).
- **Check each turn:** its citations, the rendered pages against the printed book, the risk flags and the audit row's `sources`.

---

## Known limits
- **The check proves that a citation exists, not that it supports the claim** (citations decision).
- **The book can't reach attorneys in the pilot yet.** On the VM it is reachable only through the API, until precedence and access are decided.
- **Contract ingest becomes loud.** With D3, a contract chunk over 2,048 tokens now fails ingest instead of being cut. Run on the book, the contract PDF parser made chunks of up to 1,911 words, so a long contract could hit this too. No client documents are ingested in production today.
- **The BM25 tokenizer on legal tokens** ("§", "(a)", "104A") is unmeasured. Direct lookup covers citations.
- **Temperature 0 and thinking loops:** a public benchmark saw Qwen loop at temperature 0. Watch the book-lookup traces in Phoenix.
- **Chainlit's review screen shows raw ids.** During attorney review, Chainlit displays the raw `llm_response`, so the reviewer sees raw `ref_id`s rather than rendered citations. Local only.

## Rollout
1. **Before the build, Spark's Qdrant 1.17.1:** check server-side BM25 there with a throwaway collection, created and then deleted. It is a write on a shared host, so it needs the owner's OK, and it must leave compliance-bot's collections alone.
   - If it fails, the fallback is fastembed's `Qdrant/bm25` computed in our code. That adds a dependency to both requirement sets, and its model files must be baked into the image with offline mode forced (constraint 7).
2. **Local:** build, ingest, run the integration questions.
3. **The VM:** copy the PDF to `data/books/` and mount it into the backend, then deploy the backend. The owner's OK and choice of who runs it are needed (rule 6).
4. **Ingest on the VM:** run `scripts.ingest_book` against Spark's Qdrant (the owner's OK again), then put the integration questions to the API.

## Docs to update in the same change
- **`rag.md`:** the collection, ingest, book search, settings and D3.
- **`architecture.md`:** the tools, `book_refs_retrieved` and the audit column.
- **`operations.md`:** the settings, the ingest command and the audit column.
- **`observability.md`:** the two codes.
- **The record:** `shipped-log.md`, the roadmap row, the wiki date, and this spec's status in the [specs index](README.md).

## Open questions for the owner, before planning
- **The research agent's context window.** *Decided by the owner, 2026-11-02: pin it to `ollama_num_ctx` and leave thinking as it is.* `_build_agent` creates its `ChatOllama` without `num_ctx`.
  - **Locally** the model then loads at 131,072 tokens (checked 2026-10-30).
  - **On Spark it is unknown.** It depends on the server's default, and any value other than the resident 131,072 forces a reload.
  - **The book results are large.** One `search_books` call can return about 8 × 800 = 6,400 tokens, `lookup_book` up to 6,000, and a ReAct run accumulates several. Ollama cuts overflow silently.
  - **Pinning it to `ollama_num_ctx`** (131,072), as doc-chat does, sends tool-calling requests at a large window to Spark. That is one condition (thinking off) away from the CUDA crash in ollama#17434. The agent runs with thinking on.
  - The alternative is to leave the window unpinned and shrink the D12 defaults to fit the unknown default.

## Open questions (stay open)
- **The chunk budget and the embedding model:** decided on the test set.
- **`BOOK_MIN_SCORE`:** calibrated on the test set, including questions the book can't answer.
- **Short chunks:** whether to merge the ones under 60 tokens.
- **History questions:** a tool parameter, as here, or a router.
- **Precedence and access:** whether, and how, the Word Chat tab and contract review may use the book.
- **Where the evidence lives:** the report and the prototype are only in the research vault.
