"""Postgres store for condensed conversation segments, keyed to (document_id, attorney_id).

Append-only and never re-summarised: each segment covers a FIXED from_id->to_id
range of conversation_store rows, so a fabrication cannot propagate into a later
generation and every block stays auditable against exactly the rows it came
from. A rolling summary would, by turn 100, have re-summarised itself several
times, leaving an error indistinguishable from fact.

Raw conversation_store rows are never deleted. A summary is a VIEW, never the
record — the same principle behind stripping fenced blocks on read rather than
on write. Any segment can be audited against its range, or discarded, without
touching history.

Writes raise at the module boundary (like save_review); the caller decides the
policy. Compaction's caller treats a failure as LOUD — the attorney clicked and
was told it happened.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from memory.db import get_pool
from observability.spans import traced

logger = logging.getLogger(__name__)


def append_segment(
    document_id: str, attorney_id: str, from_id: int, to_id: int, content: str
) -> int:
    """Append one condensed segment. Returns its row id. Raises on failure."""
    ts = datetime.now(timezone.utc).isoformat()
    with get_pool().connection() as conn:
        cur = conn.execute(
            """INSERT INTO conversation_summary
               (timestamp, document_id, attorney_id, from_id, to_id, content)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            (ts, document_id, attorney_id, from_id, to_id, content),
        )
        row_id = cur.fetchone()[0]
    logger.info(
        "Conversation segment saved: document_id=%s attorney_id=%s rows=%d-%d id=%s",
        document_id, attorney_id, from_id, to_id, row_id,
    )
    return int(row_id)


@traced("db.load_segments")
def load_segments(document_id: str, attorney_id: str, max_segments: int) -> list[dict]:
    """The most recent max_segments segments, returned oldest-first.

    Windowed, not complete: the store retains every segment; only this many
    reach the prompt (config.compaction_max_injected_segments). Callers that
    need the verbatim floor must use latest_to_id(), which spans ALL segments.
    """
    if not document_id or not attorney_id or max_segments <= 0:
        return []
    with get_pool().connection() as conn:
        cur = conn.execute(
            """SELECT id, from_id, to_id, content FROM conversation_summary
               WHERE document_id = %s AND attorney_id = %s
               ORDER BY id DESC LIMIT %s""",
            (document_id, attorney_id, max_segments),
        )
        rows = cur.fetchall()
    rows.reverse()  # DESC fetch -> chronological
    return [
        {"id": r[0], "from_id": r[1], "to_id": r[2], "content": r[3]} for r in rows
    ]


@traced("db.latest_to_id")
def latest_to_id(document_id: str, attorney_id: str) -> int:
    """Highest condensed row id across ALL segments, or 0 when none exist.

    This is the verbatim floor. It deliberately spans every segment, not just
    the injected window: rows at or below it are already condensed, and
    replaying them verbatim would undo the compaction that condensed them.
    """
    if not document_id or not attorney_id:
        return 0
    with get_pool().connection() as conn:
        cur = conn.execute(
            """SELECT COALESCE(MAX(to_id), 0) FROM conversation_summary
               WHERE document_id = %s AND attorney_id = %s""",
            (document_id, attorney_id),
        )
        return int(cur.fetchone()[0])
