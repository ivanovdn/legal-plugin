# tests/test_reranker.py
import httpx
from unittest.mock import MagicMock, patch


def test_rerank_calls_endpoint(monkeypatch):
    """rerank() calls /v1/rerank and returns sorted results."""
    monkeypatch.setenv("RERANKER_ENABLED", "true")
    monkeypatch.setenv("RERANKER_BACKEND", "llama-cpp")
    monkeypatch.setenv("RERANKER_URL", "http://localhost:8081/v1/rerank")
    monkeypatch.setenv("RERANKER_MODEL", "bge-reranker")
    monkeypatch.setenv("RERANKER_TOP_N", "2")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")

    import importlib
    import rag.reranker as mod
    importlib.reload(mod)

    results = [
        {"chunk_id": "c1", "text": "low relevance text"},
        {"chunk_id": "c2", "text": "high relevance text about contracts"},
        {"chunk_id": "c3", "text": "medium relevance"},
    ]

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "results": [
            {"index": 1, "relevance_score": 0.9},
            {"index": 2, "relevance_score": 0.5},
            {"index": 0, "relevance_score": 0.1},
        ]
    }

    with patch("rag.reranker.httpx.post", return_value=fake_response):
        reranked = mod.rerank("contract terms", results, top_n=2)

    assert len(reranked) == 2
    assert reranked[0]["chunk_id"] == "c2"
    assert "rerank_score" in reranked[0]


def test_rerank_disabled_returns_original(monkeypatch):
    """When reranker is disabled, returns original results unchanged."""
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")

    import importlib
    import rag.reranker as mod
    importlib.reload(mod)

    results = [{"chunk_id": "c1", "text": "a"}, {"chunk_id": "c2", "text": "b"}]
    reranked = mod.rerank("query", results, top_n=2)

    assert len(reranked) == 2
    assert reranked[0]["chunk_id"] == "c1"


def test_rerank_connection_error_returns_original(monkeypatch):
    """On connection error, gracefully returns original results."""
    monkeypatch.setenv("RERANKER_ENABLED", "true")
    monkeypatch.setenv("RERANKER_BACKEND", "llama-cpp")
    monkeypatch.setenv("RERANKER_URL", "http://localhost:8081/v1/rerank")
    monkeypatch.setenv("RERANKER_MODEL", "bge-reranker")
    monkeypatch.setenv("RERANKER_TOP_N", "2")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")

    import importlib
    import rag.reranker as mod
    importlib.reload(mod)

    results = [{"chunk_id": "c1", "text": "a"}]

    with patch("rag.reranker.httpx.post", side_effect=httpx.ConnectError("down")):
        reranked = mod.rerank("query", results, top_n=1)

    assert len(reranked) == 1
    assert reranked[0]["chunk_id"] == "c1"
