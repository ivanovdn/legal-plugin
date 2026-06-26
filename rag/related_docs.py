# rag/related_docs.py
"""Retrieve related/parent documents for cross-document review.

Currently serves one case: the governing MSA for a SOW review. A SOW is a child
document issued under a parent MSA; `contract_review` uses this to pull the MSA
so the SOW can be reviewed against it.
"""
from __future__ import annotations

import logging

from rag.vector_store import scroll_by_filter

logger = logging.getLogger(__name__)

# Generous ceiling; one MSA is far fewer chunks than this.
_MAX_MSA_CHUNKS = 500


def get_parent_msa(client_id: str, collection: str = "legal_docs") -> tuple[str, str] | None:
    """Return (doc_title, full_text) of the governing MSA on file for client_id.

    Filters Qdrant by doc_type="msa" + client_id. When more than one MSA is
    present, picks the first by doc_id (a one-MSA-per-client demo assumption;
    matching the *specific* parent by party name is future work) and logs it.
    Sorts the chosen MSA's chunks by chunk_index and concatenates their text.
    Returns None when client_id is falsy or no MSA chunks are found.
    """
    if not client_id:
        return None

    chunks = scroll_by_filter(
        filter_conditions={"doc_type": "msa", "client_id": client_id},
        collection=collection,
        limit=_MAX_MSA_CHUNKS,
    )
    if not chunks:
        return None

    doc_ids = sorted({c.get("doc_id", "") for c in chunks})
    if len(doc_ids) > 1:
        logger.warning(
            "[get_parent_msa] %d MSAs on file for client_id=%s; using %r "
            "(party-name matching is future work)",
            len(doc_ids), client_id, doc_ids[0],
        )
    chosen = doc_ids[0]
    msa_chunks = [c for c in chunks if c.get("doc_id", "") == chosen]
    msa_chunks.sort(key=lambda c: c.get("chunk_index", 0))

    title = msa_chunks[0].get("doc_title", "Master Services Agreement")
    text = "\n\n".join(c.get("text", "") for c in msa_chunks)
    return title, text
