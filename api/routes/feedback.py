"""Tester feedback endpoints — written reports and interaction telemetry.

Identity comes from resolve_user_id/resolve_user_name, never the request body,
so the O365 SSO cutover reaches feedback with no change here.

The two endpoints fail differently on purpose. /api/feedback surfaces a store
failure as a 500 because the attorney was told their report sent. /api/events
always returns 200 because a telemetry outage must be invisible in the pane.
"""
from fastapi import APIRouter, Depends, HTTPException

from api.auth import resolve_user_id, resolve_user_name
from api.models import ApiResponse, FeedbackSubmission, InteractionEventBatch
from config import get_settings
from memory.feedback_store import record_events, save_feedback, truncate_snapshot

router = APIRouter(prefix="/api")


@router.post("/feedback", response_model=ApiResponse)
def post_feedback(
    body: FeedbackSubmission,
    user_id: str = Depends(resolve_user_id),
    user_name: str = Depends(resolve_user_name),
) -> ApiResponse:
    settings = get_settings()
    if not settings.feedback_enabled:
        raise HTTPException(status_code=403, detail="feedback is disabled")
    if not body.comment.strip():
        raise HTTPException(status_code=400, detail="comment is empty")
    # No try/except: a store failure must reach the attorney as a 500.
    row_id = save_feedback(
        turn_id=body.turn_id,
        trace_id=body.trace_id,
        session_id=body.session_id,
        document_id=body.document_id,
        attorney_id=user_id,
        user_name=user_name,
        surface=body.surface,
        target_kind=body.target_kind,
        target_ref=body.target_ref,
        comment=body.comment,
        snapshot=truncate_snapshot(body.snapshot, settings.feedback_snapshot_max_chars),
    )
    return ApiResponse(status="ok", data={"saved": True, "id": row_id})


@router.post("/events", response_model=ApiResponse)
def post_events(
    body: InteractionEventBatch,
    user_id: str = Depends(resolve_user_id),
) -> ApiResponse:
    if not get_settings().feedback_enabled:
        return ApiResponse(status="ok", data={"recorded": 0})
    written = record_events([e.model_dump() for e in body.events], attorney_id=user_id)
    return ApiResponse(status="ok", data={"recorded": written})
