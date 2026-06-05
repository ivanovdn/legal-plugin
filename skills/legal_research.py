# skills/legal_research.py
"""Legal research — direct ChatOllama for doc-attached chats, ReAct agent for KB research."""

import json
import logging
import re

from langchain_ollama import ChatOllama
from langgraph.prebuilt import create_react_agent

from config import get_settings
from graph.state import LegalAgentState
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
- "replace": rewrite existing text. Needs "target_text" + "new_text".
- "insert":  add new text. Needs "anchor_text" + "position" ("after"|"before") + "new_text".
- "delete":  remove text. Needs "target_text".

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

# Phrases that imply the model intends to make an edit. Mirrors the regex used
# in clients/word/src/components/ChatTab.tsx so backend retry logic and the UI
# warning fire on the same signal.
_EDIT_PROMISE_RE = re.compile(
    r"\bi['’]?(?:ll| will| am going to| have)\b[^.?!\n]*"
    r"\b(?:replace|insert|delete|fill|add|remove|change|rewrite|tighten|loosen|update|edit|modify|set)\b",
    re.IGNORECASE,
)


def _looks_like_edit_promise(prose: str) -> bool:
    """Heuristic: did the model claim it would make an edit (without emitting a block)?"""
    return bool(_EDIT_PROMISE_RE.search(prose or ""))


def _extract_proposed_edits(prose: str) -> list[dict]:
    """Pull fenced ```json``` blocks out of the agent's prose into structured edit proposals.

    Tolerant of malformed JSON — any block that fails to parse is skipped with a warning.
    The original prose is left untouched; the frontend strips blocks for display.
    """
    proposals: list[dict] = []
    for match in _JSON_BLOCK_RE.finditer(prose or ""):
        raw = match.group(1).strip()
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning("[legal_research] skipping malformed JSON block: %s", e)
            continue
        if isinstance(obj, dict) and obj.get("action") in {"replace", "insert", "delete"}:
            proposals.append(obj)
        else:
            logger.warning("[legal_research] JSON block missing/invalid action: %r", obj)
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
    response = llm.invoke(messages)
    content = response.content if hasattr(response, "content") else str(response)
    edits = _extract_proposed_edits(content)

    # Retry once if the model promised an edit in prose but forgot the JSON
    # block. The local LLM (qwen3.6) does this for multi-location requests
    # like "fill every Signed by: [__]" — it says "I will replace..." but
    # emits no block, so the change never reaches the document. A focused
    # follow-up prompt fixes it most of the time without changing the
    # conversational UX (the user sees the original prose plus a working
    # Apply card).
    if not edits and _looks_like_edit_promise(content):
        logger.info("[legal_research] edit-promise detected without block — retrying for JSON")
        retry_messages = [
            *messages,
            {"role": "assistant", "content": content},
            {"role": "user", "content": (
                "You described an edit but didn't emit the required ```json``` block, "
                "so the change can't reach the document. Output ONLY the fenced "
                "```json``` block(s) for the edit you described — no prose, no "
                "preamble, no closing remark. If the same placeholder text appears "
                "in multiple locations, emit one block PER LOCATION and include "
                "surrounding context in each target_text so the matches are unique."
            )},
        ]
        retry_response = llm.invoke(retry_messages)
        retry_content = (
            retry_response.content if hasattr(retry_response, "content") else str(retry_response)
        )
        retry_edits = _extract_proposed_edits(retry_content)
        if retry_edits:
            # Keep the original prose as the user-facing answer; append the
            # retry's blocks so _extract_proposed_edits on the combined text
            # picks them up (the frontend strips blocks for display anyway).
            content = f"{content}\n\n{retry_content}"
            edits = retry_edits
            logger.info("[legal_research] retry yielded %d block(s)", len(edits))
        else:
            logger.warning("[legal_research] retry also produced no edit block")

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
    result = agent.invoke({"messages": agent_messages})

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
