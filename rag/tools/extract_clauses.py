# rag/tools/extract_clauses.py
"""Extract clauses by type from case_history for contract generation."""

from langchain_core.tools import tool

from rag.vector_store import scroll_by_filter


@tool
def extract_clauses(
    clause_type: str,
    client_id: str,
    jurisdiction: str | None = None,
    limit: int = 20,
) -> str:
    """Extract historical clauses of a specific type from signed contracts.

    Searches the case_history collection for past clauses matching the type.
    Used by contract generation to find patterns.

    Args:
        clause_type: Type of clause (e.g., indemnification, termination, payment).
        client_id: Client ID — required, never cross-client.
        jurisdiction: Optional jurisdiction filter.
        limit: Maximum clauses to return.

    Returns:
        Formatted list of historical clauses with metadata.
    """
    filters: dict = {
        "clause_type": clause_type,
        "client_id": client_id,
    }
    if jurisdiction:
        filters["jurisdiction"] = jurisdiction

    results = scroll_by_filter(
        filter_conditions=filters,
        collection="case_history",
        limit=limit,
    )

    if not results:
        return f"No historical clauses found for type={clause_type}, client={client_id}"

    formatted = []
    for i, r in enumerate(results, 1):
        formatted.append(
            f"[{i}] {r.get('doc_title', 'Unknown')} "
            f"(jurisdiction: {r.get('jurisdiction', '?')})\n"
            f"Clause: {r.get('text', '')}"
        )

    return f"Found {len(results)} '{clause_type}' clauses:\n\n" + "\n\n---\n\n".join(formatted)
