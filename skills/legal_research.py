# skills/legal_research.py
"""Legal research — multi-hop retrieval ReAct agent."""

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


_agent_cache = {}


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


def legal_research(state: LegalAgentState) -> LegalAgentState:
    """Run the legal research ReAct agent.

    If an open document is attached via uploaded_docs (e.g. the Word add-in
    chat tab sending the active document on every turn), embed it in the
    user message so the agent answers from that context rather than relying
    solely on RAG search.
    """
    request = state["request"]
    filters = state.get("filters", {})
    client_id = filters.get("client_id", "internal")

    context_parts = [f"Question: {request}", f"Client ID: {client_id}"]
    if filters.get("jurisdiction"):
        context_parts.append(f"Jurisdiction: {filters['jurisdiction']}")

    user_message = "\n".join(context_parts)

    uploaded_text = _extract_uploaded_text(state)
    if uploaded_text:
        user_message += (
            f"\n\n--- ATTACHED DOCUMENT (the user is asking about THIS document; prefer it over RAG) ---\n"
            f"{uploaded_text}\n"
            f"--- END ATTACHED DOCUMENT ---\n\n"
            f"--- RESPONSE STYLE ---\n"
            f"This is an in-Word chat conversation. Answer conversationally in 2–5 sentences. "
            f"Do NOT emit section headers like 'Direct Answer', 'Supporting Citations', "
            f"'Open Gaps', or 'Confidence Assessment'. Cite specific section numbers or "
            f"clause names inline when relevant (e.g., 'Per Section 4, ...'). Skip the "
            f"structured research report format — that's reserved for explicit research "
            f"requests without an attached document."
        )

    attorney_notes = (state.get("attorney_notes") or "").strip()
    if attorney_notes:
        user_message += (
            f"\n\n--- ATTORNEY REVIEW NOTES (incorporate these changes) ---\n"
            f"{attorney_notes}"
        )

    try:
        agent = _build_agent()
        chat_history = state.get("chat_history", []) or []
        agent_messages = [*chat_history, {"role": "user", "content": user_message}]
        result = agent.invoke({"messages": agent_messages})

        messages = result.get("messages", [])
        if messages:
            last_msg = messages[-1]
            content = last_msg.content if hasattr(last_msg, "content") else str(last_msg)
            state["llm_response"] = content
        else:
            state["llm_response"] = "Error: Agent returned no messages."

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
            "[legal_research] agent completed, response=%d chars, sources=%d",
            len(state["llm_response"]), len(source_docs),
        )

    except Exception as e:
        logger.error("[legal_research] agent failed: %s", e)
        state["llm_response"] = f"Error: Legal research agent failed — {e}"

    return state
