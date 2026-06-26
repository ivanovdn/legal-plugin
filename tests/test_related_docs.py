# tests/test_related_docs.py
"""Unit tests for parent-document retrieval (the governing MSA for a SOW)."""
from __future__ import annotations

import rag.related_docs as mod
from rag.related_docs import get_parent_msa


def test_get_parent_msa_concatenates_chunks_in_index_order(monkeypatch):
    # Returned out of order; must be sorted by chunk_index before joining.
    monkeypatch.setattr(mod, "scroll_by_filter", lambda **kw: [
        {"doc_id": "msa-1", "doc_title": "Model MSA", "chunk_index": 2, "text": "third"},
        {"doc_id": "msa-1", "doc_title": "Model MSA", "chunk_index": 0, "text": "first"},
        {"doc_id": "msa-1", "doc_title": "Model MSA", "chunk_index": 1, "text": "second"},
    ])
    result = get_parent_msa("internal")
    assert result == ("Model MSA", "first\n\nsecond\n\nthird")


def test_get_parent_msa_returns_none_when_no_chunks(monkeypatch):
    monkeypatch.setattr(mod, "scroll_by_filter", lambda **kw: [])
    assert get_parent_msa("internal") is None


def test_get_parent_msa_returns_none_without_client_id(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(mod, "scroll_by_filter",
                        lambda **kw: called.__setitem__("n", called["n"] + 1) or [])
    assert get_parent_msa("") is None
    assert called["n"] == 0  # never queries Qdrant without a client_id


def test_get_parent_msa_picks_one_deterministically_when_multiple(monkeypatch):
    # Two MSAs on file → pick the first doc_id alphabetically, only its chunks.
    monkeypatch.setattr(mod, "scroll_by_filter", lambda **kw: [
        {"doc_id": "b-msa", "doc_title": "B", "chunk_index": 0, "text": "from B"},
        {"doc_id": "a-msa", "doc_title": "A", "chunk_index": 0, "text": "from A"},
    ])
    title, text = get_parent_msa("internal")
    assert title == "A"
    assert text == "from A"
