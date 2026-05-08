# graph/nodes/human_review.py
"""Human review — pauses graph for attorney approval."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def human_review(state: LegalAgentState) -> LegalAgentState:
    """Stub: marks awaiting_review and passes through. Real impl uses interrupt()."""
    state["awaiting_review"] = True
    logger.info("[human_review] stub — marked awaiting_review=True")
    return state
