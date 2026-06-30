"""SQLite store for persisted contract reviews, keyed to a document_id.

Mirrors memory/audit.py (plain sqlite3, db_path-per-call). Stores the FULL
markdown review, one row per (document_id, session_id) — list-shaped so a
re-review appends a new session rather than overwriting. The schema is the
natural home for the later FTS cross-matter precedent layer.

Writes are LOUD: save_review lets exceptions propagate. A lost review write must
never be silent — the user believes their review was saved.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS review_store (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    document_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    contract_type TEXT NOT NULL DEFAULT '',
    review_markdown TEXT NOT NULL
)
"""

_CREATE_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_review_doc ON review_store (document_id, id)"
)


def init_review_db(db_path: str) -> None:
    """Create the review_store table + index if absent."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(_CREATE_TABLE)
        conn.execute(_CREATE_INDEX)
        conn.commit()
    finally:
        conn.close()
    logger.info("Review store initialized at %s", db_path)


def save_review(
    db_path: str, document_id: str, session_id: str, markdown: str, contract_type: str
) -> None:
    """Append one review row. Raises on any failure — never a silent no-op."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """INSERT INTO review_store
               (timestamp, document_id, session_id, contract_type, review_markdown)
               VALUES (?, ?, ?, ?, ?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                document_id, session_id, contract_type, markdown,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    logger.info("Review saved: document_id=%s session=%s", document_id, session_id)


def _row_to_dict(row: tuple) -> dict:
    return {
        "timestamp": row[0], "session_id": row[1],
        "contract_type": row[2], "markdown": row[3],
    }


def load_latest_review(db_path: str, document_id: str) -> dict | None:
    """Most recent review for this document, or None."""
    if not document_id:
        return None
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """SELECT timestamp, session_id, contract_type, review_markdown
               FROM review_store WHERE document_id = ? ORDER BY id DESC LIMIT 1""",
            (document_id,),
        )
        row = cur.fetchone()
    finally:
        conn.close()
    return _row_to_dict(row) if row else None


def load_history(db_path: str, document_id: str) -> list[dict]:
    """All reviews for this document, newest first."""
    if not document_id:
        return []
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """SELECT timestamp, session_id, contract_type, review_markdown
               FROM review_store WHERE document_id = ? ORDER BY id DESC""",
            (document_id,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]
