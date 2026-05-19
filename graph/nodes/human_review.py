# graph/nodes/human_review.py
"""Human review — pauses graph for attorney approval using LangGraph interrupt."""

import logging

from langfuse.decorators import observe
from langgraph.types import interrupt

from config import get_settings
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


@observe(name="human_review")
def human_review(state: LegalAgentState) -> LegalAgentState:
    """Pause for human review. Uses interrupt() when both checkpointer and interrupt_enabled are present."""
    state["awaiting_review"] = True
    logger.info(
        "[human_review] review required: task_type=%s, risk_level=%s",
        state.get("task_type"), state.get("risk_level"),
    )

    settings = get_settings()
    if not settings.interrupt_enabled:
        logger.info("[human_review] interrupt disabled by config — flagging and continuing")
        return state

    try:
        review = interrupt({
            "type": "human_review",
            "task_type": state.get("task_type"),
            "risk_level": state.get("risk_level"),
            "llm_response": state.get("llm_response", "")[:500],
            "risk_flags": state.get("risk_flags", []),
        })
        if isinstance(review, dict):
            state["attorney_notes"] = review.get("notes", "")
            if review.get("approved", True):
                state["awaiting_review"] = False
                logger.info("[human_review] approved by attorney")
            else:
                state["llm_response"] = review.get("revised_response", state["llm_response"])
                state["awaiting_review"] = False
                logger.info("[human_review] revised by attorney")
    except Exception as e:
        logger.warning("[human_review] interrupt unavailable (%s) — marking and continuing", type(e).__name__)

    return state
