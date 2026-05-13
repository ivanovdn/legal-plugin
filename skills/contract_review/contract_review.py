# skills/contract_review/contract_review.py
"""Contract review — clause-by-clause analysis against the SKILL.md playbook.
Handles uploaded contract text from Chainlit session or RAG-retrieved chunks."""

import logging
from pathlib import Path

from graph.state import LegalAgentState

logger = logging.getLogger(__name__)

_SKILL_DIR = Path(__file__).parent
_SKILL_MD = _SKILL_DIR / "SKILL.md"


def _load_prompt() -> str:
    """Load system prompt from SKILL.md. Strips YAML frontmatter."""
    text = _SKILL_MD.read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            text = text[end + 3:].strip()
    return text


def _extract_uploaded_text(state: LegalAgentState) -> str:
    """Extract contract text from uploaded_docs in state."""
    docs = state.get("uploaded_docs", [])
    if not docs:
        return ""
    # uploaded_docs can be LegalChunk dicts or objects with .text
    parts = []
    for doc in docs:
        if isinstance(doc, dict):
            parts.append(doc.get("text", ""))
        elif hasattr(doc, "text"):
            parts.append(doc.text)
    return "\n\n".join(parts)


def contract_review(state: LegalAgentState) -> LegalAgentState:
    """Prepare state for clause analysis via rag_retriever + llm_caller.

    If uploaded contract text is available (from Chainlit file upload),
    it's included directly in the user message. Otherwise, rag_retriever
    will search for relevant contract text.
    """
    request = state["request"]
    playbook = _load_prompt()
    uploaded_text = _extract_uploaded_text(state)

    # Build user message with contract text if available
    if uploaded_text:
        user_content = (
            f"{request}\n\n"
            f"--- CONTRACT TEXT ---\n"
            f"{uploaded_text}\n"
            f"--- END CONTRACT TEXT ---"
        )
        # No need for RAG retrieval when contract is uploaded
        state["retrieval_query"] = ""
    else:
        user_content = request
        state["retrieval_query"] = request

    state["messages"] = [
        {"role": "system", "content": playbook},
        {"role": "user", "content": user_content},
    ]

    logger.info(
        "[contract_review] prepared: uploaded=%d chars, rag=%s",
        len(uploaded_text),
        "yes" if state["retrieval_query"] else "no",
    )
    return state
