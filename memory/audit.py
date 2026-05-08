# memory/audit.py
"""SQLite audit log — records every skill invocation."""

import sqlite3
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    skill_name TEXT NOT NULL,
    task_type TEXT NOT NULL,
    request_summary TEXT NOT NULL,
    risk_level TEXT NOT NULL DEFAULT 'low',
    review_status TEXT NOT NULL DEFAULT 'not_required',
    review_notes TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0
)
"""


def init_audit_db(db_path: str) -> None:
    """Create the audit_log table if it doesn't exist."""
    conn = sqlite3.connect(db_path)
    conn.execute(_CREATE_TABLE)
    conn.commit()
    conn.close()
    logger.info("Audit DB initialized at %s", db_path)


def write_audit_log(
    db_path: str,
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
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO audit_log
           (timestamp, session_id, user_id, skill_name, task_type,
            request_summary, risk_level, review_status, review_notes, duration_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            datetime.now(timezone.utc).isoformat(),
            session_id,
            user_id,
            skill_name,
            task_type,
            request_summary,
            risk_level,
            review_status,
            review_notes,
            duration_ms,
        ),
    )
    conn.commit()
    conn.close()
    logger.info("Audit log: %s/%s for user %s", skill_name, task_type, user_id)
