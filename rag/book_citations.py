# rag/book_citations.py
"""Check what an answer cites from the reference books (spec D8; ruling 13).

Every cited ref_id is resolved once, here, called from risk_assessor; the risk flags, the rendered
response (output_formatter) and the audit row (memory_writer) all read the list this returns, so the
three cannot disagree. The citation an attorney reads is built from the stored node, never from the
model's text.

A cited id is valid when the conversation retrieved it, or when it sits inside a chunk the conversation
retrieved (`NodeInfo.containers`). Either way it has to exist in the stored book, because that is what
its citation is rendered from.
"""
import logging
from collections.abc import Collection

from ingest.books.citations import render_citation
from ingest.books.ref_ids import find_cited_ref_ids
from ingest.books.registry import UnknownBook, book_for_ref
from rag.book_search import fetch_nodes

logger = logging.getLogger(__name__)

NOT_RETRIEVED = "Cites a reference that was not retrieved: {ref_id}"
UNVERIFIABLE = "Could not verify a cited reference: {ref_id}"


def _valid(ref_id: str, citation: str) -> dict:
    return {"ref_id": ref_id, "valid": True, "citation": citation, "reason": None}


def _invalid(ref_id: str, reason: str) -> dict:
    return {"ref_id": ref_id, "valid": False, "citation": None, "reason": reason.format(ref_id=ref_id)}


def check_book_citations(text: str, retrieved: Collection[str]) -> list[dict]:
    """One `{"ref_id", "valid", "citation", "reason"}` per id `text` cites, in the order first cited.

    `retrieved` is the conversation's retrieved book ids (`book_refs_retrieved`). Returns [] without
    calling Qdrant when nothing is cited. When Qdrant fails, no citation can be rendered from the store,
    so every cited id comes back invalid as "Could not verify" (D13: a risk flag, not a degradation)."""
    cited = find_cited_ref_ids(text)
    if not cited:
        return []
    retrieved_ids = set(retrieved)

    try:
        nodes = fetch_nodes(cited)
    except Exception as exc:
        logger.error(
            "[book_citations] could not resolve %d cited ref_id(s), all flagged unverified: %s: %s",
            len(cited), exc.__class__.__name__, exc,
        )
        return [_invalid(ref_id, UNVERIFIABLE) for ref_id in cited]

    results = []
    for ref_id in cited:
        node = nodes.get(ref_id)
        if node is None:
            # Retrieved but gone from the store (a re-ingest since) can't be rendered; anything else
            # was never in front of the model.
            results.append(_invalid(ref_id, UNVERIFIABLE if ref_id in retrieved_ids else NOT_RETRIEVED))
            continue
        if ref_id not in retrieved_ids and not node.containers & retrieved_ids:
            results.append(_invalid(ref_id, NOT_RETRIEVED))
            continue
        try:
            book = book_for_ref(ref_id)
        except UnknownBook:  # an edition the registry doesn't list: nothing to cite it as
            results.append(_invalid(ref_id, NOT_RETRIEVED))
            continue
        results.append(_valid(ref_id, render_citation(
            label=node.label,
            kind=node.kind,
            source_type=node.source_type,
            appendix=node.appendix,
            page_start=node.page_start,
            page_end=node.page_end,
            book=book,
        )))
    return results
