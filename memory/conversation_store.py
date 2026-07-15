"""SQLite store for per-attorney chat conversations, keyed to (document_id, attorney_id).

Mirrors memory/review_store.py (plain sqlite3, db_path-per-call). Append-per-turn:
each chat turn inserts one 'user' row then one 'assistant' row. Reads return the
most-recent window in chronological order for injection into the chat prompt.

Writes raise on failure at the module boundary (like save_review); the caller
(memory_writer) applies a best-effort policy — a lost conversation turn is a
convenience loss, not a lost legal record, so it must not break the turn.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS conversation_store (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    document_id TEXT NOT NULL,
    attorney_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL
)
"""

_CREATE_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_conv "
    "ON conversation_store (document_id, attorney_id, id)"
)


def init_conversation_db(db_path: str) -> None:
    """Create the conversation_store table + index if absent."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(_CREATE_TABLE)
        conn.execute(_CREATE_INDEX)
        conn.commit()
    finally:
        conn.close()
    logger.info("Conversation store initialized at %s", db_path)


def append_turn(
    db_path: str, document_id: str, attorney_id: str,
    user_text: str, assistant_text: str,
) -> None:
    """Append one turn: a 'user' row then an 'assistant' row. Raises on failure."""
    ts = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(
            """INSERT INTO conversation_store
               (timestamp, document_id, attorney_id, role, content)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (ts, document_id, attorney_id, "user", user_text),
                (ts, document_id, attorney_id, "assistant", assistant_text),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    logger.info(
        "Conversation turn saved: document_id=%s attorney_id=%s",
        document_id, attorney_id,
    )


def load_recent(
    db_path: str, document_id: str, attorney_id: str, max_messages: int
) -> list[dict]:
    """Up to max_messages most-recent messages for the pair, chronological (oldest first)."""
    if not document_id or not attorney_id:
        return []
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """SELECT role, content FROM conversation_store
               WHERE document_id = ? AND attorney_id = ?
               ORDER BY id DESC LIMIT ?""",
            (document_id, attorney_id, max_messages),
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    rows.reverse()  # DESC fetch -> chronological
    return [{"role": r[0], "content": r[1]} for r in rows]
