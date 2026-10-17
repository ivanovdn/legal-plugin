"""scripts/delete_document_data.py (SP4, spec D9): one document's or one
attorney's copies go, everything else stays, and it says what it does not touch."""
from __future__ import annotations

import re

import pytest

import memory.document_store as ds
from memory.db import get_pool
from memory.feedback_store import record_events
from scripts.delete_document_data import main


def _seed():
    for doc, atty in (("doc-1", "atty-1"), ("doc-2", "atty-2")):
        ds.save_snapshot(attorney_id=atty, session_id="s", document_id=doc, moment="idle",
                         current_text=f"{doc} now", original_text=f"{doc} before",
                         tracked_changes=[], tracked_total=0, client={}, backend_build="dev")
        record_events([{"document_id": doc, "action": "edit_applied",
                        "applied": {"action": "replace", "target_text": "a", "new_text": "b"}}],
                      attorney_id=atty)


def _count(sql: str, *params) -> int:
    with get_pool().connection() as conn:
        return conn.execute(sql, params).fetchone()[0]


def _left(doc: str) -> tuple[int, int, int, int]:
    """(versions, snapshots, events with applied text, events) of one document."""
    return (
        _count("SELECT count(*) FROM document_version WHERE document_id = %s", doc),
        _count("SELECT count(*) FROM document_snapshot WHERE document_id = %s", doc),
        _count("SELECT count(*) FROM interaction_event WHERE document_id = %s "
               "AND applied IS NOT NULL", doc),
        _count("SELECT count(*) FROM interaction_event WHERE document_id = %s", doc),
    )


def _shown(out: str, label: str) -> str:
    """What the 'Not touched' line for one label shows, from its first value on."""
    match = re.search(rf"^  {re.escape(label)}\s+(.*)$", out, flags=re.MULTILINE)
    assert match, f"no 'Not touched' line for {label!r} in:\n{out}"
    return match.group(1)


def _audit_row(user: str) -> None:
    """One audit_log row with the NOT NULL columns that have no default."""
    with get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO audit_log (timestamp, session_id, user_id, skill_name, task_type, "
            "request_summary) VALUES (%s, %s, %s, %s, %s, %s)",
            ("2026-10-16T09:00:00+00:00", "s", user, "legal_research", "research", "who signs?"))


def test_a_dry_run_counts_and_deletes_nothing():
    """Mutation: delete when dry_run is True."""
    _seed()
    assert ds.delete_document_data(document_id="doc-1") == {
        "document_version": 2, "document_snapshot": 1, "interaction_event.applied": 1}
    assert _left("doc-1") == (2, 1, 1, 1)


def test_deleting_one_document_keeps_its_events_and_every_other_document():
    """The Apply still happened — its applied text goes; the event row and its short excerpts stay.
    Mutations: ignore the filter; delete the event instead of clearing `applied`."""
    _seed()
    ds.delete_document_data(document_id="doc-1", dry_run=False)
    assert _left("doc-1") == (0, 0, 0, 1)
    assert _left("doc-2") == (2, 1, 1, 1)


def test_deleting_by_attorney():
    _seed()
    ds.delete_document_data(attorney_id="atty-2", dry_run=False)
    assert _left("doc-2") == (0, 0, 0, 1) and _left("doc-1") == (2, 1, 1, 1)


@pytest.mark.parametrize("scope", [{}, {"document_id": "doc-1", "attorney_id": "atty-1"}])
def test_exactly_one_scope(scope):
    with pytest.raises(ValueError):
        ds.delete_document_data(**scope)


def test_the_command_counts_by_default_and_names_what_it_leaves(capsys):
    """The manual dumps taken before a risky deploy never expire, so they are named
    beside the nightly ones. Mutation: drop the manual dumps line."""
    _seed()
    main(["--document", "doc-1"])
    out = capsys.readouterr().out
    assert "Would delete (dry run — add --yes to delete) for document doc-1:" in out
    assert "Not touched" in out and "Phoenix" in out
    assert "kept up to 14 days" in _shown(out, "nightly dumps")
    manual = _shown(out, "manual dumps")
    assert "data/backups/app-db-*.sql.gz other than -nightly" in manual and "never expire" in manual
    assert _left("doc-1") == (2, 1, 1, 1)


def test_the_command_deletes_with_yes(capsys):
    _seed()
    main(["--attorney", "atty-1", "--yes"])
    out = capsys.readouterr().out
    assert "Deleted for attorney atty-1:" in out
    assert "review_store" in out and "n/a (keyed by document only)" in out
    assert _left("doc-1") == (0, 0, 0, 1)


def test_the_command_counts_the_event_text_it_leaves(capsys):
    """interaction_event keeps the short excerpts an Apply, a failure and a
    question carry. One row per field here, so each branch of the rule is
    pinned: dropping any one of target_ref, detail or request makes this 2, not
    3. The other document's excerpt is not counted."""
    _seed()
    record_events([
        {"document_id": "doc-1", "action": "edit_applied", "target_ref": "old clause"},
        {"document_id": "doc-1", "action": "edit_failed", "detail": "Couldn't find \"old\""},
        {"document_id": "doc-1", "action": "edits_proposed", "request": "who signs?"},
    ], attorney_id="atty-1")
    record_events([{"document_id": "doc-2", "action": "edit_applied", "target_ref": "other"}],
                  attorney_id="atty-2")
    main(["--document", "doc-1"])
    assert _shown(capsys.readouterr().out, "interaction_event (text excerpts)").split()[0] == "3"


def test_the_command_says_the_audit_log_is_not_keyed_by_document(capsys):
    _seed()
    main(["--document", "doc-1"])
    assert _shown(capsys.readouterr().out, "audit_log").startswith("n/a (not keyed by document)")


def test_the_attorney_scope_counts_its_audit_log_rows(capsys):
    _seed()
    _audit_row("atty-1")
    _audit_row("atty-2")
    main(["--attorney", "atty-1"])
    assert _shown(capsys.readouterr().out, "audit_log").split()[0] == "1"
