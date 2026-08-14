# skills/legal_research/edit_parsing.py
"""Parse structured edit / preference proposals out of the LLM's prose.

The Python mirror of the Word add-in's parseEditBlocks.ts, and it must stay
tolerant of the same three shapes the local model actually emits inside one
fenced block: a single object, an array, and stacked top-level objects.

Imports nothing from this project — pure text in, structured data out.
"""
import json
import logging
import re

logger = logging.getLogger(__name__)


_JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)
_PREFERENCE_BLOCK_RE = re.compile(r"```preference\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def _strip_structured_blocks(prose: str) -> str:
    """Remove fenced ```json``` / ```preference``` blocks, keeping the prose.

    An assistant reply carries TWO things fused into one string: conversation
    for the human, and machinery for the client. Every consumer separates them
    — the backend parses the blocks out, the frontend strips them for display —
    except the path that replays the reply into the next prompt.

    That matters because history is not a log to a model, it is prompt. A
    replayed block becomes an in-context demonstration, and demonstrations beat
    system instructions: three stored "note that …" turns that emitted a
    preference block kept the model emitting them for hours after the prompt
    was changed to forbid exactly that (VM conversation `2ae99ecc`). Prompt
    fixes are not retroactive while the store replays raw output.

    Stripping loses nothing the conversation needs — the prose still says "I
    have updated the signatory name to John Doe", so a later "the legal name we
    filled recently" still resolves. Only the machinery goes.
    """
    if not prose:
        return ""
    out = _JSON_BLOCK_RE.sub("", prose)
    out = _PREFERENCE_BLOCK_RE.sub("", out)
    # A stripped block leaves its blank lines behind; collapse them so the
    # replayed turn reads as ordinary prose.
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def _sanitize_history(messages: list[dict]) -> list[dict]:
    """Strip structured blocks from ASSISTANT turns before they re-enter a prompt.

    User turns are left byte-identical: they are the attorney's own words, and a
    quoted block there is evidence about what they asked for.
    """
    cleaned: list[dict] = []
    for m in messages:
        if m.get("role") != "assistant":
            cleaned.append(m)
            continue
        content = _strip_structured_blocks(m.get("content", ""))
        if content:
            cleaned.append({**m, "content": content})
        # A reply that was ONLY a block carries no conversational meaning —
        # drop it rather than replay an empty assistant turn.
    return cleaned


# Phrasing that asks us to hold something in mind for the CURRENT conversation
# rather than store it. The chat history already carries these forward, so a
# preference card is noise the attorney must dismiss.
#
# Used as a NEGATIVE list, never as a positive requirement: demanding a match
# before allowing a block would suppress "always flag uncapped indemnity",
# which cannot be matched without also matching "does this clause always
# apply?" (see _PREFERENCE_REQUEST_RE). An explicit storage request in the same
# message always wins — see the caller.
_CONTEXT_ONLY_RE = re.compile(
    r"\b(keep in mind|bear in mind|keep in view|note that|make a note|"
    r"for this document|for this conversation|for this review|just acknowledge)\b",
    re.I,
)


def _looks_like_context_only_request(request: str) -> bool:
    """Did the USER ask us to use something now, rather than store it?"""
    return bool(_CONTEXT_ONLY_RE.search(request or ""))


def _escape_unescaped_whitespace_in_strings(raw: str) -> str:
    """Escape literal LF / CR / TAB characters that sit INSIDE JSON string
    values. Local LLMs occasionally line-wrap long string values mid-content,
    producing JSON that's structurally fine outside strings but invalid inside
    them (a JSON string can't contain a raw newline). This walks the text,
    tracks whether we're inside a quoted string, and replaces raw whitespace
    with the proper backslash-escape sequences."""
    out: list[str] = []
    in_string = False
    escape_next = False
    table = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}
    for ch in raw:
        if escape_next:
            out.append(ch)
            escape_next = False
            continue
        if in_string and ch == "\\":
            out.append(ch)
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            out.append(ch)
            continue
        if in_string and ch in table:
            out.append(table[ch])
        else:
            out.append(ch)
    return "".join(out)


def _tolerant_json_loads(raw: str):
    """json.loads with a best-effort fallback for raw newlines/tabs inside
    string values. Returns the parsed value or None if both attempts fail."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(_escape_unescaped_whitespace_in_strings(raw))
    except json.JSONDecodeError:
        return None


def _iter_json_values(raw: str) -> list:
    """Decode one or more concatenated top-level JSON values from `raw`.

    Local LLMs frequently stack several edit objects in a single fenced block,
    separated only by newlines ({...}\\n{...}) instead of wrapping them in a JSON
    array — which `json.loads` rejects as "extra data", so the whole block used to
    be dropped (traces cea50c6b / f15f8a9b). We decode values one at a time with
    `raw_decode`, skipping whitespace and stray separators between them. The same
    in-string-whitespace fix as `_tolerant_json_loads` is applied first so a raw
    newline inside a value doesn't abort the scan. Returns [] for a genuinely
    malformed block (nothing decodes)."""
    s = _escape_unescaped_whitespace_in_strings(raw)
    decoder = json.JSONDecoder()
    values: list = []
    idx, n = 0, len(s)
    while idx < n:
        while idx < n and s[idx] in " \t\r\n,":
            idx += 1
        if idx >= n:
            break
        try:
            obj, end = decoder.raw_decode(s, idx)
        except json.JSONDecodeError:
            break
        values.append(obj)
        idx = end
    return values


def _flatten_edit_values(values: list) -> list:
    """Normalize decoded JSON values into a flat list of edit-dict candidates.
    A value may be a bare edit dict, a list of edits, or a {"edits": [...]}
    wrapper (Ollama format=json mode)."""
    out: list = []
    for v in values:
        if isinstance(v, dict) and isinstance(v.get("edits"), list):
            out.extend(v["edits"])
        elif isinstance(v, list):
            out.extend(v)
        else:
            out.append(v)
    return out


def _parse_json_edits(raw: str) -> list[dict]:
    """Pull edit dicts out of a free-form JSON response.

    Accepts every shape the local LLM produces in format=json mode:
      {"edits": [{...}, {...}]}       (preferred wrapping)
      [{...}, {...}]                   (bare array)
      {"action": "replace", ...}       (single bare edit)
      {...}\\n{...}                     (stacked top-level objects)
    """
    candidates = _flatten_edit_values(_iter_json_values(raw))
    return [
        c for c in candidates
        if isinstance(c, dict) and c.get("action") in _VALID_ACTIONS
    ]


# Phrases that imply the model intends to (or claims to have) made an edit.
# Mirrors the regex used in clients/word/src/components/ChatTab.tsx so backend
# retry logic and the UI warning fire on the same signal.
#
# Verb-stem trick: stems are shortened so the `\w{0,3}\b` tail matches both the
# present (replace, replaces, replacing) AND past (replaced) tenses. The
# original `\breplace\b` form silently missed "I have replaced..." because the
# trailing `d` is still a word char so no word boundary existed before it.
_EDIT_PROMISE_RE = re.compile(
    r"\bi['’]?(?:ll|ve| will| have| am going to)\b[^.?!\n]*"
    r"\b(?:replac|insert|delet|fill|add|remov|chang|rewrit|tighten|loosen|updat|edit|modif|set)"
    r"\w{0,3}\b",
    re.IGNORECASE,
)


def _looks_like_edit_promise(prose: str) -> bool:
    """Heuristic: did the model claim it would make an edit (without emitting a block)?"""
    return bool(_EDIT_PROMISE_RE.search(prose or ""))


# Phrasing that asks us to STORE something, in the USER's message.
#
# The line is storage, not usefulness: "remember…" / "from now on…" / "for future
# reference…" ask for persistence, while "keep in mind…" / "bear in mind…" /
# "note that…" ask only that we take something into account in the current
# conversation — which the chat history already does, so they must NOT surface an
# Add-preference card. Those were matched here initially and the distinction was
# wrong (trace 4e3a2d94: "keep in mind that our client is EA games" produced an
# unwanted preference suggestion).
#
# "always"/"never" were also tried and removed: even restricted to imperative
# verbs they fire on ordinary contract questions — "does this clause always
# apply?" matches "always apply" — and every false positive costs a real LLM
# round-trip on the most common kind of turn.
#
# The narrowness is affordable because this is only a SAFETY NET for a forgotten
# block; the prompt draws the same storage/context line. A miss costs the
# attorney a manual Preferences-tab entry, never a wrong write.
_PREFERENCE_REQUEST_RE = re.compile(
    r"\b(remember|from now on|going forward|for future reference|"
    r"i prefer|my preference)\b",
    re.I,
)


# Standing instructions phrased with always/never, and the interrogative test
# that keeps ordinary questions out.
#
# These were excluded from _PREFERENCE_REQUEST_RE because "does this clause
# always apply?" matched, and a false positive costs a real LLM round-trip. The
# justification was that "always flag X" is the prompt's own worked example and
# the model emits a block for it reliably — which trace `004bccfe` disproved:
# "Always flag uncapped indemnity" produced "I have remembered that you want me
# to always flag uncapped indemnity in future reviews" with NO block and no
# retry, so the preference was lost while the model claimed it was stored.
#
# The real discriminator was never the verb, it was the sentence form.
_ALWAYS_NEVER_RE = re.compile(r"\b(?:always|never)\b", re.I)
_INTERROGATIVE_RE = re.compile(
    r"^\s*(?:do|does|did|is|are|was|were|can|could|should|would|will|may|"
    r"what|which|who|whom|whose|why|when|where|how)\b",
    re.I,
)


def _is_question(request: str) -> bool:
    r = (request or "").strip()
    return r.endswith("?") or bool(_INTERROGATIVE_RE.match(r))


def _should_retry_preference(request: str) -> bool:
    """Should we spend a second call recovering a forgotten preference block?

    Broader than `_looks_like_preference_request` on purpose. That one also
    decides whether an explicit storage request OVERRIDES the context-only
    suppression, and widening it there would let "keep in mind we always use
    Tennessee law" produce a card — the opposite of what "keep in mind" means.
    Retrying is cheap and self-correcting (the retry prompt returns an empty
    block for a one-off), so only THIS path takes the wider net.
    """
    if _looks_like_preference_request(request):
        return True
    return bool(_ALWAYS_NEVER_RE.search(request or "")) and not _is_question(request)


def _looks_like_preference_request(request: str) -> bool:
    """Heuristic: did the USER ask us to carry something beyond this turn?

    Runs on the user's own message, not the model's reply — the model may
    acknowledge the request in prose ("noted for this conversation") while
    forgetting the ```preference``` block, which is exactly the miss this
    detects (trace cc81804f: "remember that uor client is blizzard corp"
    produced a correct acknowledgment and no block).
    """
    return bool(_PREFERENCE_REQUEST_RE.search(request or ""))


# Edit actions the chat skill emits. `replace_all` is the multi-location variant
# of `replace` — the client loops body.search on every match instead of just the
# first. Lets the LLM stop hallucinating positions for "fill every X" requests.
_VALID_ACTIONS = {"replace", "replace_all", "insert", "delete"}


def _extract_proposed_edits(prose: str) -> list[dict]:
    """Pull fenced ```json``` blocks out of the agent's prose into structured edit proposals.

    A block can hold a single edit object, an array of edits, OR several edit
    objects stacked one per line ({...}\\n{...}) — the local LLM uses all three
    interchangeably. `_iter_json_values` decodes whichever shape is present so a
    stacked block is no longer silently dropped (which used to trigger a lossy
    JSON-retry — traces cea50c6b / f15f8a9b).

    Tolerant of malformed JSON — any block that yields no values is skipped with
    a warning. The original prose is left untouched; the frontend strips blocks
    for display.
    """
    proposals: list[dict] = []
    for match in _JSON_BLOCK_RE.finditer(prose or ""):
        raw = match.group(1).strip()
        values = _iter_json_values(raw)
        if not values:
            logger.warning("[legal_research] skipping malformed JSON block: %r", raw[:120])
            continue
        for c in _flatten_edit_values(values):
            if isinstance(c, dict) and c.get("action") in _VALID_ACTIONS:
                proposals.append(c)
            else:
                logger.warning("[legal_research] edit entry missing/invalid action: %r", c)
    return proposals


# A preference line is STORED and re-read on future documents by someone with no
# memory of this conversation, so it must read as a statement, not as an order
# given to the assistant. The model half-complies: told to write standalone, it
# resolved the pronoun ("our" -> "the") but kept the imperative — live output was
# "Remember that the client is Sony." (2026-08-14).
#
# This strips ONLY the leading wrapper and never touches what follows, because a
# preference is not always a fact about the client. Both shapes must survive:
#   "Remember that the client is Sony."             -> "The client is Sony."
#   "Remember I always want indemnity flagged red." -> "I always want indemnity flagged red."
# Rewriting the remainder would need to understand it, which is exactly the kind
# of judgment that belongs to the attorney approving the card.
#
# `note` requires a following word boundary, so "Notes must be short" is untouched.
_PREFERENCE_LEAD_RE = re.compile(
    r"^\s*(?:please\s+)?(?:remember|keep in mind|bear in mind|make a note|note)\b"
    r"\s*(?:that\b|to\b)?\s*",
    re.I,
)


def _normalize_preference_line(line: str) -> str:
    """Drop a leading 'remember that …' wrapper; leave the substance alone."""
    stripped = _PREFERENCE_LEAD_RE.sub("", line, count=1).strip()
    if not stripped:
        return line.strip()          # the whole line was the wrapper — keep it
    return stripped[0].upper() + stripped[1:]


def _extract_proposed_preferences(prose: str) -> list[str]:
    """Pull ```preference``` fenced blocks into individual preference lines.

    Plain text, one preference per line (a leading '-'/'*' bullet is stripped) —
    deliberately NOT JSON, to avoid the edit-block parsing fragility. Non-fatal:
    no block → []. The suggestion the attorney approves; not a document edit.

    Normalizing HERE covers both paths that produce preferences — the model's
    first reply and the second-chance retry — since both are parsed through this
    one function.
    """
    prefs: list[str] = []
    for match in _PREFERENCE_BLOCK_RE.finditer(prose or ""):
        for line in match.group(1).splitlines():
            t = re.sub(r"^\s*[-*]\s+", "", line).strip()
            if t:
                prefs.append(_normalize_preference_line(t))
    return prefs
