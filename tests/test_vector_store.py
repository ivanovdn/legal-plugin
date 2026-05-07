# tests/test_vector_store.py
from unittest.mock import MagicMock, patch


def test_get_qdrant_client_singleton(monkeypatch):
    """get_qdrant_client() returns same instance on repeated calls."""
    monkeypatch.setenv("QDRANT_URL", "http://localhost:6333")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")

    import importlib
    import rag.vector_store as mod
    importlib.reload(mod)
    mod._client = None

    with patch("rag.vector_store.QdrantClient") as MockClient:
        c1 = mod.get_qdrant_client()
        c2 = mod.get_qdrant_client()
        assert c1 is c2
        MockClient.assert_called_once()


def test_upsert_chunks_calls_qdrant(monkeypatch):
    """upsert_chunks() calls client.upsert with correct collection."""
    monkeypatch.setenv("QDRANT_URL", "http://localhost:6333")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")

    import importlib
    import rag.vector_store as mod
    importlib.reload(mod)

    mock_client = MagicMock()
    mod._client = mock_client

    from ingest.chunk_models import LegalChunk
    chunks = [
        LegalChunk(
            chunk_id="c1", doc_id="d1", doc_title="t", doc_filename="f.docx",
            doc_type="contract", client_id="client-a", jurisdiction="US",
            sensitivity="internal", text="hello",
        )
    ]
    embeddings = [[0.1] * 768]

    mod.upsert_chunks(chunks, embeddings, collection="legal_docs")

    mock_client.upsert.assert_called_once()
    call_kwargs = mock_client.upsert.call_args[1]
    assert call_kwargs["collection_name"] == "legal_docs"


def test_search_vectors_passes_collection(monkeypatch):
    """search_vectors() queries the specified collection."""
    monkeypatch.setenv("QDRANT_URL", "http://localhost:6333")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")

    import importlib
    import rag.vector_store as mod
    importlib.reload(mod)

    mock_client = MagicMock()
    mock_client.query_points.return_value.points = []
    mod._client = mock_client

    mod.search_vectors([0.1] * 768, top_k=5, collection="case_history")

    call_kwargs = mock_client.query_points.call_args[1]
    assert call_kwargs["collection_name"] == "case_history"
