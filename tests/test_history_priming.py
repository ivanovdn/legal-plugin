"""Stored assistant output must not become few-shot instruction.

Root cause, diagnosed from VM conversation `2ae99ecc` (2026-08-13): an assistant
reply fuses conversation (prose) with machinery (fenced ```json``` / ```preference```
blocks). Every consumer separates them — the backend parses blocks out, the
frontend strips them for display — except `_load_prior_conversation`, which
replays the reply verbatim into the next prompt.

To a model, history is not a log; it IS prompt, and in-context demonstrations
outrank system instructions. Three stored "note that our client is X" turns that
each emitted a preference block kept the model emitting them long after the
prompt was corrected to forbid exactly that. **Prompt fixes are not retroactive
while the store replays raw output.**

Two defences, tested here:
  A. `_looks_like_context_only_request` — suppress an unsolicited preference
     block based on the ATTORNEY's words, independent of model or context size.
  B. `_sanitize_history` — strip fenced blocks from replayed assistant turns,
     removing the priming vector for preferences AND edits.

Package-attribute hazard (CLAUDE.md): `_run_doc_chat` resolves both names in the
ENTRY module's globals, so drive/patch `skills.legal_research.legal_research`,
reached via importlib — a plain import binds the re-exported function.
"""

import importlib

from skills.legal_research.edit_parsing import (
    _looks_like_context_only_request,
    _sanitize_history,
    _strip_structured_blocks,
)

legal_research = importlib.import_module("skills.legal_research.legal_research")


# Verbatim from the VM store — the turns that primed the failure.
_CONTAMINATED_HISTORY = [
    {"role": "user", "content": "note that our client is Sony company"},
    {
        "role": "assistant",
        "content": "Understood. I have noted that your client is Sony Company.\n\n"
        "```preference\nOur client is Sony Company.\n```",
    },
    {"role": "user", "content": "keep in mynd that our company is Blizzard"},
    {
        "role": "assistant",
        "content": "Understood. I have noted that your company is Blizzard.\n\n"
        "```preference\nOur company is Blizzard.\n```",
    },
    {"role": "user", "content": "fill blanc signed by with John Doe"},
    {
        "role": "assistant",
        "content": "I have updated the signatory name in the signature block to John Doe.\n\n"
        '```json\n{"action": "replace", "target_text": "Signed by: [__]", '
        '"new_text": "Signed by: John Doe"}\n```',
    },
]


# --- B: stripping --------------------------------------------------------

def test_strip_removes_both_block_kinds_and_keeps_prose():
    assert _strip_structured_blocks(
        "Understood.\n\n```preference\nOur client is Sony.\n```"
    ) == "Understood."
    assert _strip_structured_blocks(
        'Done.\n\n```json\n{"action": "delete", "target_text": "x"}\n```'
    ) == "Done."
    assert _strip_structured_blocks("just prose") == "just prose"
    assert _strip_structured_blocks("") == ""


def test_sanitize_strips_assistant_turns_only():
    """The attorney's own words are evidence — never rewritten."""
    cleaned = _sanitize_history(_CONTAMINATED_HISTORY)
    joined = "\n".join(m["content"] for m in cleaned)

    assert "```preference" not in joined
    assert "```json" not in joined
    # Every user turn survives byte-identical.
    users_before = [m for m in _CONTAMINATED_HISTORY if m["role"] == "user"]
    users_after = [m for m in cleaned if m["role"] == "user"]
    assert users_after == users_before


def test_sanitize_preserves_the_value_carrying_prose():
    """Stripping must not break "the legal name we filled recently" recall —
    the reason chat_history cannot simply be scoped out (CLAUDE.md)."""
    cleaned = _sanitize_history(_CONTAMINATED_HISTORY)
    joined = "\n".join(m["content"] for m in cleaned)

    assert "John Doe" in joined          # the filled value survives
    assert "Sony Company" in joined
    assert "Blizzard" in joined


def test_sanitize_drops_a_reply_that_was_only_a_block():
    """No conversational content left → replaying an empty turn helps nobody."""
    only_block = [
        {"role": "user", "content": "fill it"},
        {"role": "assistant", "content": '```json\n{"action": "delete", "target_text": "x"}\n```'},
    ]
    cleaned = _sanitize_history(only_block)
    assert [m["role"] for m in cleaned] == ["user"]


def test_contaminated_history_never_reaches_the_prompt(monkeypatch):
    """End-to-end: the exact VM history assembles into a block-free prompt."""
    captured = {}

    class _Resp:
        content = "Understood."

    monkeypatch.setattr(legal_research, "_build_llm", lambda: object())
    monkeypatch.setattr(
        legal_research, "traced_invoke",
        lambda llm, messages, name=None: captured.update(messages=messages) or _Resp(),
    )
    monkeypatch.setattr(legal_research, "_load_prior_conversation", lambda s: list(_CONTAMINATED_HISTORY))

    state = {"request": "who signs?", "user_id": "a-1", "filters": {}, "chat_history": []}
    legal_research._run_doc_chat(state, "NON-DISCLOSURE AGREEMENT")

    replayed = "\n".join(
        m["content"] for m in captured["messages"] if m["content"] not in ("",)
    )
    # The system prompt legitimately contains the literal "```json" as
    # instruction, so assert on the REPLAYED TURNS only.
    history_only = "\n".join(
        m["content"] for m in captured["messages"]
        if m["role"] == "assistant"
    )
    assert "```preference" not in history_only
    assert "```json" not in history_only
    assert "John Doe" in replayed          # prose still there


def test_redis_fallback_history_is_sanitized_too(monkeypatch):
    """The durable store and the Redis fallback both replay raw output — a
    transform inside one loader is bypassed whenever the other path wins."""
    captured = {}

    class _Resp:
        content = "ok"

    monkeypatch.setattr(legal_research, "_build_llm", lambda: object())
    monkeypatch.setattr(
        legal_research, "traced_invoke",
        lambda llm, messages, name=None: captured.update(messages=messages) or _Resp(),
    )
    monkeypatch.setattr(legal_research, "_load_prior_conversation", lambda s: [])

    state = {
        "request": "who signs?", "user_id": "a-1", "filters": {},
        "chat_history": list(_CONTAMINATED_HISTORY),
    }
    legal_research._run_doc_chat(state, "NDA")

    history_only = "\n".join(
        m["content"] for m in captured["messages"] if m["role"] == "assistant"
    )
    assert "```preference" not in history_only
    assert "```json" not in history_only


# --- A: the negative-list guard -----------------------------------------

def test_context_only_detector():
    for req in [
        "keep in mind that our client is blizzard",
        "bear in mind our client is Acme",
        "note that our client is Sony company",
        "for this document, assume the client is Acme",
        "For context, my client on this deal is Acme Corp — please just acknowledge.",
    ]:
        assert _looks_like_context_only_request(req), req

    for req in ["remember our client is EA", "who signs?", "from now on use 2x", ""]:
        assert not _looks_like_context_only_request(req), req


def _drive(monkeypatch, request, *replies):
    """Feed `replies` to successive traced_invoke calls.

    Distinct replies matter: a single fixed reply makes the preference-retry
    return the SAME block it just suppressed, so a broken guard silently
    repairs itself and the test passes while testing nothing. Caught by
    mutation, not by the suite.
    """
    calls = []

    class _Resp:
        def __init__(self, content):
            self.content = content

    def fake_invoke(llm, messages, name=None):
        calls.append(name)
        return _Resp(replies[min(len(calls) - 1, len(replies) - 1)])

    monkeypatch.setattr(legal_research, "_build_llm", lambda: object())
    monkeypatch.setattr(legal_research, "traced_invoke", fake_invoke)
    state = {"request": request, "user_id": "a-1", "filters": {}, "chat_history": []}
    return (*legal_research._run_doc_chat(state, "NDA"), calls)


_WITH_BLOCK = "Understood.\n\n```preference\nOur client is Blizzard.\n```"


def test_unsolicited_preference_is_dropped_for_context_only_request(monkeypatch):
    """Exactly the VM failure: the model offers a card the attorney never asked
    for, because stored history taught it to."""
    _, _, prefs, calls = _drive(
        monkeypatch, "keep in mind that our client is blizzard", _WITH_BLOCK
    )
    assert prefs == []
    # And no retry — "keep in mind" is not a storage request, so the dropped
    # block must not come straight back through the second-chance call.
    assert calls == ["doc_chat"]


def test_preference_survives_an_explicit_storage_request(monkeypatch):
    _, _, prefs, calls = _drive(
        monkeypatch, "remember that our client is blizzard", _WITH_BLOCK
    )
    assert prefs == ["Our client is Blizzard."]
    assert calls == ["doc_chat"], "block was already present — no retry needed"


def test_explicit_storage_wins_when_both_phrasings_appear(monkeypatch):
    """"keep in mind X, and remember Y" did ask us to store something.

    The retry reply deliberately carries NO block: if the guard wrongly
    suppresses here, the retry cannot paper over it and prefs come back empty.
    """
    _, _, prefs, calls = _drive(
        monkeypatch,
        "keep in mind we act for the seller, and remember our client is Blizzard",
        _WITH_BLOCK,                      # first call
        "Sorry, nothing to store.",       # retry, no block
    )
    assert prefs == ["Our client is Blizzard."]
    assert calls == ["doc_chat"]


def test_guard_does_not_touch_an_ordinary_turn(monkeypatch):
    _, _, prefs, calls = _drive(monkeypatch, "who signs this?", "Two directors sign, per Section 9.")
    assert prefs == []
    assert calls == ["doc_chat"]
