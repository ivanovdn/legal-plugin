# observability/tracing.py
"""Helpers for GENERATION-type spans and token usage on LLM calls.

Two call styles exist in this codebase: raw httpx POSTs to Ollama (graph nodes)
and LangChain `.invoke()` calls (skills). `ollama_usage` serves the first;
`langchain_callbacks` serves the second.
"""
from __future__ import annotations

from typing import Any

from langfuse.decorators import langfuse_context
from langfuse.model import ModelUsage


def ollama_usage(response_json: dict[str, Any]) -> ModelUsage | None:
    """Map a non-streaming Ollama /api/chat response to Langfuse token usage.

    Ollama returns `prompt_eval_count` (input tokens) and `eval_count` (output
    tokens) at the top level. Returns None when neither is present (e.g. an
    error response) so callers can pass the result straight through.
    """
    pin = response_json.get("prompt_eval_count")
    pout = response_json.get("eval_count")
    if pin is None and pout is None:
        return None
    return {
        "input": pin,
        "output": pout,
        "total": (pin or 0) + (pout or 0),
        "unit": "TOKENS",
    }


def langchain_callbacks() -> list:
    """Langfuse callback handler bound to the current @observe span, wrapped for
    LangChain's `config={"callbacks": ...}`. Returns [] when no span/trace is
    active (tracing disabled, or called outside an @observe context) so the
    LangChain call runs unaffected. Tracing must never break the real call.
    """
    try:
        handler = langfuse_context.get_current_langchain_handler()
    except Exception:
        return []
    return [handler] if handler else []
