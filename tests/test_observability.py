# tests/test_observability.py
"""OpenTelemetry span helpers + GENERATION/usage wiring."""
from __future__ import annotations

import importlib
import json
from types import SimpleNamespace

import pytest
from opentelemetry.trace import StatusCode

from observability.tracing import (
    ollama_usage,
    message_usage,
    traced_invoke,
)
from tests.conftest import spans_by_name as _spans_by_name


def test_traced_creates_span_and_returns_value():
    from observability.spans import traced

    @traced("unit_node")
    def node(x):
        return x + 1

    assert node(41) == 42
    spans = _spans_by_name("unit_node")
    assert len(spans) == 1


def test_traced_llm_kind_sets_openinference_span_kind():
    from observability.spans import traced

    @traced("gen_node", kind="LLM")
    def gen():
        return "ok"

    gen()
    span = _spans_by_name("gen_node")[0]
    assert span.attributes.get("openinference.span.kind") == "LLM"


def test_traced_propagates_exceptions_and_resets_root():
    from observability.spans import traced, set_trace_attributes, _root_span

    @traced("boom")
    def boom():
        raise ValueError("kaboom")

    with pytest.raises(ValueError, match="kaboom"):
        boom()

    span = _spans_by_name("boom")[0]
    assert span.status.status_code == StatusCode.ERROR      # __exit__ recorded the exception
    assert _root_span.get() is None                          # root token was reset in finally, no leak

    # a subsequent independent traced call becomes its own root
    @traced("after")
    def after():
        set_trace_attributes(user_id="u2")
    after()
    assert _spans_by_name("after")[0].attributes.get("user.id") == "u2"


def test_set_trace_attributes_lands_on_root_from_nested_span():
    from observability.spans import traced, set_trace_attributes

    @traced("child")
    def child():
        # a deep node stamps trace-wide facts; they must hit the ROOT span
        set_trace_attributes(user_id="u1", session_id="s1", tags=["research"])

    @traced("root")
    def root():
        child()

    root()
    root_span = _spans_by_name("root")[0]
    child_span = _spans_by_name("child")[0]
    assert root_span.attributes.get("user.id") == "u1"
    assert root_span.attributes.get("session.id") == "s1"
    assert json.loads(root_span.attributes.get("tag.tags")) == ["research"]
    assert "user.id" not in child_span.attributes


def test_set_trace_attributes_merges_metadata_on_root():
    from observability.spans import traced, set_trace_attributes

    @traced("root")
    def root():
        set_trace_attributes(metadata={"contract_type_detected": "nda"})
        set_trace_attributes(metadata={"review_risk_level": "red"})

    root()
    root_span = _spans_by_name("root")[0]
    md = json.loads(root_span.attributes.get("metadata"))
    assert md == {"contract_type_detected": "nda", "review_risk_level": "red"}


def test_set_gen_attributes_sets_llm_attrs_on_current_span():
    from observability.spans import traced, set_gen_attributes

    @traced("gen", kind="LLM")
    def gen():
        set_gen_attributes(
            name="doc_chat",
            input=[{"role": "user", "content": "q"}],
            output="the answer",
            model="qwen3.6:latest",
            usage={"input": 90, "output": 10, "total": 100, "unit": "TOKENS"},
        )

    gen()
    # name override renames the span
    span = _spans_by_name("doc_chat")[0]
    assert span.attributes.get("llm.model_name") == "qwen3.6:latest"
    assert span.attributes.get("llm.token_count.prompt") == 90
    assert span.attributes.get("llm.token_count.completion") == 10
    assert span.attributes.get("llm.token_count.total") == 100
    assert span.attributes.get("output.value") == "the answer"
    assert json.loads(span.attributes.get("input.value")) == [{"role": "user", "content": "q"}]


def test_helpers_no_op_and_never_raise_without_active_span():
    from observability.degradations import (
        CHAT_GROUNDING_FAILED, LLM_CALL_FAILED, OUTCOME_FAILED,
    )
    from observability.spans import (
        mark_failed, record_degradation, set_gen_attributes, set_outcome,
        set_trace_attributes,
    )
    # called outside any @traced span → current span is invalid/non-recording
    set_trace_attributes(user_id="u", metadata={"k": "v"})
    set_gen_attributes(model="m", usage={"input": 1, "output": 2, "total": 3, "unit": "TOKENS"})
    # The three degradation helpers matter most here: they are the ones called
    # from INSIDE an `except`, where a raise would replace the application's
    # real failure with a tracing one — the single thing this seam must never
    # do. Outside a root there is also no accumulator, so they must tolerate a
    # None contextvar as well as a non-recording span.
    record_degradation(CHAT_GROUNDING_FAILED, announced=False, detail="d")
    mark_failed(LLM_CALL_FAILED, exc=RuntimeError("boom"), detail="RuntimeError")
    set_outcome(OUTCOME_FAILED)
    # no exception == pass


def test_traced_is_noop_under_noop_tracer(monkeypatch):
    import observability.spans as mod
    from opentelemetry.trace import NoOpTracer

    monkeypatch.setattr(mod, "_tracer", NoOpTracer())

    @mod.traced("dark", kind="LLM")
    def fn():
        mod.set_gen_attributes(model="m")
        return "value"

    assert fn() == "value"                 # returns normally
    assert _spans_by_name("dark") == []    # nothing exported


def test_ollama_usage_maps_token_counts():
    usage = ollama_usage({"message": {"content": "hi"}, "prompt_eval_count": 150, "eval_count": 42})
    assert usage == {"input": 150, "output": 42, "total": 192, "unit": "TOKENS"}


def test_ollama_usage_none_when_absent():
    assert ollama_usage({"message": {"content": "hi"}}) is None


def test_ollama_usage_partial_counts():
    usage = ollama_usage({"eval_count": 10})
    assert usage == {"input": None, "output": 10, "total": 10, "unit": "TOKENS"}


class _FakeMessage:
    def __init__(self, content="", usage_metadata=None, response_metadata=None):
        self.content = content
        self.usage_metadata = usage_metadata
        self.response_metadata = response_metadata or {}


def test_message_usage_from_usage_metadata():
    msg = _FakeMessage(usage_metadata={"input_tokens": 120, "output_tokens": 30, "total_tokens": 150})
    assert message_usage(msg) == {"input": 120, "output": 30, "total": 150, "unit": "TOKENS"}


def test_message_usage_falls_back_to_response_metadata():
    msg = _FakeMessage(response_metadata={"prompt_eval_count": 12, "eval_count": 8, "model": "m"})
    assert message_usage(msg) == {"input": 12, "output": 8, "total": 20, "unit": "TOKENS"}


def test_message_usage_none_without_counts():
    assert message_usage(_FakeMessage(content="hi")) is None


def test_traced_invoke_records_generation_and_returns_response():
    from observability.tracing import traced_invoke

    class _FakeMessage:
        def __init__(self, content, usage_metadata, response_metadata):
            self.content = content
            self.usage_metadata = usage_metadata
            self.response_metadata = response_metadata

    resp = _FakeMessage(
        content="the answer",
        usage_metadata={"input_tokens": 90, "output_tokens": 10, "total_tokens": 100},
        response_metadata={"model": "qwen3.6:latest"},
    )

    class FakeLLM:
        def invoke(self, messages):
            return resp

    out = traced_invoke(FakeLLM(), [{"role": "user", "content": "q"}], name="doc_chat")

    assert out is resp                                   # response passed through
    span = _spans_by_name("doc_chat")[0]
    assert span.attributes.get("openinference.span.kind") == "LLM"
    assert span.attributes.get("llm.token_count.prompt") == 90
    assert span.attributes.get("llm.token_count.completion") == 10
    assert span.attributes.get("llm.token_count.total") == 100
    assert span.attributes.get("llm.model_name") == "qwen3.6:latest"
    assert span.attributes.get("output.value") == "the answer"


def test_llm_caller_reports_generation_usage(monkeypatch):
    from graph.nodes import llm_caller as mod

    class FakeResp:
        def raise_for_status(self): ...
        def json(self):
            return {"message": {"content": "answer"}, "prompt_eval_count": 200, "eval_count": 50}

    monkeypatch.setattr(mod.httpx, "post", lambda *a, **k: FakeResp())
    state = {"request": "q", "retrieved_chunks": [], "messages": [], "task_type": "research"}
    mod.llm_caller(state)

    span = _spans_by_name("llm_caller")[0]
    assert span.attributes.get("llm.token_count.prompt") == 200
    assert span.attributes.get("llm.token_count.completion") == 50
    assert span.attributes.get("llm.token_count.total") == 250
    assert span.attributes.get("output.value") == "answer"
    assert span.attributes.get("llm.model_name")


def test_planner_reports_generation_usage(monkeypatch):
    from graph.nodes import planner as mod

    class FakeResp:
        def raise_for_status(self): ...
        def json(self):
            return {"message": {"content": '{"task_type":"research","skill_plan":["research"]}'},
                    "prompt_eval_count": 30, "eval_count": 12}

    monkeypatch.setattr(mod.httpx, "post", lambda *a, **k: FakeResp())
    state = {"request": "review then research", "skill_plan": ["contract_review", "research"]}
    mod.planner(state)

    span = _spans_by_name("planner")[0]
    assert span.attributes.get("llm.token_count.total") == 42
    assert span.attributes.get("llm.model_name")


def test_intent_router_reports_generation_usage(monkeypatch):
    from graph.nodes import intent_router as mod

    class FakeResp:
        def raise_for_status(self): ...
        def json(self):
            return {"message": {"content": '{"task_type":"research"}'},
                    "prompt_eval_count": 18, "eval_count": 4}

    monkeypatch.setattr(mod.httpx, "post", lambda *a, **k: FakeResp())
    state = {"request": "what is an NDA?"}
    mod.intent_router(state)

    span = _spans_by_name("intent_router")[0]
    assert span.attributes.get("llm.token_count.total") == 22
    assert span.attributes.get("llm.model_name")


def test_doc_chat_routes_llm_through_traced_invoke(monkeypatch):
    """The (previously invisible) Word chat-tab LLM call must go through
    traced_invoke so it becomes a nested GENERATION with token usage."""
    mod = importlib.import_module("skills.legal_research.legal_research")

    sentinel_llm = object()
    captured: dict = {}

    def fake_traced_invoke(llm, messages, *, name="llm"):
        captured["llm"] = llm
        captured["name"] = name

        class _R:
            content = "Here is the summary."
        return _R()

    monkeypatch.setattr(mod, "_build_llm", lambda: sentinel_llm)
    monkeypatch.setattr(mod, "traced_invoke", fake_traced_invoke)

    state = {"request": "summarize this", "uploaded_docs": [{"text": "Some contract text."}]}
    mod.legal_research(state)

    assert captured["llm"] is sentinel_llm            # the built LLM was wrapped
    assert state["llm_response"] == "Here is the summary."


def test_llm_caller_sends_num_ctx_in_options(monkeypatch):
    """llm_caller must include num_ctx in the options dict posted to Ollama.
    Without it Ollama defaults to ~4096 tokens, which truncates large prompts.
    The value comes from settings.ollama_num_ctx (default 131072 — pinned to the
    window Spark serves; see config.py for why it must be EQUAL, not larger)."""
    from graph.nodes import llm_caller as mod
    from config import get_settings

    captured_json: dict = {}

    class FakeResp:
        def raise_for_status(self): ...
        def json(self):
            return {"message": {"content": "answer"}, "prompt_eval_count": 10, "eval_count": 5}

    def fake_post(url, *, json=None, timeout=None):
        captured_json.update(json or {})
        return FakeResp()

    monkeypatch.setattr(mod.httpx, "post", fake_post)
    monkeypatch.setenv("OLLAMA_NUM_CTX", "16384")
    get_settings.cache_clear()

    state = {"request": "q", "retrieved_chunks": [], "messages": [], "task_type": "research"}
    mod.llm_caller(state)

    assert captured_json["options"].get("num_ctx") == 16384
    get_settings.cache_clear()


def test_intake_stamps_identity_on_root(monkeypatch):
    from graph.nodes import intake as mod
    from observability.spans import traced

    @traced("query")                      # simulate the route root span
    def run():
        return mod.intake({
            "user_id": "u42", "session_id": "s7", "uploaded_docs": [],
            "task_type": "research", "request": "what is an NDA?",
        })

    run()
    root = _spans_by_name("query")[0]
    assert root.attributes.get("user.id") == "u42"
    assert root.attributes.get("session.id") == "s7"


def test_llm_caller_routes_token_usage_to_state(monkeypatch):
    """The usage llm_caller already computes for spans must also reach state,
    so the review path can report real token counts to the pane."""
    import httpx
    from graph.nodes import llm_caller as mod

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "review"}, "prompt_eval_count": 25270,
                    "eval_count": 412}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    state = {"request": "review", "task_type": "contract_review", "retrieved_chunks": []}
    result = mod.llm_caller(state)
    assert result["token_usage"]["input"] == 25270
    assert result["token_usage"]["output"] == 412


def test_llm_caller_flags_review_input_over_headroom(monkeypatch):
    """A review whose assembled input exceeds the window's headroom must SAY so.

    contract_review has no input cap, so at some document size Ollama
    middle-drops the prompt — removing exactly the playbook/MSA. Detection turns
    a silently-wrong review into a visibly-degraded one. It must never block the
    review.
    """
    import httpx
    from graph.nodes import llm_caller as mod

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "review"}}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    # num_ctx 1000 - num_predict_review 500 = 500 tokens * 4.0 chars = 2000 chars headroom
    monkeypatch.setattr(mod, "get_settings", lambda: SimpleNamespace(
        llm_model="m", ollama_base_url="http://x", ollama_num_ctx=1000,
        ollama_num_predict_chat=100, ollama_num_predict_review=500,
        est_chars_per_token=4.0,
    ))
    state = {"request": "x" * 5000, "task_type": "contract_review", "retrieved_chunks": []}
    result = mod.llm_caller(state)

    assert result["llm_response"] == "review"          # never blocked
    assert result["context_truncated"] is not None
    assert result["context_truncated"]["kept_pct"] < 100


def test_llm_caller_does_not_flag_review_within_headroom(monkeypatch):
    """A review that fits leaves the flag alone, so the notice cannot cry wolf."""
    import httpx
    from graph.nodes import llm_caller as mod

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "review"}}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    monkeypatch.setattr(mod, "get_settings", lambda: SimpleNamespace(
        llm_model="m", ollama_base_url="http://x", ollama_num_ctx=131072,
        ollama_num_predict_chat=2048, ollama_num_predict_review=8192,
        est_chars_per_token=4.89,
    ))
    state = {"request": "short request", "task_type": "contract_review", "retrieved_chunks": []}
    result = mod.llm_caller(state)
    assert result.get("context_truncated") is None


def test_llm_caller_clears_stale_context_truncated_flag(monkeypatch):
    """A stale flag from a PRIOR over-budget turn must not bleed into this one.

    State persists per thread via the Redis checkpointer, and initial_state
    never seeds context_truncated — so a key absent from this turn's input
    keeps its checkpointed value unless the producing node resets it itself.
    """
    import httpx
    from graph.nodes import llm_caller as mod

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "review"}}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    monkeypatch.setattr(mod, "get_settings", lambda: SimpleNamespace(
        llm_model="m", ollama_base_url="http://x", ollama_num_ctx=131072,
        ollama_num_predict_chat=2048, ollama_num_predict_review=8192,
        est_chars_per_token=4.89,
    ))
    state = {
        "request": "short request", "task_type": "contract_review", "retrieved_chunks": [],
        "context_truncated": {"doc_chars": 999, "kept_chars": 1, "kept_pct": 0},
    }
    result = mod.llm_caller(state)
    assert result["context_truncated"] is None


def test_llm_caller_early_return_leaves_chat_path_flag_untouched(monkeypatch):
    """When legal_research already answered (chat path), llm_caller must hit
    the early-return BEFORE any reset — so it must not clobber the flag
    legal_research just set for this same turn."""
    from graph.nodes import llm_caller as mod

    state = {
        "llm_response": "already answered",
        "context_truncated": {"doc_chars": 999, "kept_chars": 1, "kept_pct": 0},
        "token_usage": {"input": 1, "output": 2},
    }
    result = mod.llm_caller(state)
    assert result["context_truncated"] == {"doc_chars": 999, "kept_chars": 1, "kept_pct": 0}
    assert result["token_usage"] == {"input": 1, "output": 2}


def test_ollama_timings_converts_nanoseconds_to_milliseconds():
    from observability.tracing import ollama_timings

    timings = ollama_timings({
        "total_duration": 34_659_000_000,
        "load_duration": 4_713_000_000,
        "prompt_eval_duration": 8_902_000_000,
        "eval_duration": 21_044_000_000,
    })
    assert timings == {
        "total_ms": 34_659, "load_ms": 4_713,
        "prompt_eval_ms": 8_902, "eval_ms": 21_044,
    }


def test_ollama_timings_none_when_absent():
    from observability.tracing import ollama_timings
    assert ollama_timings({"message": {"content": "hi"}}) is None


def test_ollama_timings_partial_payload():
    from observability.tracing import ollama_timings
    assert ollama_timings({"load_duration": 4_713_000_000}) == {"load_ms": 4_713}


def test_set_gen_attributes_records_timings_on_the_span():
    from observability.spans import traced, set_gen_attributes

    @traced("gen", kind="LLM")
    def gen():
        set_gen_attributes(model="m", timings={"load_ms": 4713, "eval_ms": 21044})

    gen()
    attrs = _spans_by_name("gen")[0].attributes
    assert attrs["llm.ollama.load_ms"] == 4713
    assert attrs["llm.ollama.eval_ms"] == 21044
