"""feedback_report --changes and the capture lines in --turns (SP4): a document's
story read back from turn_log, document_version, document_snapshot and the
applied text on interaction_event (spec 2026-10-15)."""
from __future__ import annotations

import sys

from psycopg.types.json import Jsonb

import memory.document_store as ds
from memory.db import get_pool
from memory.fingerprint import fingerprint
from memory.turn_log import build_turn_row, write_turn
from scripts.feedback_report import format_capture, main


def _turn(turn_id, arrived_at, doc_text):
    return build_turn_row(
        kind="query", turn_id=turn_id, trace_id="", arrived_at=arrived_at, duration_ms=1000,
        attorney_id="atty-1", user_name="A. Tester", session_id="s-1", document_id="doc-1",
        task_type="research", request="who signs?", doc_text=doc_text, status="ok",
        outcome="ok", degradations=[], api_error="", awaiting_review=False, facts={},
        result=None, compaction=None, client={}, backend_build="dev", runtime={},
    )


def _capture(ts, moment, current_text, *, session_id="s-1", document_id="doc-1",
             tracked=(), total=None):
    """A capture at a chosen time — save_snapshot always stamps now()."""
    sha = ds.save_version(document_id, current_text, attorney_id="atty-1", source="pane")
    with get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO document_snapshot (timestamp, attorney_id, session_id, document_id, "
            "moment, current_sha256, original_sha256, tracked_changes, tracked_total) "
            "VALUES (%s, 'atty-1', %s, %s, %s, %s, '', %s, %s)",
            (ts, session_id, document_id, moment, sha, Jsonb(list(tracked)),
             len(tracked) if total is None else total),
        )


def _applied(ts, applied, *, document_id="doc-1"):
    """An Apply at a chosen time, with its text — record_events stamps now()."""
    with get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO interaction_event (timestamp, turn_id, session_id, document_id, "
            "attorney_id, surface, action, applied) "
            "VALUES (%s, 't-1', 's-1', %s, 'atty-1', 'chat', 'edit_applied', %s)",
            (ts, document_id, Jsonb(applied)),
        )


_FIRST = "MUTUAL NDA\rParties: [__]\rTerm: 1 year"
_LAST = "MUTUAL NDA\rParties: Acme Ltd\rTerm: 2 years"


def _story():
    write_turn(_turn("t-1", "2026-10-16T09:00:00+00:00", _FIRST))
    ds.save_version("doc-1", _FIRST, attorney_id="atty-1", source="turn")
    _applied("2026-10-16T09:01:00+00:00",
             {"action": "replace", "target_text": "[__]", "new_text": "Acme Ltd"})
    _capture("2026-10-16T09:05:00+00:00", "idle", _LAST, tracked=[
        {"type": "Added", "author": "Jane Smith", "date": "", "text": "Acme Ltd"},
        {"type": "Deleted", "author": "Jane Smith", "date": "", "text": "[__]"}])


def test_observations_keep_every_sighting_in_time_order():
    """A → B → A: two versions, three sightings, the last of them A.
    Mutation: order by document_version.first_seen, or read versions instead."""
    write_turn(_turn("t-1", "2026-10-16T09:00:00+00:00", "Fees: 10 days."))
    ds.save_version("doc-1", "Fees: 10 days.", attorney_id="atty-1", source="turn")
    _capture("2026-10-16T09:05:00+00:00", "idle", "Fees: 30 days.")
    _capture("2026-10-16T09:09:00+00:00", "idle", "Fees: 10 days.")
    seen = ds.observations("doc-1")
    assert [o["kind"] for o in seen] == ["turn", "idle", "idle"]
    assert seen[-1]["sha256"] == seen[0]["sha256"] == fingerprint("Fees: 10 days.")[1]
    assert len(ds.versions("doc-1")) == 2


def test_changes_tells_one_documents_story(monkeypatch, capsys):
    _story()
    monkeypatch.setattr(sys, "argv", ["feedback_report", "--changes", "--document", "doc-1"])
    main()
    out = capsys.readouterr().out
    assert "=== Document changes (1 document) ===" in out
    assert "document doc-1  A. Tester (atty-1) · 2026-10-16 09:00 → 2026-10-16 09:05" in out
    assert "applied edits 1: 1 kept · 0 altered · 0 reverted · 0 no later capture" in out
    assert "tracked changes at the last capture: 2 — Jane Smith 2 (1 ins, 1 del)" in out
    assert '+ "Term: 2 years"   not suggested' in out


def test_changes_lists_only_the_documents_in_scope(monkeypatch, capsys):
    _story()
    _capture("2026-10-16T10:00:00+00:00", "pane_open", "OTHER CONTRACT",
             document_id="doc-2", session_id="s-2")
    monkeypatch.setattr(sys, "argv", ["feedback_report", "--changes", "--document", "doc-2"])
    main()
    out = capsys.readouterr().out
    assert "document doc-2" in out and "document doc-1" not in out


def test_the_timeline_shows_each_capture_and_whether_the_text_changed(monkeypatch, capsys):
    """Mutation: compare each capture with the previous capture only, ignoring turns."""
    write_turn(_turn("t-1", "2026-10-16T09:00:00+00:00", _FIRST))
    _capture("2026-10-16T09:01:00+00:00", "idle", _FIRST)
    _capture("2026-10-16T09:02:00+00:00", "idle", _LAST, total=3)
    _capture("2026-10-16T09:03:00+00:00", "finalize", _LAST, session_id="s-other")
    monkeypatch.setattr(sys, "argv", ["feedback_report", "--turns"])
    main()
    out = capsys.readouterr().out
    assert out.index("turn: t-1") < out.index("· capture idle: text unchanged, 0 tracked changes")
    assert "· capture idle: text changed, 3 tracked changes" in out
    assert "capture finalize" not in out             # another session is not on this page
    assert "=== Turns (1) ===" in out                # captures are not counted as turns


def test_a_capture_line_says_what_it_can():
    c = {"timestamp": "2026-10-16T09:02:00+00:00", "moment": "pane_open",
         "current_sha256": "a", "tracked_total": -1}
    assert format_capture(c, None) == (
        "  2026-10-16T09:02:00  · capture pane_open: first text on this page, "
        "tracked changes unreadable")
    assert format_capture({**c, "tracked_total": 1}, "a").endswith(
        "text unchanged, 1 tracked change")
