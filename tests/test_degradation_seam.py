"""The degradation vocabulary and the four seam helpers."""
from __future__ import annotations

from opentelemetry.trace import StatusCode

from tests.conftest import spans_by_name


def test_vocabulary_is_closed_and_partitioned():
    import observability.degradations as D

    assert len(D.FAILED_REASONS) == 8
    assert len(D.ANNOUNCED_REASONS) == 7
    assert len(D.SILENT_REASONS) == 7
    assert len(D.ALL_REASONS) == 22
    # The three classes must not overlap — a code is failed, announced or
    # silent, never two of them.
    assert D.FAILED_REASONS & D.ANNOUNCED_REASONS == frozenset()
    assert D.FAILED_REASONS & D.SILENT_REASONS == frozenset()
    assert D.ANNOUNCED_REASONS & D.SILENT_REASONS == frozenset()
    assert D.ALL_REASONS == D.FAILED_REASONS | D.ANNOUNCED_REASONS | D.SILENT_REASONS


def test_every_constant_value_matches_its_name():
    """A constant whose value drifts from its name makes greps lie.

    Outcome constants carry an OUTCOME_ prefix their value does not repeat
    (OUTCOME_OK = "ok", matching set_outcome's/derive_outcome's literal
    strings) — stripped before comparing.
    """
    import observability.degradations as D

    for attr in dir(D):
        if attr.isupper() and isinstance(getattr(D, attr), str):
            name = attr.removeprefix("OUTCOME_")
            assert getattr(D, attr) == name.lower(), f"{attr} value does not match its name"


def test_derive_outcome_maps_reasons_to_the_three_states():
    from observability.degradations import (
        AUDIT_WRITE_FAILED, CHAT_GROUNDING_FAILED, GRAPH_INVOKE_FAILED,
        OUTCOME_DEGRADED, OUTCOME_FAILED, OUTCOME_OK, derive_outcome,
    )

    assert derive_outcome([]) == OUTCOME_OK
    assert derive_outcome([CHAT_GROUNDING_FAILED]) == OUTCOME_DEGRADED
    assert derive_outcome([AUDIT_WRITE_FAILED]) == OUTCOME_DEGRADED
    assert derive_outcome([GRAPH_INVOKE_FAILED]) == OUTCOME_FAILED
    # A failure anywhere in the list wins over any number of degradations.
    assert derive_outcome([CHAT_GROUNDING_FAILED, GRAPH_INVOKE_FAILED]) == OUTCOME_FAILED


def test_record_degradation_adds_event_and_accumulates_on_root():
    from observability.degradations import CHAT_GROUNDING_FAILED
    from observability.spans import traced, record_degradation, degradations

    seen = {}

    @traced("child")
    def child():
        record_degradation(CHAT_GROUNDING_FAILED, announced=False, detail="qdrant down")

    @traced("root")
    def root():
        child()
        seen["reasons"] = degradations()

    root()
    events = [e for e in spans_by_name("child")[0].events if e.name == "degradation"]
    assert len(events) == 1
    assert events[0].attributes["degradation.reason"] == CHAT_GROUNDING_FAILED
    assert events[0].attributes["degradation.announced"] is False
    assert events[0].attributes["degradation.detail"] == "qdrant down"
    # Recorded on the child span, but accumulated for the ROOT to read back.
    assert seen["reasons"] == [CHAT_GROUNDING_FAILED]


def test_record_degradation_never_changes_span_status():
    """A fallback that worked is not an error. This is the whole class-2/3 point."""
    from observability.degradations import PREFERENCES_LOAD_FAILED
    from observability.spans import traced, record_degradation

    @traced("node")
    def node():
        record_degradation(PREFERENCES_LOAD_FAILED, announced=False)

    node()
    assert spans_by_name("node")[0].status.status_code != StatusCode.ERROR


def test_degradations_do_not_leak_between_turns():
    from observability.degradations import AUDIT_WRITE_FAILED, SUMMARY_LOAD_FAILED
    from observability.spans import traced, record_degradation, degradations

    @traced("turn")
    def turn(reason):
        record_degradation(reason, announced=True)
        return degradations()

    assert turn(AUDIT_WRITE_FAILED) == [AUDIT_WRITE_FAILED]
    assert turn(SUMMARY_LOAD_FAILED) == [SUMMARY_LOAD_FAILED]
    # Outside any root, the accumulator must be back at its reset default —
    # not still holding the last turn's list. Pins the deg_token reset in
    # traced's finally block, which the two asserts above do not: `traced`
    # re-enters the `if _root_span.get() is None:` branch on the next call
    # regardless, and unconditionally does `_root_degradations.set([])`
    # there, so a missing reset is invisible from inside a subsequent turn —
    # only a read from OUTSIDE any root can see it.
    assert degradations() == []


def test_record_degradation_deduplicates():
    """A retry loop must not inflate the reason list."""
    from observability.degradations import MSA_LOOKUP_FAILED
    from observability.spans import traced, record_degradation, degradations

    @traced("turn")
    def turn():
        record_degradation(MSA_LOOKUP_FAILED, announced=False)
        record_degradation(MSA_LOOKUP_FAILED, announced=False)
        return degradations()

    assert turn() == [MSA_LOOKUP_FAILED]


def test_mark_failed_sets_error_status_on_the_current_span():
    """OTel only records exceptions that PROPAGATE. This app catches them, so
    the status has to be set by hand at the point of the catch."""
    from observability.degradations import LLM_CALL_FAILED
    from observability.spans import traced, mark_failed

    @traced("llm_caller")
    def node():
        try:
            raise RuntimeError("ollama refused the connection")
        except RuntimeError as e:
            mark_failed(LLM_CALL_FAILED, exc=e)
        return "Error: LLM call failed"      # what the real node does

    assert node() == "Error: LLM call failed"
    span = spans_by_name("llm_caller")[0]
    assert span.status.status_code == StatusCode.ERROR
    assert any(e.name == "exception" for e in span.events)


def test_mark_failed_accumulates_like_a_degradation():
    from observability.degradations import LLM_CALL_FAILED
    from observability.spans import traced, mark_failed, degradations

    @traced("root")
    def root():
        mark_failed(LLM_CALL_FAILED)
        return degradations()

    assert root() == [LLM_CALL_FAILED]


def test_set_outcome_lands_on_root_from_a_nested_span():
    from observability.degradations import OUTCOME_DEGRADED
    from observability.spans import traced, set_outcome

    @traced("child")
    def child():
        set_outcome(OUTCOME_DEGRADED)

    @traced("root")
    def root():
        child()

    root()
    assert spans_by_name("root")[0].attributes.get("app.outcome") == OUTCOME_DEGRADED
    assert "app.outcome" not in spans_by_name("child")[0].attributes


def test_failed_outcome_also_sets_root_status_error():
    """Two spans end up ERROR and that is intended: the node says WHERE it
    broke, the root says the attorney got nothing."""
    import json
    from observability.degradations import LLM_CALL_FAILED, OUTCOME_FAILED
    from observability.spans import traced, mark_failed, set_outcome, degradations

    @traced("llm_caller")
    def node():
        mark_failed(LLM_CALL_FAILED)

    @traced("root")
    def root():
        node()
        set_outcome(OUTCOME_FAILED)

    root()
    root_span = spans_by_name("root")[0]
    assert root_span.status.status_code == StatusCode.ERROR
    assert root_span.attributes["app.outcome"] == OUTCOME_FAILED
    assert json.loads(root_span.attributes["app.degradations"]) == [LLM_CALL_FAILED]
    assert spans_by_name("llm_caller")[0].status.status_code == StatusCode.ERROR


def test_ok_outcome_leaves_status_alone_and_writes_no_reason_list():
    from observability.degradations import OUTCOME_OK
    from observability.spans import traced, set_outcome

    @traced("root")
    def root():
        set_outcome(OUTCOME_OK)

    root()
    span = spans_by_name("root")[0]
    assert span.attributes["app.outcome"] == OUTCOME_OK
    assert span.status.status_code != StatusCode.ERROR
    assert "app.degradations" not in span.attributes


def test_record_degradation_accumulates_even_when_the_span_is_not_recording(monkeypatch):
    """The headline property: a turn's verdict must not depend on whether
    anyone was watching. Every test above runs against the session's
    recording provider, so none of them can tell `_accumulate(reason)` apart
    from a version moved below the `is_recording()` guard — this one can,
    by forcing trace.get_current_span() itself to hand back a non-recording
    span while still inside a real traced root."""
    from opentelemetry import trace as otel_trace
    from observability.degradations import CHAT_GROUNDING_FAILED
    from observability.spans import traced, record_degradation, degradations

    @traced("root")
    def root():
        monkeypatch.setattr(otel_trace, "get_current_span", lambda: otel_trace.INVALID_SPAN)
        record_degradation(CHAT_GROUNDING_FAILED, announced=False)
        return degradations()

    assert root() == [CHAT_GROUNDING_FAILED]
    assert [e for e in spans_by_name("root")[0].events if e.name == "degradation"] == []


def test_mark_failed_accumulates_even_when_the_span_is_not_recording(monkeypatch):
    """Same property as record_degradation, for the other accumulating seam."""
    from opentelemetry import trace as otel_trace
    from observability.degradations import LLM_CALL_FAILED
    from observability.spans import traced, mark_failed, degradations

    @traced("root")
    def root():
        monkeypatch.setattr(otel_trace, "get_current_span", lambda: otel_trace.INVALID_SPAN)
        mark_failed(LLM_CALL_FAILED)
        return degradations()

    assert root() == [LLM_CALL_FAILED]
    assert spans_by_name("root")[0].status.status_code != StatusCode.ERROR
