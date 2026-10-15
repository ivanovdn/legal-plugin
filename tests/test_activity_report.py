"""feedback_report --activity: one line per attorney per day — pane use beside the turns."""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from memory import feedback_store as fs
from memory.turn_log import build_turn_row, turn_summaries, write_turn
from scripts.feedback_report import build_activity, format_activity, main


def _turn(turn_id, **overrides):
    args = dict(
        kind="query", turn_id=turn_id, trace_id="", arrived_at="2026-10-15T09:46:56+00:00",
        duration_ms=7200, attorney_id="atty-1", user_name="A. Tester", session_id="s-1",
        document_id="doc-1", task_type="research", request="Who signs?",
        doc_text="NDA", status="ok", outcome="ok", degradations=[],
        api_error="", awaiting_review=False, facts={}, result=None, compaction=None,
        client={"office_platform": "Mac", "office_version": "16.89",
                "pane_build": "abc1234", "user_agent": "ua"},
        backend_build="abc1234", runtime={},
    )
    args.update(overrides)
    return build_turn_row(**args)


def _t(ts, task_type="research", duration_ms=7200, attorney_id="atty-1", user_name="A. Tester"):
    return {"timestamp": ts, "attorney_id": attorney_id, "user_name": user_name,
            "task_type": task_type, "duration_ms": duration_ms}


def _e(ts, action, attorney_id="atty-1", target_ref="", detail="", pane_build="p1", backend_build="p1"):
    return {"timestamp": ts, "attorney_id": attorney_id, "session_id": "s-1", "turn_id": "",
            "action": action, "target_ref": target_ref, "detail": detail,
            "pane_build": pane_build, "backend_build": backend_build}


def test_a_day_with_opens_and_no_questions_still_shows():
    """The blind spot this exists for: the pane opened, nothing asked.
    Mutation: count opens from the turns."""
    (day,) = build_activity([], [_e("2026-10-15T09:36:00+00:00", "pane_opened"),
                                 _e("2026-10-15T14:02:00+00:00", "pane_opened")])
    assert (day["day"], day["opens"], day["chat"], day["reviews"]) == ("2026-10-15", 2, 0, 0)
    assert "opens 2 · chat 0 · reviews 0" in format_activity(day)[0]


def test_turns_are_counted_by_kind():
    (day,) = build_activity(
        [_t("2026-10-15T10:00:00+00:00"), _t("2026-10-15T10:05:00+00:00"),
         _t("2026-10-15T10:09:00+00:00", task_type="contract_review")],
        [])
    assert (day["chat"], day["reviews"]) == (2, 1)


def test_medians_keep_chat_and_review_apart():
    """Mutation: pool chat and review samples into one list."""
    turns = [_t("2026-10-15T10:00:00+00:00", duration_ms=500),
             _t("2026-10-15T10:01:00+00:00", duration_ms=700),
             _t("2026-10-15T10:02:00+00:00", task_type="contract_review", duration_ms=88000)]
    events = [_e("2026-10-15T10:00:09+00:00", "turn_waited", target_ref="chat", detail="1000"),
              _e("2026-10-15T10:01:09+00:00", "turn_waited", target_ref="chat", detail="3000"),
              _e("2026-10-15T10:01:30+00:00", "turn_waited", target_ref="chat", detail="9000"),
              _e("2026-10-15T10:03:40+00:00", "turn_waited", target_ref="review", detail="96100"),
              _e("2026-10-15T10:04:00+00:00", "turn_waited", target_ref="chat", detail="oops")]
    (day,) = build_activity(turns, events)
    line = format_activity(day)[1]
    assert "waited in Word: chat 3.0s · review 96.1s" in line
    assert "server: chat 0.6s · review 88.0s" in line


def test_attorneys_and_days_are_separate_entries_in_order():
    rows = build_activity(
        [_t("2026-10-16T09:00:00+00:00", attorney_id="atty-2", user_name="B. Two")],
        [_e("2026-10-15T09:00:00+00:00", "pane_opened"),
         _e("2026-10-16T08:00:00+00:00", "pane_opened")])
    assert [(r["day"], r["attorney_id"]) for r in rows] == [
        ("2026-10-15", "atty-1"), ("2026-10-16", "atty-1"), ("2026-10-16", "atty-2")]


def test_errors_and_finalize_are_counted():
    (day,) = build_activity([], [_e("2026-10-15T09:00:00+00:00", "client_error", target_ref="chat"),
                                 _e("2026-10-15T09:10:00+00:00", "finalize_applied"),
                                 _e("2026-10-15T09:11:00+00:00", "finalize_failed")])
    assert (day["errors"], day["finalize"]) == (1, 1)


def test_the_build_line_marks_a_stale_pane_only_when_both_builds_differ():
    (stale,) = build_activity([], [_e("2026-10-15T09:00:00+00:00", "pane_opened",
                                      pane_build="old1", backend_build="new2")])
    assert format_activity(stale)[2].endswith("pane old1 · backend new2  ⚠ stale pane")
    (same,) = build_activity([], [_e("2026-10-15T09:00:00+00:00", "pane_opened")])
    assert "stale" not in format_activity(same)[2]


def test_a_day_from_a_pre_sp3_pane_shows_its_turns_and_no_opens():
    """Review Focus #3: turns but no pane events — opens 0, builds unknown, no crash."""
    (day,) = build_activity([_t("2026-10-15T10:00:00+00:00")], [])
    lines = format_activity(day)
    assert "opens 0 · chat 1" in lines[0]
    assert "waited in Word: chat — · review —" in lines[1]
    assert lines[2].strip() == "pane — · backend —"


def test_the_name_comes_from_the_turns_and_falls_back_to_the_id():
    named, unnamed = build_activity(
        [_t("2026-10-15T10:00:00+00:00")],
        [_e("2026-10-15T09:00:00+00:00", "pane_opened", attorney_id="atty-zzz")])
    assert "A. Tester (atty-1)" in format_activity(named)[0]
    assert "  atty-zzz   opens 1" in format_activity(unnamed)[0]


def test_turn_summaries_keeps_only_questions():
    """A resume continues a review already counted; a compaction is not a question."""
    write_turn(_turn("q-1"))
    write_turn(_turn("r-1", kind="resume", task_type="contract_review"))
    write_turn(_turn("c-1", kind="compact", task_type=""))
    assert [t["task_type"] for t in turn_summaries()] == ["research"]


def test_pane_events_returns_only_the_pane_surface_oldest_first():
    fs.record_events([{"surface": "chat", "action": "edit_applied"},
                      {"surface": "pane", "action": "pane_opened", "detail": "saved"},
                      {"surface": "pane", "action": "tab_viewed", "target_ref": "chat"}],
                     attorney_id="atty-1")
    assert [e["action"] for e in fs.pane_events()] == ["pane_opened", "tab_viewed"]


def test_the_cli_reads_both_tables(monkeypatch, capsys):
    now = datetime.now(timezone.utc).isoformat()
    write_turn(_turn("q-1", arrived_at=now, duration_ms=7200))
    fs.record_events([{"surface": "pane", "action": "pane_opened", "session_id": "s-1"},
                      {"surface": "pane", "action": "turn_waited", "turn_id": "q-1",
                       "target_ref": "chat", "detail": "9800"}],
                     attorney_id="atty-1",
                     client={"office_platform": "Mac", "pane_build": "abc1234"},
                     backend_build="abc1234")
    monkeypatch.setattr(sys, "argv", ["feedback_report", "--activity"])
    main()
    out = capsys.readouterr().out
    assert "=== Activity (1 attorney-days) ===" in out
    assert "A. Tester (atty-1)   opens 1 · chat 1 · reviews 0" in out
    assert "waited in Word: chat 9.8s" in out and "server: chat 7.2s" in out
    assert "pane abc1234 · backend abc1234" in out


def test_a_wait_that_is_not_a_plain_number_is_ignored_not_fatal():
    """detail is client-controlled; '²' passes str.isdigit() and int() rejects it."""
    (day,) = build_activity([], [
        _e("2026-10-15T10:00:00+00:00", "turn_waited", target_ref="chat", detail="²"),
        _e("2026-10-15T10:00:01+00:00", "turn_waited", target_ref="chat", detail="9" * 20),
        _e("2026-10-15T10:00:02+00:00", "turn_waited", target_ref="chat", detail="4200"),
    ])
    assert day["wait"]["chat"] == [4200]
