# api/routes/query.py
"""Query endpoints — submit requests, resume interrupts, check status."""

import logging
import uuid

from fastapi import APIRouter, Header
from langfuse.decorators import observe, langfuse_context
from langgraph.types import Command
from redis.exceptions import RedisError

from api.models import ApiResponse, QueryRequest, ResumeRequest
from config import get_settings
from graph.checkpointer import build_checkpointer, refresh_ttl
from graph.graph import build_graph

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

_graph = None
_checkpointer_active = False
_stateless_graph = None


def _get_stateless_graph():
    """A checkpointer-less graph for the degraded fallback (no Redis, no history)."""
    global _stateless_graph
    if _stateless_graph is None:
        _stateless_graph = build_graph(checkpointer=None)
    return _stateless_graph


def _is_redis_failure(exc: BaseException) -> bool:
    """True if exc (or a cause/context in its chain) is a Redis error, or its
    message shows a connection failure — used to detect a mid-invoke checkpointer
    outage so the turn can degrade instead of hard-failing."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, RedisError):
            return True
        cur = cur.__cause__ or cur.__context__
    msg = str(exc).lower()
    return "connection refused" in msg or "connecting to" in msg


def _get_graph():
    """Lazy-init compiled graph with optional Redis checkpointer."""
    global _graph, _checkpointer_active
    if _graph is None:
        settings = get_settings()
        cp = build_checkpointer() if settings.checkpointer_enabled else None
        _checkpointer_active = cp is not None
        if settings.checkpointer_enabled and cp is None:
            logger.error(
                "Checkpointer ENABLED but unavailable — sessions are stateless this run. "
                "Responses will report memory_degraded=True."
            )
        _graph = build_graph(checkpointer=cp)
    return _graph


def _memory_degraded(report: dict) -> bool:
    """True when this turn's memory was degraded — either the report flagged it
    (in-graph read failure) or the checkpointer is enabled but unavailable."""
    if report.get("memory_degraded"):
        return True
    return get_settings().checkpointer_enabled and not _checkpointer_active


def _payload_from_result(result: dict, session_id: str) -> dict:
    """Shape the response payload for both submit and resume.

    LangGraph 0.6 surfaces an active interrupt via the `__interrupt__` key
    on the result dict. State mutations made before interrupt() raises are
    NOT persisted, so we read the interrupt's `.value` (the dict passed to
    interrupt()) for the payload. The legacy `awaiting_review` flag is also
    honored so unit tests that mock graph.invoke continue to work.
    """
    interrupts = result.get("__interrupt__") or []
    if interrupts:
        # interrupts[0] is a langgraph Interrupt with .value (the dict passed to interrupt())
        first = interrupts[0]
        value = getattr(first, "value", first) if not isinstance(first, dict) else first
        return {
            "session_id": session_id,
            "awaiting_review": True,
            "interrupt_payload": {
                "task_type": value.get("task_type", ""),
                "risk_level": value.get("risk_level", ""),
                "llm_response": value.get("llm_response", ""),
                "risk_flags": value.get("risk_flags", []),
                "review_iterations": value.get("review_iterations", 0),
            },
            "report": {},
            "memory_degraded": _memory_degraded(result.get("report", {}) or {}),
        }
    if result.get("awaiting_review"):
        # Legacy / test-mocked shape — keep working for unit tests that don't use real interrupts.
        return {
            "session_id": session_id,
            "awaiting_review": True,
            "interrupt_payload": {
                "task_type": result.get("task_type", ""),
                "risk_level": result.get("risk_level", ""),
                "llm_response": result.get("llm_response", ""),
                "risk_flags": result.get("risk_flags", []),
                "review_iterations": result.get("review_iterations", 0),
            },
            "report": {},
            "memory_degraded": _memory_degraded(result.get("report", {}) or {}),
        }
    report = result.get("report", {})
    return {
        "session_id": session_id,
        "task_type": result.get("task_type", ""),
        "report": report,
        "risk_level": result.get("risk_level", ""),
        "awaiting_review": False,
        "memory_degraded": _memory_degraded(report),
    }


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
        "chat_history": [],
        "review_iterations": 0,
        "report_notes_unincorporated": "",
        "previous_draft": "",
        "requires_attorney": False,
        "interactive_review": body.interactive_review,
        "document_id": "",
        "memory_degraded": False,
    }

    graph = _get_graph()
    config = {"configurable": {"thread_id": session_id}}

    try:
        result = graph.invoke(initial_state, config=config)
        refresh_ttl(session_id)
        return ApiResponse(status="ok", data=_payload_from_result(result, session_id))
    except Exception as e:
        if _checkpointer_active and _is_redis_failure(e):
            logger.error(
                "Checkpointer (Redis) failed mid-invoke (%s) — degrading to a stateless "
                "run; chat_history is lost this turn (memory_degraded=True).", e,
            )
            try:
                initial_state["memory_degraded"] = True
                result = _get_stateless_graph().invoke(initial_state)
                report = result.get("report") or {}
                if isinstance(report, dict):
                    report["memory_degraded"] = True
                    result["report"] = report
                return ApiResponse(status="ok", data=_payload_from_result(result, session_id))
            except Exception as e2:
                logger.exception("Stateless fallback failed after checkpointer outage")
                return ApiResponse(
                    status="error",
                    errors=[f"Session memory unavailable and fallback failed: {e2}"],
                )
        logger.exception("Graph execution failed")
        return ApiResponse(status="error", errors=[str(e)])


@router.post("/query/{session_id}/resume", response_model=ApiResponse)
@observe(name="resume")
def resume_query(session_id: str, body: ResumeRequest):
    """Resume graph execution after human review interrupt."""
    langfuse_context.update_current_trace(
        name=f"resume:{session_id}",
        session_id=session_id,
        input={
            "approved": body.approved,
            "notes": body.notes,
            "has_revised": bool(body.revised_response),
        },
    )

    graph = _get_graph()
    config = {"configurable": {"thread_id": session_id}}

    try:
        prior = graph.get_state(config)
    except Exception as e:
        logger.warning("resume: get_state failed for %s: %s", session_id, e)
        return ApiResponse(status="error", errors=["session expired or not found"])
    if not prior or not prior.values:
        return ApiResponse(status="error", errors=["session expired or not found"])

    try:
        result = graph.invoke(
            Command(resume={
                "approved": body.approved,
                "notes": body.notes,
                "revised_response": body.revised_response,
            }),
            config=config,
        )
        refresh_ttl(session_id)
        return ApiResponse(status="ok", data=_payload_from_result(result, session_id))
    except Exception as e:
        logger.exception("resume: graph invoke failed for %s", session_id)
        return ApiResponse(status="error", errors=[str(e)])


@router.get("/query/{session_id}/status", response_model=ApiResponse)
def query_status(session_id: str):
    """Check the status of a graph execution."""
    return ApiResponse(
        status="ok",
        data={"session_id": session_id, "status": "unknown"},
    )
