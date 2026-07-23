# api/routes/preferences.py
"""Attorney preference memory endpoints — read/write the caller's USER.md.

Keyed by attorney_id (resolve_user_id → X-User-ID today, O365 oid when SSO on).
Stage 1 of the self-improving harness; storage in memory/preferences.py.
"""
from fastapi import APIRouter, Depends, HTTPException

from api.auth import resolve_user_id
from api.models import ApiResponse, PreferencesUpdate
from config import get_settings
from memory.preferences import (
    PreferenceTooLargeError,
    load_preferences,
    save_preferences,
)

router = APIRouter(prefix="/api")


@router.get("/preferences", response_model=ApiResponse)
def get_preferences(user_id: str = Depends(resolve_user_id)) -> ApiResponse:
    settings = get_settings()
    if not settings.preferences_enabled:
        return ApiResponse(status="ok", data={"markdown": ""})
    try:
        markdown = load_preferences(settings.preferences_dir, user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ApiResponse(status="ok", data={"markdown": markdown})


@router.put("/preferences", response_model=ApiResponse)
def put_preferences(
    body: PreferencesUpdate, user_id: str = Depends(resolve_user_id)
) -> ApiResponse:
    settings = get_settings()
    if not settings.preferences_enabled:
        raise HTTPException(status_code=403, detail="preferences are disabled")
    try:
        save_preferences(settings.preferences_dir, user_id, body.markdown)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PreferenceTooLargeError as e:
        raise HTTPException(status_code=413, detail=str(e))
    return ApiResponse(status="ok", data={"saved": True})
