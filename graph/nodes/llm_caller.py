# graph/nodes/llm_caller.py
"""LLM caller — sends prompt + context to Ollama."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def llm_caller(state: LegalAgentState) -> LegalAgentState:
    """Stub: pass through. Real impl calls Ollama with temperature=0.0."""
    logger.info("[llm_caller] stub — no LLM call")
    return state
