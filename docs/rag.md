# Legal Plugin — RAG

> Part of the [project wiki](wiki.md).

Retrieval over the firm's documents in Qdrant. This page covers which model runs where, the search pipeline as the code runs it today, where it is going, the collections, and ingest.

## Where each piece runs

| Piece | Model or engine | Local (Mac) | VM (production) | Settings |
|---|---|---|---|---|
| Embeddings | `embeddinggemma:latest`, 768 dimensions, cosine | Ollama on the Mac (`localhost:11434`) | Ollama on Spark (`172.20.0.22:11434`) | `OLLAMA_BASE_URL`, `EMBEDDING_MODEL`, `QDRANT_VECTOR_DIM=768` |
| Vector store | Qdrant | Docker, `qdrant/qdrant:v1.17.0` (`localhost:6333`) | Spark's Qdrant 1.17.1 (`QDRANT_REMOTE_URL`), beside compliance-bot's collections | `QDRANT_URL` |
| Reranker | Qwen3-Reranker-4B | llama.cpp's `llama-server`, `POST /v1/rerank` | vLLM, `POST /v1/rerank` (or `/v1/score`) | `RERANKER_ENABLED`, `RERANKER_BACKEND`, `RERANKER_URL`, `RERANKER_MODEL` |
| Keyword search | BM25 in pure Python ([`rag/bm25_index.py`](../rag/bm25_index.py)) | off | off | `BM25_ENABLED=false`, the default |

- **The reranker is not wired up yet.** The local `.env` has `RERANKER_ENABLED=true` with `RERANKER_URL` empty, and `.env.remote.example` sets no reranker keys; the VM's live `.env` hasn't been checked. With the URL empty, every rerank call fails, and the search silently falls back to the fused order. The fix is the [roadmap](roadmap.md) row *The reranker has never run*.
- **`RERANKER_BACKEND`** picks the request format. `vllm` and `vllm-score` wrap the query and the documents in Qwen3's chat template; `vllm-score` also calls `/v1/score` instead of `/v1/rerank`. Any other value, empty included, sends llama-server's plain `{model, query, documents, top_n}`.
- **Keyword search is off in both places**, so retrieval today is dense search, then the reranker. Keyword search is moving into Qdrant; see [Where it is going](#where-it-is-going).

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

The work is tracked as the [roadmap](roadmap.md) row *Move keyword search into Qdrant's hybrid search*. The legal reference books (row *Index the legal reference books*) are the first corpus that needs it.

## Qdrant collections

| Collection | Content | Chunking |
|---|---|---|
| `legal_docs` | Contracts, legislation, templates, policies | Varies by doc_type |
| `case_history` | Past signed contracts (5 demo + CUAD) | Clause-level (one chunk per clause) |
| `memory` | Unused stub (attorney preferences shipped as per-attorney `USER.md` files, not here) | Small entries |

`scripts/create_collections.py` creates all three, each with one dense vector (`QDRANT_VECTOR_DIM`, cosine) and **no payload indexes**.
- **Unindexed filters.** Filters such as `client_id` work without an index, but Qdrant has to check each point's payload. On 2026-10-30 the local collections had no indexes (`legal_docs` 154 points, `case_history` 43, `memory` 0).
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
