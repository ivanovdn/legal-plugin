# graph/nodes/output_formatter.py
"""Output formatter — structures the final response."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def output_formatter(state: LegalAgentState) -> LegalAgentState:
    """Build structured report from LLM response and metadata."""
    state["report"] = {
        "task_type": state.get("task_type", ""),
        "response": state.get("llm_response", ""),
        "risk_level": state.get("risk_level", "low"),
        "risk_flags": state.get("risk_flags", []),
        "awaiting_review": state.get("awaiting_review", False),
        "sources": [
            {"doc_id": c.get("doc_id"), "doc_title": c.get("doc_title")}
            for c in state.get("retrieved_chunks", [])
        ],
    }
    logger.info("[output_formatter] report built, task_type=%s", state["task_type"])
    return state
