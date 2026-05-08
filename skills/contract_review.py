# skills/contract_review.py
"""Contract review — clause extraction and risk analysis (stub)."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def contract_review(state: LegalAgentState) -> LegalAgentState:
    """Stub: will be replaced with real clause analysis."""
    logger.info("[contract_review] stub")
    state["llm_response"] = "[contract_review stub] No implementation yet."
    return state
