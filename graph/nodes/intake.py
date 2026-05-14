# graph/nodes/intake.py
"""Intake node — validates and enriches incoming request."""

import logging

from langfuse.decorators import observe, langfuse_context

from graph.state import LegalAgentState

logger = logging.getLogger(__name__)

_USER_CLIENT_MAP: dict[str, str] = {}
_DEFAULT_CLIENT_ID = "internal"


@observe(name="intake")
def intake(state: LegalAgentState) -> LegalAgentState:
    """Resolve client_id from user_id, set filters and retrieval_query."""
    user_id = state["user_id"]
    client_id = _USER_CLIENT_MAP.get(user_id, _DEFAULT_CLIENT_ID)

    langfuse_context.update_current_trace(
        user_id=user_id,
        session_id=state.get("session_id", ""),
        tags=[state.get("task_type") or "unclassified"],
    )

    state["filters"] = {
        "client_id": client_id,
        **{k: v for k, v in state.get("filters", {}).items() if k != "client_id"},
    }

    if not state.get("retrieval_query"):
        state["retrieval_query"] = state["request"]

    logger.info("[intake] user=%s, client_id=%s", user_id, client_id)
    return state
