"""Persisted review store — append-per-session, latest lookup, loud writes."""
import pytest

import memory.review_store as rs
from memory.review_store import save_review, load_latest_review, load_history


def test_save_then_load_latest():
    save_review("doc-1", "sess-1", "# Review\nFinding A", "sow")
    latest = load_latest_review("doc-1")
    assert latest["markdown"] == "# Review\nFinding A"
    assert latest["session_id"] == "sess-1"
    assert latest["contract_type"] == "sow"


def test_append_keeps_history_latest_wins():
    save_review("doc-1", "sess-1", "first", "sow")
    save_review("doc-1", "sess-2", "second", "sow")
    assert load_latest_review("doc-1")["markdown"] == "second"
    history = load_history("doc-1")
    assert [h["markdown"] for h in history] == ["second", "first"]  # newest first


def test_load_latest_returns_none_when_absent():
    assert load_latest_review("no-such-doc") is None


def test_save_raises_loudly_on_store_failure(monkeypatch):
    # A store failure must propagate — never a silent no-op (the user believes
    # the review saved). We simulate the failure at the pool boundary.
    def _boom():
        raise RuntimeError("pool down")
    monkeypatch.setattr(rs, "get_pool", _boom)
    with pytest.raises(Exception):
        save_review("doc-1", "s", "md", "sow")
