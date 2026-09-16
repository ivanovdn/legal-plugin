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
#
# _checkpointer_active is set EXPLICITLY, deliberately, in every test below —
# even where a given scenario doesn't otherwise care about its value — because
# it also feeds a second producer (_record_startup_checkpointer_degradation,
# fired early in both handlers whenever checkpointer_enabled and not
# _checkpointer_active). Leaving it at whatever the module/a previous test
# happened to leave behind would make a test's outcome depend on execution
# order instead of stating which world it means to test.


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
    # Checkpointer ACTIVE — keeps this test to exactly one reason code.
    # _is_redis_failure(ValueError(...)) is False either way, so this
    # doesn't change which branch fires; it only keeps
    # _record_startup_checkpointer_degradation from also firing here.
    monkeypatch.setattr(mod, "_checkpointer_active", True)

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
    """A genuinely healthy turn: checkpointer ACTIVE, not merely
    un-exercised — otherwise this would be indistinguishable from the
    startup-absent world below and pass for the wrong reason."""
    from api.routes import query as mod
    from api.models import QueryRequest
    from observability.degradations import OUTCOME_OK

    class CleanGraph:
        def invoke(self, state, **k):
            return {**state, "llm_response": "done", "report": {}}

    monkeypatch.setattr(mod, "_get_graph", lambda: CleanGraph())
    monkeypatch.setattr(mod, "_checkpointer_active", True)
    monkeypatch.setattr(mod, "refresh_ttl", lambda *a, **k: None)

    resp = mod.submit_query(
        QueryRequest(request="hi"), user_id="u1", user_name="U",
    )
    assert resp.status == "ok"
    assert resp.data["memory_degraded"] is False
    span = spans_by_name("query:auto")[0]
    assert span.attributes["app.outcome"] == OUTCOME_OK
    assert span.status.status_code != StatusCode.ERROR


def test_submit_query_is_degraded_when_the_checkpointer_was_never_available(monkeypatch):
    """Redis down at boot. _get_graph() caches _graph, so
    build_checkpointer() runs exactly once, inside whichever request is
    first — _checkpointer_active then stays False for the life of the
    process, not just for that first request. Before this fix, EVERY turn in
    that deployment returned memory_degraded=True to the pane while its root
    span stamped app.outcome=ok with no reason recorded anywhere: the pane
    and the span silently disagreed for the life of the process. This is the
    case where they finally agree."""
    import json
    from api.routes import query as mod
    from api.models import QueryRequest
    from observability.degradations import CHECKPOINTER_UNAVAILABLE, OUTCOME_DEGRADED

    class CleanGraph:
        def invoke(self, state, **k):
            return {**state, "llm_response": "done", "report": {}}

    monkeypatch.setattr(mod, "_get_graph", lambda: CleanGraph())
    monkeypatch.setattr(mod, "_checkpointer_active", False)
    monkeypatch.setattr(mod, "refresh_ttl", lambda *a, **k: None)

    resp = mod.submit_query(
        QueryRequest(request="hi"), user_id="u1", user_name="U",
    )
    assert resp.status == "ok"
    assert resp.data["memory_degraded"] is True

    span = spans_by_name("query:auto")[0]
    assert span.attributes["app.outcome"] == OUTCOME_DEGRADED
    assert span.status.status_code != StatusCode.ERROR
    assert CHECKPOINTER_UNAVAILABLE in json.loads(span.attributes["app.degradations"])


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
    import json
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
    assert STATELESS_FALLBACK_FAILED in json.loads(span.attributes["app.degradations"])


# --- resume_query -------------------------------------------------------
# Five exit paths now (see below): get_state failure (exception), get_state
# empty (no exception — an unknown/expired thread_id), invoke failure,
# success, and the startup-absent-checkpointer variant of the empty-state
# path. All land on the request root span — created as "resume" by @traced,
# then renamed to f"resume:{session_id}" by the same
# set_trace_attributes(name=...) pattern as submit_query (see note above);
# look each one up by its own session_id suffix, never by bare "resume".
# _checkpointer_active is set EXPLICITLY in every test below, same reasoning
# as the submit_query section above.
#
# get_state-empty is deliberately NOT the same shape as get_state-raises. An
# exception means the load failed (RESUME_STATE_LOAD_FAILED, failed). An
# empty result means the load succeeded and correctly found nothing — the
# checkpoint TTL working as designed — so it is reason-code-free and lands on
# ok (absent any OTHER degradation on the same request — see the
# startup-absent variant below), even though the attorney still sees
# status="error". Reusing the exception's reason code there would smuggle the
# except block's "any exception -> session expired" conflation into the
# telemetry too.


def test_resume_query_marks_failed_when_get_state_raises(monkeypatch):
    """Pins the NEW visibility, not a fix: `get_state` catches ANY exception
    and reports "session expired or not found" to the attorney, so a Redis
    outage during resume currently presents as an expired session. That is a
    real, pre-existing product bug this task deliberately leaves alone — it
    only makes the failure show up on the span instead of looking identical
    to a healthy resume. Checkpointer ACTIVE, to keep this test to exactly
    one reason code."""
    import json
    from api.routes import query as mod
    from api.models import ResumeRequest
    from observability.degradations import OUTCOME_FAILED, RESUME_STATE_LOAD_FAILED

    class BoomGetStateGraph:
        def get_state(self, config):
            raise RuntimeError("redis down")

    monkeypatch.setattr(mod, "_get_graph", lambda: BoomGetStateGraph())
    monkeypatch.setattr(mod, "_checkpointer_active", True)

    resp = mod.resume_query(
        "sess-2", ResumeRequest(approved=True, notes="", revised_response=""),
    )
    assert resp.status == "error"
    assert resp.errors == ["session expired or not found"]

    span = spans_by_name("resume:sess-2")[0]
    assert span.attributes["app.outcome"] == OUTCOME_FAILED
    assert span.attributes["degradation.reason"] == RESUME_STATE_LOAD_FAILED
    assert span.status.status_code == StatusCode.ERROR
    assert RESUME_STATE_LOAD_FAILED in json.loads(span.attributes["app.degradations"])


def test_resume_query_is_ok_when_prior_state_is_empty(monkeypatch):
    """A path not named in the brief's worked list: get_state succeeds but
    finds nothing to resume (unknown/expired thread_id) — the checkpoint TTL
    working as designed, not a failure. The attorney still gets an error
    string as the answer (status="error" + the message, unchanged), but
    app.outcome must NOT be "failed" and no reason code may be invented or
    reused: RESUME_STATE_LOAD_FAILED means the load FAILED, and this load
    succeeded. Reusing it here would smuggle the sibling except block's "any
    exception -> session expired" conflation into the telemetry too.
    Checkpointer ACTIVE, so this is a genuinely clean world — see the
    startup-absent variant directly below for the compound case."""
    from api.routes import query as mod
    from api.models import ResumeRequest
    from observability.degradations import OUTCOME_OK

    class FakeState:
        values = {}

    class EmptyStateGraph:
        def get_state(self, config):
            return FakeState()

    monkeypatch.setattr(mod, "_get_graph", lambda: EmptyStateGraph())
    monkeypatch.setattr(mod, "_checkpointer_active", True)

    resp = mod.resume_query(
        "sess-missing", ResumeRequest(approved=True, notes="", revised_response=""),
    )
    assert resp.status == "error"
    assert resp.errors == ["session expired or not found"]

    span = spans_by_name("resume:sess-missing")[0]
    assert span.attributes["app.outcome"] == OUTCOME_OK
    assert span.status.status_code != StatusCode.ERROR
    assert "app.degradations" not in span.attributes
    assert "degradation.reason" not in span.attributes


def test_resume_query_is_degraded_when_prior_state_is_empty_and_checkpointer_startup_absent(monkeypatch):
    """Once a startup-absent checkpointer can record a degradation before
    this branch runs, the derived form
    (set_outcome(derive_outcome(degradations()))) genuinely differs from a
    hardcoded OUTCOME_OK — this is the case that proves it. A hardcoded OK
    could not produce "degraded" here; only reading the accumulator can."""
    import json
    from api.routes import query as mod
    from api.models import ResumeRequest
    from observability.degradations import CHECKPOINTER_UNAVAILABLE, OUTCOME_DEGRADED

    class FakeState:
        values = {}

    class EmptyStateGraph:
        def get_state(self, config):
            return FakeState()

    monkeypatch.setattr(mod, "_get_graph", lambda: EmptyStateGraph())
    monkeypatch.setattr(mod, "_checkpointer_active", False)

    resp = mod.resume_query(
        "sess-missing-2", ResumeRequest(approved=True, notes="", revised_response=""),
    )
    assert resp.status == "error"
    assert resp.errors == ["session expired or not found"]

    span = spans_by_name("resume:sess-missing-2")[0]
    assert span.attributes["app.outcome"] == OUTCOME_DEGRADED
    assert span.status.status_code != StatusCode.ERROR
    assert CHECKPOINTER_UNAVAILABLE in json.loads(span.attributes["app.degradations"])


def test_resume_query_marks_failed_when_invoke_raises(monkeypatch):
    """Checkpointer ACTIVE, to keep this test to exactly one reason code."""
    import json
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
    monkeypatch.setattr(mod, "_checkpointer_active", True)

    resp = mod.resume_query(
        "sess-3", ResumeRequest(approved=True, notes="", revised_response=""),
    )
    assert resp.status == "error"

    span = spans_by_name("resume:sess-3")[0]
    assert span.attributes["app.outcome"] == OUTCOME_FAILED
    assert span.attributes["degradation.reason"] == RESUME_FAILED
    assert span.status.status_code == StatusCode.ERROR
    assert RESUME_FAILED in json.loads(span.attributes["app.degradations"])


def test_resume_query_is_ok_on_a_clean_resume(monkeypatch):
    """A genuinely healthy resume: checkpointer ACTIVE, symmetric with
    test_submit_query_is_ok_on_a_clean_turn."""
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
    monkeypatch.setattr(mod, "_checkpointer_active", True)
    monkeypatch.setattr(mod, "refresh_ttl", lambda *a, **k: None)

    resp = mod.resume_query(
        "sess-4", ResumeRequest(approved=True, notes="", revised_response=""),
    )
    assert resp.status == "ok"
    assert resp.data["memory_degraded"] is False

    span = spans_by_name("resume:sess-4")[0]
    assert span.attributes["app.outcome"] == OUTCOME_OK
    assert span.status.status_code != StatusCode.ERROR
