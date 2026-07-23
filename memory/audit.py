# memory/audit.py
"""Audit log — records every skill invocation (Postgres-backed)."""

import logging
from datetime import datetime, timezone

from memory.db import get_pool

logger = logging.getLogger(__name__)


def write_audit_log(
    session_id: str,
    user_id: str,
    skill_name: str,
    task_type: str,
    request_summary: str,
    risk_level: str = "low",
    review_status: str = "not_required",
    review_notes: str = "",
    duration_ms: int = 0,
) -> None:
    """Write a single audit log entry."""
    with get_pool().connection() as conn:
        conn.execute(
            """INSERT INTO audit_log
               (timestamp, session_id, user_id, skill_name, task_type,
                request_summary, risk_level, review_status, review_notes, duration_ms)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                datetime.now(timezone.utc).isoformat(),
                session_id, user_id, skill_name, task_type,
                request_summary, risk_level, review_status, review_notes, duration_ms,
            ),
        )
    logger.info("Audit log: %s/%s for user %s", skill_name, task_type, user_id)
