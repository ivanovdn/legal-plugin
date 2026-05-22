# clients/web/api_client.py
"""HTTP client for communicating with the FastAPI backend."""

import httpx

from config import get_settings


def _base_url() -> str:
    settings = get_settings()
    return f"http://localhost:{settings.api_port}"


async def submit_query(
    request: str,
    user_id: str,
    task_type: str = "",
    filters: dict | None = None,
    session_id: str = "",
    uploaded_text: str = "",
) -> dict:
    """POST /api/query — submit a legal request."""
    async with httpx.AsyncClient(timeout=300.0) as client:
        body = {
            "request": request,
            "task_type": task_type,
            "session_id": session_id,
            "filters": filters or {},
        }
        if uploaded_text:
            body["uploaded_text"] = uploaded_text
        response = await client.post(
            f"{_base_url()}/api/query",
            json=body,
            headers={"X-User-ID": user_id},
        )
        response.raise_for_status()
        return response.json()


async def resume_query(
    session_id: str,
    approved: bool,
    notes: str = "",
    revised_response: str = "",
) -> dict:
    """POST /api/query/{session_id}/resume — submit attorney verdict."""
    async with httpx.AsyncClient(timeout=900.0) as client:
        response = await client.post(
            f"{_base_url()}/api/query/{session_id}/resume",
            json={
                "approved": approved,
                "notes": notes,
                "revised_response": revised_response,
            },
        )
        response.raise_for_status()
        return response.json()


async def ingest_file(
    file_path: str,
    filename: str,
    client_id: str = "internal",
    jurisdiction: str = "",
    doc_type: str = "contract",
    sensitivity: str = "internal",
    collection: str = "legal_docs",
) -> dict:
    """POST /api/ingest — upload and ingest a document."""
    async with httpx.AsyncClient(timeout=300.0) as client:
        with open(file_path, "rb") as f:
            response = await client.post(
                f"{_base_url()}/api/ingest",
                files={"file": (filename, f)},
                data={
                    "client_id": client_id,
                    "jurisdiction": jurisdiction,
                    "doc_type": doc_type,
                    "sensitivity": sensitivity,
                    "collection": collection,
                },
            )
        response.raise_for_status()
        return response.json()


async def health_check() -> dict:
    """GET /health — check backend status."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{_base_url()}/health")
        response.raise_for_status()
        return response.json()
