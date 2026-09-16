# tests/test_turn_outcome.py
"""Real code paths with injected failures, asserted on spans.

These are the tests the spec requires to be RED before implementation — a green
suite here means nothing unless you have watched them fail first.
"""
from __future__ import annotations

import httpx
import pytest
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


# --- skill spans: legal_research / contract_generation ----------------------
# Neither skill runs through llm_caller: legal_research sets
# state["llm_response"] itself and never reaches it, and contract_generation
# calls the LLM / ReAct agent directly on both of its paths. Each skill's own
# @traced span is therefore the only place a caught failure can be recorded.


def test_legal_research_failure_marks_the_skill_span_error(monkeypatch):
    """The Word chat path — the most-used route in the product. An error
    string reaches the attorney; the span must say so.

    Import via importlib, NOT `from skills.legal_research import legal_research
    as lr` — the package's __init__.py does `from
    skills.legal_research.legal_research import legal_research`, which
    re-exports the FUNCTION over the submodule name. `lr` would then be the
    function object, not the module, and `monkeypatch.setattr(lr,
    "traced_invoke", boom)` cannot reach the module global that
    `_run_doc_chat` actually calls through (confirmed empirically: that
    import shape raises AttributeError here, since a function object has no
    `traced_invoke` attribute for monkeypatch's raising=True to find)."""
    import importlib

    from observability.degradations import LEGAL_RESEARCH_FAILED

    lr = importlib.import_module("skills.legal_research.legal_research")

    def boom(*a, **k):
        raise RuntimeError("model unreachable")

    monkeypatch.setattr(lr, "traced_invoke", boom)

    state = {
        "request": "who signs?",
        "uploaded_docs": [{"text": "AGREEMENT ..."}],
        "task_type": "research",
        "user_id": "u1",
        "document_id": "doc-1",
        "session_id": "s1",
    }
    lr.legal_research(state)

    assert state["llm_response"].startswith("Error: Legal research failed")
    span = spans_by_name("legal_research")[0]
    assert span.status.status_code == StatusCode.ERROR
    # The reason itself (R9), not just ERROR status. Asserting detail too:
    # it doubles as proof the *patched* traced_invoke is what fired — a
    # genuine unpatched network failure here would carry a different
    # exception class name, not "RuntimeError".
    assert span.attributes["degradation.reason"] == LEGAL_RESEARCH_FAILED
    assert span.attributes["degradation.detail"] == "RuntimeError"


def test_contract_generation_revision_failure_marks_the_skill_span_error(monkeypatch):
    """Loop-back path: previous_draft + attorney_notes both set -> a direct
    LLM revision call, bypassing the ReAct agent entirely. Shares
    CONTRACT_GENERATION_FAILED with the agent path below (deliberately one
    code, not two); detail="revision" is what tells them apart.

    Same importlib requirement as the legal_research test above:
    skills/contract_generation/__init__.py re-exports the FUNCTION over the
    submodule name too."""
    import importlib

    from observability.degradations import CONTRACT_GENERATION_FAILED

    mod = importlib.import_module("skills.contract_generation.contract_generation")

    def boom(*a, **k):
        raise RuntimeError("model unreachable")

    monkeypatch.setattr(mod, "traced_invoke", boom)

    state = {
        "request": "revise the indemnity clause",
        "attorney_notes": "tighten the liability cap",
        "previous_draft": "This Agreement is entered into ...",
        "filters": {},
    }
    mod.contract_generation(state)

    assert state["llm_response"].startswith("Error: Contract revision failed")
    span = spans_by_name("contract_generation")[0]
    assert span.status.status_code == StatusCode.ERROR
    assert span.attributes["degradation.reason"] == CONTRACT_GENERATION_FAILED
    assert span.attributes["degradation.detail"] == "revision"


def test_contract_generation_agent_failure_marks_the_skill_span_error(monkeypatch):
    """No previous draft -> the ReAct agent path. Same reason code as the
    revision path above; detail="agent" is what tells them apart."""
    import importlib

    from observability.degradations import CONTRACT_GENERATION_FAILED

    mod = importlib.import_module("skills.contract_generation.contract_generation")

    def boom(*a, **k):
        raise RuntimeError("agent unreachable")

    monkeypatch.setattr(mod, "traced_agent_invoke", boom)

    state = {
        "request": "draft an NDA for Acme Corp",
        "filters": {"client_id": "acme"},
    }
    mod.contract_generation(state)

    assert state["llm_response"].startswith("Error: Contract generation agent failed")
    span = spans_by_name("contract_generation")[0]
    assert span.status.status_code == StatusCode.ERROR
    assert span.attributes["degradation.reason"] == CONTRACT_GENERATION_FAILED
    assert span.attributes["degradation.detail"] == "agent"


def test_audit_write_failure_records_an_announced_degradation(monkeypatch):
    from graph.nodes import memory_writer as mod
    from observability.degradations import AUDIT_WRITE_FAILED

    def boom(**kwargs):
        raise RuntimeError("pool timeout")

    monkeypatch.setattr(mod, "write_audit_log", boom)

    out = mod.memory_writer({
        "session_id": "s1", "user_id": "u1", "task_type": "research",
        "request": "q", "llm_response": "a", "report": {},
    })
    # The existing contract is unchanged: the flag travels on the RETURNED report.
    assert out["report"]["memory_degraded"] is True

    events = [e for e in spans_by_name("memory_writer")[0].events if e.name == "degradation"]
    assert [e.attributes["degradation.reason"] for e in events] == [AUDIT_WRITE_FAILED]
    assert events[0].attributes["degradation.announced"] is True
    assert spans_by_name("memory_writer")[0].status.status_code != StatusCode.ERROR


# --- Task 10 continued: the remaining announced (class 2) sites -------------
# The brief's own worked example above (audit write) anchors the TDD cycle;
# these pin the other four announced sites the same batch wires, each
# asserting both `degradation.reason` and `degradation.announced` — the
# boolean is the entire class-2/class-3 boundary, so a test that never reads
# it back cannot catch a flipped one.


def test_review_persist_failure_records_an_announced_degradation(monkeypatch):
    from graph.nodes import memory_writer as mod
    from observability.degradations import REVIEW_PERSIST_FAILED

    monkeypatch.setattr(mod, "write_audit_log", lambda **kwargs: None)

    def boom(**kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(mod, "save_review", boom)

    out = mod.memory_writer({
        "session_id": "s1", "user_id": "u1", "task_type": "contract_review",
        "request": "review this", "llm_response": "# Review\nFinding",
        "document_id": "doc-1", "contract_type_detected": "nda", "report": {},
    })
    assert "disk full" in out["report"]["review_persist_error"]

    events = [e for e in spans_by_name("memory_writer")[0].events if e.name == "degradation"]
    assert [e.attributes["degradation.reason"] for e in events] == [REVIEW_PERSIST_FAILED]
    assert events[0].attributes["degradation.announced"] is True
    assert spans_by_name("memory_writer")[0].status.status_code != StatusCode.ERROR


def test_prior_review_load_failure_records_an_announced_degradation(monkeypatch):
    """Calls `_load_prior_review_block` directly, so per context.py's own
    testing note this patches the CONTEXT module (load_latest_review is not
    re-imported into legal_research.py)."""
    import importlib
    from observability.degradations import PRIOR_REVIEW_LOAD_FAILED
    from observability.spans import traced

    ctx = importlib.import_module("skills.legal_research.context")

    def boom(document_id):
        raise RuntimeError("pool timeout")

    monkeypatch.setattr(ctx, "load_latest_review", boom)
    state = {"document_id": "doc-pr"}

    @traced("turn")
    def turn():
        return ctx._load_prior_review_block(state, "")

    result = turn()
    assert result == ""
    assert state["memory_degraded"] is True  # existing contract unchanged

    events = [e for e in spans_by_name("turn")[0].events if e.name == "degradation"]
    assert [e.attributes["degradation.reason"] for e in events] == [PRIOR_REVIEW_LOAD_FAILED]
    assert events[0].attributes["degradation.announced"] is True


def test_summary_load_failure_records_an_announced_degradation(monkeypatch):
    """`latest_to_id` failing inside `_load_prior_conversation`'s summary-read
    block. The verbatim `load_recent` call right after is left unpatched — it
    runs for real against the empty test store and succeeds, isolating this
    site from the sibling PRIOR_CONVERSATION_LOAD_FAILED one below."""
    import importlib
    from observability.degradations import SUMMARY_LOAD_FAILED
    from observability.spans import traced

    ctx = importlib.import_module("skills.legal_research.context")

    def boom(*a, **k):
        raise RuntimeError("pool timeout")

    monkeypatch.setattr(ctx, "latest_to_id", boom)
    state = {"document_id": "doc-sl", "user_id": "atty-sl"}

    @traced("turn")
    def turn():
        return ctx._load_prior_conversation(state)

    result = turn()
    assert result == []
    assert state["memory_degraded"] is True

    events = [e for e in spans_by_name("turn")[0].events if e.name == "degradation"]
    assert [e.attributes["degradation.reason"] for e in events] == [SUMMARY_LOAD_FAILED]
    assert events[0].attributes["degradation.announced"] is True


def test_prior_conversation_load_failure_records_an_announced_degradation(monkeypatch):
    """`load_recent` failing AFTER the summary-read block succeeded for real
    (fresh document/attorney ids, nothing stored) — isolates this from the
    SUMMARY_LOAD_FAILED site above."""
    import importlib
    from observability.degradations import PRIOR_CONVERSATION_LOAD_FAILED
    from observability.spans import traced

    ctx = importlib.import_module("skills.legal_research.context")

    def boom(*a, **k):
        raise RuntimeError("pool timeout")

    monkeypatch.setattr(ctx, "load_recent", boom)
    state = {"document_id": "doc-pc", "user_id": "atty-pc"}

    @traced("turn")
    def turn():
        return ctx._load_prior_conversation(state)

    result = turn()
    assert result == []
    assert state["memory_degraded"] is True

    events = [e for e in spans_by_name("turn")[0].events if e.name == "degradation"]
    assert [e.attributes["degradation.reason"] for e in events] == [PRIOR_CONVERSATION_LOAD_FAILED]
    assert events[0].attributes["degradation.announced"] is True


# --- Task 11: the silent (class 3) sites -------------------------------
# The class this work exists for: the answer lands, quality is quietly
# worse, and nobody is told. announced=False on every site below.


def test_grounding_failure_records_a_SILENT_degradation(monkeypatch):
    """RED TEST #2. The attorney gets a fluent answer with no playbook and no
    MSA, and is told nothing. announced=False is the whole point."""
    from skills.legal_research import context as ctx
    from observability.degradations import CHAT_GROUNDING_FAILED
    from observability.spans import traced, degradations

    def boom(*a, **k):
        raise RuntimeError("qdrant unreachable")

    monkeypatch.setattr(ctx, "load_playbook_bundle", boom)

    @traced("turn")
    def turn():
        playbook, msa = ctx._build_chat_grounding(
            {"filters": {"client_id": "c1"}}, "SOME AGREEMENT TEXT"
        )
        return playbook, msa, degradations()

    playbook, msa, reasons = turn()
    assert playbook == "" and msa == ""          # answers ungrounded, as before
    assert reasons == [CHAT_GROUNDING_FAILED]

    events = [e for e in spans_by_name("turn")[0].events if e.name == "degradation"]
    assert events[0].attributes["degradation.announced"] is False


def test_review_reconciliation_failure_records_a_silent_degradation(monkeypatch):
    """Companion to the PRIOR_REVIEW_LOAD_FAILED test above: same function,
    the SECOND except block. `load_latest_review` succeeds this time —
    `_reconcile_review_with_doc` is what fails — so the review still injects
    (unchanged) and memory_degraded must NOT be set: this is reserved for
    real store failures, not a reconciliation hiccup."""
    import importlib
    from observability.degradations import REVIEW_RECONCILIATION_FAILED
    from observability.spans import traced

    ctx = importlib.import_module("skills.legal_research.context")

    monkeypatch.setattr(
        ctx, "load_latest_review",
        lambda document_id: {"markdown": "# Prior Review\nFinding"},
    )

    def boom(review_text, uploaded_text):
        raise RuntimeError("bad regex state")

    monkeypatch.setattr(ctx, "_reconcile_review_with_doc", boom)
    state = {"document_id": "doc-rc"}

    @traced("turn")
    def turn():
        return ctx._load_prior_review_block(state, "SOME AGREEMENT TEXT")

    result = turn()
    assert "Prior Review" in result            # injected unchanged, as before
    assert "memory_degraded" not in state      # reserved for real store failures

    events = [e for e in spans_by_name("turn")[0].events if e.name == "degradation"]
    assert [e.attributes["degradation.reason"] for e in events] == [REVIEW_RECONCILIATION_FAILED]
    assert events[0].attributes["degradation.announced"] is False


def test_compressible_history_failure_records_a_silent_degradation(monkeypatch):
    """Same seam as test_compressible_count_survives_a_store_failure_without_
    flagging_degraded in test_context_breakdown.py (patches ctx.latest_to_id),
    wrapped in a traced root so the event can be asserted."""
    import importlib
    from observability.degradations import COMPRESSIBLE_HISTORY_READ_FAILED
    from observability.spans import traced

    ctx = importlib.import_module("skills.legal_research.context")

    def boom(*a, **k):
        raise RuntimeError("app-db unavailable")

    monkeypatch.setattr(ctx, "latest_to_id", boom)
    state = {"document_id": "doc-ch", "user_id": "atty-ch"}

    @traced("turn")
    def turn():
        return ctx.compressible_history(state)

    result = turn()
    assert result == (0, 0)
    assert "memory_degraded" not in state

    events = [e for e in spans_by_name("turn")[0].events if e.name == "degradation"]
    assert [e.attributes["degradation.reason"] for e in events] == [COMPRESSIBLE_HISTORY_READ_FAILED]
    assert events[0].attributes["degradation.announced"] is False


def test_preferences_load_failure_records_a_silent_degradation(monkeypatch, tmp_path):
    import importlib
    from observability.degradations import PREFERENCES_LOAD_FAILED
    from observability.spans import traced

    grounding = importlib.import_module("skills.grounding")

    def boom(*a, **k):
        raise RuntimeError("disk error")

    monkeypatch.setattr(grounding, "load_preferences", boom)

    @traced("turn")
    def turn():
        return grounding.load_attorney_preferences_block("atty-1", str(tmp_path), 4000)

    result = turn()
    assert result == ""

    events = [e for e in spans_by_name("turn")[0].events if e.name == "degradation"]
    assert [e.attributes["degradation.reason"] for e in events] == [PREFERENCES_LOAD_FAILED]
    assert events[0].attributes["degradation.announced"] is False


def test_msa_lookup_failure_records_a_silent_degradation(monkeypatch):
    """Same seam as test_contract_review_msa_lookup_error_reviews_standalone in
    test_skills.py: patch get_parent_msa on the grounding module (attach_parent_msa
    calls it for real), which is what actually raises inside contract_review's
    bare `except Exception:`."""
    import skills.grounding as grounding
    from skills.contract_review.contract_review import contract_review
    from observability.degradations import MSA_LOOKUP_FAILED

    def boom(client_id, **kw):
        raise RuntimeError("qdrant down")

    monkeypatch.setattr(grounding, "get_parent_msa", boom)

    state = {
        "request": "Review this contract.",
        "uploaded_docs": [{"text": (
            "STATEMENT OF WORK\n\n"
            "This Statement of Work is issued under the Master Services Agreement dated...\n"
            "Project scope: design a new web portal.\n"
        )}],
        "filters": {"client_id": "internal"},
    }
    result = contract_review(state)  # must NOT raise

    assert result["contract_type_detected"] == "sow"
    assert "GOVERNING MSA" not in result["messages"][-1]["content"]

    span = spans_by_name("contract_review")[0]
    events = [e for e in span.events if e.name == "degradation"]
    assert [e.attributes["degradation.reason"] for e in events] == [MSA_LOOKUP_FAILED]
    assert events[0].attributes["degradation.announced"] is False
    assert span.status.status_code != StatusCode.ERROR


def test_intent_classification_failure_records_a_silent_degradation(monkeypatch):
    from graph.nodes import intent_router as mod
    from observability.degradations import INTENT_CLASSIFICATION_FAILED

    def boom(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(mod.httpx, "post", boom)

    state = {"request": "who signs this?", "task_type": "", "skill_plan": []}
    result = mod.intent_router(state)

    assert result["task_type"] == "research"   # existing fallback contract unchanged

    span = spans_by_name("intent_router")[0]
    events = [e for e in span.events if e.name == "degradation"]
    assert [e.attributes["degradation.reason"] for e in events] == [INTENT_CLASSIFICATION_FAILED]
    assert events[0].attributes["degradation.announced"] is False
    assert span.status.status_code != StatusCode.ERROR


def test_planning_failure_records_a_silent_degradation(monkeypatch):
    from graph.nodes import planner as mod
    from observability.degradations import PLANNING_FAILED

    def boom(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(mod.httpx, "post", boom)

    state = {"request": "review and then draft", "skill_plan": ["contract_review", "drafting"]}
    result = mod.planner(state)

    assert result["task_type"] == "contract_review"   # skill_plan[0], unchanged fallback

    span = spans_by_name("planner")[0]
    events = [e for e in span.events if e.name == "degradation"]
    assert [e.attributes["degradation.reason"] for e in events] == [PLANNING_FAILED]
    assert events[0].attributes["degradation.announced"] is False
    assert span.status.status_code != StatusCode.ERROR


# --- post_compact -------------------------------------------------------
# /api/compact has no user, session or document attached to it today, so
# compact_conversation's internal traced_invoke ("llm") span is its own
# parentless trace — the most-debugged subsystem in the repo (the auto-fire
# decision, its floors, its disarm latch) is the least traceable thing in it.
# Three exit paths, three distinct outcomes: success (ok), a terminal error
# from compact_conversation (failed), and a net-benefit refusal (ok — the
# guard working correctly, not a degradation).


def test_compact_route_gives_the_llm_span_a_parent(monkeypatch):
    """RED TEST #3. Today the compaction LLM span is an orphan root with no
    user, session or document attached to it."""
    from api.routes import compact as mod
    from api.models import CompactRequest

    monkeypatch.setattr(mod, "compact_conversation", lambda *a, **k: {
        "error": "", "reason": "", "segment_id": 1, "freed_chars": 500,
    })

    mod.post_compact(CompactRequest(document_id="doc-1", reclaim_chars=5000), user_id="u1")

    span = spans_by_name("compact")[0]
    assert span.parent is None, "compact is a request root"
    assert span.attributes["app.outcome"] == "ok"


def test_compact_route_marks_failed_when_compaction_errors(monkeypatch):
    import json
    from fastapi import HTTPException
    from api.routes import compact as mod
    from api.models import CompactRequest
    from observability.degradations import COMPACTION_FAILED

    monkeypatch.setattr(mod, "compact_conversation", lambda *a, **k: {
        "error": "the condensed segment could not be saved (OperationalError)",
    })

    with pytest.raises(HTTPException):
        mod.post_compact(CompactRequest(document_id="doc-1", reclaim_chars=5000), user_id="u1")

    span = spans_by_name("compact")[0]
    assert span.attributes["app.outcome"] == "failed"
    assert span.status.status_code == StatusCode.ERROR
    # Not just the status — the specific reason, so a test asserting only
    # ERROR would not pass if the wrong constant were ever passed to mark_failed.
    assert span.attributes["degradation.reason"] == COMPACTION_FAILED
    assert COMPACTION_FAILED in json.loads(span.attributes["app.degradations"])


def test_compact_route_records_a_net_benefit_refusal_without_marking_it_failed(monkeypatch):
    """The net-benefit guard declining to condense (the summary would be no
    smaller than the messages it replaces) is correct behaviour, not a
    degradation: app.outcome must stay "ok" and the span must not go to
    ERROR. But the refusal disarms the pane's auto-compaction latch, which is
    operationally significant, so it still has to land on the span."""
    import json
    from api.routes import compact as mod
    from api.models import CompactRequest

    refusal_reason = (
        "condensing these messages would not save space — the summary's own "
        "header and per-quote labels cost more than the messages do"
    )
    monkeypatch.setattr(mod, "compact_conversation", lambda *a, **k: {
        "error": "", "reason": refusal_reason, "segment_id": 0,
        "compacted": False, "reclaimed": 0,
    })

    mod.post_compact(CompactRequest(document_id="doc-1", reclaim_chars=5000), user_id="u1")

    span = spans_by_name("compact")[0]
    assert span.attributes["app.outcome"] == "ok"
    assert span.status.status_code != StatusCode.ERROR
    metadata = json.loads(span.attributes["metadata"])
    assert metadata["compaction.refused_reason"] == refusal_reason
    # A refusal is not a degradation, so it must not touch the reason accumulator.
    assert "app.degradations" not in span.attributes


@pytest.mark.parametrize("span_name", [
    "db.write_audit_log", "db.save_review", "db.load_latest_review",
    "db.append_turn", "db.load_recent", "db.row_lengths_after",
    "db.load_segments", "db.latest_to_id",
])
def test_store_functions_are_named_spans(span_name):
    """Named per operation. Wrapping get_pool().connection() instead would give
    a pile of spans all called 'db'."""
    import memory.audit, memory.conversation_store, memory.conversation_summary, memory.review_store

    fn_name = span_name.removeprefix("db.")
    module = {
        "write_audit_log": memory.audit,
        "save_review": memory.review_store,
        "load_latest_review": memory.review_store,
        "append_turn": memory.conversation_store,
        "load_recent": memory.conversation_store,
        "row_lengths_after": memory.conversation_store,
        "load_segments": memory.conversation_summary,
        "latest_to_id": memory.conversation_summary,
    }[fn_name]
    fn = getattr(module, fn_name)
    # span_name (not __wrapped__) so this proves WHICH name the function was
    # decorated with — functools.wraps sets __wrapped__ unconditionally, so a
    # mere "is this @traced at all" check can't catch two span names swapped
    # between two functions; this can.
    assert getattr(fn, "span_name", None) == span_name, f"{fn_name} is not @traced({span_name!r})"


def test_store_span_is_emitted_on_a_real_call():
    from memory.conversation_summary import latest_to_id

    latest_to_id("doc-nonexistent", "u1")
    assert len(spans_by_name("db.latest_to_id")) == 1
