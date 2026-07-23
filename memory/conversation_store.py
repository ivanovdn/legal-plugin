"""Postgres store for per-attorney chat conversations, keyed to (document_id, attorney_id).

Append-per-turn: each chat turn inserts one 'user' row then one 'assistant' row.
Reads return the most-recent window in chronological order for the chat prompt.

Writes raise on failure at the module boundary (like save_review); the caller
(memory_writer) applies a best-effort policy — a lost conversation turn is a
convenience loss, not a lost legal record, so it must not break the turn.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from memory.db import get_pool

logger = logging.getLogger(__name__)


def append_turn(document_id: str, attorney_id: str, user_text: str, assistant_text: str) -> None:
    """Append one turn: a 'user' row then an 'assistant' row. Raises on failure."""
    ts = datetime.now(timezone.utc).isoformat()
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO conversation_store
                   (timestamp, document_id, attorney_id, role, content)
                   VALUES (%s, %s, %s, %s, %s)""",
                [
                    (ts, document_id, attorney_id, "user", user_text),
                    (ts, document_id, attorney_id, "assistant", assistant_text),
                ],
            )
    logger.info("Conversation turn saved: document_id=%s attorney_id=%s", document_id, attorney_id)


def load_recent(document_id: str, attorney_id: str, max_messages: int) -> list[dict]:
    """Up to max_messages most-recent messages for the pair, chronological (oldest first)."""
    if not document_id or not attorney_id or max_messages <= 0:
        return []
    with get_pool().connection() as conn:
        cur = conn.execute(
            """SELECT role, content FROM conversation_store
               WHERE document_id = %s AND attorney_id = %s
               ORDER BY id DESC LIMIT %s""",
            (document_id, attorney_id, max_messages),
        )
        rows = cur.fetchall()
    rows.reverse()  # DESC fetch -> chronological
    return [{"role": r[0], "content": r[1]} for r in rows]
