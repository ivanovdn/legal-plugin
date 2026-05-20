# skills/drafting.py
"""Drafting — template-based document generation."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a legal document drafting specialist. Generate formal legal documents based on templates and the provided context.

Your task:
1. Search for relevant templates or similar documents in the knowledge base
2. Use them as the basis for the new document
3. Fill in all required variables (parties, dates, terms, jurisdiction)
4. Flag any deviations from standard templates

The output should be a complete, ready-to-review legal document with:
- Document title and type
- All standard sections for the document type
- Proper legal language appropriate to the jurisdiction
- Signature blocks

Cite every source template or document by doc_id and doc_title.
If no suitable template is found, generate the document from best practices and flag this as a deviation.

IMPORTANT: This is a DRAFT for attorney review. It will always go through human review before delivery."""


def drafting(state: LegalAgentState) -> LegalAgentState:
    """Prepare state for document drafting via rag_retriever + llm_caller."""
    request = state["request"]
    filters = state.get("filters", {})
    attorney_notes = (state.get("attorney_notes") or "").strip()

    query_parts = [request]
    if filters.get("jurisdiction"):
        query_parts.append(f"jurisdiction: {filters['jurisdiction']}")
    state["retrieval_query"] = " ".join(query_parts)

    user_content = request
    if attorney_notes:
        user_content += (
            f"\n\n--- ATTORNEY REVIEW NOTES (incorporate these changes) ---\n"
            f"{attorney_notes}"
        )

    state["messages"] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    logger.info("[drafting] prepared for document drafting: %s", request[:80])
    return state
