# skills/drafting.py
"""Drafting — template-based document generation (stub)."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def drafting(state: LegalAgentState) -> LegalAgentState:
    """Stub: will be replaced with real template filling."""
    logger.info("[drafting] stub")
    state["llm_response"] = "[drafting stub] No implementation yet."
    return state
