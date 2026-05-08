# graph/nodes/skill_dispatcher.py
"""Skill dispatcher — routes to the correct skill node."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)

SKILL_MAP = {
    "contract_generation": "contract_generation",
    "contract_review": "contract_review",
    "compliance": "compliance_check",
    "research": "legal_research",
    "drafting": "drafting",
}


def skill_dispatcher(state: LegalAgentState) -> LegalAgentState:
    """Sets routing info for conditional edge. Actual dispatch via graph edges."""
    logger.info("[skill_dispatcher] dispatching task_type=%s", state["task_type"])
    return state


def route_to_skill(state: LegalAgentState) -> str:
    """Conditional edge: returns the skill node name to route to."""
    return SKILL_MAP.get(state["task_type"], "legal_research")
