# skills/contract_generation.py
"""Contract generation — ReAct agent that searches case history and generates contracts."""

import logging
import re

from langchain_ollama import ChatOllama
from langgraph.prebuilt import create_react_agent

from config import get_settings
from graph.state import LegalAgentState
from rag.tools.search_legal import search_legal
from rag.tools.get_document import get_document
from rag.tools.extract_clauses import extract_clauses
from rag.tools.escalate import escalate

logger = logging.getLogger(__name__)

CONTRACT_GEN_SYSTEM_PROMPT = """You are a contract generation agent for an internal legal team. Your job is to generate a new contract based on historical signed contracts and templates.

PROCESS:
1. Search for relevant existing contracts using search_legal (collection="legal_docs", doc_type="contract")
2. If case history is available, extract clause patterns using extract_clauses for key clause types (indemnification, termination, payment, liability, confidentiality)
3. Use get_document to retrieve full text of the most relevant source contracts
4. Generate the new contract incorporating patterns from historical contracts
5. Flag any deviations — clause types that appear in the request but have no historical pattern

RULES:
- Always filter by client_id — never use another client's contracts
- Cite every source contract by doc_id
- If you cannot find enough source material, use escalate to flag for attorney review
- The generated contract must be complete and ready for attorney review
- Use formal legal language appropriate to the jurisdiction

OUTPUT FORMAT:
After gathering all information, produce the complete contract text. Start with the contract title, then parties, recitals, and all clauses. End with signature blocks.

IMPORTANT: You are generating a DRAFT for attorney review. This will always go through human review before delivery."""


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

    agent = create_react_agent(
        model=llm,
        tools=tools,
        prompt=CONTRACT_GEN_SYSTEM_PROMPT,
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
