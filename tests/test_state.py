"""Unit tests for state reducer."""

from typing import get_type_hints

from config import get_settings
from graph.state import BOOK_REFS_CAP, LegalAgentState, _book_refs_reducer, _history_reducer


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


def test_state_has_review_iterations_field(monkeypatch):
    """LegalAgentState supports review_iterations and report_notes_unincorporated keys."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()
    s: LegalAgentState = {
        "request": "r",
        "user_id": "u",
        "uploaded_docs": [],
        "task_type": "",
        "skill_plan": [],
        "retrieval_query": "",
        "retrieved_chunks": [],
        "filters": {},
        "messages": [],
        "llm_response": "",
        "risk_level": "",
        "risk_flags": [],
        "awaiting_review": False,
        "attorney_notes": "",
        "report": {},
        "session_id": "",
        "checkpoint_ref": "",
        "trace_id": "",
        "chat_history": [],
        "review_iterations": 0,
        "report_notes_unincorporated": "",
    }
    assert s["review_iterations"] == 0
    assert s["report_notes_unincorporated"] == ""


# --- book_refs_retrieved (ruling 19) -------------------------------------------------------------------

def test_book_refs_reducer():
    a, b, c = "circ92-2025-12:usc17-s107", "circ92-2025-12:usc17-s108", "circ92-2025-12:ch1-n40"
    # A node forwarding the state unchanged: no change, and the same list back.
    old = [a, b]
    assert _book_refs_reducer(old, [a, b]) is old
    # New ids go to the end; one already there moves to the end (recency).
    assert _book_refs_reducer([a, b], [b, c]) == [a, b, c]
    assert _book_refs_reducer([a, b], [a]) == [b, a]
    # A turn that retrieved nothing keeps the conversation's ids (the seed in initial_state is []).
    assert _book_refs_reducer([a, b], []) == [a, b]
    assert _book_refs_reducer([a, b], None) == [a, b]
    assert _book_refs_reducer(None, [a]) == [a]
    assert _book_refs_reducer(None, None) == []
    # The cap keeps the most recent.
    many = [f"circ92-2025-12:usc17-s{i}" for i in range(600)]
    assert _book_refs_reducer([], many) == many[-BOOK_REFS_CAP:]
    assert BOOK_REFS_CAP == 500
    assert _book_refs_reducer(many[:500], [many[0]]) == [*many[1:500], many[0]]
    assert _book_refs_reducer(many[:500], [many[500]]) == [*many[1:501]]


def test_book_refs_fields_are_declared():
    hints = get_type_hints(LegalAgentState, include_extras=True)
    assert hints["book_refs_retrieved"].__metadata__ == (_book_refs_reducer,)
    assert hints["book_refs_turn"] == list[str]
