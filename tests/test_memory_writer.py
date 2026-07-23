"""memory_writer persists contract reviews; failures are surfaced, not silent."""
import graph.nodes.memory_writer as mod


def _state(**kw):
    base = {
        "session_id": "s1", "user_id": "u1", "task_type": "contract_review",
        "request": "Review this", "risk_level": "low", "attorney_notes": "",
        "document_id": "doc-1", "llm_response": "# Review\nFinding",
        "contract_type_detected": "sow", "report": {"response": "# Review\nFinding"},
        "awaiting_review": False,
    }
    base.update(kw)
    return base


def test_persists_review_for_contract_review_turn(monkeypatch):
    monkeypatch.setattr(mod, "write_audit_log", lambda **kw: None)
    saved = {}
    monkeypatch.setattr(mod, "save_review",
                        lambda document_id, session_id, markdown, contract_type:
                        saved.update(document_id=document_id, markdown=markdown,
                                     contract_type=contract_type))
    mod.memory_writer(_state())
    assert saved["document_id"] == "doc-1"
    assert saved["markdown"] == "# Review\nFinding"
    assert saved["contract_type"] == "sow"


def test_does_not_persist_for_non_review_turn(monkeypatch):
    monkeypatch.setattr(mod, "write_audit_log", lambda **kw: None)
    # A research turn now triggers the conversation-store write path — mock it so
    # this test can't do a live write to the real Postgres store.
    monkeypatch.setattr(mod, "append_turn", lambda **kw: None)
    called = {"n": 0}
    monkeypatch.setattr(mod, "save_review", lambda **kw: called.__setitem__("n", called["n"] + 1))
    mod.memory_writer(_state(task_type="research"))
    assert called["n"] == 0


def test_write_failure_is_surfaced_in_report(monkeypatch):
    monkeypatch.setattr(mod, "write_audit_log", lambda **kw: None)
    def _boom(**kw):
        raise RuntimeError("disk full")
    monkeypatch.setattr(mod, "save_review", _boom)
    out = mod.memory_writer(_state())
    assert "review_persist_error" in out["report"]
    assert "disk full" in out["report"]["review_persist_error"]


def test_persists_conversation_for_research_turn(monkeypatch):
    monkeypatch.setattr(mod, "write_audit_log", lambda **kw: None)
    saved = {}
    monkeypatch.setattr(
        mod, "append_turn",
        lambda document_id, attorney_id, user_text, assistant_text:
        saved.update(document_id=document_id, attorney_id=attorney_id,
                     user_text=user_text, assistant_text=assistant_text),
    )
    mod.memory_writer(_state(
        task_type="research", user_id="atty-1",
        request="who signs?", llm_response="Boris signs.",
    ))
    assert saved == {
        "document_id": "doc-1", "attorney_id": "atty-1",
        "user_text": "who signs?", "assistant_text": "Boris signs.",
    }


def test_does_not_persist_conversation_for_review_turn(monkeypatch):
    monkeypatch.setattr(mod, "write_audit_log", lambda **kw: None)
    monkeypatch.setattr(mod, "save_review", lambda **kw: None)
    called = {"n": 0}
    monkeypatch.setattr(mod, "append_turn",
                        lambda **kw: called.__setitem__("n", called["n"] + 1))
    mod.memory_writer(_state(task_type="contract_review"))
    assert called["n"] == 0


def test_skips_conversation_when_no_document_id(monkeypatch):
    monkeypatch.setattr(mod, "write_audit_log", lambda **kw: None)
    called = {"n": 0}
    monkeypatch.setattr(mod, "append_turn",
                        lambda **kw: called.__setitem__("n", called["n"] + 1))
    mod.memory_writer(_state(task_type="research", user_id="atty-1", document_id=""))
    assert called["n"] == 0


def test_conversation_write_failure_is_non_fatal(monkeypatch):
    monkeypatch.setattr(mod, "write_audit_log", lambda **kw: None)
    def _boom(**kw):
        raise RuntimeError("disk full")
    monkeypatch.setattr(mod, "append_turn", _boom)
    out = mod.memory_writer(_state(task_type="research", user_id="atty-1"))
    assert "review_persist_error" not in (out.get("report") or {})
