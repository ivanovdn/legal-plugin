"""_load_prior_conversation — durable history load, guards, degraded posture."""
from types import SimpleNamespace

import skills.legal_research as lr
from memory.conversation_store import append_turn


def _settings(enabled=True, max_messages=20):
    return SimpleNamespace(
        conversation_store_enabled=enabled,
        conversation_max_messages=max_messages,
    )


def test_loads_durable_history(monkeypatch):
    append_turn("doc-1", "atty-1", "q1", "a1")
    monkeypatch.setattr(lr, "get_settings", lambda: _settings())
    msgs = lr._load_prior_conversation({"document_id": "doc-1", "user_id": "atty-1"})
    assert [m["content"] for m in msgs] == ["q1", "a1"]


def test_empty_when_disabled(monkeypatch):
    append_turn("doc-1", "atty-1", "q1", "a1")
    monkeypatch.setattr(lr, "get_settings", lambda: _settings(enabled=False))
    assert lr._load_prior_conversation({"document_id": "doc-1", "user_id": "atty-1"}) == []


def test_empty_when_ids_missing(monkeypatch):
    monkeypatch.setattr(lr, "get_settings", lambda: _settings())
    assert lr._load_prior_conversation({"document_id": "", "user_id": "atty-1"}) == []
    assert lr._load_prior_conversation({"document_id": "doc-1", "user_id": ""}) == []


def test_read_failure_flags_degraded(monkeypatch):
    monkeypatch.setattr(lr, "get_settings", lambda: _settings())
    def _boom(*a, **k):
        raise RuntimeError("db gone")
    monkeypatch.setattr(lr, "load_recent", _boom)
    state = {"document_id": "doc-1", "user_id": "atty-1"}
    assert lr._load_prior_conversation(state) == []
    assert state["memory_degraded"] is True
