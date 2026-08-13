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


# Explicit "carry this past the current turn" phrasing in the USER's message.
#
# Deliberately limited to unambiguous memory verbs. "always"/"never" were tried
# and removed: even restricted to imperative verbs they fire on ordinary contract
# questions — "does this clause always apply?" matches "always apply" — and every
# false positive costs a real LLM round-trip on the most common kind of turn.
#
# The narrowness is affordable because this is only a SAFETY NET. Instruction-
# shaped preferences ("always flag uncapped indemnity") are the prompt's own
# worked examples and the model emits blocks for them reliably; the retry exists
# for the fact-shaped request the prompt used to misclassify as conversation
# context. A miss here costs the attorney a manual Preferences-tab entry, never
# a wrong write — the block is always a suggestion they approve.
_PREFERENCE_REQUEST_RE = re.compile(
    r"\b(remember|keep in mind|bear in mind|from now on|going forward|"
    r"for future reference|note that|make a note|i prefer|my preference)\b",
    re.I,
)


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


def _extract_proposed_preferences(prose: str) -> list[str]:
    """Pull ```preference``` fenced blocks into individual preference lines.

    Plain text, one preference per line (a leading '-'/'*' bullet is stripped) —
    deliberately NOT JSON, to avoid the edit-block parsing fragility. Non-fatal:
    no block → []. The suggestion the attorney approves; not a document edit.
    """
    prefs: list[str] = []
    for match in _PREFERENCE_BLOCK_RE.finditer(prose or ""):
        for line in match.group(1).splitlines():
            t = re.sub(r"^\s*[-*]\s+", "", line).strip()
            if t:
                prefs.append(t)
    return prefs
