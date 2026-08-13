# skills/legal_research.py
"""Legal research — direct ChatOllama for doc-attached chats, ReAct agent for KB research."""

import logging
import re

from langchain_ollama import ChatOllama
from langgraph.prebuilt import create_react_agent

from config import get_settings
from graph.state import LegalAgentState
from observability.spans import traced
from observability.tracing import traced_invoke, traced_agent_invoke
from rag.tools.search_legal import search_legal
from rag.tools.get_document import get_document
from rag.tools.escalate import escalate
from skills.grounding import preferences_block_for_state
from skills.legal_research.context import (
    _build_chat_grounding,
    _cap_chat_context,
    _load_prior_conversation,
    _load_prior_review_block,
    _needs_grounding,
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
