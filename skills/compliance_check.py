# skills/compliance_check.py
"""Compliance check — policy/regulation verification."""

import logging

from langfuse.decorators import observe

from graph.state import LegalAgentState

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a compliance verification specialist. Check the provided documents against applicable policies, regulations, and jurisdiction rules.

For each compliance check, provide:
- rule_id: identifier for the rule or regulation being checked
- rule_text: the text of the rule
- source_chunk: the relevant document text being checked
- status: "pass", "fail", "partial", or "n/a"
- evidence: specific evidence from the document supporting the status
- remediation: suggested fix if status is "fail" or "partial" (or null)

Determine the overall compliance status (pass, fail, or partial).
If any check has high severity or you are uncertain, set escalate to true.

Cite every source document by doc_id and doc_title. If context is insufficient, say so.

Respond with your analysis as structured text with clear sections for each check."""


@observe(name="compliance_check", capture_input=False, capture_output=False)
def compliance_check(state: LegalAgentState) -> LegalAgentState:
    """Prepare state for compliance verification via rag_retriever + llm_caller."""
    request = state["request"]
    attorney_notes = (state.get("attorney_notes") or "").strip()

    user_content = request
    if attorney_notes:
        user_content += (
            f"\n\n--- ATTORNEY REVIEW NOTES (incorporate these changes) ---\n"
            f"{attorney_notes}"
        )

    state["retrieval_query"] = request
    state["messages"] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    logger.info("[compliance_check] prepared for compliance verification: %s", request[:80])
    return state
