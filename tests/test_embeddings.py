# tests/test_embeddings.py
from unittest.mock import MagicMock, patch


def test_embed_texts_calls_ollama(monkeypatch):
    """embed_texts() sends correct payload to Ollama /api/embed."""
    monkeypatch.setenv("EMBEDDING_MODEL", "embeddinggemma:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")

    import importlib
    import rag.embeddings as mod
    importlib.reload(mod)

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "embeddings": [[0.1] * 768, [0.2] * 768]
    }

    with patch("rag.embeddings.httpx.post", return_value=fake_response) as mock_post:
        result = mod.embed_texts(["hello", "world"])

    assert len(result) == 2
    assert len(result[0]) == 768
    call_args = mock_post.call_args
    body = call_args[1]["json"]
    assert body["model"] == "embeddinggemma:latest"


def test_embed_query_calls_ollama(monkeypatch):
    """embed_query() returns a single vector."""
    monkeypatch.setenv("EMBEDDING_MODEL", "embeddinggemma:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")

    import importlib
    import rag.embeddings as mod
    importlib.reload(mod)

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "embeddings": [[0.5] * 768]
    }

    with patch("rag.embeddings.httpx.post", return_value=fake_response):
        result = mod.embed_query("test query")

    assert len(result) == 768
