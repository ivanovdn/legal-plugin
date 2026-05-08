# graph/nodes/intake.py
"""Intake node — validates and enriches incoming request."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)

# Simple user -> client mapping for now. Real auth replaces this.
_USER_CLIENT_MAP: dict[str, str] = {}
_DEFAULT_CLIENT_ID = "internal"


def intake(state: LegalAgentState) -> LegalAgentState:
    """Resolve client_id from user_id, set filters and retrieval_query."""
    user_id = state["user_id"]
    client_id = _USER_CLIENT_MAP.get(user_id, _DEFAULT_CLIENT_ID)

    state["filters"] = {
        "client_id": client_id,
        **{k: v for k, v in state.get("filters", {}).items() if k != "client_id"},
    }

    if not state.get("retrieval_query"):
        state["retrieval_query"] = state["request"]

    logger.info(
        "[intake] user=%s, client_id=%s, request=%s",
        user_id, client_id, state["request"][:80],
    )
    return state
