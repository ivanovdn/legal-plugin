# graph/nodes/output_formatter.py
"""Output formatter — structures the final response."""

import logging
from langfuse.decorators import observe

from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


@observe(name="output_formatter")
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
        "notes_unincorporated": state.get("report_notes_unincorporated", ""),
        "proposed_edits": state.get("proposed_edits", []),
        "contract_type_detected": state.get("contract_type_detected", ""),
        "requires_attorney": state.get("requires_attorney", False),
    }
    logger.info("[output_formatter] report built, task_type=%s", state["task_type"])
    return state
