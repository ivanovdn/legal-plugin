"""The per-request facts collector behind turn_log (spec 2026-10-09, D5).

It mirrors the reason-code accumulator: opened by the outermost @traced span,
reset when that span closes, and fed BEFORE any is_recording() guard, so the
row a turn writes is identical with tracing off.
"""
from __future__ import annotations

from opentelemetry import trace as otel_trace

from observability.degradations import COMPACTION_FAILED, LLM_CALL_FAILED
from observability.spans import (
    mark_failed, note_turn, set_gen_attributes, traced, turn_facts,
)

_USAGE = {"input": 900, "output": 120, "total": 1020, "unit": "TOKENS"}
_TIMINGS = {"total_ms": 13200, "load_ms": 5900, "prompt_eval_ms": 6100, "eval_ms": 1100}


def test_a_completed_model_call_is_recorded_even_when_the_span_is_not_recording(monkeypatch):
    """Every other test runs against the session's recording provider, so none
    can tell a recorder placed before the is_recording() guard from one placed
    after it. This one forces a non-recording span inside a real root."""
    @traced("root")
    def root():
        monkeypatch.setattr(otel_trace, "get_current_span", lambda: otel_trace.INVALID_SPAN)
        set_gen_attributes(name="doc_chat", model="qwen3.6:latest", output="answer",
                           usage=_USAGE, timings=_TIMINGS)
        return turn_facts()

    assert root()["llm_detail"] == [{
        "name": "doc_chat", "model": "qwen3.6:latest",
        "tokens_in": 900, "tokens_out": 120,
        "load_ms": 5900, "prompt_ms": 6100, "gen_ms": 1100, "total_ms": 13200,
    }]


def test_calls_are_kept_in_order_and_unreported_values_stay_none():
    @traced("root")
    def root():
        set_gen_attributes(name="doc_chat", model="m", output="a", usage=_USAGE)
        set_gen_attributes(name="doc_chat_json_retry", model="m", output="{}")
        return turn_facts()["llm_detail"]

    first, second = root()
    assert [first["name"], second["name"]] == ["doc_chat", "doc_chat_json_retry"]
    assert first["load_ms"] is None                      # no timings were reported
    assert second["tokens_in"] is None and second["gen_ms"] is None


def test_attribute_only_calls_are_not_model_calls():
    """set_gen_attributes(model=...) alone records no generation; counting it
    would invent a model call that never happened."""
    @traced("root")
    def root():
        set_gen_attributes(model="m")
        return turn_facts()

    assert "llm_detail" not in root()


def test_the_first_failure_message_is_kept_even_when_the_span_is_not_recording(monkeypatch):
    @traced("root")
    def root():
        monkeypatch.setattr(otel_trace, "get_current_span", lambda: otel_trace.INVALID_SPAN)
        mark_failed(LLM_CALL_FAILED, exc=TimeoutError("read timed out"))
        mark_failed(COMPACTION_FAILED, detail="a later failure")
        return turn_facts()

    assert root()["error"] == "llm_call_failed: TimeoutError: read timed out"


def test_a_failure_without_an_exception_uses_its_detail():
    @traced("root")
    def root():
        mark_failed(COMPACTION_FAILED, detail="the condensed summary could not be verified")
        return turn_facts()

    assert root()["error"] == "compaction_failed: the condensed summary could not be verified"


def test_note_turn_keeps_the_last_write_and_turn_facts_is_a_copy():
    @traced("root")
    def root():
        note_turn(contract_type="nda", contract_type_defaulted=True)
        note_turn(contract_type="sow", contract_type_defaulted=False)
        turn_facts()["contract_type"] = "mutated"
        return turn_facts()

    assert root() == {"contract_type": "sow", "contract_type_defaulted": False}


def test_facts_do_not_leak_between_requests_and_are_empty_outside_one():
    @traced("turn")
    def turn(kind):
        note_turn(contract_type=kind)
        return turn_facts()

    assert turn("nda") == {"contract_type": "nda"}
    assert turn("sow") == {"contract_type": "sow"}
    note_turn(contract_type="orphan")          # outside any root: a no-op, never raises
    assert turn_facts() == {}


def test_facts_recorded_in_a_child_span_reach_the_root_after_it_closes():
    @traced("child")
    def child():
        set_gen_attributes(name="doc_chat", model="m", output="a", usage=_USAGE)
        note_turn(contract_type="msa")
        mark_failed(LLM_CALL_FAILED, exc=RuntimeError("boom"))

    @traced("root")
    def root():
        child()
        return turn_facts()

    facts = root()
    assert [c["name"] for c in facts["llm_detail"]] == ["doc_chat"]
    assert facts["contract_type"] == "msa"
    assert facts["error"] == "llm_call_failed: RuntimeError: boom"
