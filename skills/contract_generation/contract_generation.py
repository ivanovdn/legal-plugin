# skills/contract_generation/contract_generation.py
"""Contract generation — ReAct agent that searches case history and generates contracts.
Prompt loaded from SKILL.md (editable by legal team)."""

import logging
import re
from pathlib import Path

from langchain_ollama import ChatOllama
from langgraph.prebuilt import create_react_agent

from config import get_settings
from graph.state import LegalAgentState
from rag.tools.search_legal import search_legal
from rag.tools.get_document import get_document
from rag.tools.extract_clauses import extract_clauses
from rag.tools.escalate import escalate
from skills.base import load_skill_prompt

logger = logging.getLogger(__name__)

_SKILL_DIR = Path(__file__).parent


_agent_cache = {}


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


def contract_generation(state: LegalAgentState) -> LegalAgentState:
    """Run the contract generation ReAct agent."""
    request = state["request"]
    filters = state.get("filters", {})
    client_id = filters.get("client_id", "internal")
    jurisdiction = filters.get("jurisdiction", "")

    context_parts = [f"Request: {request}"]
    context_parts.append(f"Client ID: {client_id}")
    if jurisdiction:
        context_parts.append(f"Jurisdiction: {jurisdiction}")

    user_message = "\n".join(context_parts)

    try:
        agent = _build_agent()
        result = agent.invoke({"messages": [{"role": "user", "content": user_message}]})

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
