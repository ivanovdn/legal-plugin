"""The route's job is identity and failure policy — the store does the rest.

attorney_id must come from the identity seam and never from the body, so the
SSO cutover reaches feedback for free. Feedback failures must reach the
attorney; event failures must not reach anyone.
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.main import app
from memory import feedback_store as fs


@pytest.fixture
def client(monkeypatch):
    fake = SimpleNamespace(feedback_enabled=True, feedback_snapshot_max_chars=50)
    monkeypatch.setattr("api.routes.feedback.get_settings", lambda: fake)
    # raise_server_exceptions=False so a 500 arrives as a response, not a raise.
    return TestClient(app, raise_server_exceptions=False), fake


def _body(**over):
    b = {"turn_id": "t-1", "trace_id": "abc", "session_id": "s-1",
         "document_id": "d-1", "surface": "chat", "target_kind": "edit",
         "target_ref": "[__]", "comment": "filled the wrong field",
         "snapshot": {"document_text": "NDA"}}
    b.update(over)
    return b


def test_feedback_round_trip(client):
    c, _ = client
    r = c.post("/api/feedback", json=_body(), headers={"X-User-ID": "atty-1"})
    assert r.status_code == 200
    assert r.json()["data"]["saved"] is True
    rows = fs.recent_feedback()
    assert rows[0]["comment"] == "filled the wrong field"
    assert rows[0]["attorney_id"] == "atty-1"


def test_attorney_id_comes_from_the_seam_not_the_body(client):
    """Otherwise SSO would not reach feedback, and a client could spoof."""
    c, _ = client
    c.post("/api/feedback", json=_body(attorney_id="somebody-else"),
           headers={"X-User-ID": "atty-real"})
    assert fs.recent_feedback()[0]["attorney_id"] == "atty-real"


def test_user_name_is_captured(client):
    c, _ = client
    c.post("/api/feedback", json=_body(),
           headers={"X-User-ID": "atty-1", "X-User-Name": "Dana"})
    assert fs.recent_feedback()[0]["user_name"] == "Dana"


def test_empty_comment_rejected(client):
    c, _ = client
    r = c.post("/api/feedback", json=_body(comment="   "), headers={"X-User-ID": "a"})
    assert r.status_code == 400


def test_snapshot_is_truncated_at_the_configured_cap(client):
    c, _ = client
    c.post("/api/feedback", json=_body(snapshot={"document_text": "x" * 500}),
           headers={"X-User-ID": "a"})
    with fs.get_pool().connection() as conn:
        snap = conn.execute("SELECT snapshot FROM feedback").fetchone()[0]
    assert len(snap["document_text"]) < 500
    assert "truncated" in snap["document_text"]


def test_feedback_failure_is_loud(client, monkeypatch):
    """The attorney must learn it did not send."""
    c, _ = client
    def _boom(**_kw):
        raise RuntimeError("db down")
    monkeypatch.setattr("api.routes.feedback.save_feedback", _boom)
    assert c.post("/api/feedback", json=_body(), headers={"X-User-ID": "a"}).status_code == 500


def test_feedback_disabled_is_403(client):
    c, fake = client
    fake.feedback_enabled = False
    assert c.post("/api/feedback", json=_body(), headers={"X-User-ID": "a"}).status_code == 403


def test_events_round_trip(client):
    c, _ = client
    r = c.post("/api/events", json={"events": [
        {"turn_id": "t", "session_id": "s", "document_id": "d",
         "surface": "chat", "action": "edits_proposed", "detail": "3"},
        {"turn_id": "t", "session_id": "s", "document_id": "d",
         "surface": "chat", "action": "edit_discarded"},
    ]}, headers={"X-User-ID": "atty-1"})
    assert r.status_code == 200 and r.json()["data"]["recorded"] == 2
    assert sum(x["count"] for x in fs.event_counts()) == 2


def test_events_failure_is_quiet(client, monkeypatch):
    """A telemetry outage must be invisible to the pane."""
    c, _ = client
    def _boom():
        raise RuntimeError("db down")
    monkeypatch.setattr("memory.feedback_store.get_pool", _boom)
    r = c.post("/api/events", json={"events": [{"action": "edit_applied"}]},
               headers={"X-User-ID": "a"})
    assert r.status_code == 200


def test_events_disabled_is_still_200(client):
    c, fake = client
    fake.feedback_enabled = False
    r = c.post("/api/events", json={"events": [{"action": "edit_applied"}]},
               headers={"X-User-ID": "a"})
    assert r.status_code == 200 and r.json()["data"]["recorded"] == 0
