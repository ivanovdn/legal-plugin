#!/usr/bin/env python3
"""Read back tester feedback and interaction telemetry.

    uv run python -m scripts.feedback_report
    uv run python -m scripts.feedback_report --days 7
    uv run python -m scripts.feedback_report --attorney <id> --document <id>

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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory.db import init_db
from memory.feedback_store import (
    COUNTER_ACTIONS,
    counter_totals,
    edit_proposal_turns,
    event_counts,
    recent_feedback,
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Read back tester feedback + telemetry.")
    p.add_argument("--days", type=int, default=0,
                   help="only the last N days (default: all time)")
    p.add_argument("--since", default="",
                   help="ISO timestamp lower bound; overrides --days")
    p.add_argument("--attorney", default="", help="filter to one attorney_id")
    p.add_argument("--document", default="", help="filter to one document_id")
    p.add_argument("--limit", type=int, default=50, help="max feedback rows (default 50)")
    return p.parse_args()


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
