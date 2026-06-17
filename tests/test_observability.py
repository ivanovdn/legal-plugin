# tests/test_observability.py
"""Langfuse observability helpers + GENERATION/usage wiring."""
from __future__ import annotations

from observability.tracing import (
    ollama_usage,
    message_usage,
    traced_invoke,
)


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
