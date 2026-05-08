# skills/legal_research.py
"""Legal research — multi-hop retrieval agent (stub)."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def legal_research(state: LegalAgentState) -> LegalAgentState:
    """Stub: will be replaced by create_react_agent subgraph."""
    logger.info("[legal_research] stub")
    state["llm_response"] = "[legal_research stub] No implementation yet."
    return state
