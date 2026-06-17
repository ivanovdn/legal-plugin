# skills/contract_generation/contract_generation.py
"""Contract generation — ReAct agent that searches case history and generates contracts.
Prompt loaded from SKILL.md (editable by legal team)."""

import logging
import re
from pathlib import Path

from langchain_ollama import ChatOllama
from langfuse.decorators import observe
from langgraph.prebuilt import create_react_agent

from config import get_settings
from graph.state import LegalAgentState
from observability.tracing import traced_invoke, traced_agent_invoke
from rag.tools.search_legal import search_legal
from rag.tools.get_document import get_document
from rag.tools.extract_clauses import extract_clauses
from rag.tools.escalate import escalate
from skills.base import load_skill_prompt

logger = logging.getLogger(__name__)

_SKILL_DIR = Path(__file__).parent


_REVISE_SYSTEM_PROMPT = """You are a legal contract editor revising an existing draft.

Apply the attorney's revision notes to the PREVIOUS DRAFT. Preserve:
- Section headers and numbering
- Source citations like [Source: svc_xxx_yyyy]
- The DEVIATION REPORT block at the end
- Overall formatting and structure

Return ONLY the revised draft text — no commentary, no preamble, no markdown fences."""


_agent_cache = {}


def _revise_existing_draft(
    state: LegalAgentState, previous_draft: str, attorney_notes: str
) -> LegalAgentState:
    """Single LLM call to revise the previous draft per attorney notes.

    Skips the ReAct agent because we already have the draft text; no retrieval
    or tool use is needed for a targeted edit. Much faster than re-running the
    full agent loop on loop-back.
    """
    settings = get_settings()
    llm = ChatOllama(
        model=settings.llm_model,
        base_url=settings.ollama_base_url,
        temperature=0.0,
        reasoning=False,
    )
    user_message = (
        f"--- PREVIOUS DRAFT ---\n{previous_draft}\n\n"
        f"--- ATTORNEY REVISION NOTES ---\n{attorney_notes}\n\n"
        f"Apply the notes and return the revised draft."
    )
    try:
        result = traced_invoke(
            llm,
            [
                {"role": "system", "content": _REVISE_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            name="revise_draft",
        )
        content = result.content if hasattr(result, "content") else str(result)
        state["llm_response"] = content
        logger.info(
            "[contract_generation] revision via direct LLM, response=%d chars",
            len(content),
        )
    except Exception as e:
        logger.error("[contract_generation] revision failed: %s", e)
        state["llm_response"] = f"Error: Contract revision failed — {e}"
    return state


def _build_agent():
    """Build and cache the ReAct agent."""
    cache_key = "contract_gen"
    if cache_key in _agent_cache:
        return _agent_cache[cache_key]

    settings = get_settings()
    llm = ChatOllama(
        model=settings.llm_model,
        base_url=settings.ollama_base_url,
        temperature=0.0,
    )

    tools = [search_legal, get_document, extract_clauses, escalate]
    prompt = load_skill_prompt(_SKILL_DIR)

    agent = create_react_agent(
        model=llm,
        tools=tools,
        prompt=prompt,
        name="contract_generation_agent",
    )

    _agent_cache[cache_key] = agent
    return agent


@observe(name="contract_generation", capture_input=False, capture_output=False)
def contract_generation(state: LegalAgentState) -> LegalAgentState:
    """Run the contract generation ReAct agent.

    On loop-back (previous_draft + attorney_notes set), bypass the ReAct agent
    entirely and do a single direct LLM revision call — far faster than re-running
    multi-step tool calls when we already have the draft and just need edits.
    """
    request = state["request"]
    filters = state.get("filters", {})
    client_id = filters.get("client_id", "internal")
    jurisdiction = filters.get("jurisdiction", "")

    attorney_notes = (state.get("attorney_notes") or "").strip()
    previous_draft = (state.get("previous_draft") or "").strip()

    if attorney_notes and previous_draft:
        return _revise_existing_draft(state, previous_draft, attorney_notes)

    context_parts = [f"Request: {request}"]
    context_parts.append(f"Client ID: {client_id}")
    if jurisdiction:
        context_parts.append(f"Jurisdiction: {jurisdiction}")

    user_message = "\n".join(context_parts)

    if attorney_notes:
        user_message += (
            f"\n\n--- ATTORNEY REVIEW NOTES (incorporate these changes) ---\n"
            f"{attorney_notes}"
        )

    try:
        agent = _build_agent()
        chat_history = state.get("chat_history", []) or []
        agent_messages = [*chat_history, {"role": "user", "content": user_message}]
        result = traced_agent_invoke(agent, {"messages": agent_messages}, name="contract_generation_agent")

        messages = result.get("messages", [])
        if messages:
            last_msg = messages[-1]
            content = last_msg.content if hasattr(last_msg, "content") else str(last_msg)
            state["llm_response"] = content
        else:
            state["llm_response"] = "Error: Agent returned no messages."

        # Collect source doc_ids from tool call results
        source_docs = set()
        for msg in messages:
            msg_text = msg.content if hasattr(msg, "content") else str(msg)
            ids = re.findall(r"doc_id:\s*([a-f0-9-]+)", msg_text)
            source_docs.update(ids)

        state["retrieved_chunks"] = [
            {"doc_id": did, "doc_title": f"Source {did[:8]}"}
            for did in source_docs
        ]

        logger.info(
            "[contract_generation] agent completed, response=%d chars, sources=%d",
            len(state["llm_response"]), len(source_docs),
        )

    except Exception as e:
        logger.error("[contract_generation] agent failed: %s", e)
        state["llm_response"] = f"Error: Contract generation agent failed — {e}"

    return state
