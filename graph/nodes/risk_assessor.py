# graph/nodes/risk_assessor.py
"""Risk assessor — checks citations and evaluates risk level."""

import logging
from langfuse.decorators import observe

from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def _check_citations(
    llm_response: str, chunks: list[dict], has_uploaded_doc: bool = False
) -> list[dict]:
    """Check if the response references retrieved documents.

    When the user attached a document (e.g. the Word add-in chat answering
    about the open contract), the attached doc is the citation source, so
    having no RAG chunks is expected — not a risk. Otherwise, a response with
    no retrievable sources is flagged for attorney review.
    """
    flags = []
    if not chunks:
        if has_uploaded_doc:
            return flags
        flags.append({"reason": "No citation possible — no chunks retrieved", "severity": "high"})
        return flags

    cited = False
    for chunk in chunks:
        doc_id = chunk.get("doc_id", "")
        doc_title = chunk.get("doc_title", "")
        if doc_id and doc_id in llm_response:
            cited = True
            break
        if doc_title and doc_title.lower() in llm_response.lower():
            cited = True
            break

    if not cited:
        flags.append({"reason": "No citation found — response does not reference any retrieved document", "severity": "high"})

    return flags


@observe(name="risk_assessor")
def risk_assessor(state: LegalAgentState) -> LegalAgentState:
    """Evaluate risk based on citations, task type, and content."""
    llm_response = state.get("llm_response", "")
    chunks = state.get("retrieved_chunks", [])
    has_uploaded_doc = bool(state.get("uploaded_docs"))

    risk_flags = _check_citations(llm_response, chunks, has_uploaded_doc)
    state["risk_flags"] = risk_flags

    if any(f["severity"] == "high" for f in risk_flags):
        state["risk_level"] = "high"
    elif risk_flags:
        state["risk_level"] = "medium"
    else:
        state["risk_level"] = "low"

    logger.info("[risk_assessor] risk_level=%s, flags=%d", state["risk_level"], len(risk_flags))
    return state


def route_risk(state: LegalAgentState) -> str:
    """Conditional edge: routes to human_review or output_formatter."""
    if state["task_type"] in ("contract_generation", "drafting"):
        return "human_review"
    if state["risk_level"] in ("high", "medium"):
        return "human_review"
    return "output_formatter"
