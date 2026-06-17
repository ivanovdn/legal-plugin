# tests/test_observability.py
"""Langfuse observability helpers + GENERATION/usage wiring."""
from __future__ import annotations

from observability.tracing import ollama_usage, langchain_callbacks


def test_ollama_usage_maps_token_counts():
    usage = ollama_usage({"message": {"content": "hi"}, "prompt_eval_count": 150, "eval_count": 42})
    assert usage == {"input": 150, "output": 42, "total": 192, "unit": "TOKENS"}


def test_ollama_usage_none_when_absent():
    assert ollama_usage({"message": {"content": "hi"}}) is None


def test_ollama_usage_partial_counts():
    usage = ollama_usage({"eval_count": 10})
    assert usage == {"input": None, "output": 10, "total": 10, "unit": "TOKENS"}


def test_langchain_callbacks_empty_without_active_span():
    # No @observe context active in a plain unit test → handler is None → [].
    assert langchain_callbacks() == []


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


def test_doc_chat_forwards_langfuse_callbacks(monkeypatch):
    from skills import legal_research as mod

    captured: dict = {}

    class FakeLLM:
        def invoke(self, messages, config=None):
            captured["config"] = config
            class _R:  # minimal AIMessage stand-in
                content = "Here is the summary."
            return _R()

    monkeypatch.setattr(mod, "_build_llm", lambda: FakeLLM())
    monkeypatch.setattr(mod, "langchain_callbacks", lambda: ["SENTINEL"])

    # Shape state so _extract_uploaded_text returns non-empty (real key: uploaded_docs).
    state = {"request": "summarize this", "uploaded_docs": [{"text": "Some contract text."}]}
    mod.legal_research(state)

    assert captured["config"] == {"callbacks": ["SENTINEL"]}
