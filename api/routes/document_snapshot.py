"""POST /api/document-snapshot — the pane's copy of the document (SP4).

Quiet like /api/events: always 200, because a capture must never reach the
attorney — a failure records nothing and says so in `recorded`. Identity and
the client facts come from the request headers, never the body, so the SSO
cutover reaches this with no change here.
Spec: docs/superpowers/specs/2026-10-15-document-changes-design.md
"""
import logging
from dataclasses import asdict

from fastapi import APIRouter, Depends

from api.auth import resolve_user_id
from api.client_info import ClientInfo, resolve_client_info
from api.models import ApiResponse, DocumentSnapshot
from config import get_settings
from memory.document_store import save_snapshot

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


@router.post("/document-snapshot", response_model=ApiResponse)
def post_document_snapshot(
    body: DocumentSnapshot,
    user_id: str = Depends(resolve_user_id),
    client: ClientInfo = Depends(resolve_client_info),
) -> ApiResponse:
    settings = get_settings()
    if not settings.document_capture_enabled:
        return ApiResponse(status="ok", data={"recorded": 0, "enabled": False})
    try:
        save_snapshot(
            attorney_id=user_id, session_id=body.session_id, document_id=body.document_id,
            moment=body.moment, current_text=body.current_text, original_text=body.original_text,
            tracked_changes=[change.model_dump() for change in body.tracked_changes],
            tracked_total=body.tracked_total, client=asdict(client),
            backend_build=settings.build_sha,
        )
    except Exception as e:  # noqa: BLE001 — telemetry's own failure (class 4): nobody to tell
        # The type only: Postgres quotes offending input — contract text — in some messages.
        logger.warning("document snapshot not recorded (%s) — document %s, moment %s",
                       type(e).__name__, body.document_id, body.moment)
        return ApiResponse(status="ok", data={"recorded": 0, "enabled": True})
    return ApiResponse(status="ok", data={"recorded": 1, "enabled": True})
