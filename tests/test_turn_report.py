"""feedback_report --turns: a session read back from turn_log, turn by turn."""
from __future__ import annotations

import sys

from memory.feedback_store import record_event, save_feedback
from memory.turn_log import build_turn_row, recent_turns, write_turn
from scripts.feedback_report import format_turn, main, print_turns


def _turn(turn_id, **overrides):
    args = dict(
        kind="query", turn_id=turn_id, trace_id="", arrived_at="2026-10-09T09:46:56+00:00",
        duration_ms=13100, attorney_id="atty-1", user_name="A. Tester", session_id="s-1",
        document_id="doc-1", task_type="research", request="Is the cap market?",
        doc_text="STATEMENT OF WORK", status="ok", outcome="ok", degradations=[],
        api_error="", awaiting_review=False, facts={}, result=None, compaction=None,
        client={"office_platform": "Mac", "office_version": "16.89",
                "pane_build": "abc1234", "user_agent": "ua"},
        backend_build="abc1234",
    )
    args.update(overrides)
    return build_turn_row(**args)


def _text(row) -> str:
    return "\n".join(format_turn(row))


def test_recent_turns_counts_what_the_attorney_did_with_each_turn():
    write_turn(_turn("t-1"))
    write_turn(_turn("t-2", attorney_id="atty-2"))
    for action in ("edit_applied", "edit_applied", "edit_failed", "edit_discarded"):
        record_event(turn_id="t-1", session_id="s-1", document_id="doc-1",
                     attorney_id="atty-1", surface="chat", action=action)
    save_feedback(turn_id="t-1", trace_id="", session_id="s-1", document_id="doc-1",
                  attorney_id="atty-1", user_name="A. Tester", surface="chat",
                  target_kind="reply", target_ref="", comment="wrong cap", snapshot=None)

    (row,) = recent_turns(attorney_id="atty-1")
    assert row["turn_id"] == "t-1"
    assert (row["applied"], row["failed"], row["discarded"], row["flags"]) == (2, 1, 1, 1)


def test_recent_turns_reads_oldest_first_within_the_newest_limit():
    for i in range(3):
        write_turn(_turn(f"t-{i}", arrived_at=f"2026-10-09T10:0{i}:00+00:00"))
    assert [r["turn_id"] for r in recent_turns(limit=2)] == ["t-1", "t-2"]


def test_recent_turns_reads_in_the_order_the_requests_arrived():
    """The row is written after the response, so a slow turn is written AFTER a
    quick one that arrived later: id order is not the order the attorney lived it."""
    write_turn(_turn("t-quick", arrived_at="2026-10-09T10:00:10+00:00"))
    write_turn(_turn("t-slow", arrived_at="2026-10-09T10:00:00+00:00"))
    assert [r["turn_id"] for r in recent_turns()] == ["t-slow", "t-quick"]


def test_a_full_page_says_older_turns_are_cut_off(capsys):
    """--limit used to cut the timeline off with no sign of it: fifty rows read
    like the whole story."""
    for i in range(3):
        write_turn(_turn(f"t-{i}", arrived_at=f"2026-10-09T10:0{i}:00+00:00"))
    print_turns(recent_turns(limit=2), limit=2)
    out = capsys.readouterr().out
    assert "=== Turns (2) ===" in out
    assert "the newest 2 turns" in out and "raise --limit" in out


def test_a_page_with_room_to_spare_says_nothing_about_a_cut(capsys):
    write_turn(_turn("t-1"))
    print_turns(recent_turns(limit=5), limit=5)
    out = capsys.readouterr().out
    assert "=== Turns (1) ===" in out and "turn: t-1" in out
    assert "newest" not in out and "--limit" not in out


def test_the_cli_hands_its_limit_to_the_printer(monkeypatch, capsys):
    """The one place --limit meets print_turns: a printer that is right and a
    main() that forgets to pass the limit would still print an unmarked cut."""
    for i in range(3):
        write_turn(_turn(f"t-{i}", arrived_at=f"2026-10-09T10:0{i}:00+00:00"))
    monkeypatch.setattr(sys, "argv", ["feedback_report", "--turns", "--limit", "2"])
    main()
    out = capsys.readouterr().out
    assert "=== Turns (2) ===" in out and "the newest 2 turns" in out
    assert "turn: t-1" in out and "turn: t-2" in out and "turn: t-0" not in out


def test_since_filters_by_arrival_time():
    write_turn(_turn("old", arrived_at="2026-10-01T10:00:00+00:00"))
    write_turn(_turn("new", arrived_at="2026-10-09T10:00:00+00:00"))
    assert [r["turn_id"] for r in recent_turns(since="2026-10-05T00:00:00+00:00")] == ["new"]


def test_a_timeline_entry_says_what_happened_where_the_time_went_and_what_sent_it():
    write_turn(_turn(
        "t-1", outcome="degraded", degradations=["context_truncated"],
        facts={"contract_type": "sow", "msa_title": "Trinetix Model MSA",
               "llm_detail": [{"name": "doc_chat", "tokens_in": 9000, "tokens_out": 120,
                               "load_ms": 5900, "prompt_ms": 6100, "gen_ms": 1100}]},
        result={"context_truncated": {"kept_pct": 58}, "proposed_edits": [{}, {}]},
    ))
    text = _text(recent_turns()[0])
    assert "query:research  degraded [context_truncated]" in text
    assert "load 5.9s · prompt 6.1s · gen 1.1s · 9000→120 tokens" in text
    assert "sow · MSA: Trinetix Model MSA · document cut to 58%" in text
    assert "edits 2 proposed → 0 applied, 0 failed, 0 discarded · flags 0" in text
    assert "Mac 16.89 · pane abc1234 · backend abc1234" in text
    assert "stale pane" not in text
    assert "asked: Is the cap market?" in text and "turn: t-1" in text


def test_a_pane_older_than_the_backend_is_marked_stale():
    write_turn(_turn("t-1", client={"office_platform": "PC", "office_version": "16.0",
                                    "pane_build": "8536201", "user_agent": ""},
                     backend_build="474f1bd"))
    assert "⚠ stale pane" in _text(recent_turns()[0])


def test_a_pane_without_headers_is_named_but_not_called_stale():
    """Review Focus #1: every pane opened before the deploy sends no headers."""
    write_turn(_turn("t-1", client={"office_platform": "", "office_version": "",
                                    "pane_build": "", "user_agent": ""}))
    text = _text(recent_turns()[0])
    assert "no Office headers · pane — · backend abc1234" in text
    assert "stale pane" not in text


def test_a_failed_turn_shows_its_error():
    write_turn(_turn("t-1", status="error", outcome="failed",
                     degradations=["graph_invoke_failed"],
                     facts={"error": "graph_invoke_failed: ValueError: node exploded"}))
    assert "error: graph_invoke_failed: ValueError: node exploded" in _text(recent_turns()[0])


def test_recent_turns_counts_each_turns_own_events_by_kind():
    """Two turns with their own events and a distinct count in every column, so a
    missing turn_id correlation, a dropped redline_* action or a swapped
    failed/discarded label each shows up as a wrong number."""
    write_turn(_turn("t-1"))
    write_turn(_turn("t-2", arrived_at="2026-10-09T09:50:00+00:00"))
    for action in ("edit_applied", "edit_applied", "redline_applied",
                   "redline_failed", "redline_failed"):
        record_event(turn_id="t-1", session_id="s-1", document_id="doc-1",
                     attorney_id="atty-1", surface="findings", action=action)
    for action in ("edit_failed", "edit_discarded", "edit_discarded"):
        record_event(turn_id="t-2", session_id="s-1", document_id="doc-1",
                     attorney_id="atty-1", surface="chat", action=action)
    save_feedback(turn_id="t-2", trace_id="", session_id="s-1", document_id="doc-1",
                  attorney_id="atty-1", user_name="A. Tester", surface="chat",
                  target_kind="reply", target_ref="", comment="wrong cap", snapshot=None)

    t1, t2 = recent_turns()
    assert (t1["turn_id"], t1["applied"], t1["failed"], t1["discarded"], t1["flags"]) == ("t-1", 3, 2, 0, 0)
    assert (t2["turn_id"], t2["applied"], t2["failed"], t2["discarded"], t2["flags"]) == ("t-2", 0, 1, 2, 1)
