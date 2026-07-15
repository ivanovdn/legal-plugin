"""Per-attorney conversation store — append-per-turn, recent window, isolation."""
import pytest

from memory.conversation_store import (
    init_conversation_db, append_turn, load_recent,
)


def _db(tmp_path):
    p = str(tmp_path / "conv.db")
    init_conversation_db(p)
    return p


def test_append_turn_writes_two_rows_in_order(tmp_path):
    db = _db(tmp_path)
    append_turn(db, "doc-1", "atty-1", "hello?", "hi there")
    msgs = load_recent(db, "doc-1", "atty-1", 20)
    assert msgs == [
        {"role": "user", "content": "hello?"},
        {"role": "assistant", "content": "hi there"},
    ]


def test_load_recent_is_chronological_and_capped(tmp_path):
    db = _db(tmp_path)
    append_turn(db, "doc-1", "atty-1", "q1", "a1")
    append_turn(db, "doc-1", "atty-1", "q2", "a2")
    append_turn(db, "doc-1", "atty-1", "q3", "a3")
    # cap to the last 2 messages -> only the newest turn survives
    msgs = load_recent(db, "doc-1", "atty-1", 2)
    assert msgs == [
        {"role": "user", "content": "q3"},
        {"role": "assistant", "content": "a3"},
    ]
    # full window stays in chronological order
    full = load_recent(db, "doc-1", "atty-1", 20)
    assert [m["content"] for m in full] == ["q1", "a1", "q2", "a2", "q3", "a3"]


def test_per_attorney_isolation(tmp_path):
    db = _db(tmp_path)
    append_turn(db, "doc-1", "atty-1", "mine", "yours")
    append_turn(db, "doc-1", "atty-2", "hers", "his")
    assert [m["content"] for m in load_recent(db, "doc-1", "atty-1", 20)] == ["mine", "yours"]
    assert [m["content"] for m in load_recent(db, "doc-1", "atty-2", 20)] == ["hers", "his"]


def test_per_document_isolation(tmp_path):
    db = _db(tmp_path)
    append_turn(db, "doc-1", "atty-1", "on one", "r1")
    append_turn(db, "doc-2", "atty-1", "on two", "r2")
    assert [m["content"] for m in load_recent(db, "doc-1", "atty-1", 20)] == ["on one", "r1"]
    assert [m["content"] for m in load_recent(db, "doc-2", "atty-1", 20)] == ["on two", "r2"]


def test_load_recent_empty_ids_return_empty(tmp_path):
    db = _db(tmp_path)
    append_turn(db, "doc-1", "atty-1", "q", "a")
    assert load_recent(db, "", "atty-1", 20) == []
    assert load_recent(db, "doc-1", "", 20) == []


def test_load_recent_unknown_key_returns_empty(tmp_path):
    db = _db(tmp_path)
    assert load_recent(db, "no-doc", "no-atty", 20) == []


def test_append_raises_loudly_on_bad_path():
    # Parent dir does not exist -> cannot open -> must raise, never silent.
    with pytest.raises(Exception):
        append_turn("/no/such/dir/conv.db", "doc-1", "atty-1", "q", "a")
