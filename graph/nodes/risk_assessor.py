# graph/nodes/risk_assessor.py
"""Risk assessor — sets risk_level and the attorney-required signal.

Two signals, by task:
  - contract_review → the review VERDICT (`_assess_review_verdict`): a
    "Do not send for signature" status, or any Red / Missing Context finding,
    is a blocker. This is the contract-review concern — whether the document is
    safe to sign — and it lives in the LLM output text, not in citation state.
  - everything else → citation grounding (`_check_citations`): a research answer
    with no retrievable source is flagged for attorney review.

Detection mirrors the Word add-in parser (clients/word/src/parser.ts) so the
server-side verdict and the client-rendered blockers always reconcile.
"""

import logging
import re

from langfuse.decorators import observe, langfuse_context

from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def _check_citations(
    llm_response: str, chunks: list[dict], has_uploaded_doc: bool = False
) -> list[dict]:
    """Check if the response references retrieved documents.

    When the user attached a document (e.g. the Word add-in chat answering
    about the open contract), the attached doc is the citation source, so
    having no RAG chunks is expected — not a risk. Otherwise, a response with
    no retrievable sources is flagged for attorney review.
    """
    flags = []
    if not chunks:
        if has_uploaded_doc:
            return flags
        flags.append({"reason": "No citation possible — no chunks retrieved", "severity": "high"})
        return flags

    cited = False
    for chunk in chunks:
        doc_id = chunk.get("doc_id", "")
        doc_title = chunk.get("doc_title", "")
        if doc_id and doc_id in llm_response:
            cited = True
            break
        if doc_title and doc_title.lower() in llm_response.lower():
            cited = True
            break

    if not cited:
        flags.append({"reason": "No citation found — response does not reference any retrieved document", "severity": "high"})

    return flags


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


@observe(name="risk_assessor")
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
        risk_flags = _check_citations(llm_response, chunks, has_uploaded_doc)
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

    # Surface the verdict on the Langfuse trace (the human-review decision was
    # previously invisible). Tracing must never break the node.
    try:
        langfuse_context.update_current_trace(
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
