# graph/nodes/rag_retriever.py
"""RAG retriever — runs hybrid search for the current request."""

import logging
from graph.state import LegalAgentState
from rag.hybrid_search import hybrid_search

logger = logging.getLogger(__name__)


def rag_retriever(state: LegalAgentState) -> LegalAgentState:
    """Search for relevant chunks using hybrid search."""
    query = state.get("retrieval_query", "")
    if not query:
        logger.info("[rag_retriever] no query — skipping retrieval")
        return state

    filters = state.get("filters")

    results = hybrid_search(
        query=query,
        top_k=10,
        collection="legal_docs",
        filters=filters,
    )

    state["retrieved_chunks"] = results
    logger.info("[rag_retriever] retrieved %d chunks for query: %s", len(results), query[:60])
    return state
