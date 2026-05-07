# rag/hybrid_search.py
"""
Hybrid search: combines Qdrant vector search with BM25 keyword search
using Reciprocal Rank Fusion (RRF).

RRF formula: score(d) = sum( 1 / (k + rank_i(d)) )
where k = 60 (standard constant), rank_i is the rank in each result list.
"""

import logging

from config import get_settings
from rag.embeddings import embed_query
from rag.reranker import rerank
from rag.vector_store import search_vectors

logger = logging.getLogger(__name__)

_RRF_K = 60

_MERGE_FIELDS = [
    "chunk_id", "doc_id", "doc_title", "doc_type", "client_id",
    "jurisdiction", "sensitivity", "section", "section_number",
    "clause", "clause_number", "clause_type", "section_display", "text",
]


def hybrid_search(
    query: str,
    top_k: int = 6,
    collection: str = "legal_docs",
    filters: dict | None = None,
    vector_candidates: int | None = None,
    bm25_candidates: int | None = None,
) -> list[dict]:
    """
    Run hybrid search combining vector + BM25 (if enabled), fused with RRF.
    Results are reranked if reranker is enabled.

    Returns list of dicts with chunk metadata + rrf_score.
    """
    settings = get_settings()
    v_candidates = vector_candidates or settings.hybrid_vector_candidates
    b_candidates = bm25_candidates or settings.hybrid_bm25_candidates

    # 1. Vector search
    query_vector = embed_query(query)
    vector_results = search_vectors(
        query_vector, top_k=v_candidates, collection=collection, filters=filters,
    )
    logger.info(
        "Vector search: %d results%s",
        len(vector_results),
        f", top score: {vector_results[0]['score']:.3f}" if vector_results else "",
    )

    # 2. BM25 search (if enabled)
    bm25_results: list[dict] = []
    if settings.bm25_enabled:
        from rag.bm25_index import search_bm25
        bm25_results = search_bm25(query, top_k=b_candidates)
        logger.info(
            "BM25 search: %d results%s",
            len(bm25_results),
            f", top score: {bm25_results[0]['bm25_score']:.2f}" if bm25_results else "",
        )

    # 3. Build per-chunk data from vector results
    chunks: dict[str, dict] = {}

    for rank, result in enumerate(vector_results, start=1):
        cid = result.get("chunk_id", str(rank))
        entry = {field: result.get(field, "") for field in _MERGE_FIELDS}
        entry["chunk_id"] = cid
        entry["vector_score"] = result.get("score", 0.0)
        entry["vector_rank"] = rank
        entry["bm25_score"] = 0.0
        entry["bm25_rank"] = None
        chunks[cid] = entry

    # 4. Merge BM25 results
    for rank, result in enumerate(bm25_results, start=1):
        cid = result.get("chunk_id", "")
        bm25_score = result.get("bm25_score", 0.0)
        if cid in chunks:
            chunks[cid]["bm25_score"] = bm25_score
            chunks[cid]["bm25_rank"] = rank
        else:
            entry = {field: result.get(field, "") for field in _MERGE_FIELDS}
            entry["chunk_id"] = cid
            entry["vector_score"] = 0.0
            entry["vector_rank"] = None
            entry["bm25_score"] = bm25_score
            entry["bm25_rank"] = rank
            chunks[cid] = entry

    # 5. Compute RRF scores
    for chunk in chunks.values():
        rrf = 0.0
        if chunk["vector_rank"] is not None:
            rrf += 1.0 / (_RRF_K + chunk["vector_rank"])
        if chunk["bm25_rank"] is not None:
            rrf += 1.0 / (_RRF_K + chunk["bm25_rank"])
        chunk["rrf_score"] = rrf

    # 6. Sort by RRF score
    ranked = sorted(chunks.values(), key=lambda x: x["rrf_score"], reverse=True)

    # 7. Rerank if enabled
    if settings.reranker_enabled and ranked:
        ranked = rerank(query, ranked, top_n=top_k)
    else:
        ranked = ranked[:top_k]

    if ranked:
        best = ranked[0]
        logger.info(
            "Top result: %s | %s (rrf=%.4f)",
            best.get("doc_title", ""), best.get("section", ""),
            best.get("rrf_score", 0.0),
        )

    return ranked
