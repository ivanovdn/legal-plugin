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
    """Pause for attorney review. Applies the verdict on resume.

    Four outcomes:
      - approved=True              → exit (route_review → output_formatter)
      - revised_response non-empty → use revised text, exit
      - notes only + iter<cap      → reset llm_response/chunks/messages, +1 iter, loop back
      - cap hit or pure reject     → exit, attach notes to report_notes_unincorporated
    """
    state["awaiting_review"] = True
    logger.info(
        "[human_review] review required: task_type=%s, risk_level=%s, iter=%d",
        state.get("task_type"), state.get("risk_level"),
        state.get("review_iterations", 0),
    )

    settings = get_settings()
    if not settings.interrupt_enabled:
        logger.info("[human_review] interrupt disabled by config — flagging and continuing")
        return state

    review = interrupt({
        "type": "human_review",
        "task_type": state.get("task_type"),
        "risk_level": state.get("risk_level"),
        "llm_response": state.get("llm_response", "")[:500],
        "risk_flags": state.get("risk_flags", []),
        "review_iterations": state.get("review_iterations", 0),
    })

    if not isinstance(review, dict):
        logger.warning("[human_review] unexpected resume payload type=%s", type(review).__name__)
        state["awaiting_review"] = False
        return state

    approved = review.get("approved", True)
    notes = review.get("notes", "")
    revised = review.get("revised_response", "")
    iterations = state.get("review_iterations", 0)
    max_iter = settings.max_review_iterations

    if approved:
        state["awaiting_review"] = False
        state["attorney_notes"] = notes
        logger.info("[human_review] approved by attorney")
        return state

    if revised:
        state["llm_response"] = revised
        state["attorney_notes"] = notes
        state["awaiting_review"] = False
        logger.info("[human_review] revised by attorney")
        return state

    if notes and iterations < max_iter:
        state["attorney_notes"] = notes
        state["review_iterations"] = iterations + 1
        state["llm_response"] = ""
        state["retrieved_chunks"] = []
        state["messages"] = []
        state["awaiting_review"] = False
        logger.info("[human_review] loop-back iteration %d/%d", iterations + 1, max_iter)
        return state

    state["report_notes_unincorporated"] = notes
    state["awaiting_review"] = False
    logger.info(
        "[human_review] terminal: cap_hit=%s, pure_reject=%s",
        iterations >= max_iter, not notes,
    )
    return state
