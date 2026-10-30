# Keyword search moves into Qdrant's built-in hybrid search

Date: 2026-10-30 · Decided by: the project owner · Status: decided

Related: [roadmap.md](../roadmap.md) — *Move keyword search into Qdrant's hybrid search* · [rag.md](../rag.md#where-it-is-going)

## Context
Retrieval today is dense search in Qdrant, followed by the reranker. Keyword search exists as a pure-Python BM25 index ([`rag/bm25_index.py`](../../rag/bm25_index.py), ported from compliance-bot) that [`rag/hybrid_search.py`](../../rag/hybrid_search.py) fuses with the dense results by RRF. It is off in both environments (`BM25_ENABLED=false`). Read from the code on 2026-10-30, it can't simply be switched on:
- **It ignores filters.** `search_bm25` takes none, so with BM25 on, chunks from any client reach the fusion. That breaks rule 3 in [CLAUDE.md](../../CLAUDE.md#hard-rules-from-memory--past-incidents).
- **Its index is never saved.** Ingest adds chunks to an in-memory index in whichever process runs the ingest, and only the tests call `save()`. An index built by `scripts/ingest_all.py` is lost when the script exits.
- **It lives in one process.** On the VM the backend runs in a container while the vectors live on Spark, so even a saved index file would sit apart from the data it indexes.

The legal reference books are next ([roadmap.md](../roadmap.md), *Index the legal reference books*), and they are the first corpus where keyword matching matters.

## Options
1. **Fix the Python BM25.** Add filters, persist the index where both ingest and the backend can reach it, and keep it in step with Qdrant on every insert and delete. This is a second store to keep consistent with the first.
2. **Qdrant's built-in hybrid search.** Each point carries a sparse BM25 vector beside its dense one. One Query API call runs both searches under the same payload filter, and Qdrant fuses the results (RRF). The collections must be recreated with the sparse vector, and every document re-ingested.
3. **Dense search and the reranker only.** Drop keyword search. This is the simplest option, and it leaves exact terms to the embedding model.

## Decision
Option 2, both locally and on the VM. Embeddings (`embeddinggemma` on Ollama) and the reranker (Qwen3-Reranker-4B: llama.cpp locally, vLLM on the VM) stay as they are.

## Why
- **One store:** keyword data lives in the same points as the vectors, so the two can't drift apart, and ingest and delete each touch one place.
- **Filtered keyword matches:** the payload filter applies to both searches, so `client_id` holds for keyword matches too.
- **No server upgrade:** both Qdrants already support it (1.17.0 locally, 1.17.1 on Spark); the Query API's prefetch and fusion arrived in 1.10.
- **Exact terms:** option 3 would drop exact-term matching just before a corpus of statutes, where article numbers and defined terms are much of what people search for.

## Open questions
- **Where the sparse vectors are computed:** in our code (for example fastembed's `Qdrant/bm25`, which adds a dependency to both requirement sets and the image), or by Qdrant itself if Spark's version does BM25 server-side. Check before building.
- **Fusion settings:** RRF or DBSF, and how many candidates each side brings. Tune these on the legal books, not on the four contracts measured so far.
- **Shared Qdrant:** Spark's Qdrant also holds compliance-bot's collections, so recreating ours must leave theirs alone.
