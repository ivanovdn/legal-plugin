# graph/nodes/planner.py
"""Planner — breaks multi-skill requests into ordered skill_plan."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def planner(state: LegalAgentState) -> LegalAgentState:
    """Stub: pass through. Real impl decomposes multi-skill requests."""
    logger.info("[planner] skill_plan=%s", state["skill_plan"])
    return state
