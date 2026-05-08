# skills/compliance_check.py
"""Compliance check — policy/regulation verification (stub)."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def compliance_check(state: LegalAgentState) -> LegalAgentState:
    """Stub: will be replaced with real compliance checking."""
    logger.info("[compliance_check] stub")
    state["llm_response"] = "[compliance_check stub] No implementation yet."
    return state
