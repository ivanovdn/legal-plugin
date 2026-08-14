#!/usr/bin/env python3
"""Read back tester feedback and interaction telemetry.

    uv run python -m scripts.feedback_report

Feedback nobody reads is worse than none — it costs the attorney something and
returns nothing. This is the whole reporting story; there is deliberately no UI.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory.db import init_db
from memory.feedback_store import COUNTER_ACTIONS, counter_totals, event_counts, recent_feedback


def main() -> None:
    init_db()

    rows = recent_feedback(limit=50)
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
    totals = counter_totals()
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

    item_counts = [c for c in event_counts() if c["action"] not in COUNTER_ACTIONS]
    print(f"=== Per-item actions ({sum(c['count'] for c in item_counts)}) ===")
    print("  One row per Apply/Discard/failure/jump-not-found — an item, not a")
    print("  turn. These are the numerators; the counters above are the")
    print("  denominators, and the two must not be divided across sections.\n")
    if not item_counts:
        print("  (none yet)\n")
        return
    width = max((len(c["action"]) for c in item_counts), default=10)
    for c in item_counts:
        print(f"  {c['surface']:<10} {c['action']:<{width}}  {c['count']:>6}")
    print()


if __name__ == "__main__":
    main()
