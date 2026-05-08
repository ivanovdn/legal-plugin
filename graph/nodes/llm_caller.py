# graph/nodes/llm_caller.py
"""LLM caller — sends prompt + retrieved context to Ollama."""

import logging

import httpx

from config import get_settings
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a legal assistant for an internal legal team. Answer the user's request using ONLY the provided context. For every claim, cite the source document (doc_title and doc_id). If the context is insufficient, say so explicitly — do not fabricate information."""


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


def llm_caller(state: LegalAgentState) -> LegalAgentState:
    """Call Ollama with context + request. temperature=0.0 always."""
    settings = get_settings()
    chunks = state.get("retrieved_chunks", [])
    context = _build_context(chunks)

    user_message = f"Context:\n{context}\n\nRequest: {state['request']}"

    try:
        response = httpx.post(
            f"{settings.ollama_base_url}/api/chat",
            json={
                "model": settings.llm_model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                "stream": False,
                "options": {"temperature": 0.0},
            },
            timeout=120.0,
        )
        response.raise_for_status()
        content = response.json()["message"]["content"]
        state["llm_response"] = content
        logger.info("[llm_caller] got %d char response", len(content))
    except Exception as e:
        logger.error("[llm_caller] LLM call failed: %s", e)
        state["llm_response"] = f"Error: LLM call failed — {e}"

    return state
