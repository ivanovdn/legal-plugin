"""Postgres stores for tester feedback and interaction telemetry.

Two tables, two failure policies:

- `feedback` holds an attorney's written report plus a replayable input
  snapshot. The write is LOUD (exceptions propagate, like save_review) — the
  attorney is told it sent, so a silent loss is a lie.
- `interaction_event` holds Apply/Discard/failure telemetry. The write is QUIET
  (never raises) — telemetry must never break an Apply.

Two tables rather than one with a `kind` column: ~100:1 volume, a snapshot that
would be NULL on nearly every row, GROUP BY-shaped reads on one and prose reads
on the other, and different lifetimes (events are prunable, feedback is not).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from memory.db import get_pool

logger = logging.getLogger(__name__)

_TRUNCATION_MARK = "\n\n[... truncated at {n} chars ...]"


def truncate_snapshot(snapshot: dict | None, max_chars: int) -> dict | None:
    """Cap each top-level string value, marking the cut. Returns a new dict.

    Per-field rather than per-payload: predictable, and the two fields that can
    actually be large (document_text, assistant_output) are capped independently
    so one does not eat the other's budget.
    """
    if snapshot is None:
        return None
    out: dict = {}
    for key, value in snapshot.items():
        if isinstance(value, str) and max_chars > 0 and len(value) > max_chars:
            out[key] = value[:max_chars] + _TRUNCATION_MARK.format(n=max_chars)
        else:
            out[key] = value
    return out


def save_feedback(
    *,
    turn_id: str,
    trace_id: str,
    session_id: str,
    document_id: str,
    attorney_id: str,
    user_name: str,
    surface: str,
    target_kind: str,
    target_ref: str,
    comment: str,
    snapshot: dict | None,
) -> int:
    """Insert one feedback row and return its id. Raises on any failure."""
    with get_pool().connection() as conn:
        cur = conn.execute(
            """INSERT INTO feedback
               (timestamp, turn_id, trace_id, session_id, document_id, attorney_id,
                user_name, surface, target_kind, target_ref, comment, snapshot)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (
                datetime.now(timezone.utc).isoformat(),
                turn_id, trace_id, session_id, document_id, attorney_id,
                user_name, surface, target_kind, target_ref, comment,
                Jsonb(snapshot) if snapshot is not None else None,
            ),
        )
        row_id = cur.fetchone()[0]
    logger.info(
        "Feedback saved: id=%s attorney=%s surface=%s turn=%s trace=%s",
        row_id, attorney_id, surface, turn_id, trace_id,
    )
    return row_id


def _insert_event(
    *,
    turn_id: str,
    session_id: str,
    document_id: str,
    attorney_id: str,
    surface: str,
    action: str,
    target_kind: str,
    target_ref: str,
    detail: str,
) -> None:
    """Raw insert — RAISES. The quiet policy lives in the two callers below, so
    record_events can count what actually landed instead of what it attempted."""
    with get_pool().connection() as conn:
        conn.execute(
            """INSERT INTO interaction_event
               (timestamp, turn_id, session_id, document_id, attorney_id,
                surface, action, target_kind, target_ref, detail)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                datetime.now(timezone.utc).isoformat(),
                turn_id, session_id, document_id, attorney_id,
                surface, action, target_kind, target_ref, detail,
            ),
        )


def record_event(
    *,
    turn_id: str,
    session_id: str,
    document_id: str,
    attorney_id: str,
    surface: str,
    action: str,
    target_kind: str = "",
    target_ref: str = "",
    detail: str = "",
) -> None:
    """Insert one interaction event. NEVER raises — telemetry is not load-bearing."""
    try:
        _insert_event(
            turn_id=turn_id, session_id=session_id, document_id=document_id,
            attorney_id=attorney_id, surface=surface, action=action,
            target_kind=target_kind, target_ref=target_ref, detail=detail,
        )
    except Exception as e:
        logger.warning("interaction_event write failed (%s): %s", action, e)


def record_events(events: list[dict], *, attorney_id: str) -> int:
    """Insert a batch, returning how many actually landed. NEVER raises."""
    written = 0
    for event in events:
        try:
            _insert_event(
                turn_id=event.get("turn_id", ""),
                session_id=event.get("session_id", ""),
                document_id=event.get("document_id", ""),
                attorney_id=attorney_id,
                surface=event.get("surface", ""),
                action=event.get("action", ""),
                target_kind=event.get("target_kind", ""),
                target_ref=event.get("target_ref", ""),
                detail=event.get("detail", ""),
            )
            written += 1
        except Exception as e:
            logger.warning("interaction_event batch row failed: %s", e)
    return written


def recent_feedback(limit: int = 50) -> list[dict]:
    """Most recent feedback rows, newest first. Snapshot deliberately omitted."""
    with get_pool().connection() as conn:
        cur = conn.execute(
            """SELECT timestamp, turn_id, trace_id, attorney_id, user_name,
                      surface, target_kind, target_ref, comment
               FROM feedback ORDER BY id DESC LIMIT %s""",
            (limit,),
        )
        rows = cur.fetchall()
    keys = ("timestamp", "turn_id", "trace_id", "attorney_id", "user_name",
            "surface", "target_kind", "target_ref", "comment")
    return [dict(zip(keys, r)) for r in rows]


def event_counts() -> list[dict]:
    """Interaction counts grouped by surface and action — the denominators."""
    with get_pool().connection() as conn:
        cur = conn.execute(
            """SELECT surface, action, COUNT(*) FROM interaction_event
               GROUP BY surface, action ORDER BY surface, action"""
        )
        rows = cur.fetchall()
    return [{"surface": r[0], "action": r[1], "count": r[2]} for r in rows]
