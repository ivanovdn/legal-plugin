#!/usr/bin/env python3
"""Delete the document copies SP4 keeps, for one document or one attorney.

    uv run python -m scripts.delete_document_data --document <id>          # counts only
    uv run python -m scripts.delete_document_data --document <id> --yes    # delete
    $DC run --rm --no-deps backend python -m scripts.delete_document_data --attorney <id> --yes

Removes the stored texts (document_version), the pane's captures
(document_snapshot) and the applied text on Apply events. It is NOT a full
erasure, and prints what it leaves: reviews, conversations, feedback, the
questions in turn_log, Phoenix traces and the nightly dumps.
Spec: docs/superpowers/specs/2026-10-15-document-changes-design.md, "Deleting it".
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory.db import init_db
from memory.document_store import delete_document_data, untouched_counts


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
        print(f"  {table:<22} {'n/a (keyed by document only)' if n is None else n}")
    print("  Phoenix traces         every turn's prompt, the document included")
    print("  nightly dumps          data/backups/app-db-*-nightly.sql.gz, kept up to 14 days")


if __name__ == "__main__":
    main()
