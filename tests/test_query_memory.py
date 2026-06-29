"""Phase 0: a missing checkpointer surfaces as memory_degraded, not silence."""
import api.routes.query as q


def test_payload_flags_degraded_when_checkpointer_absent(monkeypatch):
    monkeypatch.setattr(q, "_checkpointer_active", False)
    monkeypatch.setattr(q.get_settings(), "checkpointer_enabled", True, raising=False)
    payload = q._payload_from_result({"task_type": "research", "report": {}}, "sess-1")
    assert payload["memory_degraded"] is True


def test_payload_not_degraded_when_checkpointer_active(monkeypatch):
    monkeypatch.setattr(q, "_checkpointer_active", True)
    payload = q._payload_from_result({"task_type": "research", "report": {}}, "sess-1")
    assert payload["memory_degraded"] is False


def test_payload_degraded_when_report_says_so(monkeypatch):
    monkeypatch.setattr(q, "_checkpointer_active", True)
    payload = q._payload_from_result(
        {"task_type": "research", "report": {"memory_degraded": True}}, "sess-1"
    )
    assert payload["memory_degraded"] is True
