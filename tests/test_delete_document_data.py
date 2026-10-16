"""scripts/delete_document_data.py (SP4, spec D9): one document's or one
attorney's copies go, everything else stays, and it says what it does not touch."""
from __future__ import annotations

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


def test_a_dry_run_counts_and_deletes_nothing():
    """Mutation: delete when dry_run is True."""
    _seed()
    assert ds.delete_document_data(document_id="doc-1") == {
        "document_version": 2, "document_snapshot": 1, "interaction_event.applied": 1}
    assert _left("doc-1") == (2, 1, 1, 1)


def test_deleting_one_document_keeps_its_events_and_every_other_document():
    """The Apply still happened — only its text goes.
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
    _seed()
    main(["--document", "doc-1"])
    out = capsys.readouterr().out
    assert "Would delete (dry run — add --yes to delete) for document doc-1:" in out
    assert "Not touched" in out and "Phoenix" in out and "nightly" in out
    assert _left("doc-1") == (2, 1, 1, 1)


def test_the_command_deletes_with_yes(capsys):
    _seed()
    main(["--attorney", "atty-1", "--yes"])
    out = capsys.readouterr().out
    assert "Deleted for attorney atty-1:" in out
    assert "review_store" in out and "n/a (keyed by document only)" in out
    assert _left("doc-1") == (0, 0, 0, 1)
