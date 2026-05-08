# graph/nodes/memory_writer.py
"""Memory writer — persists audit log to SQLite."""

import logging

from config import get_settings
from graph.state import LegalAgentState
from memory.audit import init_audit_db, write_audit_log

logger = logging.getLogger(__name__)

_db_initialized = False


def memory_writer(state: LegalAgentState) -> LegalAgentState:
    """Write skill invocation to SQLite audit log."""
    global _db_initialized
    settings = get_settings()

    if not _db_initialized:
        init_audit_db(settings.sqlite_path)
        _db_initialized = True

    review_status = "pending" if state.get("awaiting_review") else "not_required"

    write_audit_log(
        db_path=settings.sqlite_path,
        session_id=state.get("session_id", ""),
        user_id=state.get("user_id", ""),
        skill_name=state.get("task_type", "unknown"),
        task_type=state.get("task_type", ""),
        request_summary=state.get("request", "")[:200],
        risk_level=state.get("risk_level", "low"),
        review_status=review_status,
        review_notes=state.get("attorney_notes", ""),
        duration_ms=0,
    )

    logger.info("[memory_writer] audit log written for session=%s", state.get("session_id"))
    return state
