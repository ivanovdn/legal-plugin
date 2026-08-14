"""Feedback is loud, telemetry is quiet — the same split as review vs conversation.

A lost feedback row is a lie to the attorney, who was told it sent. A lost
interaction event is a rounding error in a rate. They must fail differently.
"""
import pytest

from memory import feedback_store as fs


def _save(**over):
    kw = dict(
        turn_id="t-1", trace_id="abc123", session_id="s-1", document_id="d-1",
        attorney_id="atty-1", user_name="Dana", surface="chat",
        target_kind="edit", target_ref="[__]", comment="wrong field",
        snapshot={"document_text": "NDA ..."},
    )
    kw.update(over)
    return fs.save_feedback(**kw)


def test_feedback_round_trip():
    _save()
    rows = fs.recent_feedback()
    assert len(rows) == 1
    assert rows[0]["comment"] == "wrong field"
    assert rows[0]["trace_id"] == "abc123"
    assert rows[0]["surface"] == "chat"
    assert isinstance(rows[0]["id"], int), "id must round-trip so a row can be hand-pulled"
    assert rows[0]["document_id"] == "d-1"


def test_recent_feedback_is_newest_first():
    _save(comment="older")
    _save(comment="newer")
    assert [r["comment"] for r in fs.recent_feedback()] == ["newer", "older"]


def test_snapshot_survives_the_round_trip():
    _save(snapshot={"document_text": "hello", "request": "who signs?"})
    with fs.get_pool().connection() as conn:
        snap = conn.execute("SELECT snapshot FROM feedback").fetchone()[0]
    assert snap["request"] == "who signs?"


def test_feedback_write_is_loud(monkeypatch):
    """The attorney was told it sent. A silent loss is a lie."""
    def _boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(fs, "get_pool", _boom)
    with pytest.raises(RuntimeError):
        _save()


def test_event_round_trip_and_counts():
    fs.record_event(turn_id="t-1", session_id="s", document_id="d",
                    attorney_id="a", surface="chat", action="edit_applied")
    fs.record_event(turn_id="t-1", session_id="s", document_id="d",
                    attorney_id="a", surface="chat", action="edit_applied")
    fs.record_event(turn_id="t-1", session_id="s", document_id="d",
                    attorney_id="a", surface="chat", action="edit_discarded")
    counts = {(c["surface"], c["action"]): c["count"] for c in fs.event_counts()}
    assert counts[("chat", "edit_applied")] == 2
    assert counts[("chat", "edit_discarded")] == 1


def test_counter_totals_round_trip():
    """turns = how many turns fired the counter; total = the summed detail."""
    fs.record_event(turn_id="t-1", session_id="s", document_id="d",
                    attorney_id="a", surface="chat", action="edits_proposed",
                    detail="3")
    fs.record_event(turn_id="t-2", session_id="s", document_id="d",
                    attorney_id="a", surface="chat", action="edits_proposed",
                    detail="4")
    fs.record_event(turn_id="t-1", session_id="s", document_id="d",
                    attorney_id="a", surface="findings", action="findings_rendered",
                    detail="2")
    # A per-item action in the same table must never leak into counter_totals().
    fs.record_event(turn_id="t-1", session_id="s", document_id="d",
                    attorney_id="a", surface="chat", action="edit_applied")

    totals = {(t["surface"], t["action"]): t for t in fs.counter_totals()}
    assert totals[("chat", "edits_proposed")] == {
        "surface": "chat", "action": "edits_proposed", "turns": 2, "total": 7,
    }
    assert totals[("findings", "findings_rendered")] == {
        "surface": "findings", "action": "findings_rendered", "turns": 1, "total": 2,
    }
    assert ("chat", "edit_applied") not in totals


def test_counter_totals_survives_a_non_numeric_detail():
    """A stray non-numeric/empty detail on a counter action must not raise —
    it contributes 0 to `total` (guarded, not trusted) while the row still
    counts toward `turns`."""
    fs.record_event(turn_id="t-1", session_id="s", document_id="d",
                    attorney_id="a", surface="chat", action="edits_proposed",
                    detail="5")
    fs.record_event(turn_id="t-2", session_id="s", document_id="d",
                    attorney_id="a", surface="chat", action="edits_proposed",
                    detail="not-a-number")
    fs.record_event(turn_id="t-3", session_id="s", document_id="d",
                    attorney_id="a", surface="chat", action="edits_proposed",
                    detail="")

    totals = {(t["surface"], t["action"]): t for t in fs.counter_totals()}
    row = totals[("chat", "edits_proposed")]
    assert row["turns"] == 3, "all three rows count as turns regardless of detail"
    assert row["total"] == 5, "the non-numeric and empty details contribute 0, not an error"


def test_counter_totals_empty_when_no_counters_fired():
    fs.record_event(turn_id="t-1", session_id="s", document_id="d",
                    attorney_id="a", surface="chat", action="edit_discarded")
    assert fs.counter_totals() == []


def test_event_write_is_quiet(monkeypatch):
    """Telemetry must never break an Apply."""
    def _boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(fs, "get_pool", _boom)
    fs.record_event(turn_id="t", session_id="s", document_id="d",
                    attorney_id="a", surface="chat", action="edit_applied")
    # no exception is the assertion


def test_record_events_batch_counts_what_landed():
    n = fs.record_events(
        [
            {"turn_id": "t", "session_id": "s", "document_id": "d",
             "surface": "chat", "action": "edits_proposed", "detail": "3"},
            {"turn_id": "t", "session_id": "s", "document_id": "d",
             "surface": "chat", "action": "edit_applied"},
        ],
        attorney_id="a",
    )
    assert n == 2
    assert sum(c["count"] for c in fs.event_counts()) == 2


def test_record_events_swallows_a_broken_pool(monkeypatch):
    def _boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(fs, "get_pool", _boom)
    assert fs.record_events([{"action": "edit_applied"}], attorney_id="a") == 0


def test_truncate_snapshot_caps_and_marks():
    out = fs.truncate_snapshot({"document_text": "x" * 100, "request": "short"}, 10)
    assert out["document_text"].startswith("x" * 10)
    assert "truncated" in out["document_text"]
    assert out["request"] == "short", "short values are untouched"


def test_truncate_snapshot_passes_none_through():
    assert fs.truncate_snapshot(None, 10) is None


def test_tables_are_truncated_between_tests():
    """Guards the conftest trap: a missed table leaks state and greens a lie."""
    assert fs.recent_feedback() == []
    assert fs.event_counts() == []
