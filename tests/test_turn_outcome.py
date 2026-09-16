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
    from observability.degradations import LLM_CALL_FAILED

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
    assert span.attributes["degradation.reason"] == LLM_CALL_FAILED
    events = [e for e in span.events if e.name == "degradation"]
    assert not events, "a failure is mark_failed, not record_degradation"
    assert any(e.name == "exception" for e in span.events)


# --- submit_query -----------------------------------------------------------
# Four exit paths: success, Redis-degrade success, stateless-fallback
# failure, graph failure. Every one must stamp app.outcome on the request
# root span — a path that returns without one is the defect this task exists
# to prevent.
#
# The root span is created as "query" by @traced("query"), but submit_query's
# own set_trace_attributes(name=f"query:{task_type or 'auto'}", ...) renames
# THAT SAME span (via _root_span.get()) before any outcome-stamping code
# runs — verified empirically, not assumed. None of these tests set
# task_type, so the exported span is always "query:auto"; look it up by
# that, never by bare "query" (spans_by_name does exact-match, no prefix).


def test_submit_query_marks_failed_when_the_graph_raises(monkeypatch):
    """Today submit_query's except swallows everything and returns HTTP 200
    with status="error" — nothing in the span distinguishes this from a
    healthy turn."""
    import json
    from api.routes import query as mod
    from api.models import QueryRequest
    from observability.degradations import GRAPH_INVOKE_FAILED, OUTCOME_FAILED

    class BoomGraph:
        def invoke(self, *a, **k):
            raise ValueError("node exploded")

    monkeypatch.setattr(mod, "_get_graph", lambda: BoomGraph())
    monkeypatch.setattr(mod, "_checkpointer_active", False)

    resp = mod.submit_query(
        QueryRequest(request="review this"), user_id="u1", user_name="U",
    )
    assert resp.status == "error"

    span = spans_by_name("query:auto")[0]
    assert span.attributes["app.outcome"] == OUTCOME_FAILED
    # The reason itself, not just ERROR status — a wrong constant at this
    # call site would still leave the status ERROR.
    assert span.attributes["degradation.reason"] == GRAPH_INVOKE_FAILED
    assert span.status.status_code == StatusCode.ERROR
    assert GRAPH_INVOKE_FAILED in json.loads(span.attributes["app.degradations"])


def test_submit_query_is_ok_on_a_clean_turn(monkeypatch):
    from api.routes import query as mod
    from api.models import QueryRequest
    from observability.degradations import OUTCOME_OK

    class CleanGraph:
        def invoke(self, state, **k):
            return {**state, "llm_response": "done", "report": {}}

    monkeypatch.setattr(mod, "_get_graph", lambda: CleanGraph())
    monkeypatch.setattr(mod, "refresh_ttl", lambda *a, **k: None)

    resp = mod.submit_query(
        QueryRequest(request="hi"), user_id="u1", user_name="U",
    )
    assert resp.status == "ok"
    span = spans_by_name("query:auto")[0]
    assert span.attributes["app.outcome"] == OUTCOME_OK
    assert span.status.status_code != StatusCode.ERROR


def test_submit_query_degrades_when_the_checkpointer_dies_mid_invoke(monkeypatch):
    """The worked example the design spec uses to explain the whole
    failed/degraded/announced taxonomy: the checkpointer dies mid-invoke, the
    stateless fallback runs and succeeds, and the turn still answers — that
    is "degraded", explicitly NOT "failed", even though an exception was
    caught along the way."""
    import json
    from api.routes import query as mod
    from api.models import QueryRequest
    from observability.degradations import CHECKPOINTER_UNAVAILABLE, OUTCOME_DEGRADED
    from redis.exceptions import RedisError

    class DyingGraph:
        def invoke(self, *a, **k):
            raise RedisError("connection refused")

    class StatelessGraph:
        def invoke(self, state, **k):
            return {**state, "llm_response": "done", "report": {}}

    monkeypatch.setattr(mod, "_get_graph", lambda: DyingGraph())
    monkeypatch.setattr(mod, "_checkpointer_active", True)
    monkeypatch.setattr(mod, "_get_stateless_graph", lambda: StatelessGraph())

    resp = mod.submit_query(
        QueryRequest(request="review this"), user_id="u1", user_name="U",
    )
    assert resp.status == "ok"

    span = spans_by_name("query:auto")[0]
    assert span.attributes["app.outcome"] == OUTCOME_DEGRADED
    assert span.status.status_code != StatusCode.ERROR
    assert CHECKPOINTER_UNAVAILABLE in json.loads(span.attributes["app.degradations"])


def test_submit_query_marks_failed_when_the_stateless_fallback_also_fails(monkeypatch):
    """Fourth exit path: the checkpointer AND the stateless fallback both
    die. A distinct reason code from a plain graph failure, even though both
    fire from the same outer except block — easy to mix the two up."""
    from api.routes import query as mod
    from api.models import QueryRequest
    from observability.degradations import OUTCOME_FAILED, STATELESS_FALLBACK_FAILED
    from redis.exceptions import RedisError

    class DyingGraph:
        def invoke(self, *a, **k):
            raise RedisError("connection refused")

    class AlsoDyingStatelessGraph:
        def invoke(self, *a, **k):
            raise ValueError("stateless graph exploded too")

    monkeypatch.setattr(mod, "_get_graph", lambda: DyingGraph())
    monkeypatch.setattr(mod, "_checkpointer_active", True)
    monkeypatch.setattr(mod, "_get_stateless_graph", lambda: AlsoDyingStatelessGraph())

    resp = mod.submit_query(
        QueryRequest(request="review this"), user_id="u1", user_name="U",
    )
    assert resp.status == "error"

    span = spans_by_name("query:auto")[0]
    assert span.attributes["app.outcome"] == OUTCOME_FAILED
    assert span.attributes["degradation.reason"] == STATELESS_FALLBACK_FAILED
    assert span.status.status_code == StatusCode.ERROR


# --- resume_query -------------------------------------------------------
# Four exit paths of its own: get_state failure (exception), get_state
# empty (no exception — an unknown/expired thread_id), invoke failure, and
# success. All land on the request root span — created as "resume" by
# @traced, then renamed to f"resume:{session_id}" by the same
# set_trace_attributes(name=...) pattern as submit_query (see note above);
# look each one up by its own session_id suffix, never by bare "resume".


def test_resume_query_marks_failed_when_get_state_raises(monkeypatch):
    """Pins the NEW visibility, not a fix: `get_state` catches ANY exception
    and reports "session expired or not found" to the attorney, so a Redis
    outage during resume currently presents as an expired session. That is a
    real, pre-existing product bug this task deliberately leaves alone — it
    only makes the failure show up on the span instead of looking identical
    to a healthy resume."""
    from api.routes import query as mod
    from api.models import ResumeRequest
    from observability.degradations import OUTCOME_FAILED, RESUME_STATE_LOAD_FAILED

    class BoomGetStateGraph:
        def get_state(self, config):
            raise RuntimeError("redis down")

    monkeypatch.setattr(mod, "_get_graph", lambda: BoomGetStateGraph())

    resp = mod.resume_query(
        "sess-2", ResumeRequest(approved=True, notes="", revised_response=""),
    )
    assert resp.status == "error"
    assert resp.errors == ["session expired or not found"]

    span = spans_by_name("resume:sess-2")[0]
    assert span.attributes["app.outcome"] == OUTCOME_FAILED
    assert span.attributes["degradation.reason"] == RESUME_STATE_LOAD_FAILED
    assert span.status.status_code == StatusCode.ERROR


def test_resume_query_marks_failed_when_prior_state_is_empty(monkeypatch):
    """A fifth path not named in the brief's worked list: get_state succeeds
    but finds nothing to resume (unknown/expired thread_id). No exception is
    raised, but the attorney still gets an error string as the answer — Class
    1 by the design spec's own definition — so it must stamp an outcome too.
    Reuses RESUME_STATE_LOAD_FAILED: either way, no usable prior state."""
    from api.routes import query as mod
    from api.models import ResumeRequest
    from observability.degradations import OUTCOME_FAILED, RESUME_STATE_LOAD_FAILED

    class FakeState:
        values = {}

    class EmptyStateGraph:
        def get_state(self, config):
            return FakeState()

    monkeypatch.setattr(mod, "_get_graph", lambda: EmptyStateGraph())

    resp = mod.resume_query(
        "sess-missing", ResumeRequest(approved=True, notes="", revised_response=""),
    )
    assert resp.status == "error"

    span = spans_by_name("resume:sess-missing")[0]
    assert span.attributes["app.outcome"] == OUTCOME_FAILED
    assert span.attributes["degradation.reason"] == RESUME_STATE_LOAD_FAILED
    assert span.status.status_code == StatusCode.ERROR


def test_resume_query_marks_failed_when_invoke_raises(monkeypatch):
    from api.routes import query as mod
    from api.models import ResumeRequest
    from observability.degradations import OUTCOME_FAILED, RESUME_FAILED

    class FakeState:
        values = {"awaiting_review": False}

    class BoomInvokeGraph:
        def get_state(self, config):
            return FakeState()

        def invoke(self, command, config=None):
            raise ValueError("node exploded on resume")

    monkeypatch.setattr(mod, "_get_graph", lambda: BoomInvokeGraph())

    resp = mod.resume_query(
        "sess-3", ResumeRequest(approved=True, notes="", revised_response=""),
    )
    assert resp.status == "error"

    span = spans_by_name("resume:sess-3")[0]
    assert span.attributes["app.outcome"] == OUTCOME_FAILED
    assert span.attributes["degradation.reason"] == RESUME_FAILED
    assert span.status.status_code == StatusCode.ERROR


def test_resume_query_is_ok_on_a_clean_resume(monkeypatch):
    from api.routes import query as mod
    from api.models import ResumeRequest
    from observability.degradations import OUTCOME_OK

    class FakeState:
        values = {"awaiting_review": False}

    class CleanGraph:
        def get_state(self, config):
            return FakeState()

        def invoke(self, command, config=None):
            return {
                "task_type": "drafting",
                "report": {},
                "risk_level": "low",
                "awaiting_review": False,
            }

    monkeypatch.setattr(mod, "_get_graph", lambda: CleanGraph())
    monkeypatch.setattr(mod, "refresh_ttl", lambda *a, **k: None)

    resp = mod.resume_query(
        "sess-4", ResumeRequest(approved=True, notes="", revised_response=""),
    )
    assert resp.status == "ok"

    span = spans_by_name("resume:sess-4")[0]
    assert span.attributes["app.outcome"] == OUTCOME_OK
    assert span.status.status_code != StatusCode.ERROR
