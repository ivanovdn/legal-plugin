# graph/nodes/memory_writer.py
"""Memory writer — persists audit log + review to Postgres; conversation turns to Postgres (best-effort)."""

import logging

from config import get_settings

from graph.state import LegalAgentState
from memory.audit import write_audit_log
from memory.conversation_store import append_turn
from memory.review_store import save_review
from observability.spans import traced

logger = logging.getLogger(__name__)


@traced("memory_writer")
def memory_writer(state: LegalAgentState) -> dict:
    """Writes the audit log; on a contract_review turn also persists the review.

    Returns {} normally, or {'report': {...}} carrying review_persist_error
    (the review write failed) and/or memory_degraded (the audit store was
    unreachable). This node runs AFTER output_formatter, so the returned report
    is the only channel back to the payload — mutating state here is too late.
    """
    settings = get_settings()

    review_status = "pending" if state.get("awaiting_review") else "not_required"
    report_updates: dict = {}

    entry = dict(
        session_id=state.get("session_id", ""),
        user_id=state.get("user_id", ""),
        skill_name=state.get("task_type", "unknown"),
        task_type=state.get("task_type", ""),
        request_summary=state.get("request", "")[:200],
        risk_level=state.get("risk_level", "low"),
        review_status=review_status,
        review_notes=state.get("attorney_notes", ""),
        duration_ms=0,
        user_name=state.get("user_name", ""),
    )

    # Degrade rather than die when the audit store is unreachable. A turn does
    # not need Postgres to answer — the document arrives in the request — and
    # this was the one unwrapped store call on the path, so an app-db outage
    # killed every turn after a long stall while an equivalent Redis outage
    # degrades cleanly. Constraint 6 ("every skill invocation → audit log") is
    # relaxed here deliberately and visibly: the entry is logged in full so it
    # stays recoverable, and the turn is flagged so the pane shows the amber
    # banner instead of pretending memory is healthy.
    try:
        write_audit_log(**entry)
        logger.info("[memory_writer] audit log written for session=%s", state.get("session_id"))
    except Exception as e:
        logger.error(
            "[memory_writer] AUDIT WRITE FAILED (%s) — entry not persisted to the "
            "audit_log table; recorded here instead: %s", e, entry,
        )
        report_updates["memory_degraded"] = True

    # Persist the full markdown review, keyed to the document. Loud on failure:
    # a lost write must not look like a save (the user believes it persisted).
    if state.get("task_type") == "contract_review" and state.get("llm_response"):
        try:
            save_review(
                document_id=state.get("document_id", ""),
                session_id=state.get("session_id", ""),
                markdown=state.get("llm_response", ""),
                contract_type=state.get("contract_type_detected", ""),
            )
        except Exception as e:
            logger.error("[memory_writer] FAILED to persist review: %s", e)
            report_updates["review_persist_error"] = str(e)

    # Persist the doc-chat conversation, keyed to (document, attorney). Best-effort:
    # a lost turn is a convenience loss, not a legal record — never fail the turn.
    if state.get("task_type") == "research" and settings.conversation_store_enabled:
        document_id = state.get("document_id", "")
        attorney_id = state.get("user_id", "")
        if document_id and attorney_id and state.get("llm_response"):
            try:
                append_turn(
                    document_id=document_id,
                    attorney_id=attorney_id,
                    user_text=state.get("request", ""),
                    assistant_text=state.get("llm_response", ""),
                )
            except Exception as e:
                logger.error(
                    "[memory_writer] conversation append failed (non-fatal): %s", e
                )

    # One return, so an audit failure and a review failure can't hide each
    # other — both stores live in app-db and one outage fails both at once.
    if report_updates:
        return {"report": {**(state.get("report") or {}), **report_updates}}
    return {}
