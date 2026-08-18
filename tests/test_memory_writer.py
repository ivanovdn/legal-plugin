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


def test_audit_receives_user_name(monkeypatch):
    monkeypatch.setattr(mod, "write_audit_log", lambda **kw: captured.update(kw))
    monkeypatch.setattr(mod, "append_turn", lambda **kw: None)
    captured = {}
    mod.memory_writer(_state(task_type="research", user_id="uuid-1",
                             user_name="Dmytro Ivanov", llm_response="a"))
    assert captured["user_name"] == "Dmytro Ivanov"


# --- Postgres outage: the audit write degrades, it does not kill the turn -----
#
# `write_audit_log` was the one unwrapped store call on the turn path. With
# app-db down it raised PoolTimeout, api/routes/query.py saw a non-Redis
# exception and returned status="error" — so a Postgres outage killed EVERY
# turn, review and chat alike, while an equivalent Redis outage degrades
# cleanly and answers. The turn does not need Postgres to answer: the document
# arrives in the request and recall is an enhancement.
#
# The entry is not lost when the table is unreachable — it goes to the app log
# at ERROR. And because memory_writer runs AFTER output_formatter
# (graph.py: output_formatter -> history_appender -> memory_writer), the flag
# has to travel back on the RETURNED report; mutating state here is too late to
# reach the payload.

def _boom(**kw):
    raise RuntimeError("couldn't get a connection after 3.00 sec")


def test_audit_failure_does_not_kill_the_turn(monkeypatch):
    monkeypatch.setattr(mod, "write_audit_log", _boom)
    monkeypatch.setattr(mod, "save_review", lambda **kw: None)
    out = mod.memory_writer(_state())          # must not raise
    assert out["report"]["memory_degraded"] is True


def test_audit_failure_preserves_the_report_already_built(monkeypatch):
    """output_formatter has already assembled the response by this point;
    degrading must add a flag, not replace the payload."""
    monkeypatch.setattr(mod, "write_audit_log", _boom)
    monkeypatch.setattr(mod, "save_review", lambda **kw: None)
    out = mod.memory_writer(_state())
    assert out["report"]["response"] == "# Review\nFinding"


def test_audit_and_review_failures_both_surface(monkeypatch):
    """Both tables live in app-db, so one outage fails both writes at once.
    The early-return this replaced could only ever report whichever came
    second — the review error would have hidden the degraded flag."""
    monkeypatch.setattr(mod, "write_audit_log", _boom)

    def _review_boom(**kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(mod, "save_review", _review_boom)
    out = mod.memory_writer(_state())
    assert out["report"]["memory_degraded"] is True
    assert "disk full" in out["report"]["review_persist_error"]


def test_healthy_turn_is_not_flagged_degraded(monkeypatch):
    """Guards the always-true mutation: a flag that is never False is no flag."""
    monkeypatch.setattr(mod, "write_audit_log", lambda **kw: None)
    monkeypatch.setattr(mod, "save_review", lambda **kw: None)
    out = mod.memory_writer(_state())
    assert not (out.get("report") or {}).get("memory_degraded")


def test_failed_audit_entry_is_written_to_the_log(monkeypatch, caplog):
    """Constraint 6 is relaxed, not abandoned: if the row cannot reach its
    table it must still be recoverable from the application log."""
    monkeypatch.setattr(mod, "write_audit_log", _boom)
    monkeypatch.setattr(mod, "save_review", lambda **kw: None)
    with caplog.at_level("ERROR"):
        mod.memory_writer(_state(session_id="s-42", user_id="atty-9"))
    logged = "\n".join(r.getMessage() for r in caplog.records if r.levelname == "ERROR")
    assert "s-42" in logged, "the session the entry belonged to must be recoverable"
    assert "atty-9" in logged, "so must who made the request"
    assert "contract_review" in logged, "and which skill ran"


def test_conversation_append_still_runs_after_an_audit_failure(monkeypatch):
    """The audit failure must not short-circuit the rest of the node. (During
    a real outage this append fails too and is swallowed at its own call site —
    what matters is that it was still attempted.)"""
    monkeypatch.setattr(mod, "write_audit_log", _boom)
    called = {"n": 0}
    monkeypatch.setattr(mod, "append_turn",
                        lambda **kw: called.__setitem__("n", called["n"] + 1))
    out = mod.memory_writer(_state(task_type="research", user_id="atty-1"))
    assert called["n"] == 1
    assert out["report"]["memory_degraded"] is True
