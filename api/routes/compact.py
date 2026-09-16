# api/routes/compact.py
"""Condense earlier conversation into validated verbatim quotes.

A distinct endpoint rather than a flag on /api/query: compaction produces no
answer, carries its own latency (~10 s of generation for a capped segment at the
measured 51.4 tok/s, plus prefill of the range), and must not sit on the turn
path.

Failure is LOUD — a 500. The attorney clicked and was told it happened, so a
silent failure is a lie. "Nothing to condense" is not a failure and answers 200.

It returns what was condensed, NOT a fresh breakdown: this endpoint has no
document text and no grounding decision, so any breakdown it synthesised would
be a guess — and the counter's own honesty constraint is that its figures are
the last turn's real measured values. The pane's counter updates on the next
message.
"""
from fastapi import APIRouter, Depends, HTTPException

from api.auth import resolve_user_id
from api.models import ApiResponse, CompactRequest
from config import get_settings
from observability.degradations import COMPACTION_FAILED, OUTCOME_FAILED, derive_outcome
from observability.spans import (
    degradations, mark_failed, set_outcome, set_trace_attributes, traced,
)
from skills.legal_research.compaction import compact_conversation

router = APIRouter(prefix="/api")


@router.post("/compact", response_model=ApiResponse)
@traced("compact")
def post_compact(
    body: CompactRequest, user_id: str = Depends(resolve_user_id)
) -> ApiResponse:
    if not get_settings().compaction_enabled:
        raise HTTPException(status_code=403, detail="compaction is disabled")
    document_id = body.document_id.strip()
    if not document_id:
        raise HTTPException(status_code=400, detail="document_id is required")

    # /api/compact was previously untraced, so compact_conversation's internal
    # traced_invoke ("llm") span had no active root to nest under and became
    # its own parentless trace — no user, session or document attached to the
    # most-debugged subsystem in the repo. @traced("compact") above makes this
    # handler that root; naming it again here (matching the decorator) is what
    # submit_query does too — set_trace_attributes renames the same span.
    set_trace_attributes(
        name="compact",
        user_id=user_id,
        metadata={"document_id": document_id, "reclaim_chars": body.reclaim_chars},
    )

    result = compact_conversation(document_id, user_id, body.reclaim_chars)

    # COMPACTION_FAILED is recorded HERE, not at compaction.py's own excepts:
    # generation is retried twice inside a loop, so recording per-attempt would
    # mark a run failed that then succeeded. Every terminal failure surfaces as
    # result["error"], so this is the one place that cannot double-count.
    if result["error"]:
        mark_failed(COMPACTION_FAILED, detail=result["error"])
        set_outcome(OUTCOME_FAILED)
        raise HTTPException(status_code=500, detail=result["error"])

    # A net-benefit refusal (the summary would be no smaller than the messages
    # it replaces) is the guard working correctly, not a degradation — it must
    # never flip app.outcome. But it disarms the pane's auto-compaction latch,
    # which is operationally significant, so it still lands on the span, as
    # its own field rather than a reason code.
    if result.get("reason"):
        set_trace_attributes(metadata={"compaction.refused_reason": result["reason"]})

    set_outcome(derive_outcome(degradations()))
    return ApiResponse(status="ok", data=result)
