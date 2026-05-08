# skills/contract_generation.py
"""Contract generation — agent subgraph (stub)."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def contract_generation(state: LegalAgentState) -> LegalAgentState:
    """Stub: will be replaced by create_react_agent subgraph in Phase 6."""
    logger.info("[contract_generation] stub")
    state["llm_response"] = "[contract_generation stub] No implementation yet."
    return state
