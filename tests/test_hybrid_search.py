# tests/test_hybrid_search.py
from unittest.mock import patch


def test_hybrid_search_merges_results(monkeypatch):
    """hybrid_search() merges vector and BM25 results via RRF."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("BM25_ENABLED", "true")
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.setenv("HYBRID_VECTOR_CANDIDATES", "5")
    monkeypatch.setenv("HYBRID_BM25_CANDIDATES", "5")

    import importlib
    import rag.hybrid_search as mod
    importlib.reload(mod)

    vector_results = [
        {"chunk_id": "c1", "doc_id": "d1", "text": "clause about indemnity",
         "score": 0.9, "doc_title": "Contract A", "doc_type": "contract",
         "client_id": "x", "jurisdiction": "US"},
        {"chunk_id": "c2", "doc_id": "d1", "text": "payment terms",
         "score": 0.7, "doc_title": "Contract A", "doc_type": "contract",
         "client_id": "x", "jurisdiction": "US"},
    ]

    bm25_results = [
        {"chunk_id": "c1", "doc_id": "d1", "text": "clause about indemnity",
         "bm25_score": 5.2, "doc_title": "Contract A"},
        {"chunk_id": "c3", "doc_id": "d2", "text": "indemnity in case law",
         "bm25_score": 3.1, "doc_title": "Case B"},
    ]

    with patch("rag.hybrid_search.embed_query", return_value=[0.1] * 768), \
         patch("rag.hybrid_search.search_vectors", return_value=vector_results), \
         patch("rag.bm25_index.search_bm25", return_value=bm25_results):

        results = mod.hybrid_search(
            query="indemnity clause",
            top_k=3,
            collection="legal_docs",
        )

    assert len(results) <= 3
    assert results[0]["chunk_id"] == "c1"
    assert "rrf_score" in results[0]


def test_hybrid_search_bm25_disabled(monkeypatch):
    """When BM25 disabled, only vector results are returned."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("BM25_ENABLED", "false")
    monkeypatch.setenv("RERANKER_ENABLED", "false")

    import importlib
    import rag.hybrid_search as mod
    importlib.reload(mod)

    vector_results = [
        {"chunk_id": "c1", "doc_id": "d1", "text": "text",
         "score": 0.9, "doc_title": "t", "doc_type": "contract",
         "client_id": "x", "jurisdiction": "US"},
    ]

    with patch("rag.hybrid_search.embed_query", return_value=[0.1] * 768), \
         patch("rag.hybrid_search.search_vectors", return_value=vector_results):

        results = mod.hybrid_search(
            query="test",
            top_k=5,
            collection="legal_docs",
        )

    assert len(results) == 1
    assert results[0]["chunk_id"] == "c1"
