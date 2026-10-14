"""Feedback is loud, telemetry is quiet — the same split as review vs conversation.

A lost feedback row is a lie to the attorney, who was told it sent. A lost
interaction event is a rounding error in a rate. They must fail differently.
"""
from datetime import datetime, timezone

import pytest

from memory import feedback_store as fs
from memory.db import get_pool, init_db


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


# --- report filters + the spurious-edit surface ------------------------------
#
# Unfiltered the store is cumulative for all time, so pilot week one and week
# ten are indistinguishable; and until `request` was stored, "did we propose
# edits on a purely factual question?" needed a manual trace lookup per turn
# and could not be computed in SQL at all.

def _ev(**over):
    kw = dict(turn_id="t-1", session_id="s-1", document_id="d-1", attorney_id="a-1",
              surface="chat", action="edits_proposed", detail="2", request="who signs?")
    kw.update(over)
    return kw


def test_request_round_trips_on_an_event():
    fs.record_event(**_ev())
    turns = fs.edit_proposal_turns()
    assert len(turns) == 1
    assert turns[0]["request"] == "who signs?"
    assert turns[0]["proposed"] == 2


def test_edit_proposal_turns_joins_the_attorneys_verdict():
    """The whole point: what was asked, what came back, what they did with it."""
    fs.record_event(**_ev())
    for action in ("edit_applied", "edit_discarded", "edit_discarded"):
        fs.record_event(**_ev(action=action, detail="", request=""))
    fs.record_event(**_ev(action="edit_failed", detail="no match", request=""))
    t = fs.edit_proposal_turns()[0]
    assert (t["applied"], t["discarded"], t["failed"]) == (1, 2, 1)


def test_edit_proposal_turns_does_not_bleed_across_turns():
    fs.record_event(**_ev(turn_id="t-A"))
    fs.record_event(**_ev(turn_id="t-B"))
    fs.record_event(**_ev(turn_id="t-A", action="edit_applied", detail=""))
    by_turn = {t["turn_id"]: t for t in fs.edit_proposal_turns()}
    assert by_turn["t-A"]["applied"] == 1
    assert by_turn["t-B"]["applied"] == 0


def test_edit_proposal_turns_survives_a_non_numeric_detail():
    """`detail` is free text on every other action; a counter row can be junk."""
    fs.record_event(**_ev(detail="not-a-number"))
    assert fs.edit_proposal_turns()[0]["proposed"] == 0


def test_filters_narrow_every_read():
    fs.record_event(**_ev(document_id="doc-A", attorney_id="atty-A"))
    fs.record_event(**_ev(turn_id="t-2", document_id="doc-B", attorney_id="atty-B"))
    assert len(fs.edit_proposal_turns(document_id="doc-A")) == 1
    assert len(fs.edit_proposal_turns(attorney_id="atty-B")) == 1
    assert sum(c["count"] for c in fs.event_counts(document_id="doc-A")) == 1
    assert sum(t["turns"] for t in fs.counter_totals(attorney_id="atty-A")) == 1


def test_since_filter_excludes_older_rows():
    """`timestamp` is TEXT, so this also pins that ISO-8601 compares correctly."""
    fs.record_event(**_ev())
    future = datetime.now(timezone.utc).replace(year=2099).isoformat()
    assert fs.edit_proposal_turns(since=future) == []
    assert fs.event_counts(since=future) == []
    assert fs.counter_totals(since=future) == []
    past = datetime.now(timezone.utc).replace(year=2000).isoformat()
    assert len(fs.edit_proposal_turns(since=past)) == 1


def test_feedback_filters_narrow_by_document_and_attorney():
    _save(document_id="doc-A", attorney_id="atty-A")
    _save(document_id="doc-B", attorney_id="atty-B")
    assert len(fs.recent_feedback(document_id="doc-A")) == 1
    assert len(fs.recent_feedback(attorney_id="atty-B")) == 1
    assert len(fs.recent_feedback()) == 2


_CLIENT = {"office_platform": "PC", "office_version": "16.0.18129", "pane_build": "p1"}
_CLIENT_COLUMNS = {"office_platform", "office_version", "pane_build", "backend_build"}

# Each table's columns as deployed on the VM before SP3 (2026-10-14). Frozen:
# never edit — they stand for every database created before this change.
_EVENT_COLUMNS_2026_10_14 = (
    "timestamp", "turn_id", "session_id", "document_id", "attorney_id", "surface",
    "action", "target_kind", "target_ref", "detail", "request",
)
_FEEDBACK_COLUMNS_2026_10_14 = (
    "timestamp", "turn_id", "trace_id", "session_id", "document_id", "attorney_id",
    "user_name", "surface", "target_kind", "target_ref", "comment", "snapshot",
)


def _client_columns(table: str) -> list[tuple]:
    with get_pool().connection() as conn:
        return conn.execute(
            f"SELECT office_platform, office_version, pane_build, backend_build "
            f"FROM {table} ORDER BY id"
        ).fetchall()


def test_record_events_stamps_what_sent_it_and_the_backend_build():
    fs.record_events([{"surface": "pane", "action": "pane_opened"}],
                     attorney_id="atty-1", client=_CLIENT, backend_build="b1")
    assert _client_columns("interaction_event") == [("PC", "16.0.18129", "p1", "b1")]


def test_save_feedback_stamps_what_sent_it_and_the_backend_build():
    _save(client=_CLIENT, backend_build="b1")
    assert _client_columns("feedback") == [("PC", "16.0.18129", "p1", "b1")]


def test_a_writer_without_client_facts_records_them_empty():
    """Chainlit and pre-SP2 panes send no client headers: unknown, not an error."""
    fs.record_events([{"action": "edit_applied"}], attorney_id="atty-1")
    _save()
    assert _client_columns("interaction_event") == [("", "", "", "")]
    assert _client_columns("feedback") == [("", "", "", "")]


@pytest.mark.parametrize("table, shipped", [
    ("interaction_event", _EVENT_COLUMNS_2026_10_14),
    ("feedback", _FEEDBACK_COLUMNS_2026_10_14),
])
def test_a_database_from_before_sp3_gains_the_client_columns(table, shipped):
    """The VM's tables exist, so CREATE TABLE IF NOT EXISTS never runs there
    again: a column added only inside the CREATE fails every write in
    production, quietly for events. Rebuild the table as it shipped, start up
    as the VM does, and require the four new columns.

    Mutation: move one client column into the CREATE and delete its ALTER."""
    with get_pool().connection() as conn:
        conn.execute(f"DROP TABLE {table}")
        conn.execute(
            f"CREATE TABLE {table} (id BIGSERIAL PRIMARY KEY, "
            + ", ".join(f"{c} TEXT" for c in shipped) + ")"
        )
    try:
        init_db()
        with get_pool().connection() as conn:
            got = {r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = %s AND column_name <> 'id'", (table,)
            ).fetchall()}
        assert got == set(shipped) | _CLIENT_COLUMNS
    finally:
        # Every other test needs the real column types back.
        with get_pool().connection() as conn:
            conn.execute(f"DROP TABLE {table}")
        init_db()
