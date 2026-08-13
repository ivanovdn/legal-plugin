# graph/nodes/llm_caller.py
"""LLM caller — sends prompt + retrieved context to Ollama."""

import logging

import httpx

from config import get_settings
from graph.state import LegalAgentState
from observability.spans import traced, set_gen_attributes
from observability.tracing import ollama_usage

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM_PROMPT = """You are a legal assistant for an internal legal team. Answer the user's request using ONLY the provided context. For every claim, cite the source document (doc_title and doc_id). If the context is insufficient, say so explicitly — do not fabricate information."""

# Task types whose prompt must NOT carry conversational history.
#
# A document review has to be deterministic in its inputs: the same document
# must yield the same findings regardless of what was said in the chat tab.
# Observed otherwise — an unrelated doc-chat turn suppressed a signature-block
# finding and cut the review from 3 Missing Context items to 1.
#
# The conversational skills (drafting, compliance_check, contract_generation)
# keep the injection — multi-turn continuity is the point there. legal_research
# never reaches this node (it sets llm_response itself) and does its own
# history handling via memory/conversation_store.
_HISTORY_FREE_TASK_TYPES = frozenset({"contract_review"})


def _build_context(chunks: list[dict]) -> str:
    """Format retrieved chunks as numbered context."""
    if not chunks:
        return "No documents retrieved."
    parts = []
    for i, c in enumerate(chunks, 1):
        parts.append(
            f"[{i}] {c.get('doc_title', 'Unknown')} (doc_id: {c.get('doc_id', '?')})\n"
            f"{c.get('text', '')}"
        )
    return "\n\n---\n\n".join(parts)


@traced("llm_caller", kind="LLM")
def llm_caller(state: LegalAgentState) -> LegalAgentState:
    """Call Ollama with context + request. temperature=0.0 always."""
    if state.get("llm_response") and not state.get("messages"):
        logger.info("[llm_caller] llm_response already set by agent — skipping")
        return state

    settings = get_settings()
    chunks = state.get("retrieved_chunks", [])
    context = _build_context(chunks)

    skill_messages = state.get("messages", [])
    task_type = state.get("task_type", "")
    chat_history = state.get("chat_history", []) or []

    if chat_history and task_type in _HISTORY_FREE_TASK_TYPES:
        logger.info(
            "[llm_caller] task_type=%s — suppressing %d chat_history message(s)",
            task_type, len(chat_history),
        )
        chat_history = []

    if skill_messages:
        base = list(skill_messages)
        if base and base[-1]["role"] == "user":
            base[-1] = {
                "role": "user",
                "content": f"Context:\n{context}\n\n{base[-1]['content']}",
            }
    else:
        base = [
            {"role": "system", "content": _DEFAULT_SYSTEM_PROMPT},
            {"role": "user", "content": f"Context:\n{context}\n\nRequest: {state['request']}"},
        ]

    # Inject chat_history between the system message (if any) and the rest.
    if base and base[0].get("role") == "system":
        messages = [base[0], *chat_history, *base[1:]]
    else:
        messages = [*chat_history, *base]

    try:
        response = httpx.post(
            f"{settings.ollama_base_url}/api/chat",
            json={
                "model": settings.llm_model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0.0, "num_ctx": settings.ollama_num_ctx},
            },
            timeout=600.0,
        )
        response.raise_for_status()
        data = response.json()
        content = data["message"]["content"]
        state["llm_response"] = content

        set_gen_attributes(
            input=messages,
            output=content,
            model=settings.llm_model,
            usage=ollama_usage(data),
            metadata={
                "task_type": state.get("task_type", ""),
                "chunks_count": len(chunks),
                "temperature": 0.0,
            },
        )
        logger.info("[llm_caller] got %d char response", len(content))
    except Exception as e:
        logger.error("[llm_caller] LLM call failed: %s", e)
        state["llm_response"] = f"Error: LLM call failed — {e}"

    return state
