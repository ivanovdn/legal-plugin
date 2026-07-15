"""_load_prior_conversation — durable history load, guards, degraded posture."""
from types import SimpleNamespace

import skills.legal_research as lr
from memory.conversation_store import init_conversation_db, append_turn


def _settings(tmp_path, enabled=True, max_messages=20):
    return SimpleNamespace(
        conversation_store_enabled=enabled,
        sqlite_path=str(tmp_path / "conv.db"),
        conversation_max_messages=max_messages,
    )


def test_loads_durable_history(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    init_conversation_db(s.sqlite_path)
    append_turn(s.sqlite_path, "doc-1", "atty-1", "q1", "a1")
    monkeypatch.setattr(lr, "get_settings", lambda: s)
    msgs = lr._load_prior_conversation({"document_id": "doc-1", "user_id": "atty-1"})
    assert [m["content"] for m in msgs] == ["q1", "a1"]


def test_empty_when_disabled(tmp_path, monkeypatch):
    s = _settings(tmp_path, enabled=False)
    init_conversation_db(s.sqlite_path)
    append_turn(s.sqlite_path, "doc-1", "atty-1", "q1", "a1")
    monkeypatch.setattr(lr, "get_settings", lambda: s)
    assert lr._load_prior_conversation({"document_id": "doc-1", "user_id": "atty-1"}) == []


def test_empty_when_ids_missing(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    init_conversation_db(s.sqlite_path)
    monkeypatch.setattr(lr, "get_settings", lambda: s)
    assert lr._load_prior_conversation({"document_id": "", "user_id": "atty-1"}) == []
    assert lr._load_prior_conversation({"document_id": "doc-1", "user_id": ""}) == []


def test_read_failure_flags_degraded(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    monkeypatch.setattr(lr, "get_settings", lambda: s)
    def _boom(*a, **k):
        raise RuntimeError("db gone")
    monkeypatch.setattr(lr, "load_recent", _boom)
    state = {"document_id": "doc-1", "user_id": "atty-1"}
    assert lr._load_prior_conversation(state) == []
    assert state["memory_degraded"] is True
