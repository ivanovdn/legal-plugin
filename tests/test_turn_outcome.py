# tests/test_turn_outcome.py
"""Real code paths with injected failures, asserted on spans.

These are the tests the spec requires to be RED before implementation — a green
suite here means nothing unless you have watched them fail first.
"""
from __future__ import annotations

import httpx
from opentelemetry.trace import StatusCode

from tests.conftest import spans_by_name


def test_llm_caller_marks_its_span_error_when_ollama_fails(monkeypatch):
    """RED TEST #1. Today the span closes clean with status UNSET, so a turn
    where Ollama timed out is byte-identical in Phoenix to a healthy one."""
    from graph.nodes import llm_caller as mod

    def boom(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(mod.httpx, "post", boom)

    state = {
        "messages": [{"role": "user", "content": "review this"}],
        "task_type": "contract_review",
        "retrieved_chunks": [],
    }
    mod.llm_caller(state)

    assert state["llm_response"].startswith("Error: LLM call failed")
    span = spans_by_name("llm_caller")[0]
    assert span.status.status_code == StatusCode.ERROR
    events = [e for e in span.events if e.name == "degradation"]
    assert not events, "a failure is mark_failed, not record_degradation"
    assert any(e.name == "exception" for e in span.events)
