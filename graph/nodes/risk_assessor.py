# graph/nodes/risk_assessor.py
"""Risk assessor — evaluates risk level of the response."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def risk_assessor(state: LegalAgentState) -> LegalAgentState:
    """Stub: sets risk_level to 'low' if not already set."""
    if not state.get("risk_level"):
        state["risk_level"] = "low"
    logger.info("[risk_assessor] risk_level=%s", state["risk_level"])
    return state


def route_risk(state: LegalAgentState) -> str:
    """Conditional edge: routes to human_review or output_formatter."""
    if state["task_type"] in ("contract_generation", "drafting"):
        return "human_review"
    if state["risk_level"] in ("high", "medium"):
        return "human_review"
    return "output_formatter"
