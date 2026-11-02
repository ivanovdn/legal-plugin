# rag/embeddings.py
"""Embedding via Ollama /api/embed endpoint.

Every request sends `truncate: false`. Left unset, Ollama silently cuts input past
the embedding model's window and returns a vector for the front of the text — a
chunk indexed on text its reader never sees. With it set, Ollama answers HTTP 400
instead, and the two callers that matter decide what that means: ingest stops
(`embed_texts` raises), a query is retried once cut and the cut is recorded
(`embed_query`).
"""

import logging

import httpx

from config import get_settings
from observability.degradations import EMBEDDING_QUERY_TRUNCATED
from observability.spans import record_degradation

logger = logging.getLogger(__name__)


class EmbeddingInputTooLong(ValueError):
    """An ingest input is longer than the embedding model's window."""

    def __init__(self, index: int, chars: int):
        self.index = index
        self.chars = chars
        super().__init__(
            f"Embedding input #{index} is {chars} chars, "
            "longer than the embedding model's window; nothing was cut"
        )


def _ollama_embed(
    texts: list[str], prefix: str = "", *, truncate: bool = False
) -> httpx.Response:
    """Call Ollama /api/embed for a batch of texts; return the response unchecked.

    Unchecked because the caller decides what an over-length answer means.
    """
    settings = get_settings()
    # .env stores \n as literal two chars — convert to real newline
    resolved_prefix = prefix.replace("\\n", "\n") if prefix else ""
    prefixed = [f"{resolved_prefix}{t}" for t in texts] if resolved_prefix else texts

    url = f"{settings.ollama_base_url}/api/embed"
    return httpx.post(
        url,
        json={"model": settings.embedding_model, "input": prefixed, "truncate": truncate},
        timeout=120.0,
    )


def _is_over_length(response: httpx.Response) -> bool:
    """Ollama's answer to an input past the window (measured 2026-11-02):
    HTTP 400, body {"error":"the input length exceeds the context length"}."""
    return response.status_code == 400 and "context length" in response.text


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts with passage prefix.

    Raises EmbeddingInputTooLong, naming the first item past the window, rather
    than embedding a cut copy of it.
    """
    prefix = get_settings().embedding_passage_prefix
    response = _ollama_embed(texts, prefix)
    if not _is_over_length(response):
        response.raise_for_status()
        return response.json()["embeddings"]

    # The batch failed as a whole and does not say which item — ask one at a time.
    vectors: list[list[float]] = []
    for index, text in enumerate(texts):
        single = _ollama_embed([text], prefix)
        if _is_over_length(single):
            raise EmbeddingInputTooLong(index, len(text))
        single.raise_for_status()
        vectors.extend(single.json()["embeddings"])
    return vectors


def embed_query(query: str) -> list[float]:
    """Embed a single query with query prefix.

    A query past the window is retried once with truncation allowed, and the cut
    is recorded: a long pasted question must not fail the turn, nor pass unnoticed.
    """
    prefix = get_settings().embedding_query_prefix
    response = _ollama_embed([query], prefix)
    truncated = _is_over_length(response)
    if truncated:
        response = _ollama_embed([query], prefix, truncate=True)
    response.raise_for_status()
    if truncated:
        # Only once the cut vector is in hand: a failed retry raises, and is not a cut query.
        record_degradation(
            EMBEDDING_QUERY_TRUNCATED, announced=False, detail=f"{len(query)} chars"
        )
    return response.json()["embeddings"][0]


def prompt_token_count(text: str) -> int | None:
    """Ollama's own token count for one passage (`prompt_eval_count`), with the
    passage prefix in front as at ingest. None when the text is past the window,
    so a caller measuring a chunk can tell "too long" from a number."""
    response = _ollama_embed([text], get_settings().embedding_passage_prefix)
    if _is_over_length(response):
        return None
    response.raise_for_status()
    return response.json()["prompt_eval_count"]
