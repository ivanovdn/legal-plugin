"""Per-attorney conversation store — append-per-turn, recent window, isolation."""
import pytest

import memory.conversation_store as cs
from memory.conversation_store import append_turn, load_recent


def test_append_turn_writes_two_rows_in_order():
    append_turn("doc-1", "atty-1", "hello?", "hi there")
    assert load_recent("doc-1", "atty-1", 20) == [
        {"role": "user", "content": "hello?"},
        {"role": "assistant", "content": "hi there"},
    ]


def test_load_recent_is_chronological_and_capped():
    append_turn("doc-1", "atty-1", "q1", "a1")
    append_turn("doc-1", "atty-1", "q2", "a2")
    append_turn("doc-1", "atty-1", "q3", "a3")
    assert load_recent("doc-1", "atty-1", 2) == [
        {"role": "user", "content": "q3"},
        {"role": "assistant", "content": "a3"},
    ]
    full = load_recent("doc-1", "atty-1", 20)
    assert [m["content"] for m in full] == ["q1", "a1", "q2", "a2", "q3", "a3"]


def test_per_attorney_isolation():
    append_turn("doc-1", "atty-1", "mine", "yours")
    append_turn("doc-1", "atty-2", "hers", "his")
    assert [m["content"] for m in load_recent("doc-1", "atty-1", 20)] == ["mine", "yours"]
    assert [m["content"] for m in load_recent("doc-1", "atty-2", 20)] == ["hers", "his"]


def test_per_document_isolation():
    append_turn("doc-1", "atty-1", "on one", "r1")
    append_turn("doc-2", "atty-1", "on two", "r2")
    assert [m["content"] for m in load_recent("doc-1", "atty-1", 20)] == ["on one", "r1"]
    assert [m["content"] for m in load_recent("doc-2", "atty-1", 20)] == ["on two", "r2"]


def test_load_recent_empty_ids_return_empty():
    append_turn("doc-1", "atty-1", "q", "a")
    assert load_recent("", "atty-1", 20) == []
    assert load_recent("doc-1", "", 20) == []


def test_load_recent_unknown_key_returns_empty():
    assert load_recent("no-doc", "no-atty", 20) == []


def test_load_recent_nonpositive_max_returns_empty():
    append_turn("doc-1", "atty-1", "q", "a")
    assert load_recent("doc-1", "atty-1", 0) == []
    assert load_recent("doc-1", "atty-1", -1) == []


def test_append_raises_loudly_on_store_failure(monkeypatch):
    def _boom():
        raise RuntimeError("pool down")
    monkeypatch.setattr(cs, "get_pool", _boom)
    with pytest.raises(Exception):
        append_turn("doc-1", "atty-1", "q", "a")
