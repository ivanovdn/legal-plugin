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

# Per-turn counters: these three actions fire ONCE PER TURN/REVIEW, carrying
# their magnitude as a number in `detail` (see InteractionEvent.detail in
# api/models.py). Every other action fires once per ITEM (one Apply, one
# Discard, one failure). A raw COUNT(*) over both kinds in one column is how
# "12 edits_proposed, 19 edit_applied" misreads as a 158% apply rate instead
# of 19-of-37 — counter_totals() below exists to keep the two separate.
COUNTER_ACTIONS = ("edits_proposed", "findings_rendered", "preferences_suggested")


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
    request: str,
) -> None:
    """Raw insert — RAISES. The quiet policy lives in the two callers below, so
    record_events can count what actually landed instead of what it attempted."""
    with get_pool().connection() as conn:
        conn.execute(
            """INSERT INTO interaction_event
               (timestamp, turn_id, session_id, document_id, attorney_id,
                surface, action, target_kind, target_ref, detail, request)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                datetime.now(timezone.utc).isoformat(),
                turn_id, session_id, document_id, attorney_id,
                surface, action, target_kind, target_ref, detail, request,
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
    request: str = "",
) -> None:
    """Insert one interaction event. NEVER raises — telemetry is not load-bearing."""
    try:
        _insert_event(
            turn_id=turn_id, session_id=session_id, document_id=document_id,
            attorney_id=attorney_id, surface=surface, action=action,
            target_kind=target_kind, target_ref=target_ref, detail=detail,
            request=request,
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
                request=event.get("request", ""),
            )
            written += 1
        except Exception as e:
            logger.warning("interaction_event batch row failed: %s", e)
    return written


def _filters(since: str, document_id: str, attorney_id: str) -> tuple[str, list]:
    """Build an AND-joined WHERE fragment (no leading AND/WHERE) + its params.

    Shared by both tables — `feedback` and `interaction_event` carry the same
    three filter columns. `timestamp` is TEXT, but every writer stores
    `datetime.now(timezone.utc).isoformat()`, so all values share one format and
    one offset and a lexicographic `>=` is a correct chronological comparison.
    """
    clauses: list[str] = []
    params: list = []
    if since:
        clauses.append("timestamp >= %s")
        params.append(since)
    if document_id:
        clauses.append("document_id = %s")
        params.append(document_id)
    if attorney_id:
        clauses.append("attorney_id = %s")
        params.append(attorney_id)
    return " AND ".join(clauses), params


def recent_feedback(
    limit: int = 50, *, since: str = "", document_id: str = "", attorney_id: str = "",
) -> list[dict]:
    """Most recent feedback rows, newest first. Snapshot deliberately omitted
    (it's the large replayable payload, not the read-back summary — pull it
    directly with `WHERE id = ...` once you have the id from here).

    Keys: id, timestamp, turn_id, trace_id, document_id, attorney_id,
    user_name, surface, target_kind, target_ref, comment.

    Optional filters narrow to one pilot window, contract, or tester.
    """
    where, params = _filters(since, document_id, attorney_id)
    with get_pool().connection() as conn:
        cur = conn.execute(
            f"""SELECT id, timestamp, turn_id, trace_id, document_id, attorney_id,
                       user_name, surface, target_kind, target_ref, comment
                FROM feedback {'WHERE ' + where if where else ''}
                ORDER BY id DESC LIMIT %s""",
            (*params, limit),
        )
        rows = cur.fetchall()
    keys = ("id", "timestamp", "turn_id", "trace_id", "document_id", "attorney_id",
            "user_name", "surface", "target_kind", "target_ref", "comment")
    return [dict(zip(keys, r)) for r in rows]


def event_counts(
    *, since: str = "", document_id: str = "", attorney_id: str = "",
) -> list[dict]:
    """Interaction counts grouped by surface and action — the denominators.

    A raw COUNT(*): correct for per-item actions (edit_applied, ...), but for
    the three entries in COUNTER_ACTIONS this counts TURNS, not the magnitude
    they carry in `detail` — use counter_totals() for those instead. This
    function keeps reporting the honest row count either way; it is the
    caller's job (see scripts/feedback_report.py) not to present the two as
    comparable.
    """
    where, params = _filters(since, document_id, attorney_id)
    with get_pool().connection() as conn:
        cur = conn.execute(
            f"""SELECT surface, action, COUNT(*) FROM interaction_event
                {'WHERE ' + where if where else ''}
                GROUP BY surface, action ORDER BY surface, action""",
            params,
        )
        rows = cur.fetchall()
    return [{"surface": r[0], "action": r[1], "count": r[2]} for r in rows]


def counter_totals(
    *, since: str = "", document_id: str = "", attorney_id: str = "",
) -> list[dict]:
    """Turns fired vs. summed magnitude, for the three per-turn counters only.

    Returns one dict per (surface, action) actually present, with:
      - turns: COUNT(*) — how many turns/reviews fired this counter
      - total: SUM of the numeric value each of those turns carried in
        `detail` (e.g. 12 turns proposing edits might sum to 37 edits total)

    `detail` is free text everywhere else in this table (error messages on
    edit_failed, etc.) and can be empty, so a non-numeric or blank value is
    guarded with a `detail ~ '^[0-9]+$'` predicate rather than trusting the
    action filter alone to keep it clean — it contributes 0 to `total` and
    never raises; the row still counts toward `turns`.
    """
    where, params = _filters(since, document_id, attorney_id)
    with get_pool().connection() as conn:
        placeholders = ", ".join(["%s"] * len(COUNTER_ACTIONS))
        cur = conn.execute(
            f"""SELECT surface, action, COUNT(*),
                       COALESCE(SUM(CASE WHEN detail ~ '^[0-9]+$'
                                          THEN detail::bigint ELSE 0 END), 0)
                FROM interaction_event
                WHERE action IN ({placeholders})
                {'AND ' + where if where else ''}
                GROUP BY surface, action ORDER BY surface, action""",
            (*COUNTER_ACTIONS, *params),
        )
        rows = cur.fetchall()
    return [{"surface": r[0], "action": r[1], "turns": r[2], "total": r[3]} for r in rows]


def edit_proposal_turns(
    limit: int = 50, *, since: str = "", document_id: str = "", attorney_id: str = "",
) -> list[dict]:
    """One row per chat turn that proposed edits: what was asked, and what the
    attorney did with what came back. Newest first.

    This is the surface for the spurious-edit question — "did we propose edits
    on a purely factual question?" A turn whose `request` reads as a question
    ("who signs?") but whose `proposed` is non-zero is the bug reproducing, and
    `applied`/`discarded` record the attorney's verdict on each one. Reading it
    needs no trace lookup, which is what makes the rate computable at all.

    Keys: timestamp, turn_id, request, proposed, applied, discarded, failed.
    """
    where, params = _filters(since, document_id, attorney_id)
    with get_pool().connection() as conn:
        cur = conn.execute(
            f"""SELECT e.timestamp, e.turn_id, e.request,
                       CASE WHEN e.detail ~ '^[0-9]+$' THEN e.detail::bigint ELSE 0 END,
                       (SELECT COUNT(*) FROM interaction_event a
                         WHERE a.turn_id = e.turn_id AND a.action = 'edit_applied'),
                       (SELECT COUNT(*) FROM interaction_event d
                         WHERE d.turn_id = e.turn_id AND d.action = 'edit_discarded'),
                       (SELECT COUNT(*) FROM interaction_event f
                         WHERE f.turn_id = e.turn_id AND f.action = 'edit_failed')
                FROM interaction_event e
                WHERE e.action = 'edits_proposed'
                {'AND ' + where if where else ''}
                ORDER BY e.id DESC LIMIT %s""",
            (*params, limit),
        )
        rows = cur.fetchall()
    keys = ("timestamp", "turn_id", "request", "proposed", "applied", "discarded", "failed")
    return [dict(zip(keys, r)) for r in rows]
