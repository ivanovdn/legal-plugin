"""A turn must be addressable.

Before this, api/routes/query.py put the SESSION id in state["trace_id"] and
returned nothing turn-scoped, while one session_id covers every turn in both
Word tabs. Feedback captured against that would point at a dozen prompts.
"""
from types import SimpleNamespace

from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider

import api.routes.query as q
from observability.spans import current_trace_id


def test_trace_id_is_empty_when_the_span_is_not_recording(monkeypatch):
    """Tracing off is a supported configuration, not an error."""
    monkeypatch.setattr(otel_trace, "get_current_span", lambda: otel_trace.INVALID_SPAN)
    assert current_trace_id() == ""


def test_trace_id_is_32_hex_inside_a_real_span():
    """The format every trace lookup in this project already uses."""
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("t"):
        tid = current_trace_id()
    assert len(tid) == 32
    assert int(tid, 16) != 0
    assert tid == tid.lower()


def test_trace_id_never_raises(monkeypatch):
    """Same contract as every other helper in spans.py — tracing never breaks a turn."""
    def _boom():
        raise RuntimeError("provider exploded")
    monkeypatch.setattr(otel_trace, "get_current_span", _boom)
    assert current_trace_id() == ""


def _fake_graph(recorder: dict):
    def _invoke(state, config):
        recorder.update(state)
        return {"task_type": "research", "report": {"response": "ok"}}
    return SimpleNamespace(invoke=_invoke)


def _client(monkeypatch, recorder):
    from fastapi.testclient import TestClient
    from api.main import app

    monkeypatch.setattr(q, "_get_graph", lambda: _fake_graph(recorder))
    monkeypatch.setattr(q, "refresh_ttl", lambda _s: None)
    return TestClient(app)


def test_query_payload_carries_both_ids(monkeypatch):
    recorder = {}
    c = _client(monkeypatch, recorder)
    data = c.post("/api/query", json={"request": "hi", "task_type": "research"}).json()["data"]
    assert len(data["turn_id"]) == 36, "turn_id should be a uuid4 string"
    assert "trace_id" in data


def test_each_turn_gets_a_distinct_turn_id(monkeypatch):
    """The whole point: one session_id covers many turns, a turn_id covers one."""
    recorder = {}
    c = _client(monkeypatch, recorder)
    body = {"request": "hi", "task_type": "research", "session_id": "same-session"}
    first = c.post("/api/query", json=body).json()["data"]
    second = c.post("/api/query", json=body).json()["data"]
    assert first["session_id"] == second["session_id"] == "same-session"
    assert first["turn_id"] != second["turn_id"]


def test_state_trace_id_is_no_longer_the_session_id(monkeypatch):
    """It used to be `session_id`, which made the field's name a lie."""
    recorder = {}
    c = _client(monkeypatch, recorder)
    data = c.post(
        "/api/query", json={"request": "hi", "task_type": "research", "session_id": "s-1"}
    ).json()["data"]
    assert recorder["trace_id"] == data["trace_id"]
    assert recorder["trace_id"] != "s-1"


def test_interrupt_branch_carries_the_ids():
    """A blocked review is still a turn, and still flaggable."""
    class _Interrupt:
        value = {"task_type": "contract_review", "risk_level": "high",
                 "llm_response": "", "risk_flags": [], "review_iterations": 0}

    payload = q._payload_from_result({"__interrupt__": [_Interrupt()]}, "s1", "t-9", "abc")
    assert payload["turn_id"] == "t-9"
    assert payload["trace_id"] == "abc"


def test_legacy_awaiting_review_branch_carries_the_ids():
    payload = q._payload_from_result(
        {"awaiting_review": True, "task_type": "contract_review", "report": {}},
        "s1", "t-9", "abc",
    )
    assert payload["turn_id"] == "t-9"
    assert payload["trace_id"] == "abc"
