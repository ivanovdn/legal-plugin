# rag/tools/search_legal.py
"""Hybrid search tool for LangGraph agents."""

from langchain_core.tools import tool

from rag.hybrid_search import hybrid_search


@tool
def search_legal(
    query: str,
    collection: str = "legal_docs",
    client_id: str | None = None,
    jurisdiction: str | None = None,
    doc_type: str | None = None,
    top_k: int = 10,
) -> str:
    """Search the legal knowledge base using hybrid search (vector + BM25 + reranking).

    Args:
        query: The search query.
        collection: Qdrant collection to search. One of: legal_docs, case_history, memory.
        client_id: Filter by client ID. Always required for cross-client safety.
        jurisdiction: Filter by jurisdiction.
        doc_type: Filter by document type (contract, legislation, template, policy, case_law).
        top_k: Number of results to return.

    Returns:
        Formatted search results with chunk text and metadata.
    """
    filters = {}
    if client_id:
        filters["client_id"] = client_id
    if jurisdiction:
        filters["jurisdiction"] = jurisdiction
    if doc_type:
        filters["doc_type"] = doc_type

    results = hybrid_search(
        query=query,
        top_k=top_k,
        collection=collection,
        filters=filters if filters else None,
    )

    if not results:
        return "No results found."

    formatted = []
    for i, r in enumerate(results, 1):
        formatted.append(
            f"[{i}] {r.get('doc_title', 'Unknown')} "
            f"(doc_id: {r.get('doc_id', '?')}, "
            f"type: {r.get('doc_type', '?')}, "
            f"jurisdiction: {r.get('jurisdiction', '?')})\n"
            f"Section: {r.get('section', '')}\n"
            f"Text: {r.get('text', '')}\n"
            f"Score: {r.get('rrf_score', r.get('score', '?')):.4f}"
        )

    return "\n\n---\n\n".join(formatted)
