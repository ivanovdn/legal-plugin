#!/usr/bin/env python3
"""Delete the document copies SP4 keeps, for one document or one attorney.

    uv run python -m scripts.delete_document_data --document <id>          # counts only
    uv run python -m scripts.delete_document_data --document <id> --yes    # delete
    $DC run --rm --no-deps backend python -m scripts.delete_document_data --attorney <id> --yes

Removes the stored texts (document_version), the pane's captures
(document_snapshot) and the applied text on Apply events (the events stay, with
their short excerpts). It is NOT a full erasure, and prints what it leaves:
reviews, conversations, feedback, the questions in turn_log, the excerpts on
interaction_event rows, the request summaries in audit_log, Phoenix traces, the
nightly dumps and the manual dumps (which never expire).
Spec: docs/superpowers/specs/2026-10-15-document-changes-design.md, "Deleting it".
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory.db import init_db
from memory.document_store import delete_document_data, untouched_counts

# Width of the label column in the "Not touched" block: its longest label.
_LABEL_WIDTH = 34

# Why a table cannot be counted for a scope (untouched_counts gives None).
_NOT_SCOPED = {
    "review_store": "keyed by document only",
    "audit_log": "not keyed by document",
}

# What the rows of a table in the "Not touched" block hold, beside its count.
_HOLDS = {
    "interaction_event (text excerpts)":
        "target excerpts, error messages and questions (up to 200-500 chars each)",
    "audit_log": "the first 200 characters of each request",
}


def _untouched_line(label: str, value: str = "", note: str = "") -> str:
    """One line of the 'Not touched' block: the label, its value, what it holds."""
    return f"  {label:<{_LABEL_WIDTH}} {value:<28}{note}".rstrip()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Delete SP4's document copies for one document or attorney.")
    scope = p.add_mutually_exclusive_group(required=True)
    scope.add_argument("--document", default="", help="one document_id")
    scope.add_argument("--attorney", default="", help="one attorney_id")
    p.add_argument("--yes", action="store_true", help="delete; without it, only count")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    init_db()
    scope = {"document_id": args.document, "attorney_id": args.attorney}
    counts = delete_document_data(**scope, dry_run=not args.yes)
    verb = "Deleted" if args.yes else "Would delete (dry run — add --yes to delete)"
    target = f"document {args.document}" if args.document else f"attorney {args.attorney}"
    print(f"{verb} for {target}:")
    print(f"  stored texts (document_version)    {counts['document_version']}")
    print(f"  pane captures (document_snapshot)  {counts['document_snapshot']}")
    print(f"  applied text on Apply events       {counts['interaction_event.applied']}")
    print()
    print("Not touched — these also hold text from this scope:")
    for table, n in untouched_counts(**scope).items():
        if n is None:
            value = f"n/a ({_NOT_SCOPED.get(table, 'cannot be scoped this way')})"
        else:
            value = str(n)
        print(_untouched_line(table, value, _HOLDS.get(table, "")))
    print(_untouched_line("Phoenix traces", note="every turn's prompt, the document included"))
    print(_untouched_line("nightly dumps",
                          note="data/backups/app-db-*-nightly.sql.gz, kept up to 14 days"))
    print(_untouched_line("manual dumps",
                          note="data/backups/app-db-*.sql.gz other than -nightly — never expire"))


if __name__ == "__main__":
    main()
