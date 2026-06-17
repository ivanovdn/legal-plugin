# skills/legal_research.py
"""Legal research — direct ChatOllama for doc-attached chats, ReAct agent for KB research."""

import json
import logging
import re

from langchain_ollama import ChatOllama
from langfuse.decorators import observe
from langgraph.prebuilt import create_react_agent

from config import get_settings
from graph.state import LegalAgentState
from observability.tracing import traced_invoke, traced_agent_invoke
from rag.tools.search_legal import search_legal
from rag.tools.get_document import get_document
from rag.tools.escalate import escalate

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

RIGHT (one block per location):
  > Filling both blank signatures with John Doe:
  > ```json
  > {"action": "replace", "target_text": "Signed by: [__]\\tSigned by: Boris Bukengolts", "new_text": "Signed by: John Doe\\tSigned by: Boris Bukengolts", "rationale": "Fills the disclosing-party signature placeholder."}
  > ```

MULTIPLE LOCATIONS — important: the client uses Word's body.search and replaces only the FIRST match per block. If the same placeholder text appears N times and the user wants all of them filled, emit N separate replace blocks whose target_text strings are each made unique by including the surrounding context (a neighbouring word, the line above, the column separator). Each block must target ONE specific occurrence.

Worked example — user says "tighten the liability cap to 2x":
Sure — here's a 2x cap for Section 5.
```json
{"action": "replace", "target_text": "shall be limited to the fees paid by Client in the 12 months preceding the relevant claim", "new_text": "shall be limited to two times (2x) the fees paid by Client in the 12 months preceding the relevant claim", "rationale": "Doubles the cap, keeps the 12-month period."}
```

Actions and required fields:
- "replace":     rewrite ONE specific occurrence. Needs "target_text" + "new_text". Use when the user is changing a single, uniquely-identifiable phrase.
- "replace_all": rewrite EVERY occurrence of an exact short string. Needs "target_text" + "new_text". USE THIS for "every", "all", or "each" requests (e.g. "fill every blank Signed by: [__]", "replace all [Year] placeholders"). The client loops body.search and replaces each match in turn — you don't need to identify positions or emit one block per location.
- "insert":      add new text. Needs "anchor_text" + "position" ("after"|"before") + "new_text".
- "delete":      remove text. Needs "target_text".

For replace_all, target_text should be the SHORTEST UNIQUE PLACEHOLDER string (e.g. "Signed by: [__]", "[Year]", "[Legal Name]"). Don't include surrounding context — the whole point of replace_all is that the same string appears multiple times.

The target_text / anchor_text MUST be copied VERBATIM from the attached document (exact words, punctuation, and casing) — the client searches for it literally, so paraphrasing breaks the match. Do NOT emit a block when the user is only asking a question (e.g. "why is this risky?")."""


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
        )
    return _llm_cache["json"]


def _parse_json_edits(raw: str) -> list[dict]:
    """Pull edit dicts out of a free-form JSON response.

    Accepts three shapes Ollama's format=json mode actually produces:
      {"edits": [{...}, {...}]}       (preferred wrapping)
      [{...}, {...}]                   (bare array)
      {"action": "replace", ...}       (single bare edit)
    """
    parsed = _tolerant_json_loads(raw)
    if parsed is None:
        return []
    if isinstance(parsed, list):
        candidates = parsed
    elif isinstance(parsed, dict):
        if isinstance(parsed.get("edits"), list):
            candidates = parsed["edits"]
        elif parsed.get("action") in {"replace", "insert", "delete"}:
            candidates = [parsed]
        else:
            return []
    else:
        return []
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

For "every X" / "all X" requests, USE replace_all with the shortest unique placeholder text as target_text — the client iterates body.search and replaces each occurrence. Do NOT emit multiple replace blocks with the same target_text; use one replace_all block instead."""


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

    A block can contain a single edit object OR an array of edits — the LLM
    sometimes consolidates a multi-location request into one fenced block with
    an array (e.g. ```json [{...}, {...}] ```). Both shapes are accepted.

    Tolerant of malformed JSON — any block that fails to parse is skipped with
    a warning. The original prose is left untouched; the frontend strips blocks
    for display.
    """
    proposals: list[dict] = []
    for match in _JSON_BLOCK_RE.finditer(prose or ""):
        raw = match.group(1).strip()
        obj = _tolerant_json_loads(raw)
        if obj is None:
            logger.warning("[legal_research] skipping malformed JSON block: %r", raw[:120])
            continue
        candidates = obj if isinstance(obj, list) else [obj]
        for c in candidates:
            if isinstance(c, dict) and c.get("action") in _VALID_ACTIONS:
                proposals.append(c)
            else:
                logger.warning("[legal_research] edit entry missing/invalid action: %r", c)
    return proposals


def _run_doc_chat(state: LegalAgentState, uploaded_text: str) -> tuple[str, list[dict]]:
    """In-Word chat path: direct ChatOllama with the attached doc, no tools.

    Returns (response, proposed_edits). Skipping the ReAct agent avoids the
    search_legal / get_document / escalate tool-call loops, which add minutes
    of latency on the local LLM for chats whose source is already the
    attached document.
    """
    request = state["request"]
    user_message = (
        f"User request: {request}\n\n"
        f"--- ATTACHED DOCUMENT (the source of truth — answer from this) ---\n"
        f"{uploaded_text}\n"
        f"--- END ATTACHED DOCUMENT ---"
    )
    attorney_notes = (state.get("attorney_notes") or "").strip()
    if attorney_notes:
        user_message += (
            f"\n\n--- ATTORNEY REVIEW NOTES (incorporate these changes) ---\n"
            f"{attorney_notes}"
        )

    chat_history = state.get("chat_history", []) or []
    messages: list[dict] = [
        {"role": "system", "content": CHAT_SYSTEM_PROMPT},
        *chat_history,
        {"role": "user", "content": user_message},
    ]

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
