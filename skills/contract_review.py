# skills/contract_review.py
"""Contract review — clause extraction and risk analysis."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a contract review specialist. Analyze the provided contract text and identify all clauses.

For each clause, provide:
- clause_type: the category (e.g., indemnification, termination, payment, confidentiality, liability, force_majeure, governing_law, dispute_resolution)
- original_text: the exact clause text
- risk_level: "low", "medium", or "high"
- risk_reason: why this risk level was assigned
- standard_ref: reference to any standard or regulation this clause relates to (or null)
- suggested_edit: suggested improvement if risk is medium or high (or null)

Also identify any missing clauses that should typically be present.

Cite every source document by doc_id and doc_title. If context is insufficient, say so.

Respond with your analysis as structured text with clear sections for each clause."""


def contract_review(state: LegalAgentState) -> LegalAgentState:
    """Prepare state for clause analysis via rag_retriever + llm_caller."""
    request = state["request"]

    state["retrieval_query"] = request
    state["messages"] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": request},
    ]

    logger.info("[contract_review] prepared for clause analysis: %s", request[:80])
    return state
