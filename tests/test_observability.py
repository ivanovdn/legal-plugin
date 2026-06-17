# tests/test_observability.py
"""Langfuse observability helpers + GENERATION/usage wiring."""
from __future__ import annotations

from observability.tracing import ollama_usage, langchain_callbacks


def test_ollama_usage_maps_token_counts():
    usage = ollama_usage({"message": {"content": "hi"}, "prompt_eval_count": 150, "eval_count": 42})
    assert usage == {"input": 150, "output": 42, "total": 192, "unit": "TOKENS"}


def test_ollama_usage_none_when_absent():
    assert ollama_usage({"message": {"content": "hi"}}) is None


def test_ollama_usage_partial_counts():
    usage = ollama_usage({"eval_count": 10})
    assert usage == {"input": None, "output": 10, "total": 10, "unit": "TOKENS"}


def test_langchain_callbacks_empty_without_active_span():
    # No @observe context active in a plain unit test → handler is None → [].
    assert langchain_callbacks() == []
