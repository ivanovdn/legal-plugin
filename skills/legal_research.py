# skills/legal_research.py
"""Legal research — multi-hop retrieval ReAct agent."""

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


_JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)


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
            f"requests without an attached document.\n\n"
            f"--- PROPOSING EDITS (REQUIRED when the user asks for a change) ---\n"
            f"If the user asks you to change, rewrite, tighten, loosen, add, insert, remove, "
            f"delete, or redraft ANYTHING in the attached document, you MUST end your reply "
            f"with a fenced ```json``` block describing the edit. This is NOT optional — the "
            f"block is the ONLY way the change reaches the document. If you describe a change "
            f"in prose but omit the block, nothing happens and the user is stuck. Always emit "
            f"the block, even alongside your prose explanation. One block per edit.\n\n"
            f"Worked example — user says \"tighten the liability cap to 2x\":\n"
            f"Sure — here's a 2x cap for Section 5.\n"
            f"```json\n"
            f'{{"action": "replace", "target_text": "shall be limited to the fees paid by '
            f'Client in the 12 months preceding the relevant claim", "new_text": "shall be '
            f'limited to two times (2x) the fees paid by Client in the 12 months preceding '
            f'the relevant claim", "rationale": "Doubles the cap, keeps the 12-month period."}}\n'
            f"```\n\n"
            f"Actions and required fields:\n"
            f'- "replace": rewrite existing text. Needs "target_text" + "new_text".\n'
            f'- "insert":  add new text. Needs "anchor_text" + "position" ("after"|"before") '
            f'+ "new_text".\n'
            f'- "delete":  remove text. Needs "target_text".\n'
            f"The target_text / anchor_text MUST be copied VERBATIM from the attached document "
            f"(exact words, punctuation, and casing) — the client searches for it literally, "
            f"so paraphrasing breaks the match. Do NOT emit a block when the user is only "
            f"asking a question (e.g. 'why is this risky?')."
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
            state["proposed_edits"] = _extract_proposed_edits(content)
        else:
            state["llm_response"] = "Error: Agent returned no messages."
            state["proposed_edits"] = []

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
            "[legal_research] agent completed, response=%d chars, sources=%d, edits=%d",
            len(state["llm_response"]), len(source_docs),
            len(state.get("proposed_edits", [])),
        )

    except Exception as e:
        logger.error("[legal_research] agent failed: %s", e)
        state["llm_response"] = f"Error: Legal research agent failed — {e}"
        state["proposed_edits"] = []

    return state
