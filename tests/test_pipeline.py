# tests/test_pipeline.py
from unittest.mock import patch, MagicMock, call
from pathlib import Path
from ingest.chunk_models import LegalChunk


def _make_chunks(n: int) -> list[LegalChunk]:
    return [
        LegalChunk(
            chunk_id=f"c{i}", doc_id="d1", doc_title="t",
            doc_filename="f.docx", doc_type="contract",
            client_id="client-a", jurisdiction="US",
            sensitivity="internal", text=f"chunk text {i}",
        )
        for i in range(n)
    ]


def test_ingest_document_calls_embed_and_upsert(monkeypatch):
    """ingest_document() parses, embeds, and upserts to Qdrant."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("EMBEDDING_MODEL", "embeddinggemma:latest")
    monkeypatch.setenv("BM25_ENABLED", "false")

    from config import get_settings
    get_settings.cache_clear()

    from ingest import pipeline as mod

    chunks = _make_chunks(3)
    embeddings = [[0.1] * 768] * 3

    mock_parser = MagicMock(return_value=chunks)
    with patch.dict(mod.PARSERS, {".docx": mock_parser}), \
         patch.object(mod, "embed_texts", return_value=embeddings) as mock_embed, \
         patch.object(mod, "upsert_chunks") as mock_upsert:

        result = mod.ingest_document(
            filepath=Path("test.docx"),
            client_id="client-a",
            jurisdiction="US",
            doc_type="contract",
            sensitivity="internal",
            collection="legal_docs",
        )

    assert result == 3
    mock_embed.assert_called_once()
    mock_upsert.assert_called_once()
