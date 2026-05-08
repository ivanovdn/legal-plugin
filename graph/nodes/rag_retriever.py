# graph/nodes/rag_retriever.py
"""RAG retriever — runs hybrid search for the current request."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def rag_retriever(state: LegalAgentState) -> LegalAgentState:
    """Stub: pass through. Real impl calls hybrid_search."""
    logger.info("[rag_retriever] query=%s", state.get("retrieval_query", "")[:50])
    return state
