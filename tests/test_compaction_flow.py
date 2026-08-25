"""Segment selection, generation, the retry, and loud failure.

The LLM call is replaced at the _generate_quote_lines seam. That name is
resolved in compaction.py's OWN globals by compact_conversation, so patching
skills.legal_research.compaction._generate_quote_lines is the target that
actually takes effect — the package's re-export hazard makes the wrong target
silently no-op (see CLAUDE.md).
"""
import pytest

import skills.legal_research.compaction as compaction
from config import get_settings
from memory.conversation_store import append_turn, load_rows_after
from memory.conversation_summary import latest_to_id, load_segments


def _seed(turns: int, document_id: str = "doc-1", attorney_id: str = "atty-1") -> list[dict]:
    for i in range(turns):
        append_turn(document_id, attorney_id, f"question {i}", f"answer {i}")
    return load_rows_after(document_id, attorney_id, 0, 500)


def _quotes_for(rows, ids):
    by_id = {r["id"]: r for r in rows}
    out = []
    for rid in ids:
        row = by_id[rid]
        speaker = "attorney" if row["role"] == "user" else "assistant, said earlier"
        out.append(f'[#{rid} {speaker}] "{row["content"]}"')
    return "\n".join(out)


def test_selection_keeps_the_recent_window_verbatim():
    rows = _seed(5)                      # 10 messages
    keep = get_settings().compaction_keep_recent_messages   # 6
    from_id, to_id, selected = compaction.select_compactable_rows("doc-1", "atty-1")
    assert len(selected) == len(rows) - keep
    assert from_id == rows[0]["id"]
    assert to_id == rows[len(rows) - keep - 1]["id"]


def test_selection_returns_nothing_when_only_the_recent_window_exists():
    _seed(3)                             # 6 messages == keep_recent
    assert compaction.select_compactable_rows("doc-1", "atty-1") == (0, 0, [])


def test_selection_starts_after_the_previous_segment(monkeypatch):
    rows = _seed(6)                      # 12 messages
    monkeypatch.setattr(
        compaction, "_generate_quote_lines",
        lambda r, n: _quotes_for(r, [r[0]["id"]]),
    )
    first = compaction.compact_conversation("doc-1", "atty-1")
    assert first["compacted"] is True
    _seed(3)                             # 6 more messages
    from_id, _to_id, selected = compaction.select_compactable_rows("doc-1", "atty-1")
    # Ranges never overlap and never gap.
    assert from_id == first["to_id"] + 1
    assert all(r["id"] > first["to_id"] for r in selected)


def test_happy_path_writes_one_validated_segment(monkeypatch):
    rows = _seed(5)
    calls = []

    def fake(r, n):
        calls.append((len(r), n))
        return _quotes_for(r, [r[0]["id"], r[1]["id"]])

    monkeypatch.setattr(compaction, "_generate_quote_lines", fake)
    result = compaction.compact_conversation("doc-1", "atty-1")
    assert result["compacted"] is True
    assert result["quotes"] == 2
    assert result["messages"] == len(rows) - get_settings().compaction_keep_recent_messages
    assert calls[0][1] == get_settings().compaction_max_quotes
    segs = load_segments("doc-1", "atty-1", 5)
    assert len(segs) == 1
    assert "EARLIER IN THIS CONVERSATION" in segs[0]["content"]
    assert latest_to_id("doc-1", "atty-1") == result["to_id"]


def test_a_fabricated_quote_is_retried_once_then_written(monkeypatch):
    rows = _seed(5)
    attempts = {"n": 0}

    def flaky(r, n):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return '[#999 attorney] "something nobody said"'
        return _quotes_for(r, [r[0]["id"]])

    monkeypatch.setattr(compaction, "_generate_quote_lines", flaky)
    result = compaction.compact_conversation("doc-1", "atty-1")
    assert attempts["n"] == 2
    assert result["compacted"] is True
    assert len(load_segments("doc-1", "atty-1", 5)) == 1


def test_two_fabricated_attempts_write_nothing_and_report_loudly(monkeypatch):
    _seed(5)
    monkeypatch.setattr(
        compaction, "_generate_quote_lines",
        lambda r, n: '[#999 attorney] "something nobody said"',
    )
    result = compaction.compact_conversation("doc-1", "atty-1")
    assert result["compacted"] is False
    assert result["error"]
    # LOUD, and nothing written: a failed compaction must change nothing.
    assert load_segments("doc-1", "atty-1", 5) == []
    assert latest_to_id("doc-1", "atty-1") == 0


def test_nothing_to_condense_is_not_an_error(monkeypatch):
    _seed(2)
    monkeypatch.setattr(
        compaction, "_generate_quote_lines",
        lambda r, n: pytest.fail("must not call the LLM with nothing to condense"),
    )
    result = compaction.compact_conversation("doc-1", "atty-1")
    assert result["compacted"] is False
    assert result["error"] == ""
    assert result["reason"]


def test_excess_quotes_are_capped_not_rejected(monkeypatch):
    rows = _seed(30)                     # 60 messages, 54 compactable
    _, _, selected = compaction.select_compactable_rows("doc-1", "atty-1")
    monkeypatch.setattr(
        compaction, "_generate_quote_lines",
        lambda r, n: _quotes_for(r, [row["id"] for row in r[:40]]),
    )
    result = compaction.compact_conversation("doc-1", "atty-1")
    cap = get_settings().compaction_max_quotes
    assert result["compacted"] is True
    # Over-production is not fabrication — every extra line still validated, so
    # trim rather than burn a retry.
    assert result["quotes"] == cap
    assert load_segments("doc-1", "atty-1", 5)[0]["content"].count("[#") == cap
    assert len(selected) > cap


def test_machinery_only_assistant_rows_are_never_quotable(monkeypatch):
    append_turn("doc-1", "atty-1", "fill the blanks", '```json\n{"action":"replace"}\n```')
    for i in range(4):
        append_turn("doc-1", "atty-1", f"q{i}", f"a{i}")
    _from, _to, selected = compaction.select_compactable_rows("doc-1", "atty-1")
    # _sanitize_history drops an assistant turn that was ONLY a fenced block, so
    # a ```json``` example can never be quoted into a summary and replayed as a
    # few-shot example (the 2ae99ecc failure).
    assert all("```" not in r["content"] for r in selected)
    assert all('"action"' not in r["content"] for r in selected)


def test_round_trip_shrinks_the_injected_history(monkeypatch):
    """Compaction's whole purpose: the next turn's assembled history is smaller.

    This is the test that pins WHY the feature exists. Everything else checks
    that compaction is safe; this checks that it works.
    """
    import skills.legal_research.context as ctx

    for i in range(12):
        append_turn("doc-rt", "atty-rt", f"question number {i} " * 20, f"answer number {i} " * 20)
    state = {"document_id": "doc-rt", "user_id": "atty-rt"}
    before = sum(len(m["content"]) for m in ctx._load_prior_conversation(state))

    monkeypatch.setattr(
        compaction, "_generate_quote_lines",
        lambda r, n: _quotes_for(r, [row["id"] for row in r[:3]]),
    )
    assert compaction.compact_conversation("doc-rt", "atty-rt")["compacted"] is True

    after = sum(len(m["content"]) for m in ctx._load_prior_conversation(state))
    assert after < before
