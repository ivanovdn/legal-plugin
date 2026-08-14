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
from memory.feedback_store import event_counts, recent_feedback


def main() -> None:
    init_db()

    rows = recent_feedback(limit=50)
    print(f"=== Feedback ({len(rows)}) ===\n")
    if not rows:
        print("  (none yet)\n")
    for r in rows:
        who = r["user_name"] or r["attorney_id"]
        target = f" · {r['target_kind']}: {r['target_ref'][:60]}" if r["target_kind"] else ""
        print(f"  {r['timestamp'][:19]}  {who}  [{r['surface']}]{target}")
        print(f"    {r['comment']}")
        if r["trace_id"]:
            print(f"    trace: {r['trace_id']}  turn: {r['turn_id']}")
        print()

    counts = event_counts()
    print(f"=== Interaction events ({sum(c['count'] for c in counts)}) ===\n")
    if not counts:
        print("  (none yet)\n")
        return
    width = max((len(c["action"]) for c in counts), default=10)
    for c in counts:
        print(f"  {c['surface']:<10} {c['action']:<{width}}  {c['count']:>6}")
    print()


if __name__ == "__main__":
    main()
