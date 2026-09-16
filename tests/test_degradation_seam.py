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
    """A constant whose value drifts from its name makes greps lie."""
    import observability.degradations as D

    for attr in dir(D):
        if attr.isupper() and isinstance(getattr(D, attr), str):
            assert getattr(D, attr) == attr.lower(), f"{attr} value does not match its name"


def test_derive_outcome_maps_reasons_to_the_three_states():
    from observability.degradations import (
        AUDIT_WRITE_FAILED, CHAT_GROUNDING_FAILED, GRAPH_INVOKE_FAILED, derive_outcome,
    )

    assert derive_outcome([]) == "ok"
    assert derive_outcome([CHAT_GROUNDING_FAILED]) == "degraded"
    assert derive_outcome([AUDIT_WRITE_FAILED]) == "degraded"
    assert derive_outcome([GRAPH_INVOKE_FAILED]) == "failed"
    # A failure anywhere in the list wins over any number of degradations.
    assert derive_outcome([CHAT_GROUNDING_FAILED, GRAPH_INVOKE_FAILED]) == "failed"


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
