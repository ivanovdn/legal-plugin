"""The counter's arithmetic and its threshold.

The figures are the LAST TURN'S REAL MEASURED VALUES, never a forecast: grounding
is question-dependent (_needs_grounding keys off wording), so the same contract
costs 49k or 131k depending on what is asked.
"""
import pytest
import skills.legal_research.context as ctx
from config import get_settings
from memory.conversation_store import append_turn
from memory.conversation_summary import append_segment
from skills.legal_research.context import build_context_breakdown, compressible_history


def _bd(**kw):
    base = dict(
        doc_chars=0, playbook_chars=0, msa_chars=0, review_chars=0,
        history_chars=0, system_chars=0, compressible_messages=0, compressible_chars=0,
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


def test_compressible_count_excludes_the_verbatim_window():
    # 5 turns = 10 messages; the most recent compaction_keep_recent_messages (2)
    # stay verbatim, so 8 are condensable. The assertion reads the setting rather
    # than the number, because that floor has already moved once.
    for i in range(5):
        append_turn("doc-cc", "atty-cc", f"q{i}", f"a{i}")
    state = {"document_id": "doc-cc", "user_id": "atty-cc"}
    keep = get_settings().compaction_keep_recent_messages
    assert compressible_history(state)[0] == 10 - keep


def test_compressible_count_excludes_rows_already_condensed():
    # Rows at or below the highest condensed id are already represented by a
    # segment; counting them again would offer to condense what is condensed.
    for i in range(6):
        append_turn("doc-cc", "atty-cc", f"q{i}", f"a{i}")     # 12 messages
    from memory.conversation_store import load_rows_after
    rows = load_rows_after("doc-cc", "atty-cc", 0, 100)
    append_segment("doc-cc", "atty-cc", rows[0]["id"], rows[3]["id"], "SEG")
    state = {"document_id": "doc-cc", "user_id": "atty-cc"}
    keep = get_settings().compaction_keep_recent_messages
    assert compressible_history(state)[0] == (12 - 4) - keep


def test_compressible_count_is_zero_when_nothing_is_condensable():
    # Fewer messages than the verbatim window: the Condense control must not be
    # offered, because pressing it would be a no-op and a control that does
    # nothing teaches attorneys to ignore it.
    append_turn("doc-cc", "atty-cc", "q", "a")
    assert compressible_history({"document_id": "doc-cc", "user_id": "atty-cc"})[0] == 0


def test_compressible_count_is_zero_when_compaction_is_disabled(monkeypatch):
    for i in range(5):
        append_turn("doc-cc", "atty-cc", f"q{i}", f"a{i}")
    monkeypatch.setenv("COMPACTION_ENABLED", "false")
    get_settings.cache_clear()
    try:
        assert compressible_history({"document_id": "doc-cc", "user_id": "atty-cc"})[0] == 0
    finally:
        get_settings.cache_clear()


def test_compressible_count_is_zero_without_ids():
    append_turn("doc-cc", "atty-cc", "q", "a")
    assert compressible_history({"document_id": "", "user_id": "atty-cc"})[0] == 0
    assert compressible_history({"document_id": "doc-cc", "user_id": ""})[0] == 0


def test_compressible_count_survives_a_store_failure_without_flagging_degraded(monkeypatch):
    """A store hiccup must cost the attorney a BUTTON, never a turn.

    memory_degraded drives an amber "this turn wasn't remembered" banner, which is
    reserved for reads the ANSWER depends on. This count only decides whether a
    control is offered, so it must fail silently to 0 — flagging here would cry wolf
    on every turn a UI affordance could not be computed.
    """
    def boom(*_a, **_k):
        raise RuntimeError("app-db unavailable")

    monkeypatch.setattr(ctx, "latest_to_id", boom)
    state = {"document_id": "doc-cc", "user_id": "atty-cc"}
    assert compressible_history(state)[0] == 0
    assert "memory_degraded" not in state


@pytest.fixture
def settings_env(monkeypatch):
    """Set config via env for ONE test, then restore the cached Settings.

    get_settings is @lru_cache'd. Clearing only on the way in would leave this
    test's Settings object cached for every test that follows and does not clear
    it itself — the clear on the way OUT is what keeps the pollution local.
    """
    def _set(**env):
        for key, value in env.items():
            monkeypatch.setenv(key, str(value))
        get_settings.cache_clear()

    yield _set
    get_settings.cache_clear()


def test_auto_compact_needs_more_history_than_the_button_does():
    """The anti-churn floor, and the most important assertion in this slice.

    After a compaction, compressible history drops to ~0 and grows by exactly two
    rows per turn. If automatic firing shared the button's "> 0" threshold it would
    fire on the very next turn against two short messages — and a segment carries a
    261-char header plus ~20 chars per quote line, so the net-benefit guard would
    decline it. That is a 10-30s LLM call, on a shared Ollama, guaranteed to write
    nothing. So there must be a band where the button is offered and auto stays quiet.
    """
    settings = get_settings()
    b = _bd(
        doc_chars=int(settings.chat_context_max_chars * 0.95),
        compressible_messages=settings.compaction_auto_min_messages - 1,
    )
    assert b["can_compact"] is True
    assert b["auto_compact"] is False


def test_auto_compact_fires_once_the_floor_is_reached():
    settings = get_settings()
    b = _bd(
        doc_chars=int(settings.chat_context_max_chars * 0.95),
        compressible_messages=settings.compaction_auto_min_messages,
    )
    assert b["auto_compact"] is True


def test_auto_compact_is_a_narrowing_of_the_button_never_a_widening():
    """Below the warn line there is no pressure to relieve, however much history
    has piled up. auto_compact true with can_compact false is a defect anywhere."""
    b = _bd(doc_chars=1000, compressible_messages=500)
    assert b["can_compact"] is False
    assert b["auto_compact"] is False


def test_auto_is_armed_by_default():
    """compaction_auto ships ON: the flag exists so a pilot can switch the
    behaviour off after seeing it, not so someone has to switch it on to see it."""
    settings = get_settings()
    assert settings.compaction_auto is True
    b = _bd(
        doc_chars=int(settings.chat_context_max_chars * 0.95),
        compressible_messages=settings.compaction_auto_min_messages + 10,
    )
    assert b["auto_compact"] is True


def test_the_master_switch_stops_auto_without_taking_away_the_button(settings_env):
    """Turning compaction_auto off must leave the manual control exactly as it was —
    the switch governs unrequested firing, not the attorney's own button."""
    settings_env(COMPACTION_AUTO="false")
    settings = get_settings()
    assert settings.compaction_auto is False
    b = _bd(
        doc_chars=int(settings.chat_context_max_chars * 0.95),
        compressible_messages=settings.compaction_auto_min_messages + 10,
    )
    assert b["can_compact"] is True
    assert b["auto_compact"] is False


def test_disabling_compaction_entirely_stops_both(settings_env):
    settings_env(COMPACTION_ENABLED="false")
    settings = get_settings()
    b = _bd(
        doc_chars=int(settings.chat_context_max_chars * 0.95),
        compressible_messages=settings.compaction_auto_min_messages + 10,
    )
    assert b["can_compact"] is False
    assert b["auto_compact"] is False


def test_a_huge_history_of_few_messages_arms_auto():
    """The VM failure of 2026-08-27, pinned.

    An attorney pasted a contract into the chat box. It was stored, replayed as
    history, and reached 87,282 chars = 96% of budget — while the attached document
    was truncated to nothing. Compaction was exactly the right medicine and auto
    stayed silent, because three messages is fewer than compaction_auto_min_messages.

    A message COUNT cannot guard a SIZE budget. This is the same unit error already
    fixed once in compaction_keep_recent_messages.
    """
    settings = get_settings()
    b = _bd(
        playbook_chars=30412, history_chars=87282, system_chars=7188,
        compressible_messages=3, compressible_chars=87282,
    )
    assert b["pct"] >= b["warn_pct"]
    assert 3 < settings.compaction_auto_min_messages     # the message floor says no
    assert 87282 >= settings.compaction_auto_min_chars   # the size floor says yes
    assert b["auto_compact"] is True


def test_the_size_floor_does_not_arm_on_a_small_history():
    """The anti-churn property has to survive the new floor. Two short messages are
    what the net-benefit guard declines, and they must still not arm anything."""
    settings = get_settings()
    b = _bd(
        doc_chars=int(get_settings().chat_context_max_chars * 0.95),
        compressible_messages=2, compressible_chars=1300,
    )
    assert b["can_compact"] is True
    assert 2 < settings.compaction_auto_min_messages
    assert 1300 < settings.compaction_auto_min_chars
    assert b["auto_compact"] is False


def test_neither_floor_arms_when_there_is_nothing_to_condense():
    """Both floors are ANDed with can_compact, so a huge char count cannot arm auto
    when the compressible pool is empty — otherwise a long already-condensed history
    would fire a call that condenses nothing, get refused, and disarm for the session."""
    b = _bd(
        doc_chars=int(get_settings().chat_context_max_chars * 0.95),
        compressible_messages=0, compressible_chars=90000,
    )
    assert b["can_compact"] is False
    assert b["auto_compact"] is False


def test_compressible_history_reports_chars_excluding_the_verbatim_window():
    """The kept-verbatim rows are not reclaimable, so their characters must not
    count toward the size floor — otherwise the floor arms on history compaction
    would leave exactly where it is."""
    append_turn("doc-ch", "atty-ch", "q" * 100, "a" * 200)
    append_turn("doc-ch", "atty-ch", "q" * 300, "a" * 400)
    state = {"document_id": "doc-ch", "user_id": "atty-ch"}
    keep = get_settings().compaction_keep_recent_messages
    assert keep == 2, "this test's arithmetic assumes the shipped floor of 2"
    messages, chars = compressible_history(state)
    # 4 rows of 100/200/300/400; the newest 2 (300, 400) stay verbatim.
    assert messages == 2
    assert chars == 300
