# graph/nodes/output_formatter.py
"""Output formatter — structures the final response."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def output_formatter(state: LegalAgentState) -> LegalAgentState:
    """Stub: pass through. Real impl builds structured report from llm_response."""
    logger.info("[output_formatter] stub — pass through")
    return state
