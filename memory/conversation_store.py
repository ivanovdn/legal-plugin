"""Postgres store for per-attorney chat conversations, keyed to (document_id, attorney_id).

Append-per-turn: each chat turn inserts one 'user' row then one 'assistant' row.
Reads return the most-recent window in chronological order for the chat prompt,
optionally floored at the highest row already condensed into a
conversation_summary segment (see memory/conversation_summary.py).

Writes raise on failure at the module boundary (like save_review); the caller
(memory_writer) applies a best-effort policy — a lost conversation turn is a
convenience loss, not a lost legal record, so it must not break the turn.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from memory.db import get_pool
from observability.spans import traced

logger = logging.getLogger(__name__)


@traced("db.append_turn")
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


@traced("db.load_recent")
def load_recent(
    document_id: str, attorney_id: str, max_messages: int, after_id: int = 0
) -> list[dict]:
    """Up to max_messages most-recent messages for the pair, chronological (oldest first).

    after_id is the compaction floor: rows at or below it have already been
    condensed into a conversation_summary segment, so replaying them verbatim
    would undo the compaction. 0 (the default) means no floor, which is the
    honest state of a conversation that has never been condensed.
    """
    if not document_id or not attorney_id or max_messages <= 0:
        return []
    with get_pool().connection() as conn:
        cur = conn.execute(
            """SELECT role, content FROM conversation_store
               WHERE document_id = %s AND attorney_id = %s AND id > %s
               ORDER BY id DESC LIMIT %s""",
            (document_id, attorney_id, after_id, max_messages),
        )
        rows = cur.fetchall()
    rows.reverse()  # DESC fetch -> chronological
    return [{"role": r[0], "content": r[1]} for r in rows]


def load_rows_after(
    document_id: str, attorney_id: str, after_id: int, limit: int
) -> list[dict]:
    """Up to `limit` rows with id > after_id, OLDEST first, carrying their ids.

    The mirror image of load_recent: compaction condenses the oldest rows it
    has not condensed yet, so it truncates the tail where load_recent truncates
    the head. Ids travel because the validation gate cites them — a quote is
    only checkable against the row it names.
    """
    if not document_id or not attorney_id or limit <= 0:
        return []
    with get_pool().connection() as conn:
        cur = conn.execute(
            """SELECT id, role, content FROM conversation_store
               WHERE document_id = %s AND attorney_id = %s AND id > %s
               ORDER BY id ASC LIMIT %s""",
            (document_id, attorney_id, after_id, limit),
        )
        rows = cur.fetchall()
    return [{"id": r[0], "role": r[1], "content": r[2]} for r in rows]


@traced("db.row_lengths_after")
def row_lengths_after(document_id: str, attorney_id: str, after_id: int) -> list[int]:
    """Content lengths of the messages past the compaction floor, OLDEST FIRST.

    Feeds the counter's "compressible history exists" condition — without it the
    Condense control would appear when there is nothing left to condense, and a
    control that offers a no-op teaches attorneys to ignore it.

    Lengths rather than a bare COUNT(*) because the automatic floor is measured in
    characters: six one-line questions and two pasted contracts are the same count
    and nothing alike as a budget problem. Order matters — the caller drops the
    newest `compaction_keep_recent_messages` before summing, and those are the rows
    compaction would leave verbatim. Cheap enough for a per-turn UI query: integers,
    never content.
    """
    if not document_id or not attorney_id:
        return []
    with get_pool().connection() as conn:
        cur = conn.execute(
            """SELECT length(content) FROM conversation_store
               WHERE document_id = %s AND attorney_id = %s AND id > %s
               ORDER BY id""",
            (document_id, attorney_id, after_id),
        )
        return [int(r[0]) for r in cur.fetchall()]
