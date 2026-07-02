# graph/nodes/memory_writer.py
"""Memory writer — persists audit log to SQLite."""

import logging

from config import get_settings
from langfuse.decorators import observe

from graph.state import LegalAgentState
from memory.audit import init_audit_db, write_audit_log
from memory.review_store import init_review_db, save_review

logger = logging.getLogger(__name__)

_db_initialized = False
_review_db_initialized = False


@observe(name="memory_writer")
def memory_writer(state: LegalAgentState) -> dict:
    """Writes the audit log; on a contract_review turn also persists the review.
    Returns {} normally, or {'report': {...}} with review_persist_error if the review write fails."""
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

    # Persist the full markdown review, keyed to the document. Loud on failure:
    # a lost write must not look like a save (the user believes it persisted).
    if state.get("task_type") == "contract_review" and state.get("llm_response"):
        global _review_db_initialized
        if not _review_db_initialized:
            init_review_db(settings.sqlite_path)
            _review_db_initialized = True
        try:
            save_review(
                db_path=settings.sqlite_path,
                document_id=state.get("document_id", ""),
                session_id=state.get("session_id", ""),
                markdown=state.get("llm_response", ""),
                contract_type=state.get("contract_type_detected", ""),
            )
        except Exception as e:
            logger.error("[memory_writer] FAILED to persist review: %s", e)
            report = {**(state.get("report") or {}), "review_persist_error": str(e)}
            return {"report": report}

    return {}
