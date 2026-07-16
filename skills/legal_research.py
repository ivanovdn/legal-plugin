# skills/legal_research.py
"""Legal research — direct ChatOllama for doc-attached chats, ReAct agent for KB research."""

import json
import logging
import re
import unicodedata

from langchain_ollama import ChatOllama
from langfuse.decorators import observe
from langgraph.prebuilt import create_react_agent

from config import get_settings
from graph.state import LegalAgentState
from memory.conversation_store import load_recent
from memory.review_store import load_latest_review
from observability.tracing import traced_invoke, traced_agent_invoke
from rag.tools.search_legal import search_legal
from rag.tools.get_document import get_document
from rag.tools.escalate import escalate
from skills.grounding import attach_parent_msa, detect_contract_type, load_playbook_bundle

logger = logging.getLogger(__name__)

RESEARCH_SYSTEM_PROMPT = """You are a legal research agent for an internal legal team. Your job is to answer legal questions by searching the knowledge base.

PROCESS:
1. Search for relevant documents using search_legal with appropriate filters
2. If a result looks promising, use get_document to get the full text
3. Perform multiple searches with different query formulations if initial results are insufficient
4. Synthesize findings into a clear, well-cited answer
5. If you cannot find sufficient information, use escalate

RULES:
- Always filter by client_id — never access another client's documents
- Cite every claim with doc_id and doc_title
- If sources conflict, note the conflict explicitly
- If gaps remain in the answer, list them as open questions
- Be precise about what the sources say vs. your interpretation

OUTPUT:
Provide a comprehensive answer with:
- Direct answer to the question
- Supporting citations from retrieved documents
- Any conflicts between sources
- Open gaps that need further research
- Confidence assessment (how well-supported is the answer)"""


# System prompt for the in-Word chat path. When uploaded_docs is present, the
# document IS the source — RAG is unnecessary and tool calls add multi-minute
# latency on the local LLM. The directive forbids tool talk, mandates the
# JSON edit-block format for change requests, and keeps responses brief.
CHAT_SYSTEM_PROMPT = """You are a contract-review assistant embedded in a Microsoft Word task pane. The user is reading an open document; the document is attached below as the source of truth.

RULES:
- The attached document is the ONLY source. Do not invent facts, suggest external research, or call any tools.
- Answer conversationally in 2–5 sentences. No section headers like "Direct Answer", "Supporting Citations", "Open Gaps", or "Confidence Assessment".
- Cite specific section numbers or clause names inline (e.g., "Per Section 4, …").

PROPOSING EDITS (REQUIRED when the user asks for a change):
If the user asks you to change, rewrite, tighten, loosen, add, insert, remove, delete, fill, or redraft ANYTHING in the attached document, you MUST end your reply with one or more fenced ```json``` blocks describing the edit(s). This is NOT optional — the block is the ONLY way the change reaches the document.

Do NOT promise an edit in prose without emitting the block. The client reads ONLY the JSON blocks; if you say "I will replace X with Y" and emit no block, nothing happens, the user sees nothing change, and the request fails silently. ALWAYS emit the block — alongside your prose explanation, never instead of it.

WRONG (rejected — no block emitted):
  > "I will replace 'Signed by: [__]' with 'Signed by: John Doe' in two locations within the document."

RIGHT: include the fenced block(s) alongside your prose — see the worked example below.

ONE EDIT = ONE TARGET. Each change is its own edit object; emit several when several things change. Keep every target_text to a SINGLE field on a SINGLE line: never join two table columns into one target, and never span multiple rows. The client matches target_text literally with Word's body.search, which cannot reach across a tab between columns or a break between rows, so a bundled target fails silently. When the SAME exact string repeats and every copy should get the SAME value, use one replace_all block (see below) instead of enumerating positions.

SCOPE — change ONLY what the user asked for. Do not add edits the user did not request (e.g. "to keep it consistent" or to mirror a value you set on a previous turn), and do NOT overwrite a field that already holds a real value unless the user explicitly asks to change THAT value. "Fill" means putting a value into an EMPTY placeholder (e.g. [__], [Legal Name], [Date], [Address]) — it never means replacing text that is already filled in. If one side of a signature block (or any field) is already completed (e.g. the counterparty's signatory), leave it untouched.

Worked example — user says "tighten the liability cap to 2x":
Sure — here's a 2x cap for Section 5.
```json
{"action": "replace", "target_text": "shall be limited to the fees paid by Client in the 12 months preceding the relevant claim", "new_text": "shall be limited to two times (2x) the fees paid by Client in the 12 months preceding the relevant claim", "rationale": "Doubles the cap, keeps the 12-month period."}
```

Actions and required fields:
- "replace":     rewrite ONE specific occurrence. Needs "target_text" + "new_text". Use when the user is changing a single, uniquely-identifiable phrase.
- "replace_all": rewrite EVERY occurrence of an exact string to the SAME new text. Needs "target_text" + "new_text". Use it ONLY when every occurrence should become identical (e.g. "replace all [Year] with 2026"). The client loops body.search and replaces each match, so you don't enumerate positions.
- "insert":      add new text. Needs "anchor_text" + "position" ("after"|"before") + "new_text".
- "delete":      remove text. Needs "target_text".

replace_all applies ONE new_text to EVERY match, so its target must correspond to exactly ONE intended value (e.g. "[Year]" → "2026"). Do NOT replace_all a generic blank like "[__]" when different fields need different values — the same "[__]" stands for the name on one line and the title on another, so a single value cannot fill them correctly. In that case emit a separate "replace" for each field, targeting that field's own line (the label plus its blank).

The target_text / anchor_text MUST be copied VERBATIM from the attached document (exact words, punctuation, and casing) — the client searches for it literally, so paraphrasing breaks the match. Do NOT emit a block when the user is only asking a question (e.g. "why is this risky?")."""


# Structural, model-neutral note added when a governing MSA is attached on the
# chat path. Mirrors the review path's directive; SKILL.md stays the ceiling.
_CHAT_MSA_NOTE = (
    "The Master Services Agreement below GOVERNS this document. Ground any "
    "MSA-conflict answer in its actual text; if the MSA is silent on a point, say "
    "so rather than assuming. Do not invent MSA terms."
)

_agent_cache = {}
_llm_cache: dict[str, ChatOllama] = {}


def _build_llm() -> ChatOllama:
    """Build and cache a tool-less ChatOllama. Used for the doc-attached chat path."""
    if "chat" not in _llm_cache:
        settings = get_settings()
        _llm_cache["chat"] = ChatOllama(
            model=settings.llm_model,
            base_url=settings.ollama_base_url,
            temperature=0.0,
            reasoning=False,
            num_ctx=settings.ollama_num_ctx,
        )
    return _llm_cache["chat"]


def _build_json_llm() -> ChatOllama:
    """ChatOllama in JSON-output mode. Used for the edit-extraction retry path
    when the conversational LLM refused to emit a fenced JSON block. Ollama's
    `format='json'` parameter forces the response to be valid JSON — no prose,
    no markdown, no "I will replace…" hand-waving. More expensive than asking
    nicely but actually deterministic."""
    if "json" not in _llm_cache:
        settings = get_settings()
        _llm_cache["json"] = ChatOllama(
            model=settings.llm_model,
            base_url=settings.ollama_base_url,
            temperature=0.0,
            reasoning=False,
            format="json",
            num_ctx=settings.ollama_num_ctx,
        )
    return _llm_cache["json"]


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


_JSON_RETRY_SYSTEM = """You output ONE JSON object describing the edit(s) to apply to a document. No prose, no markdown, no fenced code blocks — just the JSON object.

Schema:
  {"edits": [<edit>, <edit>, ...]}

Each <edit> is one of:
  {"action": "replace",     "target_text": "...", "new_text": "..."}
  {"action": "replace_all", "target_text": "...", "new_text": "..."}
  {"action": "insert",      "anchor_text": "...", "position": "after"|"before", "new_text": "..."}
  {"action": "delete",      "target_text": "..."}

Every target_text must be a SINGLE field on a SINGLE line — never join table columns or span rows; a bundled target cannot be located and the edit fails silently.

replace_all applies ONE new_text to EVERY match, so use it only when every occurrence becomes identical (e.g. "[Year]" → "2026"); do NOT emit multiple replace blocks with the same target_text — use one replace_all instead. But never replace_all a generic blank like "[__]" when different fields need different values: emit a separate replace per field, each targeting that field's own line (label plus blank).

Scope: emit edits ONLY for what the user asked. Do not overwrite a field that already holds a real value; "fill" puts a value into an EMPTY placeholder (e.g. [__], [Legal Name]), never text that is already filled in."""


def _build_agent():
    """Build and cache the ReAct agent."""
    cache_key = "legal_research"
    if cache_key in _agent_cache:
        return _agent_cache[cache_key]

    settings = get_settings()
    llm = ChatOllama(
        model=settings.llm_model,
        base_url=settings.ollama_base_url,
        temperature=0.0,
    )

    tools = [search_legal, get_document, escalate]

    agent = create_react_agent(
        model=llm,
        tools=tools,
        prompt=RESEARCH_SYSTEM_PROMPT,
        name="legal_research_agent",
    )

    _agent_cache[cache_key] = agent
    return agent


def _extract_uploaded_text(state: LegalAgentState) -> str:
    """Extract contract text from uploaded_docs in state."""
    docs = state.get("uploaded_docs", [])
    if not docs:
        return ""
    parts = []
    for doc in docs:
        if isinstance(doc, dict):
            parts.append(doc.get("text", ""))
        elif hasattr(doc, "text"):
            parts.append(doc.text)
    return "\n\n".join(parts)


_JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)


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


def _strip_redlines_section(markdown: str) -> str:
    """Remove the 'Suggested Redlines / Fallbacks' section from review markdown.

    Finds the first heading (any level #–##) whose text contains
    "suggested redlines" (case-insensitive) and drops everything from that
    heading up to (but not including) the next heading of the same or higher
    level, or end-of-string if it is the last section. All other sections are
    preserved unchanged.

    Returns the markdown unchanged when no such section exists.
    """
    match = re.search(r"^(#{1,6})\s+.*suggested redlines.*$", markdown, re.IGNORECASE | re.MULTILINE)
    if not match:
        return markdown
    level = len(match.group(1))          # e.g. 1 for "#", 2 for "##"
    start = match.start()
    # Find the next heading at the same or higher level (fewer #s).
    next_heading = re.search(
        r"^#{1," + str(level) + r"}\s",
        markdown[match.end():],
        re.MULTILINE,
    )
    if next_heading:
        end = match.end() + next_heading.start()
    else:
        end = len(markdown)
    return markdown[:start] + markdown[end:]


# Placeholder reconciliation ---------------------------------------------------
# A backtick span qualifies as a placeholder quote if it contains any bracket
# token or underscore blank. A BARE bracket token is only treated as a
# placeholder when it is LABELED (starts with an uppercase letter, e.g.
# "[Legal Name]", "[Date]") — a generic blank like "[__]" is ambiguous across
# fields, so it is only ever considered inside its full backtick context.
_MARKER_IN_SPAN_RE = re.compile(r"\[[^\]]{0,40}\]|_{2,}")
_BARE_LABEL_RE = re.compile(r"\[[A-Z][^\]]{0,38}\]")
_SOURCE_TAG_RE = re.compile(r"\[\s*source\s*:", re.IGNORECASE)


def _normalize_for_match(text: str) -> str:
    """NFC + curly->straight quotes + nbsp/whitespace collapse, for tolerant
    substring matching between a review quote and the current document."""
    text = unicodedata.normalize("NFC", text)
    text = (
        text.replace("’", "'").replace("‘", "'")   # curly single quotes
        .replace("“", '"').replace("”", '"')       # curly double quotes
        .replace(" ", " ")                              # non-breaking space
    )
    return re.sub(r"\s+", " ", text).strip()


def _placeholder_candidates(review_markdown: str) -> list[str]:
    """Distinct placeholder strings quoted in the review: full backtick spans that
    carry a marker (e.g. `Signed by: [__]`) plus bare LABELED bracket tokens
    (e.g. [Legal Name]). Excludes generated-draft [Source: id] tags."""
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        s = raw.strip()
        if not s or s in seen or _SOURCE_TAG_RE.search(s):
            return
        seen.add(s)
        candidates.append(s)

    for m in re.finditer(r"`([^`]+)`", review_markdown):      # full field context
        span = m.group(1)
        if _MARKER_IN_SPAN_RE.search(span):
            _add(span)
    for m in _BARE_LABEL_RE.finditer(review_markdown):        # bare labeled tokens
        _add(m.group(0))
    return candidates


def _reconcile_review_with_doc(review_markdown: str, doc_text: str) -> tuple[str, list[str]]:
    """Drop placeholder findings the current doc proves are filled.

    Returns (reconciled_markdown, filled_tokens). A candidate is 'filled' when its
    normalized form is no longer a substring of the normalized document. A line is
    dropped only when EVERY placeholder it references is filled (a line still
    holding a live placeholder is kept); section headings are never dropped. When
    nothing is stale the input is returned unchanged with an empty list.
    """
    if not review_markdown or not doc_text:
        return review_markdown, []
    candidates = _placeholder_candidates(review_markdown)
    if not candidates:
        return review_markdown, []
    norm_doc = _normalize_for_match(doc_text)
    norm_cand = {c: _normalize_for_match(c) for c in candidates}
    filled = [c for c in candidates if norm_cand[c] not in norm_doc]
    if not filled:
        return review_markdown, []
    filled_set = set(filled)

    kept: list[str] = []
    for line in review_markdown.splitlines():
        if line.lstrip().startswith("#"):        # never drop section headings
            kept.append(line)
            continue
        norm_line = _normalize_for_match(line)
        refs = [c for c in candidates if norm_cand[c] in norm_line]
        if refs and all(c in filled_set for c in refs):
            continue                             # every placeholder here is filled -> drop
        kept.append(line)

    note = (
        f"> **Auto-reconciled:** {len(filled)} placeholder(s) flagged in the prior "
        f"review were filled in the document afterward and have been removed from the "
        f"recalled findings: {', '.join('`' + c + '`' for c in filled)}. If these were "
        f"the only signature-block blockers, re-review to confirm the No-Signature gate "
        f"now passes.\n\n"
    )
    return note + "\n".join(kept), filled


def _load_prior_review_block(state: LegalAgentState) -> str:
    """Latest stored review for this document, as a system block. Empty string
    when none exists. On a store-read failure, flags memory_degraded and returns
    empty — tracing/memory must never break the chat turn."""
    document_id = state.get("document_id", "")
    if not document_id:
        return ""
    try:
        latest = load_latest_review(get_settings().sqlite_path, document_id)
    except Exception as e:
        logger.error("[legal_research] prior-review load failed: %s", e)
        state["memory_degraded"] = True
        return ""
    if not latest:
        return ""
    review_text = _strip_redlines_section(latest["markdown"])
    return (
        "--- PRIOR REVIEW (most recent, this document) ---\n"
        "Answer recall questions from this review; do not re-derive or contradict it.\n\n"
        f"{review_text}\n"
        "--- END PRIOR REVIEW ---"
    )


def _load_prior_conversation(state: LegalAgentState) -> list[dict]:
    """Durable per-(document, attorney) chat history for the doc-chat prompt.
    Empty when disabled, keys missing, or on a store-read failure (which flags
    memory_degraded) — memory must never break the chat turn."""
    settings = get_settings()
    if not settings.conversation_store_enabled:
        return []
    document_id = state.get("document_id", "")
    attorney_id = state.get("user_id", "")
    if not document_id or not attorney_id:
        return []
    try:
        return load_recent(
            settings.sqlite_path, document_id, attorney_id,
            settings.conversation_max_messages,
        )
    except Exception as e:
        logger.error("[legal_research] prior-conversation load failed: %s", e)
        state["memory_degraded"] = True
        return []


_GROUNDING_TRIGGER_RE = re.compile(
    r"""
    # Edit / action stems
    chang|edit|modif|revis|rewrit|redraft|redline|amend|soften|tighten|loosen|
    strengthen|\bfill|insert|\badd\b|remov|delet|replac|updat|\bfix|draft|shorten|
    extend|adjust
    |
    # Position / judgment stems
    should|acceptab|standard|policy|playbook|fallback|position|\bmarket|allow|
    complian|\bcomply|\brisk|aggressiv|unusual|favorab|unfavorab|protect|\bweak|
    negotiat|pushback|concession|deviat
    |
    # Cross-doc / MSA stems
    \bmsa\b|master\s+service|\bparent\b|governing|precedenc|conflict|inconsist|
    overrid|incorporat|breach|subject\s+to
    |
    # Clause names — legal judgment calls
    indemn|liabilit|warrant|confidential|intellectual\s+property|\bip\b|ownership|
    terminat|jurisdiction|governing\s+law|non-compet|non-solicit|penalt|\bsla\b|
    service\s+level|\bcap\b|limitation
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _needs_grounding(question: str) -> bool:
    """True when a chat turn needs the firm playbook / governing MSA attached —
    i.e. it asks for an edit/redline, a firm position/standard, a cross-document
    (MSA) judgment, or names a clause whose treatment is a legal-judgment call.
    Biased toward True: a plain factual extraction ('who signs?', 'what is the
    effective date?') returns False and takes the lean, fast path. This is a
    zero-LLM heuristic — when in doubt it attaches (never under-grounds)."""
    return bool(_GROUNDING_TRIGGER_RE.search(question))


def _build_chat_grounding(state: LegalAgentState, uploaded_text: str) -> tuple[str, str]:
    """(playbook_bundle, msa_block) for the chat path. Empty strings on failure —
    grounding must never break the chat turn. MSA only for SOWs."""
    playbook = ""
    msa_block = ""
    try:
        contract_type, _ = detect_contract_type(uploaded_text)
        playbook = load_playbook_bundle(contract_type)
        if contract_type == "sow":
            client_id = (state.get("filters") or {}).get("client_id", "")
            parent = attach_parent_msa(uploaded_text, client_id, get_settings().msa_max_chars)
            if parent:
                title, msa_text = parent
                msa_block = (
                    f"{_CHAT_MSA_NOTE}\n\n--- GOVERNING MSA ({title}) ---\n"
                    f"{msa_text}\n--- END GOVERNING MSA ---"
                )
    except Exception as e:
        logger.warning("[legal_research] chat grounding failed: %s — answering ungrounded", e)
    return playbook, msa_block


def _cap_chat_context(messages: list[dict], uploaded_text: str, request: str) -> None:
    """If total assembled content exceeds the budget, truncate ONLY the document
    portion of the trailing user message — never the grounding. Mutates messages
    in place; marks + logs the truncation. Crude on purpose (Phase 3)."""
    budget = get_settings().chat_context_max_chars
    total = sum(len(m["content"]) for m in messages)
    if total <= budget:
        return
    overflow = total - budget
    keep = max(0, len(uploaded_text) - overflow - len("\n\n[document truncated for context budget]"))
    truncated_doc = uploaded_text[:keep] + "\n\n[document truncated for context budget]"
    messages[-1]["content"] = (
        f"--- ATTACHED DOCUMENT (the source of truth — answer from this) ---\n"
        f"{truncated_doc}\n"
        f"--- END ATTACHED DOCUMENT ---\n\n"
        f"User request: {request}"
    )
    logger.warning("[legal_research] chat context %d > budget %d — truncated document to %d chars",
                   total, budget, keep)


def _run_doc_chat(state: LegalAgentState, uploaded_text: str) -> tuple[str, list[dict]]:
    """In-Word chat path: direct ChatOllama with the attached doc, no tools.

    Returns (response, proposed_edits). Skipping the ReAct agent avoids the
    search_legal / get_document / escalate tool-call loops, which add minutes
    of latency on the local LLM for chats whose source is already the
    attached document.
    """
    request = state["request"]
    user_message = (
        f"--- ATTACHED DOCUMENT (the source of truth — answer from this) ---\n"
        f"{uploaded_text}\n"
        f"--- END ATTACHED DOCUMENT ---\n\n"
        f"User request: {request}"
    )
    attorney_notes = (state.get("attorney_notes") or "").strip()
    if attorney_notes:
        user_message += (
            f"\n\n--- ATTORNEY REVIEW NOTES (incorporate these changes) ---\n"
            f"{attorney_notes}"
        )

    review_block = _load_prior_review_block(state)
    attach = (not get_settings().chat_conditional_grounding) or _needs_grounding(request)
    if attach:
        playbook, msa_block = _build_chat_grounding(state, uploaded_text)
    else:
        playbook, msa_block = "", ""

    chat_history = _load_prior_conversation(state)
    if not chat_history:
        chat_history = state.get("chat_history", []) or []
    system_messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
    if playbook:
        system_messages.append({"role": "system", "content": playbook})
    if msa_block:
        system_messages.append({"role": "system", "content": msa_block})
    if review_block:
        system_messages.append({"role": "system", "content": review_block})
    messages: list[dict] = [
        *system_messages,        # stable across turns → cached prefix
        *chat_history,
        {"role": "user", "content": user_message},   # changes → trailing tokens
    ]

    _cap_chat_context(messages, uploaded_text, request)

    llm = _build_llm()
    response = traced_invoke(llm, messages, name="doc_chat")
    content = response.content if hasattr(response, "content") else str(response)
    edits = _extract_proposed_edits(content)

    # Retry path: when the model promised an edit in prose but forgot the
    # JSON block, ask again with Ollama's format='json' mode. The previous
    # "please emit a ```json``` block" retry was just another conversational
    # plea the LLM ignored. format='json' forces structurally-valid JSON
    # output — no more "I will replace…" hand-waving without action.
    if not edits and _looks_like_edit_promise(content):
        logger.info("[legal_research] edit-promise detected without block — retrying in JSON mode")
        json_llm = _build_json_llm()
        retry_user = (
            f"User request: {request}\n\n"
            f"--- ATTACHED DOCUMENT ---\n{uploaded_text}\n--- END ATTACHED DOCUMENT ---\n\n"
            f"Your previous prose answer (which forgot the JSON block):\n{content}\n\n"
            f"Now output the edits JSON for the change you described above."
        )
        retry_response = traced_invoke(
            json_llm,
            [
                {"role": "system", "content": _JSON_RETRY_SYSTEM},
                {"role": "user", "content": retry_user},
            ],
            name="doc_chat_json_retry",
        )
        retry_raw = (
            retry_response.content if hasattr(retry_response, "content") else str(retry_response)
        )
        retry_edits = _parse_json_edits(retry_raw)
        if retry_edits:
            edits = retry_edits
            logger.info("[legal_research] JSON-mode retry yielded %d edit(s)", len(edits))
        else:
            logger.warning(
                "[legal_research] JSON-mode retry produced no usable edits; raw=%r",
                retry_raw[:200],
            )

    return content, edits


def _run_kb_research(state: LegalAgentState) -> tuple[str, list[dict], set[str]]:
    """KB research path: ReAct agent with search_legal / get_document / escalate.

    Used when no document is attached and the user is asking a research
    question against the firm's RAG corpus. Returns (response, proposed_edits,
    source_doc_ids).
    """
    request = state["request"]
    filters = state.get("filters", {})
    client_id = filters.get("client_id", "internal")
    context_parts = [f"Question: {request}", f"Client ID: {client_id}"]
    if filters.get("jurisdiction"):
        context_parts.append(f"Jurisdiction: {filters['jurisdiction']}")
    user_message = "\n".join(context_parts)

    attorney_notes = (state.get("attorney_notes") or "").strip()
    if attorney_notes:
        user_message += (
            f"\n\n--- ATTORNEY REVIEW NOTES (incorporate these changes) ---\n"
            f"{attorney_notes}"
        )

    agent = _build_agent()
    chat_history = state.get("chat_history", []) or []
    agent_messages = [*chat_history, {"role": "user", "content": user_message}]
    result = traced_agent_invoke(agent, {"messages": agent_messages}, name="research_agent")

    messages = result.get("messages", [])
    content = ""
    if messages:
        last_msg = messages[-1]
        content = last_msg.content if hasattr(last_msg, "content") else str(last_msg)

    source_docs: set[str] = set()
    for msg in messages:
        msg_text = msg.content if hasattr(msg, "content") else str(msg)
        ids = re.findall(r"doc_id:\s*([a-f0-9-]+)", msg_text)
        source_docs.update(ids)

    return content, _extract_proposed_edits(content), source_docs


@observe(name="legal_research", capture_input=False, capture_output=False)
def legal_research(state: LegalAgentState) -> LegalAgentState:
    """Answer the user's request.

    Two paths:
      - Doc attached (Word add-in chat tab) → direct ChatOllama, no tools.
      - No doc → ReAct agent with KB search tools.
    """
    uploaded_text = _extract_uploaded_text(state)

    # Always reset proposed_edits at the start so a turn that produces no
    # edit block doesn't carry the prior turn's proposal forward.
    state["proposed_edits"] = []

    try:
        if uploaded_text:
            content, edits = _run_doc_chat(state, uploaded_text)
            state["llm_response"] = content
            state["proposed_edits"] = edits
            state["retrieved_chunks"] = []
            logger.info(
                "[legal_research] doc-chat completed, response=%d chars, edits=%d",
                len(content), len(edits),
            )
        else:
            content, edits, source_docs = _run_kb_research(state)
            state["llm_response"] = content or "Error: Agent returned no messages."
            state["proposed_edits"] = edits
            state["retrieved_chunks"] = [
                {"doc_id": did, "doc_title": f"Source {did[:8]}"}
                for did in source_docs
            ]
            logger.info(
                "[legal_research] kb-research completed, response=%d chars, sources=%d, edits=%d",
                len(content), len(source_docs), len(edits),
            )

    except Exception as e:
        logger.error("[legal_research] failed: %s", e)
        state["llm_response"] = f"Error: Legal research failed — {e}"
        state["proposed_edits"] = []

    return state
