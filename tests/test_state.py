"""Unit tests for state reducer."""

from config import get_settings
from graph.state import _history_reducer


def test_history_reducer_concatenates_empty_and_new(monkeypatch):
    """Empty old + non-empty new → just new."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()

    a = {"role": "user", "content": "hello"}
    b = {"role": "assistant", "content": "world"}
    assert _history_reducer([], [a, b]) == [a, b]


def test_history_reducer_caps_at_2n(monkeypatch):
    """With N=5, old of 10 + new of 4 → last 10 entries kept."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("CHAT_HISTORY_N_TURNS", "5")
    get_settings.cache_clear()

    old = [{"role": "user", "content": f"u{i}"} for i in range(10)]
    new = [{"role": "assistant", "content": f"a{i}"} for i in range(4)]
    result = _history_reducer(old, new)
    assert len(result) == 10
    # Oldest 4 from `old` are dropped; remaining 6 from `old` + 4 new
    assert result[0]["content"] == "u4"
    assert result[-1]["content"] == "a3"


def test_history_reducer_preserves_order(monkeypatch):
    """Reducer preserves FIFO order after cap."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("CHAT_HISTORY_N_TURNS", "2")
    get_settings.cache_clear()

    old = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]
    new = [
        {"role": "user", "content": "u3"},
        {"role": "assistant", "content": "a3"},
    ]
    result = _history_reducer(old, new)
    # 2*N = 4, so keep last 4: u2, a2, u3, a3
    assert [m["content"] for m in result] == ["u2", "a2", "u3", "a3"]


def test_history_reducer_handles_none_old(monkeypatch):
    """If old is None (first turn before reducer ever ran), reducer still works."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()

    new = [{"role": "user", "content": "hello"}]
    # The reducer should be defensive
    assert _history_reducer(None, new) == new
