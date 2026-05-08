# graph/nodes/intent_router.py
"""Intent router — classifies task_type from request."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)

VALID_TASK_TYPES = {
    "contract_generation", "contract_review", "compliance",
    "research", "drafting", "multi",
}


def intent_router(state: LegalAgentState) -> LegalAgentState:
    """Stub: sets task_type to 'research' if not already set."""
    if not state.get("task_type") or state["task_type"] not in VALID_TASK_TYPES:
        state["task_type"] = "research"
    if not state.get("skill_plan"):
        state["skill_plan"] = [state["task_type"]]
    logger.info("[intent_router] task_type=%s, skill_plan=%s", state["task_type"], state["skill_plan"])
    return state
