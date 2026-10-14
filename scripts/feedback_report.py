#!/usr/bin/env python3
"""Read back tester feedback and interaction telemetry.

    uv run python -m scripts.feedback_report
    uv run python -m scripts.feedback_report --days 7
    uv run python -m scripts.feedback_report --attorney <id> --document <id>
    uv run python -m scripts.feedback_report --turns --attorney <id> --days 2
    uv run python -m scripts.feedback_report --activity --days 7

Feedback nobody reads is worse than none — it costs the attorney something and
returns nothing. This is the whole reporting story; there is deliberately no UI.

Unfiltered, this is cumulative for all time — pilot week one and week ten look
identical. `--days` is what makes a trend visible; `--attorney` and `--document`
separate one tester or one contract from the pool.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory.db import init_db
from memory.feedback_store import (
    COUNTER_ACTIONS,
    counter_totals,
    edit_proposal_turns,
    event_counts,
    pane_events,
    recent_feedback,
)
from memory.turn_log import recent_turns, turn_summaries


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Read back tester feedback + telemetry.")
    p.add_argument("--days", type=int, default=0,
                   help="only the last N days (default: all time)")
    p.add_argument("--since", default="",
                   help="ISO timestamp lower bound; overrides --days")
    p.add_argument("--attorney", default="", help="filter to one attorney_id")
    p.add_argument("--document", default="", help="filter to one document_id")
    p.add_argument("--limit", type=int, default=50,
                   help="max rows per list: feedback rows, or the newest turns under --turns "
                        "(default 50)")
    p.add_argument("--turns", action="store_true",
                   help="print the turn timeline (turn_log) instead of the feedback summary")
    p.add_argument("--activity", action="store_true",
                   help="print one line per attorney per day: pane use beside the turns")
    return p.parse_args()


def _secs(ms: float) -> str:
    return f"{ms / 1000:.1f}s"


# turn_log task types as --activity names them; anything else (compaction) is not counted.
_TASK_KIND = {"research": "chat", "contract_review": "review"}


def _median_secs(values: list[int]) -> str:
    return _secs(median(values)) if values else "—"


def build_activity(turns: list[dict], events: list[dict]) -> list[dict]:
    """One entry per (UTC day, attorney), oldest day first: pane use from the
    pane's events beside the questions the backend served. A day with pane
    opens and no turns is exactly the one the turn tables cannot show.
    `events` must be oldest first, so the last build seen is the day's latest."""
    names = {t["attorney_id"]: t["user_name"] for t in turns if t["user_name"]}
    days: dict[tuple[str, str], dict] = {}

    def entry(timestamp: str, attorney_id: str) -> dict:
        key = (timestamp[:10], attorney_id)
        if key not in days:
            days[key] = {
                "day": key[0], "attorney_id": attorney_id, "name": names.get(attorney_id, ""),
                "opens": 0, "chat": 0, "reviews": 0, "finalize": 0, "errors": 0,
                "wait": {"chat": [], "review": []}, "server": {"chat": [], "review": []},
                "pane_build": "", "backend_build": "",
            }
        return days[key]

    for t in turns:
        kind = _TASK_KIND.get(t["task_type"])
        if kind is None:
            continue
        d = entry(t["timestamp"], t["attorney_id"])
        d["chat" if kind == "chat" else "reviews"] += 1
        d["server"][kind].append(t["duration_ms"])
    for e in events:
        d = entry(e["timestamp"], e["attorney_id"])
        if e["action"] == "pane_opened":
            d["opens"] += 1
        elif e["action"] == "finalize_applied":
            d["finalize"] += 1
        elif e["action"] == "client_error":
            d["errors"] += 1
        elif (e["action"] == "turn_waited" and e["target_ref"] in d["wait"]
              and e["detail"].isdigit()):
            d["wait"][e["target_ref"]].append(int(e["detail"]))
        if e["pane_build"] or e["backend_build"]:
            d["pane_build"], d["backend_build"] = e["pane_build"], e["backend_build"]
    return [days[k] for k in sorted(days)]


def format_activity(d: dict) -> list[str]:
    """One attorney-day: counts, medians (Word wait beside server time), builds."""
    who = f"{d['name']} ({d['attorney_id']})" if d["name"] else d["attorney_id"]
    stale = ("  ⚠ stale pane" if d["pane_build"] and d["backend_build"]
             and d["pane_build"] != d["backend_build"] else "")
    return [
        f"  {d['day']}  {who}   opens {d['opens']} · chat {d['chat']} · reviews {d['reviews']}"
        f" · finalize {d['finalize']} · errors {d['errors']}",
        f"    waited in Word: chat {_median_secs(d['wait']['chat'])} · review "
        f"{_median_secs(d['wait']['review'])}   server: chat {_median_secs(d['server']['chat'])}"
        f" · review {_median_secs(d['server']['review'])}   (medians)",
        f"    pane {d['pane_build'] or '—'} · backend {d['backend_build'] or '—'}{stale}",
    ]


def print_activity(rows: list[dict]) -> None:
    print(f"=== Activity ({len(rows)} attorney-days) ===")
    print("  One entry per attorney per UTC day: pane opens and what was done in the")
    print("  pane, beside the questions the backend served. How to read it: docs/feedback-loop.md.")
    print()
    if not rows:
        print("  (none yet)\n")
        return
    for d in rows:
        print("\n".join(format_activity(d)))
        print()


def format_turn(t: dict) -> list[str]:
    """One turn as the timeline prints it: what happened, where the time went,
    what grounded it, what the attorney did with it, what sent it, and the
    question with the ids to look it up by."""
    task = t["task_type"] or t["kind"]
    codes = f" [{', '.join(t['degradations'])}]" if t["degradations"] else ""
    who = t["user_name"] or t["attorney_id"]
    lines = [f"  {t['timestamp'][:19]}  {who}  {t['kind']}:{task}  {t['outcome']}{codes}"]
    if t["error"]:
        lines.append(f"    error: {t['error'][:160]}")
    lines.append(
        f"    {_secs(t['duration_ms'])} total · {t['llm_calls']} model call(s): "
        f"load {_secs(t['llm_load_ms'])} · prompt {_secs(t['llm_prompt_ms'])} · "
        f"gen {_secs(t['llm_gen_ms'])} · {t['tokens_in']}→{t['tokens_out']} tokens"
    )
    grounding = t["contract_type"] or "no playbook"
    if t["contract_type_defaulted"]:
        grounding += " (type defaulted)"
    grounding += f" · MSA: {t['msa_title'] or '—'}"
    if t["doc_kept_pct"] is not None:
        grounding += f" · document cut to {t['doc_kept_pct']}%"
    lines.append(f"    {grounding} · doc {t['doc_chars']} chars")
    lines.append(
        f"    edits {t['edits_proposed']} proposed → {t['applied']} applied, "
        f"{t['failed']} failed, {t['discarded']} discarded · flags {t['flags']}"
    )
    # An empty pane_build is a pane from before SP2 (or Chainlit) — unknown,
    # not stale. Only two different builds are a stale pane.
    stale = ("  ⚠ stale pane"
             if t["pane_build"] and t["pane_build"] != t["backend_build"] else "")
    sender = t["office_platform"] or "no Office headers"
    if t["office_version"]:
        sender += f" {t['office_version']}"
    lines.append(
        f"    {sender} · pane {t['pane_build'] or '—'} · backend {t['backend_build']}{stale}"
    )
    if t["request"]:
        lines.append(f"    asked: {t['request'][:100]}")
    trace = f"  trace: {t['trace_id']}" if t["trace_id"] else ""
    lines.append(f"    turn: {t['turn_id']}{trace}")
    return lines


def print_turns(rows: list[dict], limit: int) -> None:
    """`rows` is recent_turns(limit=limit + 1): the one extra row, when it exists,
    is the proof that older turns were cut off. It is the oldest written (lowest
    id), not printed — a page of exactly `limit` turns cut nothing and says so."""
    cut = len(rows) > limit
    if cut:
        oldest = min(row["id"] for row in rows)
        rows = [row for row in rows if row["id"] != oldest]
    print(f"=== Turns ({len(rows)}) ===")
    print("  One entry per request to /api/query, its resume or /api/compact —")
    print("  failures included — oldest first. How to read it: docs/feedback-loop.md.")
    # A cut page has lost the START of the story, and fifty rows read like the whole of it.
    if cut:
        print(f"  Only the newest {limit} turns are shown — raise --limit for more.")
    print()
    if not rows:
        print("  (none yet)\n")
        return
    for row in rows:
        print("\n".join(format_turn(row)))
        print()


def main() -> None:
    args = _parse_args()
    since = args.since
    if not since and args.days > 0:
        since = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat()
    f = {"since": since, "document_id": args.document, "attorney_id": args.attorney}

    init_db()

    scope = []
    if since:
        scope.append(f"since {since[:19]}")
    if args.attorney:
        scope.append(f"attorney {args.attorney}")
    if args.document:
        scope.append(f"document {args.document}")
    print(f"Scope: {' · '.join(scope) if scope else 'all time, all attorneys, all documents'}\n")

    if args.activity:
        print_activity(build_activity(
            turn_summaries(since=since, attorney_id=args.attorney, document_id=args.document),
            pane_events(**f),
        ))
        return
    if args.turns:
        print_turns(recent_turns(since=since, attorney_id=args.attorney,
                                 document_id=args.document, limit=args.limit + 1), args.limit)
        return

    rows = recent_feedback(args.limit, **f)
    print(f"=== Feedback ({len(rows)}) ===\n")
    if not rows:
        print("  (none yet)\n")
    for r in rows:
        who = r["user_name"] or r["attorney_id"]
        target = f" · {r['target_kind']}: {r['target_ref'][:60]}" if r["target_kind"] else ""
        print(f"  #{r['id']}  {r['timestamp'][:19]}  {who}  [{r['surface']}]{target}")
        print(f"    doc: {r['document_id'] or '(none)'}")
        print(f"    {r['comment']}")
        # turn_id is minted independently of tracing and is always present;
        # trace_id is only an accelerator and is empty whenever tracing is
        # off. Printing turn_id only when trace_id happens to be set would
        # make every card unjoinable in that (supported) configuration.
        trace = f"  trace: {r['trace_id']}" if r["trace_id"] else ""
        print(f"    turn: {r['turn_id']}{trace}")
        print()

    # interaction_event mixes two different kinds of row, and they must never
    # share a column: a COUNTER fires once per turn/review with its magnitude
    # in `detail` (edits_proposed, findings_rendered, preferences_suggested);
    # a PER-ITEM action fires once per Apply/Discard/failure. Printed in one
    # column, "12 edits_proposed, 19 edit_applied" reads as a 158% apply rate
    # instead of 19-of-37. The two tables below are kept visually separate on
    # purpose — never divide a per-item count by a counter's `turns`.
    totals = counter_totals(**f)
    print("=== Per-turn counters ===")
    print("  One row per turn/review that fired this counter — NOT one row per")
    print("  edit/finding/preference. `total` sums the magnitude each turn")
    print("  carried in `detail` (e.g. 12 turns proposing edits might total 37).\n")
    if not totals:
        print("  (none yet)\n")
    else:
        width = max((len(t["action"]) for t in totals), default=10)
        for t in totals:
            print(f"  {t['surface']:<10} {t['action']:<{width}}  "
                  f"turns={t['turns']:<6} total={t['total']:>6}")
        print()

    item_counts = [c for c in event_counts(**f) if c["action"] not in COUNTER_ACTIONS]
    print(f"=== Per-item actions ({sum(c['count'] for c in item_counts)}) ===")
    print("  One row per Apply/Discard/failure/jump-not-found — an item, not a")
    print("  turn. These are the numerators; the counters above are the")
    print("  denominators, and the two must not be divided across sections.\n")
    if not item_counts:
        print("  (none yet)\n")
    else:
        width = max((len(c["action"]) for c in item_counts), default=10)
        for c in item_counts:
            print(f"  {c['surface']:<10} {c['action']:<{width}}  {c['count']:>6}")
        print()

    # The spurious-edit surface: a turn whose question is purely factual but
    # whose `proposed` is non-zero is that bug reproducing, and applied/discarded
    # is the attorney's verdict on each card. Scanning this list is the closest
    # thing to a rate we have until the taxonomy exists.
    turns = edit_proposal_turns(args.limit, **f)
    print(f"=== Chat turns that proposed edits ({len(turns)}) ===")
    print("  What was asked, how many edit cards came back, and what the")
    print("  attorney did with them. A question that reads as purely factual")
    print("  with proposed > 0 is a spurious proposal — look at those first.\n")
    if not turns:
        print("  (none yet)\n")
        return
    for t in turns:
        q = t["request"] or "(question not recorded)"
        print(f"  {t['timestamp'][:19]}  proposed={t['proposed']} "
              f"applied={t['applied']} discarded={t['discarded']} failed={t['failed']}")
        print(f"    asked: {q[:100]}")
        print(f"    turn: {t['turn_id']}")
        print()


if __name__ == "__main__":
    main()
