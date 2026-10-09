"""The per-request facts collector behind turn_log (spec 2026-10-09, D5).

It mirrors the reason-code accumulator: opened by the outermost @traced span,
reset when that span closes, and fed BEFORE any is_recording() guard, so the
row a turn writes is identical with tracing off.
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace

from opentelemetry import trace as otel_trace

from graph.nodes import intent_router, llm_caller, planner
from observability.degradations import COMPACTION_FAILED, LLM_CALL_FAILED
from observability.spans import (
    mark_failed, note_turn, set_gen_attributes, traced, turn_facts,
)

# importlib, never `import skills.x.x as m`: skills/legal_research/__init__.py and
# skills/contract_review/__init__.py re-export the FUNCTION over the submodule
# attribute, so an `as` import would bind the function, not the module.
grounding = importlib.import_module("skills.grounding")
context = importlib.import_module("skills.legal_research.context")
review = importlib.import_module("skills.contract_review.contract_review")

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


_SOW = (
    "STATEMENT OF WORK\n\n"
    "This Statement of Work is issued under the Master Services Agreement dated 1 May 2026.\n"
    "Payment is due within 60 days of invoice.\n"
)


def _noted_by(fn):
    """Run fn inside a request root and return what it noted."""
    @traced("turn")
    def turn():
        fn()
        return turn_facts()
    return turn()


def test_chat_grounding_notes_the_playbook_and_the_msa_it_attached(monkeypatch):
    monkeypatch.setattr(grounding, "get_parent_msa",
                        lambda client_id, **kw: ("Trinetix Model MSA", "MSA text"))

    facts = _noted_by(lambda: context._build_chat_grounding(
        {"filters": {"client_id": "internal"}}, _SOW))

    assert facts == {"contract_type": "sow", "contract_type_defaulted": False,
                     "msa_title": "Trinetix Model MSA"}


def test_chat_grounding_notes_a_defaulted_type_and_no_msa():
    facts = _noted_by(lambda: context._build_chat_grounding(
        {"filters": {}}, "A note about lunch plans."))

    assert facts == {"contract_type": "nda", "contract_type_defaulted": True}


def test_chat_grounding_notes_nothing_when_the_playbook_fails(monkeypatch):
    """contract_type means 'the playbook attached' — a failed load attached none."""
    def boom(_contract_type):
        raise OSError("bundle missing")

    monkeypatch.setattr(context, "load_playbook_bundle", boom)
    facts = _noted_by(lambda: context._build_chat_grounding({"filters": {}}, _SOW))

    assert "contract_type" not in facts


def test_review_notes_the_playbook_and_the_msa_it_attached(monkeypatch):
    monkeypatch.setattr(grounding, "get_parent_msa",
                        lambda client_id, **kw: ("Trinetix Model MSA", "MSA text"))
    state = {"request": "Review this contract.", "uploaded_docs": [{"text": _SOW}],
             "filters": {"client_id": "internal"}}

    facts = _noted_by(lambda: review.contract_review(state))

    assert facts == {"contract_type": "sow", "contract_type_defaulted": False,
                     "msa_title": "Trinetix Model MSA"}


def test_preferences_note_the_size_of_the_block_they_inject(monkeypatch):
    monkeypatch.setattr(grounding, "get_settings", lambda: SimpleNamespace(
        preferences_enabled=True, preferences_dir="unused", preferences_max_chars=4000))
    monkeypatch.setattr(grounding, "load_preferences", lambda base, atty: "- Prefer net 30.")

    blocks: list[str] = []
    facts = _noted_by(lambda: blocks.append(
        grounding.preferences_block_for_state({"user_id": "atty-1"})))

    assert blocks[0] and facts == {"preferences_chars": len(blocks[0])}


def test_the_three_raw_ollama_nodes_name_their_calls(monkeypatch):
    """llm_caller, planner and intent_router never passed a name, so their
    llm_detail entries would be anonymous — the name is how a row tells a
    review's call from a classifier's. All three import the same httpx module,
    so one patch serves them all."""
    class FakeResp:
        def raise_for_status(self): ...
        def json(self):
            return {"message": {"content": '{"task_type":"research","skill_plan":["research"]}'},
                    "prompt_eval_count": 10, "eval_count": 2}

    monkeypatch.setattr(llm_caller.httpx, "post", lambda *a, **k: FakeResp())

    @traced("turn")
    def turn():
        intent_router.intent_router({"request": "what is an NDA?"})
        planner.planner({"request": "review then research",
                         "skill_plan": ["contract_review", "research"]})
        llm_caller.llm_caller({"request": "q", "retrieved_chunks": [], "messages": [],
                               "task_type": "research"})
        return [call["name"] for call in turn_facts()["llm_detail"]]

    assert turn() == ["intent_router", "planner", "llm_caller"]
