"""An in-flight LLM turn must be visible in the logs while it is still running.

Every log line on both LLM paths used to fire AFTER the call, and uvicorn writes
its access line only on completion — so a turn that was merely slow left no
trace anywhere. On SRV-AGENT-01 (2026-08-14) that made "queued behind
compliance-bot on the shared Ollama at 172.20.0.22" and "wedged" look identical
in `docker compose logs backend`: five completed 200s and nothing for the turn
still running.

The load-bearing property is ORDERING, not the text — a start line emitted after
the call would pass a naive "is it in the log" assertion and fix nothing.
"""

import importlib
import logging

from unittest.mock import MagicMock, patch

from config import get_settings
from graph.nodes.llm_caller import llm_caller

legal_research = importlib.import_module("skills.legal_research.legal_research")


def _state(**over):
    base = {
        "request": "who signs?", "user_id": "a-1", "uploaded_docs": [], "task_type": "",
        "skill_plan": [], "retrieval_query": "", "retrieved_chunks": [], "filters": {},
        "messages": [], "llm_response": "", "risk_level": "", "risk_flags": [],
        "awaiting_review": False, "attorney_notes": "", "report": {},
        "session_id": "s", "checkpoint_ref": "", "trace_id": "", "chat_history": [],
        "review_iterations": 0, "report_notes_unincorporated": "",
    }
    base.update(over)
    return base


# --- review / Chainlit path -------------------------------------------------

def test_llm_caller_logs_before_it_calls_ollama(monkeypatch, caplog):
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    seen_at_call_time = {}

    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"message": {"content": "answer"}}

    def _capture(*a, **kw):
        # Read the log from INSIDE the call — this is what proves the announce
        # happened first. A post-call log line would leave this empty.
        seen_at_call_time["text"] = caplog.text
        return resp

    with caplog.at_level(logging.INFO):
        with patch("graph.nodes.llm_caller.httpx.post", side_effect=_capture):
            llm_caller(_state(task_type="contract_review", request="Review this."))

    assert "-> ollama" in seen_at_call_time["text"], "no start line before the call"
    assert "task=contract_review" in seen_at_call_time["text"]
    assert "qwen3.6:latest" in seen_at_call_time["text"]
    # and the completion line, with a duration, arrives after
    assert "<- ollama" in caplog.text
    assert " in " in caplog.text.split("<- ollama")[1][:40]
    get_settings.cache_clear()


def test_llm_caller_logs_duration_on_failure(monkeypatch, caplog):
    """A turn that dies after 200s must say so — silence reads as a hang."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()

    with caplog.at_level(logging.INFO):
        with patch("graph.nodes.llm_caller.httpx.post", side_effect=RuntimeError("boom")):
            llm_caller(_state(request="Review this."))

    assert "-> ollama" in caplog.text
    assert "FAILED after" in caplog.text
    get_settings.cache_clear()


# --- doc-chat path ----------------------------------------------------------

def test_doc_chat_logs_before_it_calls_the_model(monkeypatch, caplog):
    seen_at_call_time = {}

    class _Resp:
        content = "answer"

    def fake_invoke(llm, messages, name=None):
        seen_at_call_time["text"] = caplog.text
        return _Resp()

    monkeypatch.setattr(legal_research, "_build_llm", lambda: object())
    monkeypatch.setattr(legal_research, "traced_invoke", fake_invoke)

    with caplog.at_level(logging.INFO):
        legal_research._run_doc_chat(
            _state(request="who signs?"), "NON-DISCLOSURE AGREEMENT\n\n1. Term ..."
        )

    assert "-> doc_chat" in seen_at_call_time["text"], "no start line before the call"
    # The fields that let you tell a lean turn from a grounded one at a glance.
    for field in ("model=", "doc=", "grounded=", "history=", "msgs="):
        assert field in seen_at_call_time["text"], field
    assert "<- doc_chat" in caplog.text


# --- generation must be BOUNDED ---------------------------------------------

def test_review_call_bounds_generated_tokens(monkeypatch):
    """Unbounded generation is why a degenerate loop presents as a hang.

    Observed on the VM (2026-08-14): asked to "fill the title" against a title
    that was ALREADY filled, the model looped the same self-doubt paragraph
    inside a JSON rationale until it exhausted the context window — roughly 21k
    tokens of it. Ollama's default num_predict is "fill the context", so nothing
    stopped it. A cap turns that into a truncated answer instead of a stall.
    """
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()

    sent = {}
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"message": {"content": "answer"}}

    def _capture(*a, **kw):
        sent.update(kw.get("json") or {})
        return resp

    with patch("graph.nodes.llm_caller.httpx.post", side_effect=_capture):
        llm_caller(_state(request="Review this."))

    opts = sent["options"]
    assert opts.get("num_predict"), "review generation is unbounded"
    assert opts["num_predict"] == get_settings().ollama_num_predict_review
    get_settings.cache_clear()


def test_every_chat_llm_bounds_generated_tokens(monkeypatch):
    """Both chat builders — the conversational one AND the JSON-retry one.

    The retry is the likelier one to loop (it fires precisely when the first
    reply was already malformed), so leaving it uncapped would miss the case
    this guards.
    """
    built = []
    monkeypatch.setattr(legal_research, "_llm_cache", {})
    monkeypatch.setattr(
        legal_research, "ChatOllama",
        lambda **kw: built.append(kw) or MagicMock(),
    )

    legal_research._build_llm()
    legal_research._build_json_llm()

    assert len(built) == 2
    cap = get_settings().ollama_num_predict_chat
    for kw in built:
        assert kw.get("num_predict") == cap, kw.get("format", "conversational")
