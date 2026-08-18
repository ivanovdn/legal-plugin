"""Phase 0: a missing checkpointer surfaces as memory_degraded, not silence."""
import api.routes.query as q


def test_payload_flags_degraded_when_checkpointer_absent(monkeypatch):
    monkeypatch.setattr(q, "_checkpointer_active", False)
    monkeypatch.setattr(q.get_settings(), "checkpointer_enabled", True, raising=False)
    payload = q._payload_from_result(
        {"task_type": "research", "report": {}}, "sess-1", "t-1", "trace-1")
    assert payload["memory_degraded"] is True


def test_payload_not_degraded_when_checkpointer_active(monkeypatch):
    monkeypatch.setattr(q, "_checkpointer_active", True)
    monkeypatch.setattr(q.get_settings(), "checkpointer_enabled", True, raising=False)
    payload = q._payload_from_result(
        {"task_type": "research", "report": {}}, "sess-1", "t-1", "trace-1")
    assert payload["memory_degraded"] is False


def test_payload_degraded_when_report_says_so(monkeypatch):
    monkeypatch.setattr(q, "_checkpointer_active", True)
    payload = q._payload_from_result(
        {"task_type": "research", "report": {"memory_degraded": True}}, "sess-1", "t-1", "trace-1"
    )
    assert payload["memory_degraded"] is True


def test_interrupt_branch_carries_memory_degraded(monkeypatch):
    monkeypatch.setattr(q, "_checkpointer_active", False)
    monkeypatch.setattr(q.get_settings(), "checkpointer_enabled", True, raising=False)
    class _Interrupt:
        value = {"task_type": "contract_review", "risk_level": "high",
                 "llm_response": "", "risk_flags": [], "review_iterations": 0}
    payload = q._payload_from_result({"__interrupt__": [_Interrupt()]}, "s1", "t-1", "trace-1")
    assert payload["awaiting_review"] is True
    assert payload["memory_degraded"] is True


def test_memory_writer_degradation_reaches_the_payload(monkeypatch):
    """The seam, not either half of it.

    memory_writer runs after output_formatter, so a store outage it detects can
    only reach the amber banner through the report it RETURNS. Both halves can
    be individually correct while the flag still never arrives — setting
    state["memory_degraded"] there would pass every memory_writer test and
    silently never surface. This drives the real node's output into the real
    payload builder.
    """
    import graph.nodes.memory_writer as mw

    def _boom(**kw):
        raise RuntimeError("couldn't get a connection after 3.00 sec")

    monkeypatch.setattr(mw, "write_audit_log", _boom)
    monkeypatch.setattr(mw, "save_review", lambda **kw: None)
    monkeypatch.setattr(q, "_checkpointer_active", True)

    node_out = mw.memory_writer({
        "session_id": "s1", "user_id": "u1", "task_type": "contract_review",
        "request": "Review this", "document_id": "d1",
        "llm_response": "# Review", "report": {"response": "# Review"},
    })
    payload = q._payload_from_result(
        {"task_type": "contract_review", **node_out}, "s1", "t-1", "trace-1")
    assert payload["memory_degraded"] is True


def test_awaiting_review_branch_carries_memory_degraded(monkeypatch):
    monkeypatch.setattr(q, "_checkpointer_active", False)
    monkeypatch.setattr(q.get_settings(), "checkpointer_enabled", True, raising=False)
    payload = q._payload_from_result(
        {"awaiting_review": True, "task_type": "contract_review", "report": {}},
        "s1", "t-1", "trace-1")
    assert payload["memory_degraded"] is True
