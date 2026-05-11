# api/routes/health.py
"""Health check endpoint."""

from fastapi import APIRouter

from api.models import ApiResponse

router = APIRouter()


@router.get("/health", response_model=ApiResponse)
def health_check():
    """Check that the API and core services are reachable."""
    return ApiResponse(
        status="ok",
        data={
            "services": {
                "api": "running",
            }
        },
    )
