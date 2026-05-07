# rag/tools/get_document.py
"""Retrieve full document or section by doc_id."""

from langchain_core.tools import tool

from rag.vector_store import scroll_by_filter


@tool
def get_document(
    doc_id: str,
    collection: str = "legal_docs",
    section: str | None = None,
    client_id: str | None = None,
) -> str:
    """Retrieve the full text of a document or a specific section.

    Args:
        doc_id: The document ID to retrieve.
        collection: Qdrant collection to search.
        section: Optional section name to filter by.
        client_id: Client ID filter for access control.

    Returns:
        Full document text or specific section text.
    """
    filters = {"doc_id": doc_id}
    if client_id:
        filters["client_id"] = client_id
    if section:
        filters["section_display"] = section

    results = scroll_by_filter(
        filter_conditions=filters,
        collection=collection,
        limit=500,
    )

    if not results:
        return f"No content found for doc_id={doc_id}"

    results.sort(key=lambda r: r.get("chunk_index", 0))

    title = results[0].get("doc_title", "Unknown")
    texts = [r.get("text", "") for r in results]

    return f"# {title}\n\n" + "\n\n".join(texts)
