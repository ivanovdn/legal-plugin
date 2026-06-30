"""Persisted review store — append-per-session, latest lookup, loud writes."""
import pytest

from memory.review_store import (
    init_review_db, save_review, load_latest_review, load_history,
)


def _db(tmp_path):
    p = str(tmp_path / "reviews.db")
    init_review_db(p)
    return p


def test_save_then_load_latest(tmp_path):
    db = _db(tmp_path)
    save_review(db, "doc-1", "sess-1", "# Review\nFinding A", "sow")
    latest = load_latest_review(db, "doc-1")
    assert latest["markdown"] == "# Review\nFinding A"
    assert latest["session_id"] == "sess-1"
    assert latest["contract_type"] == "sow"


def test_append_keeps_history_latest_wins(tmp_path):
    db = _db(tmp_path)
    save_review(db, "doc-1", "sess-1", "first", "sow")
    save_review(db, "doc-1", "sess-2", "second", "sow")
    assert load_latest_review(db, "doc-1")["markdown"] == "second"
    history = load_history(db, "doc-1")
    assert [h["markdown"] for h in history] == ["second", "first"]  # newest first


def test_load_latest_returns_none_when_absent(tmp_path):
    db = _db(tmp_path)
    assert load_latest_review(db, "no-such-doc") is None


def test_save_raises_loudly_on_bad_path():
    # A path whose parent directory does not exist cannot be opened — must raise,
    # never silently no-op (the user would believe the review saved).
    with pytest.raises(Exception):
        save_review("/no/such/dir/reviews.db", "doc-1", "s", "md", "sow")
