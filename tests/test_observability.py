# tests/test_observability.py
"""Langfuse observability helpers + GENERATION/usage wiring."""
from __future__ import annotations

import json
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from observability.tracing import (
    ollama_usage,
    message_usage,
    traced_invoke,
)

_exporter = InMemorySpanExporter()


@pytest.fixture(scope="session", autouse=True)
def _otel_test_provider():
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(_exporter))
    trace.set_tracer_provider(provider)   # first real set wins (app tracing is off in tests)
    yield


@pytest.fixture(autouse=True)
def _clear_spans():
    _exporter.clear()
    yield
    _exporter.clear()


def _spans_by_name(name):
    return [s for s in _exporter.get_finished_spans() if s.name == name]


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
    from observability.spans import set_trace_attributes, set_gen_attributes
    # called outside any @traced span → current span is invalid/non-recording
    set_trace_attributes(user_id="u", metadata={"k": "v"})
    set_gen_attributes(model="m", usage={"input": 1, "output": 2, "total": 3, "unit": "TOKENS"})
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


def test_traced_invoke_records_generation_and_returns_response(monkeypatch):
    import observability.tracing as mod

    resp = _FakeMessage(
        content="the answer",
        usage_metadata={"input_tokens": 90, "output_tokens": 10, "total_tokens": 100},
        response_metadata={"model": "qwen3.6:latest"},
    )

    class FakeLLM:
        def invoke(self, messages):
            return resp

    captured: dict = {}
    monkeypatch.setattr(mod.langfuse_context, "update_current_observation",
                        lambda **kw: captured.update(kw))

    out = traced_invoke(FakeLLM(), [{"role": "user", "content": "q"}], name="doc_chat")

    assert out is resp                                  # response passed through
    assert captured["usage"] == {"input": 90, "output": 10, "total": 100, "unit": "TOKENS"}
    assert captured["model"] == "qwen3.6:latest"
    assert captured["output"] == "the answer"
    assert captured["name"] == "doc_chat"


def test_llm_caller_reports_generation_usage(monkeypatch):
    from graph.nodes import llm_caller as mod

    class FakeResp:
        def raise_for_status(self): ...
        def json(self):
            return {"message": {"content": "answer"}, "prompt_eval_count": 200, "eval_count": 50}

    monkeypatch.setattr(mod.httpx, "post", lambda *a, **k: FakeResp())
    captured: dict = {}
    monkeypatch.setattr(mod.langfuse_context, "update_current_observation",
                        lambda **kw: captured.update(kw))

    state = {"request": "q", "retrieved_chunks": [], "messages": [], "task_type": "research"}
    mod.llm_caller(state)

    assert captured["usage"] == {"input": 200, "output": 50, "total": 250, "unit": "TOKENS"}
    assert captured["model"]            # settings.llm_model, non-empty
    assert captured["output"] == "answer"


def test_planner_reports_generation_usage(monkeypatch):
    from graph.nodes import planner as mod

    class FakeResp:
        def raise_for_status(self): ...
        def json(self):
            return {"message": {"content": '{"task_type":"research","skill_plan":["research"]}'},
                    "prompt_eval_count": 30, "eval_count": 12}

    monkeypatch.setattr(mod.httpx, "post", lambda *a, **k: FakeResp())
    captured: dict = {}
    monkeypatch.setattr(mod.langfuse_context, "update_current_observation",
                        lambda **kw: captured.update(kw))

    # skill_plan length > 1 so the planner actually calls the LLM
    state = {"request": "review then research", "skill_plan": ["contract_review", "research"]}
    mod.planner(state)

    assert captured["usage"] == {"input": 30, "output": 12, "total": 42, "unit": "TOKENS"}
    assert captured["model"]


def test_intent_router_reports_generation_usage(monkeypatch):
    from graph.nodes import intent_router as mod

    class FakeResp:
        def raise_for_status(self): ...
        def json(self):
            return {"message": {"content": '{"task_type":"research"}'},
                    "prompt_eval_count": 18, "eval_count": 4}

    monkeypatch.setattr(mod.httpx, "post", lambda *a, **k: FakeResp())
    captured: dict = {}
    monkeypatch.setattr(mod.langfuse_context, "update_current_observation",
                        lambda **kw: captured.update(kw))
    monkeypatch.setattr(mod.langfuse_context, "update_current_trace", lambda **kw: None)

    state = {"request": "what is an NDA?"}   # no task_type → LLM classifies
    mod.intent_router(state)

    assert captured["usage"] == {"input": 18, "output": 4, "total": 22, "unit": "TOKENS"}
    assert captured["model"]


def test_doc_chat_routes_llm_through_traced_invoke(monkeypatch):
    """The (previously invisible) Word chat-tab LLM call must go through
    traced_invoke so it becomes a nested GENERATION with token usage."""
    from skills import legal_research as mod

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
    The value comes from settings.ollama_num_ctx (default 32768)."""
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
    monkeypatch.setattr(mod.langfuse_context, "update_current_observation", lambda **kw: None)
    monkeypatch.setenv("OLLAMA_NUM_CTX", "16384")
    get_settings.cache_clear()

    state = {"request": "q", "retrieved_chunks": [], "messages": [], "task_type": "research"}
    mod.llm_caller(state)

    assert "options" in captured_json
    assert captured_json["options"].get("num_ctx") == 16384
    get_settings.cache_clear()
