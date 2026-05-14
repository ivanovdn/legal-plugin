# api/routes/query.py
"""Query endpoints — submit requests, resume interrupts, check status."""

import logging
import uuid

from fastapi import APIRouter, Header
from langfuse.decorators import observe, langfuse_context

from api.models import ApiResponse, QueryRequest, ResumeRequest
from graph.graph import build_graph

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

_graph = None


def _get_graph():
    """Lazy-init compiled graph."""
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


@router.post("/query", response_model=ApiResponse)
@observe(name="query")
def submit_query(
    body: QueryRequest,
    x_user_id: str = Header("anonymous", alias="X-User-ID"),
):
    """Submit a legal request for graph execution."""
    session_id = body.session_id or str(uuid.uuid4())

    langfuse_context.update_current_trace(
        name=f"query:{body.task_type or 'auto'}",
        user_id=x_user_id,
        session_id=session_id,
        input=body.request,
    )

    initial_state = {
        "request": body.request,
        "user_id": x_user_id,
        "uploaded_docs": [{"text": body.uploaded_text}] if body.uploaded_text else [],
        "task_type": body.task_type,
        "skill_plan": [body.task_type] if body.task_type else [],
        "retrieval_query": "",
        "retrieved_chunks": [],
        "filters": body.filters,
        "messages": [],
        "llm_response": "",
        "risk_level": "",
        "risk_flags": [],
        "awaiting_review": False,
        "attorney_notes": "",
        "report": {},
        "session_id": session_id,
        "checkpoint_ref": "",
        "trace_id": session_id,
    }

    graph = _get_graph()

    try:
        result = graph.invoke(initial_state)
        return ApiResponse(
            status="ok",
            data={
                "session_id": session_id,
                "task_type": result.get("task_type", ""),
                "report": result.get("report", {}),
                "risk_level": result.get("risk_level", ""),
                "awaiting_review": result.get("awaiting_review", False),
            },
        )
    except Exception as e:
        logger.exception("Graph execution failed")
        return ApiResponse(status="error", errors=[str(e)])


@router.post("/query/{session_id}/resume", response_model=ApiResponse)
def resume_query(session_id: str, body: ResumeRequest):
    """Resume graph execution after human review interrupt."""
    return ApiResponse(
        status="error",
        errors=["Resume requires Redis checkpointer — not yet wired"],
    )


@router.get("/query/{session_id}/status", response_model=ApiResponse)
def query_status(session_id: str):
    """Check the status of a graph execution."""
    return ApiResponse(
        status="ok",
        data={"session_id": session_id, "status": "unknown"},
    )
