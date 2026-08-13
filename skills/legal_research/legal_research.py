# skills/legal_research.py
"""Legal research — direct ChatOllama for doc-attached chats, ReAct agent for KB research."""

import logging
import re
import unicodedata

from langchain_ollama import ChatOllama
from langgraph.prebuilt import create_react_agent

from config import get_settings
from graph.state import LegalAgentState
from memory.conversation_store import load_recent
from memory.review_store import load_latest_review
from observability.spans import traced
from observability.tracing import traced_invoke, traced_agent_invoke
from rag.tools.search_legal import search_legal
from rag.tools.get_document import get_document
from rag.tools.escalate import escalate
from skills.grounding import (
    attach_parent_msa,
    detect_contract_type,
    load_playbook_bundle,
    preferences_block_for_state,
)
from skills.legal_research.edit_parsing import (
    _extract_proposed_edits,
    _extract_proposed_preferences,
    _looks_like_edit_promise,
    _parse_json_edits,
)
from skills.legal_research.prompts import (
    CHAT_SYSTEM_PROMPT,
    RESEARCH_SYSTEM_PROMPT,
    _CHAT_MSA_NOTE,
    _JSON_RETRY_SYSTEM,
)

logger = logging.getLogger(__name__)


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
        # Only single-marker spans are droppable. A span bundling >=2 markers
        # (the MSA/SOW `Signed by: [__] / Title: [__] / ...` shape) can't be
        # reconciled atomically — a partial fill would make the whole span vanish
        # from the doc and wrongly drop a still-live blocker. Leave those intact.
        if len(_MARKER_IN_SPAN_RE.findall(span)) == 1:
            _add(span)
    for m in _BARE_LABEL_RE.finditer(review_markdown):        # bare labeled tokens
        _add(m.group(0))
    return candidates


def _reconcile_review_with_doc(review_markdown: str, doc_text: str) -> tuple[str, list[str]]:
    """Drop placeholder findings the current doc proves are filled.

    Returns (reconciled_markdown, dropped_tokens) — the tokens whose finding rows
    were actually removed (drives the reconciliation note; empty when nothing was
    dropped, in which case the input is returned unchanged). A candidate is 'filled'
    when its normalized form is no longer a substring of the normalized document. A
    line is dropped only when EVERY placeholder it references is filled (a line still
    holding a live placeholder is kept); section headings are never dropped. Only
    single-marker backtick spans are droppable — a span bundling several markers is
    left intact, so a partial fill can never drop a still-live blocker.
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
    dropped_tokens: list[str] = []
    seen_dropped: set[str] = set()
    in_gate = False
    for line in review_markdown.splitlines():
        if _HEADING_RE.match(line):        # never drop section headings
            in_gate = bool(_GATE_HEADING_RE.match(line))  # gate owned by _reconcile_gate_verdict
            kept.append(line)
            continue
        if in_gate:                              # gate reconciled separately; never row-drop it
            kept.append(line)
            continue
        norm_line = _normalize_for_match(line)
        refs = [c for c in candidates if norm_cand[c] in norm_line]
        # Most-specific-wins on a line: ignore a ref that is a proper substring of
        # another ref present on the same line, so a bare label ([Legal Name]) can't
        # out-vote its own filled span (`Landlord: [Legal Name]`) and keep a row that
        # was in fact filled. Without this, a label recurring across two contexts
        # (two parties / two signature blocks) would never reconcile.
        specific = [
            c for c in refs
            if not any(
                c != other
                and norm_cand[c] != norm_cand[other]
                and norm_cand[c] in norm_cand[other]
                for other in refs
            )
        ]
        if specific and all(c in filled_set for c in specific):
            for c in specific:                   # every placeholder here is filled -> drop
                if c not in seen_dropped:
                    seen_dropped.add(c)
                    dropped_tokens.append(c)
            continue
        kept.append(line)

    if not dropped_tokens:                        # filled candidates but no row removed
        return review_markdown, []                # -> no change, no over-claiming note

    body = _reconcile_gate_verdict("\n".join(kept), dropped_tokens)
    note = (
        f"> **Auto-reconciled:** {len(dropped_tokens)} placeholder(s) flagged in the "
        f"prior review were filled in the document afterward and have been removed from "
        f"the recalled findings: {', '.join('`' + c + '`' for c in dropped_tokens)}.\n\n"
    )
    return note + body, dropped_tokens


# Gate-verdict reconciliation --------------------------------------------------
# After placeholder rows are dropped, the "No Signature Checklist Result" gate can
# still cite the now-filled placeholders. _surviving_blocker_count reads the
# structured Key Findings table (the source of truth for blockers, mirroring the
# Word parser's deriveBlockers) to decide whether any substantive blocker remains.
_KEY_FINDINGS_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*key\s+findings\s*$", re.IGNORECASE)
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")   # any GFM heading (section boundary)
_BLOCKER_RATINGS = {"red", "missing context", "missing-context", "missing_context"}


def _surviving_blocker_count(review_markdown: str) -> int | None:
    """Count Key Findings rows rated Red or Missing Context. Returns None when the
    Key Findings table or its rating column can't be located/parsed — callers treat
    None as 'blockers may remain' (conservative: no gate downgrade). Runs on the
    row-reconciled markdown, so already-dropped placeholder rows never inflate it."""
    lines = review_markdown.splitlines()
    start = None
    for i, line in enumerate(lines):
        if _KEY_FINDINGS_HEADING_RE.match(line):
            start = i + 1
            break
    if start is None:
        return None
    rows: list[str] = []
    for line in lines[start:]:
        if _HEADING_RE.match(line):          # next section heading -> stop
            break
        if line.strip().startswith("|"):
            rows.append(line)
    if not rows:
        return None
    header = [c.strip().lower() for c in rows[0].strip().strip("|").split("|")]
    rating_idx = next((i for i, c in enumerate(header) if c in ("rating", "risk")), None)
    if rating_idx is None:
        return None
    count = 0
    for row in rows[1:]:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if len(cells) <= rating_idx:
            continue
        # Strip emphasis markup (**Red**, `Red`, _Red_) before matching, like the
        # Word parser's normalizeRisk. `.strip("_")` removes only SURROUNDING
        # underscores (italic wraps) — internal ones survive, so the
        # "missing_context" spelling in _BLOCKER_RATINGS is preserved (a naive
        # .replace("_","") would collapse it to "missingcontext").
        cell = cells[rating_idx].lower().replace("*", "").replace("`", "").strip().strip("_")
        if not cell or set(cell) <= {"-", ":", " "}:     # separator row (---, :---:)
            continue
        if cell in _BLOCKER_RATINGS:
            count += 1
    return count


_GATE_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*no[\s-]*signature\s+checklist", re.IGNORECASE)
_OVERALL_STATUS_RE = re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*)?\s*overall status\s*:?", re.IGNORECASE)
_GATE_NEUTRAL_STATUS = (
    "Overall status: PENDING RE-REVIEW — the placeholder blockers recorded here were "
    "filled after this review and no other blockers remain; re-review to confirm "
    "signature readiness. (Do not treat as approved for signature.)"
)


def _reconcile_gate_verdict(review_markdown: str, dropped_tokens: list[str]) -> str:
    """Reconcile the No-Signature gate section after placeholder rows were dropped.

    Gate absent, or citing none of the dropped tokens -> unchanged. Otherwise insert
    a correction note naming the filled tokens (annotate), and — ONLY when zero Key
    Findings rows survive rated Red/Missing Context — rewrite 'Overall status:' to
    PENDING RE-REVIEW (neutralize). Never writes a signature go-ahead; never deletes
    the Blocking items text."""
    if not dropped_tokens:
        return review_markdown
    lines = review_markdown.splitlines()
    gate_start = next(
        (i for i, ln in enumerate(lines) if _GATE_HEADING_RE.match(ln)), None
    )
    if gate_start is None:
        return review_markdown
    gate_end = len(lines)
    for j in range(gate_start + 1, len(lines)):
        if _HEADING_RE.match(lines[j]):      # next section heading
            gate_end = j
            break
    norm_body = _normalize_for_match("\n".join(lines[gate_start + 1:gate_end]))
    cited = [t for t in dropped_tokens if _normalize_for_match(t) in norm_body]
    if not cited:
        return review_markdown

    neutralize = _surviving_blocker_count(review_markdown) == 0
    note = (
        "> **Reconciled:** placeholder blocker(s) cited in this gate — "
        + ", ".join("`" + t + "`" for t in cited)
        + " — were filled in the current document after this review; treat them as "
        "resolved. The current document governs the gate."
    )
    section = lines[gate_start:gate_end]
    if neutralize:
        for k in range(1, len(section)):
            if _OVERALL_STATUS_RE.match(section[k]):
                section[k] = _GATE_NEUTRAL_STATUS
                break
    section = [section[0], note] + section[1:]
    return "\n".join(lines[:gate_start] + section + lines[gate_end:])


def _load_prior_review_block(state: LegalAgentState, uploaded_text: str) -> str:
    """Latest stored review for this document, as a system block. Empty string
    when none exists. On a store-read failure, flags memory_degraded and returns
    empty — tracing/memory must never break the chat turn.

    Reconciles the recalled review against the current document (uploaded_text):
    placeholder findings the document proves were filled after the review are
    dropped, so chat does not report an already-filled field as unfilled. A
    reconciliation error injects the review unchanged (never fails the turn) and
    does NOT flag memory_degraded — that is reserved for real store failures."""
    document_id = state.get("document_id", "")
    if not document_id:
        return ""
    try:
        latest = load_latest_review(document_id)
    except Exception as e:
        logger.error("[legal_research] prior-review load failed: %s", e)
        state["memory_degraded"] = True
        return ""
    if not latest:
        return ""
    review_text = _strip_redlines_section(latest["markdown"])
    if uploaded_text:
        try:
            review_text, _filled = _reconcile_review_with_doc(review_text, uploaded_text)
        except Exception as e:
            logger.warning(
                "[legal_research] review reconciliation failed: %s — injecting review unchanged", e
            )
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
            document_id, attorney_id, settings.conversation_max_messages,
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

    review_block = _load_prior_review_block(state, uploaded_text)
    attach = (not get_settings().chat_conditional_grounding) or _needs_grounding(request)
    if attach:
        playbook, msa_block = _build_chat_grounding(state, uploaded_text)
    else:
        playbook, msa_block = "", ""

    chat_history = _load_prior_conversation(state)
    if not chat_history:
        chat_history = state.get("chat_history", []) or []
    system_messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
    prefs_block = preferences_block_for_state(state)
    if prefs_block:                     # early → subordinate to playbook/review (ceiling intact)
        system_messages.append({"role": "system", "content": prefs_block})
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


@traced("legal_research")
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
    state["proposed_preferences"] = []

    try:
        if uploaded_text:
            content, edits = _run_doc_chat(state, uploaded_text)
            state["llm_response"] = content
            state["proposed_edits"] = edits
            state["proposed_preferences"] = _extract_proposed_preferences(content)
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
