# graph/nodes/intake.py
"""Intake node — validates and enriches incoming request."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def intake(state: LegalAgentState) -> LegalAgentState:
    """Stub: pass through. Real impl resolves client_id from user_id."""
    logger.info("[intake] request=%s, user=%s", state["request"][:50], state["user_id"])
    return state
