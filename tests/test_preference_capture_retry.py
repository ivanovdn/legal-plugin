"""Preference-capture retry on the doc-chat path.

The model routinely acknowledges a "remember…" request in prose and forgets the
```preference``` block, so the attorney is never offered the suggestion. Live
example (trace cc81804f): "remember that uor client is blizzard corp" produced
"I have noted that your client is Blizzard Corp *for this conversation*" and no
block — the attorney wanted it in USER.md.

Mirrors the edit-promise -> format='json' retry: detect on the USER's phrasing,
retry once, stay silent otherwise.

NOTE the package-attribute hazard (see CLAUDE.md): `_run_doc_chat` resolves
`_looks_like_preference_request` / `_extract_proposed_preferences` through the
ENTRY module's globals, so these tests drive and patch
`skills.legal_research.legal_research`, reached via importlib (a plain
`import skills.legal_research.legal_research` binds the re-exported function).
"""

import importlib

from skills.legal_research.edit_parsing import _looks_like_preference_request

legal_research = importlib.import_module("skills.legal_research.legal_research")


def _make_state(request):
    return {"request": request, "user_id": "atty-9", "filters": {}, "chat_history": []}


def _stub_llm(monkeypatch, replies):
    """Feed `replies` to successive traced_invoke calls; record the call names."""
    calls = []

    class _Resp:
        def __init__(self, content):
            self.content = content

    def fake_invoke(llm, messages, name=None):
        calls.append({"name": name, "messages": messages})
        idx = min(len(calls) - 1, len(replies) - 1)
        return _Resp(replies[idx])

    monkeypatch.setattr(legal_research, "traced_invoke", fake_invoke)
    monkeypatch.setattr(legal_research, "_build_llm", lambda: object())
    return calls


# --- the detector itself ---------------------------------------------------

def test_detector_matches_requests_to_store():
    for req in [
        "remember that uor client is blizzard corp",   # the live miss, typo and all
        "From now on, treat Acme as the counterparty.",
        "going forward use the 2x cap",
        "For future reference, I prefer short summaries.",
    ]:
        assert _looks_like_preference_request(req), req


def test_detector_ignores_hold_in_context_phrasing():
    """"Keep in mind" asks us to use something NOW, not to store it.

    The chat history already carries it forward, so offering an Add-preference
    card is noise the attorney has to dismiss. Trace 4e3a2d94: "keep in mind
    that our client is EA games" produced an unwanted suggestion — the test is
    whether the user asked us to STORE it, not whether it would be useful later.
    """
    for req in [
        "keep in mind that our client is EA games",
        "bear in mind that our client is EA games",
        "Please note that we act for the disclosing party.",
        "make a note that the counterparty is Acme",
        "for this document, assume the client is Acme",
    ]:
        assert not _looks_like_preference_request(req), req


def test_detector_ignores_ordinary_questions():
    """Every false positive costs a real LLM round-trip on the commonest turn."""
    for req in [
        "does this clause always apply?",
        "is the cap never enforceable?",
        "who signs this agreement?",
        "what is the billing model?",
        "fill the signature block with Suzy Quatro",
        "",
    ]:
        assert not _looks_like_preference_request(req), req


def test_detector_deliberately_skips_bare_always_never():
    """Documented coverage gap, not an oversight.

    "always flag X" is the prompt's own worked example and the model emits a
    block for it reliably, so the net is not needed. Matching bare always/never
    cannot be done without also matching "does this always apply?" — see
    test_detector_ignores_ordinary_questions. If you widen the regex, make that
    test pass too.
    """
    assert not _looks_like_preference_request("always flag uncapped indemnity")


def test_prompt_draws_the_same_storage_line_as_the_detector():
    """The prompt is what actually decides — the detector only nets a forgotten
    block, and in trace 4e3a2d94 the unwanted suggestion came from the prompt
    with no retry involved. Both must draw the line in the same place."""
    from skills.legal_research.prompts import CHAT_SYSTEM_PROMPT

    low = CHAT_SYSTEM_PROMPT.lower()
    assert "keep in mind" in low                      # names the excluded phrasing
    assert "not emit a preference block" in low       # and says what to do with it
    assert "store" in low                             # states the actual test


# --- the retry ------------------------------------------------------------

def test_retry_recovers_a_forgotten_preference_block(monkeypatch):
    calls = _stub_llm(
        monkeypatch,
        [
            "I have noted that your client is Blizzard Corp for this conversation.",
            "```preference\nAssume the client is Blizzard Corp.\n```",
        ],
    )

    _, _, prefs = legal_research._run_doc_chat(
        _make_state("remember that uor client is blizzard corp"), "NON-DISCLOSURE AGREEMENT"
    )

    assert prefs == ["Assume the client is Blizzard Corp."]
    assert [c["name"] for c in calls] == ["doc_chat", "doc_chat_preference_retry"]
    # The retry must not re-send the document — it only needs request + reply.
    retry_user = calls[1]["messages"][-1]["content"]
    assert "NON-DISCLOSURE AGREEMENT" not in retry_user
    assert "remember that uor client is blizzard corp" in retry_user


def test_no_retry_when_the_model_already_emitted_a_block(monkeypatch):
    calls = _stub_llm(
        monkeypatch, ["Noted.\n```preference\nAssume the client is Blizzard Corp.\n```"]
    )

    _, _, prefs = legal_research._run_doc_chat(
        _make_state("remember our client is blizzard corp"), "NDA"
    )

    assert prefs == ["Assume the client is Blizzard Corp."]
    assert len(calls) == 1, "a block was already present — the retry is wasted latency"


def test_no_retry_on_an_ordinary_turn(monkeypatch):
    """An ordinary question must never pay for the extra call."""
    calls = _stub_llm(monkeypatch, ["Two directors sign, per Section 9."])

    _, _, prefs = legal_research._run_doc_chat(_make_state("who signs this?"), "NDA")

    assert prefs == []
    assert len(calls) == 1


def test_retry_that_yields_nothing_is_non_fatal(monkeypatch):
    """A one-off instruction gets an empty block back — answer the turn anyway."""
    calls = _stub_llm(
        monkeypatch,
        ["Noted for this document.", "```preference\n```"],
    )

    content, _, prefs = legal_research._run_doc_chat(
        _make_state("remember to check section 5 in this draft"), "NDA"
    )

    assert prefs == []
    assert content == "Noted for this document."
    assert len(calls) == 2
