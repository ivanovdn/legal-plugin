"""POST /api/compact — identity from the auth seam, loud failure, quiet no-op."""
import pytest
from fastapi.testclient import TestClient

import skills.legal_research.compaction as compaction
from api.main import app
from memory.conversation_store import append_turn, load_rows_after
from memory.conversation_summary import load_segments

client = TestClient(app)


def _quotes_for(rows, ids):
    by_id = {r["id"]: r for r in rows}
    out = []
    for rid in ids:
        row = by_id[rid]
        speaker = "attorney" if row["role"] == "user" else "assistant, said earlier"
        out.append(f'[#{rid} {speaker}] "{row["content"]}"')
    return "\n".join(out)


def test_compacts_for_the_header_identity_not_a_body_field(monkeypatch):
    for i in range(5):
        append_turn("doc-x", "atty-x", f"q{i}", f"a{i}")
    monkeypatch.setattr(
        compaction, "_generate_quote_lines",
        lambda r, n: _quotes_for(r, [r[0]["id"]]),
    )
    res = client.post(
        "/api/compact",
        json={"document_id": "doc-x", "attorney_id": "somebody-else"},
        headers={"X-User-ID": "atty-x"},
    )
    assert res.status_code == 200
    assert res.json()["data"]["compacted"] is True
    # The body's attorney_id is ignored entirely — identity comes from the auth
    # seam, so the O365 cutover reaches this endpoint with no change here.
    assert load_segments("doc-x", "atty-x", 5)
    assert load_segments("doc-x", "somebody-else", 5) == []


def test_nothing_to_condense_is_a_quiet_200(monkeypatch):
    append_turn("doc-y", "atty-y", "q", "a")
    monkeypatch.setattr(
        compaction, "_generate_quote_lines",
        lambda r, n: pytest.fail("must not call the LLM with nothing to condense"),
    )
    res = client.post(
        "/api/compact", json={"document_id": "doc-y"}, headers={"X-User-ID": "atty-y"},
    )
    assert res.status_code == 200
    assert res.json()["data"]["compacted"] is False
    assert res.json()["data"]["reason"]


def test_a_rejected_summary_is_a_500_and_writes_nothing(monkeypatch):
    for i in range(5):
        append_turn("doc-z", "atty-z", f"q{i}", f"a{i}")
    monkeypatch.setattr(
        compaction, "_generate_quote_lines",
        lambda r, n: '[#999 attorney] "never said"',
    )
    res = client.post(
        "/api/compact", json={"document_id": "doc-z"}, headers={"X-User-ID": "atty-z"},
    )
    # LOUD: the attorney clicked and was told it happened, so a silent failure
    # would be a lie (the same split as /api/feedback vs /api/events).
    assert res.status_code == 500
    assert load_segments("doc-z", "atty-z", 5) == []


def test_missing_document_id_is_a_400():
    res = client.post("/api/compact", json={"document_id": "  "}, headers={"X-User-ID": "a"})
    assert res.status_code == 400


def test_disabled_is_a_403(monkeypatch):
    import api.routes.compact as route

    monkeypatch.setattr(
        route, "get_settings",
        lambda: type("S", (), {"compaction_enabled": False})(),
    )
    res = client.post("/api/compact", json={"document_id": "doc-q"}, headers={"X-User-ID": "a"})
    assert res.status_code == 403
