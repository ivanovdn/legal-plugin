# graph/nodes/memory_writer.py
"""Memory writer — persists session data and audit log."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def memory_writer(state: LegalAgentState) -> LegalAgentState:
    """Stub: pass through. Real impl writes to SQLite audit log."""
    logger.info("[memory_writer] stub — pass through")
    return state
