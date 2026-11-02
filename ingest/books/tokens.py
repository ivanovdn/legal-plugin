# ingest/books/tokens.py
"""Counting a chunk's tokens with the embedding model itself (spec D4).

The budget is in the tokens the embedding model sees, so the count comes from Ollama (`/api/embed`'s
`prompt_eval_count`, with the passage prefix ingest embeds with), never from a chars-per-token
estimate. `None` is an answer too: the text is past the model's window.
"""
from collections.abc import Callable

from rag.embeddings import prompt_token_count

# A text's token count; None when it is past the embedding window.
TokenCounter = Callable[[str], int | None]


class CachedTokenCounter:
    """A counter that asks once per text: the chunker counts the same text more than once as it decides
    where to cut, and each ask of the real counter is a request to Ollama."""

    def __init__(self, count: TokenCounter):
        self._count = count
        self._cache: dict[str, int | None] = {}

    def __call__(self, text: str) -> int | None:
        if text not in self._cache:
            self._cache[text] = self._count(text)
        return self._cache[text]


def ollama_token_counter() -> CachedTokenCounter:
    """The ingest's counter: the embedding model's own count, cached by text."""
    return CachedTokenCounter(prompt_token_count)
