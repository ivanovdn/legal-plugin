# rag/embeddings.py
"""Embedding via Ollama /api/embed endpoint."""

import logging

import httpx

from config import get_settings

logger = logging.getLogger(__name__)


def _ollama_embed(texts: list[str], prefix: str = "") -> list[list[float]]:
    """Call Ollama /api/embed for a batch of texts."""
    settings = get_settings()
    # .env stores \n as literal two chars — convert to real newline
    resolved_prefix = prefix.replace("\\n", "\n") if prefix else ""
    prefixed = [f"{resolved_prefix}{t}" for t in texts] if resolved_prefix else texts

    url = f"{settings.ollama_base_url}/api/embed"
    response = httpx.post(
        url,
        json={"model": settings.embedding_model, "input": prefixed},
        timeout=120.0,
    )
    response.raise_for_status()
    return response.json()["embeddings"]


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts with passage prefix."""
    settings = get_settings()
    return _ollama_embed(texts, prefix=settings.embedding_passage_prefix)


def embed_query(query: str) -> list[float]:
    """Embed a single query with query prefix."""
    settings = get_settings()
    vectors = _ollama_embed([query], prefix=settings.embedding_query_prefix)
    return vectors[0]
