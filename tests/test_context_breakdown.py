"""The counter's arithmetic and its threshold.

The figures are the LAST TURN'S REAL MEASURED VALUES, never a forecast: grounding
is question-dependent (_needs_grounding keys off wording), so the same contract
costs 49k or 131k depending on what is asked.
"""
from config import get_settings
from skills.legal_research.context import build_context_breakdown


def _bd(**kw):
    base = dict(
        doc_chars=0, playbook_chars=0, msa_chars=0, review_chars=0,
        history_chars=0, system_chars=0, compressible_messages=0,
    )
    return build_context_breakdown(**{**base, **kw})


def test_parts_sum_to_the_total_and_carry_display_order():
    b = _bd(doc_chars=84859, playbook_chars=38587, review_chars=5000,
            history_chars=18554, system_chars=3000)
    assert [p["key"] for p in b["parts"]] == [
        "document", "playbook", "msa", "review", "history", "system",
    ]
    assert sum(p["chars"] for p in b["parts"]) == b["total_chars"] == 150000
    # Only history is compactable. The document is the source of truth and the
    # playbook is the ceiling — neither is ever summarised.
    assert [p["key"] for p in b["parts"] if p["compactable"]] == ["history"]


def test_tokens_use_the_measured_chars_per_token_not_chars_over_four():
    cpt = get_settings().est_chars_per_token
    assert cpt == 4.89
    b = _bd(doc_chars=48900)
    # chars/4 would say 12,225 — an overstatement of 22%, which is why every
    # budget comment that used it was wrong.
    assert b["parts"][0]["tokens"] == 10000
    assert b["total_tokens"] == 10000
    assert b["chars_per_token"] == cpt


def test_percentages_are_of_the_budget_not_of_the_total():
    budget = get_settings().chat_context_max_chars
    b = _bd(doc_chars=budget // 2)
    assert b["budget_chars"] == budget
    assert b["pct"] == 50
    assert b["parts"][0]["pct"] == 50


def test_no_compressible_history_means_no_action_offered():
    budget = get_settings().chat_context_max_chars
    # Over the threshold, but nothing older than the verbatim window: offering
    # the control here would offer a no-op, and a control that cries wolf gets
    # ignored.
    b = _bd(doc_chars=budget, compressible_messages=0)
    assert b["pct"] >= b["warn_pct"]
    assert b["can_compact"] is False


def test_below_the_threshold_means_no_action_offered():
    b = _bd(doc_chars=1000, compressible_messages=40)
    assert b["can_compact"] is False


def test_over_threshold_with_compressible_history_offers_the_action():
    budget = get_settings().chat_context_max_chars
    b = _bd(doc_chars=int(budget * 0.95), compressible_messages=14)
    assert b["warn_pct"] == get_settings().compaction_warn_pct
    assert b["can_compact"] is True
    assert b["compressible_messages"] == 14


def test_an_empty_turn_does_not_divide_by_zero():
    b = _bd()
    assert b["total_chars"] == 0
    assert b["pct"] == 0
    assert all(p["pct"] == 0 for p in b["parts"])
    assert b["can_compact"] is False
