# graph/nodes/output_formatter.py
"""Output formatter — structures the final response."""

import logging

from graph.state import LegalAgentState
from ingest.books.citations import render_response
from observability.spans import traced

logger = logging.getLogger(__name__)


@traced("output_formatter")
def output_formatter(state: LegalAgentState) -> LegalAgentState:
    """Build structured report from LLM response and metadata.

    Book citations are rendered from `book_citations`, which risk_assessor
    resolved (ruling 13): never resolved again here, so the text says what the
    risk flags say. `llm_response` keeps the raw ids, for history and the audit.
    """
    book_citations = state.get("book_citations") or []
    valid = [c for c in book_citations if c["valid"]]
    response = state.get("llm_response", "")
    if book_citations:
        response = render_response(response, {c["ref_id"]: c["citation"] for c in valid})

    state["report"] = {
        "task_type": state.get("task_type", ""),
        "response": response,
        "risk_level": state.get("risk_level", "low"),
        "risk_flags": state.get("risk_flags", []),
        "awaiting_review": state.get("awaiting_review", False),
        "sources": [
            {"doc_id": c.get("doc_id"), "doc_title": c.get("doc_title")}
            for c in state.get("retrieved_chunks", [])
        ] + [{"ref_id": c["ref_id"], "citation": c["citation"]} for c in valid],
        "notes_unincorporated": state.get("report_notes_unincorporated", ""),
        "proposed_edits": state.get("proposed_edits", []),
        "proposed_preferences": state.get("proposed_preferences", []),
        "contract_type_detected": state.get("contract_type_detected", ""),
        "requires_attorney": state.get("requires_attorney", False),
        "memory_degraded": state.get("memory_degraded", False),
        "context_truncated": state.get("context_truncated"),
        "tokens": state.get("token_usage"),
        "context_breakdown": state.get("context_breakdown"),
    }
    logger.info("[output_formatter] report built, task_type=%s", state["task_type"])
    return state
