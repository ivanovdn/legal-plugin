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


def test_selection_returns_nothing_when_only_the_floor_exists():
    _seed(1)                             # 2 messages == the floor, nothing older
    assert compaction.select_compactable_rows("doc-1", "atty-1") == (0, 0, [])


def test_selection_starts_after_the_previous_segment(monkeypatch):
    rows = _seed(6)                      # 12 messages
    monkeypatch.setattr(
        compaction, "_generate_quote_lines",
        lambda r, n, correction="": _quotes_for(r, [r[0]["id"]]),
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

    def fake(r, n, correction=""):
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

    def flaky(r, n, correction=""):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return '[#999 attorney] "something nobody said"'
        return _quotes_for(r, [r[0]["id"]])

    monkeypatch.setattr(compaction, "_generate_quote_lines", flaky)
    result = compaction.compact_conversation("doc-1", "atty-1")
    assert attempts["n"] == 2
    assert result["compacted"] is True
    assert len(load_segments("doc-1", "atty-1", 5)) == 1


def test_the_retry_tells_the_model_what_was_wrong(monkeypatch):
    """A retry that repeats the request cannot help: the model runs at temperature 0, so
    identical input yields the identical rejection. Only a corrective retry earns its
    ~10-30s."""
    rows = _seed(5)
    seen = []

    def flaky(r, n, correction=""):
        seen.append(correction)
        if len(seen) == 1:
            return '[#999 attorney] "something nobody said"'
        return _quotes_for(r, [r[0]["id"]])

    monkeypatch.setattr(compaction, "_generate_quote_lines", flaky)
    assert compaction.compact_conversation("doc-1", "atty-1")["compacted"] is True
    assert seen[0] == ""
    assert "rejected" in seen[1].lower() or "#999" in seen[1]


def test_two_fabricated_attempts_write_nothing_and_report_loudly(monkeypatch):
    _seed(5)
    monkeypatch.setattr(
        compaction, "_generate_quote_lines",
        lambda r, n, correction="": '[#999 attorney] "something nobody said"',
    )
    result = compaction.compact_conversation("doc-1", "atty-1")
    assert result["compacted"] is False
    assert result["error"]
    # LOUD, and nothing written: a failed compaction must change nothing.
    assert load_segments("doc-1", "atty-1", 5) == []
    assert latest_to_id("doc-1", "atty-1") == 0


def test_nothing_to_condense_is_not_an_error(monkeypatch):
    _seed(1)                             # only the floor exists
    monkeypatch.setattr(
        compaction, "_generate_quote_lines",
        lambda r, n, correction="": pytest.fail("must not call the LLM with nothing to condense"),
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
        lambda r, n, correction="": _quotes_for(r, [row["id"] for row in r[:40]]),
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
        lambda r, n, correction="": _quotes_for(r, [row["id"] for row in r[:3]]),
    )
    assert compaction.compact_conversation("doc-rt", "atty-rt")["compacted"] is True

    after = sum(len(m["content"]) for m in ctx._load_prior_conversation(state))
    assert after < before


def test_the_quote_cap_shrinks_to_free_the_chars_asked_for(monkeypatch):
    """compaction_max_quotes is a CEILING, not a fixed size.

    A thorough 20-quote summary of 8k chars frees almost nothing, which is how
    compaction came to run and leave the document truncated anyway. When the caller
    names a target, the cap is derived from it.
    """
    rows = _seed(20)                                   # 40 messages
    monkeypatch.setattr(
        compaction, "_generate_quote_lines",
        lambda r, n, correction="": _quotes_for(r, [row["id"] for row in r[:n]]),
    )
    loose = compaction.compact_conversation("doc-1", "atty-1")
    assert loose["quotes"] == get_settings().compaction_max_quotes
    assert loose["requested"] == 0


def test_a_large_target_trims_the_segment_until_it_actually_fits(monkeypatch):
    """The derived cap is an estimate; the trim loop is what makes it exact.

    Seeds REALISTIC message sizes. The default fixture's 10-char messages are smaller
    than the segment header (261 chars), so no target above about a third of the raw is
    reachable there and the test would be asserting something arithmetically impossible
    rather than anything about the trim loop.
    """
    for i in range(20):
        append_turn("doc-1", "atty-1",
                    f"Question {i} about the indemnity position in this agreement. " * 6,
                    f"Answer {i} setting out the firm position on that clause. " * 6)
    _from, _to, rows = compaction.select_compactable_rows("doc-1", "atty-1")
    raw = sum(len(r["content"]) for r in rows)
    monkeypatch.setattr(
        compaction, "_generate_quote_lines",
        lambda r, n, correction="": _quotes_for(r, [row["id"] for row in r[:n]]),
    )
    # Ask for nearly all of it back: the segment must end up small enough to deliver.
    want = int(raw * 0.7)
    res = compaction.compact_conversation("doc-1", "atty-1", want)
    assert res["compacted"] is True
    assert res["requested"] == want
    assert res["reclaimed"] >= want, "trim loop did not reach the target it could reach"
    assert res["quotes"] < get_settings().compaction_max_quotes


def test_an_unreachable_target_condenses_as_far_as_it_can_and_says_so(monkeypatch):
    """Grounding, not history, is usually what blows the budget.

    Asking for more than history even contains must not fail and must not silently
    look like success: condense to the floor, report what was actually freed, and let
    the pane tell the attorney the rest is document and playbook.
    """
    _seed(20)
    _from, _to, rows = compaction.select_compactable_rows("doc-1", "atty-1")
    raw = sum(len(r["content"]) for r in rows)
    monkeypatch.setattr(
        compaction, "_generate_quote_lines",
        lambda r, n, correction="": _quotes_for(r, [row["id"] for row in r[:n]]),
    )
    res = compaction.compact_conversation("doc-1", "atty-1", raw * 10)
    assert res["compacted"] is True
    assert res["quotes"] == get_settings().compaction_min_quotes
    assert res["reclaimed"] < res["requested"]


def test_a_three_turn_conversation_has_something_to_condense():
    """The requirement the old floor failed, stated behaviourally.

    With keep_recent=6 a six-message conversation had NOTHING condensable while the
    document was already being truncated — a message COUNT guarding a size budget.
    Asserting the config value would just restate the number; this asserts the property
    the number has to deliver, and fails for any floor that swallows a short
    conversation whole.
    """
    _seed(3)                                           # 6 messages
    from_id, to_id, selected = compaction.select_compactable_rows("doc-1", "atty-1")
    assert selected, "a 3-turn conversation must have condensable history"
    assert from_id and to_id


def test_a_target_makes_us_ask_the_model_for_fewer_quotes(monkeypatch):
    """The cap derivation is an OPTIMISATION, not the mechanism.

    The trim loop alone would reach the target, so this cannot be tested by its effect
    on the segment — mutation-checked, and removing the derivation left every other test
    green. What it actually buys is not generating quotes we are about to discard, so
    what must be asserted is the number the model was ASKED for.
    """
    # Two documents, not two passes: the first compaction consumes the rows it
    # condenses, so a second call on the same conversation has nothing left to size a
    # cap against and would compare against an empty range.
    for doc in ("doc-loose", "doc-tight"):
        for i in range(20):
            append_turn(doc, "atty-1",
                        f"Question {i} about the indemnity position in this agreement. " * 6,
                        f"Answer {i} setting out the firm position on that clause. " * 6)
    _from, _to, rows = compaction.select_compactable_rows("doc-tight", "atty-1")
    raw = sum(len(r["content"]) for r in rows)
    asked = []

    def fake(r, n, correction=""):
        asked.append(n)
        return _quotes_for(r, [row["id"] for row in r[:n]])

    monkeypatch.setattr(compaction, "_generate_quote_lines", fake)
    compaction.compact_conversation("doc-loose", "atty-1")                  # no target
    compaction.compact_conversation("doc-tight", "atty-1", int(raw * 0.8))  # tight target
    assert asked[0] == get_settings().compaction_max_quotes
    assert asked[-1] < asked[0], "a tight target must lower what we ask the model for"
    assert asked[-1] >= get_settings().compaction_min_quotes


def test_the_floor_is_never_condensed(monkeypatch):
    """The last turn stays verbatim so 'make that change' still resolves."""
    rows = _seed(5)
    keep = get_settings().compaction_keep_recent_messages
    _from, to_id, selected = compaction.select_compactable_rows("doc-1", "atty-1")
    assert len(selected) == len(rows) - keep
    assert to_id == rows[len(rows) - keep - 1]["id"]
