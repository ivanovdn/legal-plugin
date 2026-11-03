# graph/nodes/risk_assessor.py
"""Risk assessor — sets risk_level and the attorney-required signal.

Two signals, by task:
  - contract_review → the review VERDICT (`_assess_review_verdict`): a
    "Do not send for signature" status, or any Red / Missing Context finding,
    is a blocker. This is the contract-review concern — whether the document is
    safe to sign — and it lives in the LLM output text, not in citation state.
  - everything else → citation grounding (`_check_citations`): a research answer
    with no retrievable source is flagged for attorney review. Book citations are
    resolved here, once, into `book_citations` (spec D8, ruling 13): each invalid
    ref_id is flagged, and output_formatter and memory_writer read the same list.
    Not on a turn with an attached document (Word doc-chat): the book isn't wired
    there, so neither the book check nor the stated-page check runs (ruling R15).

Detection mirrors the Word add-in parser (clients/word/src/parser.ts) so the
server-side verdict and the client-rendered blockers always reconcile.
"""

import logging
import re
from collections.abc import Sequence

from graph.state import LegalAgentState
from ingest.books.citations import find_stated_pages
from observability.spans import traced, set_trace_attributes
from rag.book_citations import check_book_citations

logger = logging.getLogger(__name__)


def _check_citations(
    llm_response: str,
    chunks: list[dict],
    has_uploaded_doc: bool = False,
    *,
    book_citations: Sequence[dict] = (),
    book_retrieved: bool = False,
) -> list[dict]:
    """Check if the response references retrieved documents.

    When the user attached a document (e.g. the Word add-in chat answering
    about the open contract), the attached doc is the citation source, so
    having no RAG chunks is expected — not a risk. Otherwise, a response with
    no retrievable sources is flagged for attorney review.

    The reference books count as a source too (spec D8): `book_retrieved` says
    the conversation retrieved book passages, and a valid entry of
    `book_citations` (check_book_citations) is a citation. Each invalid one is
    flagged, whatever else the answer cites.
    """
    flags = []
    if not chunks and has_uploaded_doc:
        pass  # the attached document is the source
    elif not chunks and not book_retrieved:
        flags.append({"reason": "No citation possible — no chunks retrieved", "severity": "high"})
    elif not (any(c["valid"] for c in book_citations) or _names_a_chunk(llm_response, chunks)):
        flags.append({"reason": "No citation found — response does not reference any retrieved document", "severity": "high"})

    flags.extend({"reason": c["reason"], "severity": "high"} for c in book_citations if not c["valid"])
    return flags


def _names_a_chunk(llm_response: str, chunks: list[dict]) -> bool:
    """The response names a retrieved document, by doc_id or by title."""
    for chunk in chunks:
        doc_id = chunk.get("doc_id", "")
        doc_title = chunk.get("doc_title", "")
        if doc_id and doc_id in llm_response:
            return True
        if doc_title and doc_title.lower() in llm_response.lower():
            return True
    return False


# Risk vocabulary in the team's "Key Findings" Rating column. Mirrors
# parser.ts normalizeRisk: blockers are Red and Missing Context; Yellow is a
# negotiation item; Green is clean.
_RATING_BLOCKERS = {"red", "missing context"}
_RATING_WARN = {"yellow"}
# The explicit signature verdict the team's format emits in "# Review Summary"
# and "# No Signature Checklist Result". parser.ts treats this phrase anywhere
# in the text as not-ready.
_DO_NOT_SIGN = re.compile(r"do not send for signature", re.I)


def _normalize_rating(cell: str) -> str:
    """Reduce a table cell to a bare rating word for matching.

    Strips markdown emphasis and any non-letter chars (emoji/checkmarks), lowers
    and collapses whitespace — so "🔴 Red", "**Red**" and "red" all match "red",
    and "Missing-Context" / "Missing_Context" match "missing context".
    """
    s = re.sub(r"[^a-z ]", " ", cell.lower())
    return re.sub(r"\s+", " ", s).strip()


def _table_ratings(text: str) -> set[str]:
    """Collect normalized rating words that appear as a whole table cell.

    Whole-cell matching (not substring) keeps precision high: a finding's prose
    cell that happens to contain the word "red" won't be counted, but a Rating
    cell of "Red" / "Missing Context" / "Yellow" / "Green" will.
    """
    found: set[str] = set()
    for line in text.splitlines():
        if "|" not in line:
            continue
        for cell in line.split("|"):
            norm = _normalize_rating(cell)
            if norm in _RATING_BLOCKERS or norm in _RATING_WARN or norm == "green":
                found.add(norm)
    return found


def _assess_review_verdict(llm_response: str) -> tuple[str, list[dict]]:
    """Read a contract-review output and return (risk_level, flags).

    high   — an empty review, an explicit "Do not send for signature" verdict,
             or any Red / Missing Context finding (a signature blocker).
    medium — no blocker but a Yellow finding (negotiation item).
    low    — clean (all Green / ready for signature).
    """
    text = (llm_response or "").strip()
    if not text:
        return "high", [{"reason": "No review output produced", "severity": "high"}]

    flags: list[dict] = []
    ratings = _table_ratings(text)

    if _DO_NOT_SIGN.search(text):
        flags.append({"reason": "Verdict: Do not send for signature", "severity": "high"})
    blocker_ratings = ratings & _RATING_BLOCKERS
    if blocker_ratings:
        flags.append({
            "reason": f"Blocking findings present: {', '.join(sorted(blocker_ratings))}",
            "severity": "high",
        })
    if flags:
        return "high", flags

    if ratings & _RATING_WARN:
        return "medium", [{"reason": "Yellow findings present — negotiation items", "severity": "medium"}]

    return "low", []


@traced("risk_assessor")
def risk_assessor(state: LegalAgentState) -> LegalAgentState:
    """Evaluate risk. contract_review uses the verdict; others use citations."""
    task_type = state.get("task_type", "")
    llm_response = state.get("llm_response", "")

    if task_type == "contract_review":
        risk_level, risk_flags = _assess_review_verdict(llm_response)
        requires_attorney = risk_level in ("high", "medium")
    else:
        chunks = state.get("retrieved_chunks", [])
        has_uploaded_doc = bool(state.get("uploaded_docs"))
        book_retrieved = state.get("book_refs_retrieved") or []
        book_citations: list[dict] = []
        stated_page = False
        # Ruling R15: a turn on an attached document (Word doc-chat) answers
        # from that document, never from the book tools, and its pane cannot
        # resume an interrupt; a ref_id-shaped token there (hallucinated, or
        # quoted from a contract that says "ref_id: …") must not flag the turn.
        # So neither the book check nor the page check runs, and nothing is
        # rendered.
        if not has_uploaded_doc:
            book_citations = check_book_citations(llm_response, book_retrieved)
            # Ruling 12: read before rendering, so every page in the text is
            # the model's own; and only with the book in play, so "p. 3 of the
            # MSA" in an answer about a client document is never flagged.
            stated_page = bool(
                (book_citations or state.get("book_refs_turn")) and find_stated_pages(llm_response)
            )
        state["book_citations"] = book_citations
        risk_flags = _check_citations(
            llm_response, chunks, has_uploaded_doc,
            book_citations=book_citations, book_retrieved=bool(book_retrieved),
        )
        if stated_page:
            risk_flags.append({"reason": "The answer states a page itself", "severity": "high"})
        if any(f["severity"] == "high" for f in risk_flags):
            risk_level = "high"
        elif risk_flags:
            risk_level = "medium"
        else:
            risk_level = "low"
        # requires_attorney is the contract-review verdict signal; it does not
        # apply to the citation-grounding (research) path.
        requires_attorney = False

    state["risk_flags"] = risk_flags
    state["risk_level"] = risk_level
    state["requires_attorney"] = requires_attorney

    # Surface the verdict on the trace (the human-review decision was
    # previously invisible). Tracing must never break the node.
    try:
        set_trace_attributes(
            metadata={"review_risk_level": risk_level, "requires_attorney": requires_attorney},
        )
    except Exception:  # pragma: no cover - observability is best-effort
        pass

    logger.info(
        "[risk_assessor] task=%s risk_level=%s requires_attorney=%s flags=%d",
        task_type, risk_level, requires_attorney, len(risk_flags),
    )
    return state


def route_risk(state: LegalAgentState) -> str:
    """Conditional edge: human_review or output_formatter.

    The interrupt only makes sense for callers that can resume it. A contract
    review is gated on `interactive_review` (set by clients with a review loop,
    e.g. Chainlit). The Word add-in has no resume UI and shares one session
    across review + chat, so it must NOT interrupt — the blocker is carried to
    the client via `report.requires_attorney` instead.
    """
    task = state.get("task_type", "")
    if task in ("contract_generation", "drafting"):
        return "human_review"
    if task == "contract_review":
        if state.get("risk_level") in ("high", "medium") and state.get("interactive_review"):
            return "human_review"
        return "output_formatter"
    if state.get("risk_level") in ("high", "medium"):
        return "human_review"
    return "output_formatter"
