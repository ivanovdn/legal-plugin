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
from skills.legal_research.compaction import compact_conversation

router = APIRouter(prefix="/api")


@router.post("/compact", response_model=ApiResponse)
def post_compact(
    body: CompactRequest, user_id: str = Depends(resolve_user_id)
) -> ApiResponse:
    if not get_settings().compaction_enabled:
        raise HTTPException(status_code=403, detail="compaction is disabled")
    document_id = body.document_id.strip()
    if not document_id:
        raise HTTPException(status_code=400, detail="document_id is required")
    result = compact_conversation(document_id, user_id, body.reclaim_chars)
    if result["error"]:
        raise HTTPException(status_code=500, detail=result["error"])
    return ApiResponse(status="ok", data=result)
