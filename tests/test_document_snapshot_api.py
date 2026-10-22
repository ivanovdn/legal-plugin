"""POST /api/document-snapshot (SP4): quiet like /api/events, identity and
client facts from the headers, never the body (spec 2026-10-15)."""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.main import app
from memory.db import get_pool
from memory.fingerprint import fingerprint


@pytest.fixture
def client(monkeypatch):
    fake = SimpleNamespace(document_capture_enabled=True, build_sha="test-build")
    monkeypatch.setattr("api.routes.document_snapshot.get_settings", lambda: fake)
    # raise_server_exceptions=False so a 500 would arrive as a response, not a raise.
    return TestClient(app, raise_server_exceptions=False), fake


def _body(**over):
    body = {"session_id": "s-1", "document_id": "doc-1", "moment": "idle",
            "current_text": "NDA with Acme Ltd", "original_text": "NDA with [__]",
            "tracked_changes": [{"type": "Added", "author": "Jane Smith",
                                 "date": "2026-10-16T09:00:00.000Z", "text": "Acme Ltd"}],
            "tracked_total": 1}
    body.update(over)
    return body


def _snapshots() -> list[dict]:
    with get_pool().connection() as conn:
        cur = conn.execute("SELECT * FROM document_snapshot ORDER BY id")
        names = [column.name for column in cur.description]
        return [dict(zip(names, values)) for values in cur.fetchall()]


def _versions() -> int:
    with get_pool().connection() as conn:
        return conn.execute("SELECT count(*) FROM document_version").fetchone()[0]


def test_a_capture_is_recorded_with_who_and_what_sent_it(client):
    c, _ = client
    r = c.post("/api/document-snapshot", json=_body(attorney_id="somebody-else"),
               headers={"X-User-ID": "atty-1", "X-Office-Platform": "Mac", "X-Pane-Build": "abc1234"})
    assert r.status_code == 200 and r.json()["data"] == {"recorded": 1, "enabled": True}
    (s,) = _snapshots()
    assert s["attorney_id"] == "atty-1"                     # the header, never the body
    assert (s["moment"], s["session_id"], s["document_id"]) == ("idle", "s-1", "doc-1")
    assert (s["office_platform"], s["pane_build"], s["backend_build"]) == (
        "Mac", "abc1234", "test-build")
    assert _versions() == 2


@pytest.mark.parametrize("moment", ["pane_open", "apply", "idle", "finalize"])
def test_every_capture_moment_is_recorded_under_its_own_name(client, moment):
    """The copy the pane takes a few seconds after an Apply is a moment of its own — the
    report counts it, and an edit's verdict is read against it. Refusing it would lose the
    copy that catches an Apply followed by closing the document.
    Mutation: leave "apply" out of DocumentSnapshot.moment (a 422)."""
    c, _ = client
    r = c.post("/api/document-snapshot", json=_body(moment=moment), headers={"X-User-ID": "atty-1"})
    assert r.status_code == 200 and r.json()["data"] == {"recorded": 1, "enabled": True}
    (s,) = _snapshots()
    assert s["moment"] == moment


def test_a_store_failure_is_quiet_and_keeps_the_text_out_of_the_log(client, monkeypatch, caplog):
    """Review Focus 2. Mutation: let the exception through (a 500), log str(e), or drop
    the hashes and sizes that say which texts the lost capture held."""
    c, _ = client

    def boom(**_kw):
        raise RuntimeError("invalid input syntax for type json: NDA with Acme Ltd")

    monkeypatch.setattr("api.routes.document_snapshot.save_snapshot", boom)
    with caplog.at_level(logging.WARNING, logger="api.routes.document_snapshot"):
        r = c.post("/api/document-snapshot", json=_body(), headers={"X-User-ID": "atty-1"})
    assert r.status_code == 200 and r.json()["data"] == {"recorded": 0, "enabled": True}
    assert "doc-1" in caplog.text and "RuntimeError" in caplog.text and "Acme" not in caplog.text
    # What identifies the lost texts: the hash turn_log records and the version rows are
    # keyed by, with its size, each under its own label (a swap would point at the wrong one).
    current_chars, current_sha = fingerprint("NDA with Acme Ltd")
    original_chars, original_sha = fingerprint("NDA with [__]")
    assert f"current sha256 {current_sha}, {current_chars} chars" in caplog.text
    assert f"original sha256 {original_sha}, {original_chars} chars" in caplog.text


def test_switched_off_it_records_nothing_and_tells_the_pane(client):
    """Mutation: ignore the setting."""
    c, fake = client
    fake.document_capture_enabled = False
    r = c.post("/api/document-snapshot", json=_body(), headers={"X-User-ID": "atty-1"})
    assert r.json()["data"] == {"recorded": 0, "enabled": False}
    assert _snapshots() == [] and _versions() == 0


@pytest.mark.parametrize("bad", [{"moment": "hourly"}, {"document_id": ""}])
def test_a_capture_with_no_document_or_an_unknown_moment_is_refused(client, bad):
    c, _ = client
    r = c.post("/api/document-snapshot", json=_body(**bad), headers={"X-User-ID": "atty-1"})
    assert r.status_code == 422 and _snapshots() == []
