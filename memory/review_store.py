"""Postgres store for persisted contract reviews, keyed to a document_id.

Stores the FULL markdown review, one row per (document_id, session_id) — list-
shaped so a re-review appends a new row rather than overwriting. The natural
home for the later FTS cross-matter precedent layer.

Writes are LOUD: save_review lets exceptions propagate. A lost review write must
never be silent — the user believes their review was saved.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from memory.db import get_pool

logger = logging.getLogger(__name__)


def save_review(document_id: str, session_id: str, markdown: str, contract_type: str) -> None:
    """Append one review row. Raises on any failure — never a silent no-op."""
    with get_pool().connection() as conn:
        conn.execute(
            """INSERT INTO review_store
               (timestamp, document_id, session_id, contract_type, review_markdown)
               VALUES (%s, %s, %s, %s, %s)""",
            (
                datetime.now(timezone.utc).isoformat(),
                document_id, session_id, contract_type, markdown,
            ),
        )
    logger.info("Review saved: document_id=%s session=%s", document_id, session_id)


def _row_to_dict(row: tuple) -> dict:
    return {
        "timestamp": row[0], "session_id": row[1],
        "contract_type": row[2], "markdown": row[3],
    }


def load_latest_review(document_id: str) -> dict | None:
    """Most recent review for this document, or None."""
    if not document_id:
        return None
    with get_pool().connection() as conn:
        cur = conn.execute(
            """SELECT timestamp, session_id, contract_type, review_markdown
               FROM review_store WHERE document_id = %s ORDER BY id DESC LIMIT 1""",
            (document_id,),
        )
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def load_history(document_id: str) -> list[dict]:
    """All reviews for this document, newest first."""
    if not document_id:
        return []
    with get_pool().connection() as conn:
        cur = conn.execute(
            """SELECT timestamp, session_id, contract_type, review_markdown
               FROM review_store WHERE document_id = %s ORDER BY id DESC""",
            (document_id,),
        )
        rows = cur.fetchall()
    return [_row_to_dict(r) for r in rows]
