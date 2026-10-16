"""SP4: the document's text as the attorney's work changed it (spec 2026-10-15).

document_version holds each distinct text of a document once, keyed by
(document_id, sha256) — the same SHA-256 turn_log records — so a turn finds its
text and a document that goes A → B → A stores A once. document_snapshot (one
row per pane capture) and the reads behind feedback_report --changes live here
too.

Every write here holds contract text, so a failure logs the document id, the
hash, the size and the exception TYPE — never the text, and never the
exception's message: Postgres quotes offending input back in some of them.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from memory.db import get_pool
from memory.fingerprint import fingerprint, storable

logger = logging.getLogger(__name__)

TEXT_CAP = 1_000_000


def save_version(document_id: str, text: str, *, attorney_id: str, source: str) -> str:
    """Store this text of the document unless it is already stored; return its
    SHA-256 — "" for an empty text, which stores nothing. RAISES."""
    chars, sha = fingerprint(text)
    if not text:
        return ""
    with get_pool().connection() as conn:
        conn.execute(
            """INSERT INTO document_version
               (document_id, sha256, text, chars, truncated, first_seen, attorney_id, source)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (document_id, sha256) DO NOTHING""",
            (document_id, sha, storable(text[:TEXT_CAP]), chars, chars > TEXT_CAP,
             datetime.now(timezone.utc).isoformat(), attorney_id, source),
        )
    return sha


def save_version_quietly(document_id: str, text: str, *, attorney_id: str, source: str) -> None:
    """save_version that never raises — for the turn path, where the attorney
    already has their answer. Logs what identifies the text, never the text."""
    try:
        save_version(document_id, text, attorney_id=attorney_id, source=source)
    except Exception as e:  # noqa: BLE001 — telemetry's own failure (class 4)
        chars, sha = fingerprint(text)
        logger.warning(
            "document_version write failed (%s) — document %s, sha256 %s, %d chars; text not logged",
            type(e).__name__, document_id, sha, chars,
        )


TRACKED_CAP = 500
TRACKED_TEXT_CAP = 2_000
_TRACKED_FIELD_CAP = 200   # type, author, date are short; a client-controlled value still gets a bound


def cap_tracked_changes(items: list[dict]) -> list[dict]:
    """The first TRACKED_CAP changes, each one's text capped and marked `cut`,
    every string made storable (JSONB refuses a lone surrogate and a NUL)."""
    capped = []
    for item in items[:TRACKED_CAP]:
        text = str(item.get("text") or "")
        capped.append({
            "type": storable(str(item.get("type") or ""))[:_TRACKED_FIELD_CAP],
            "author": storable(str(item.get("author") or ""))[:_TRACKED_FIELD_CAP],
            "date": storable(str(item.get("date") or ""))[:_TRACKED_FIELD_CAP],
            "text": storable(text[:TRACKED_TEXT_CAP]),
            "cut": bool(item.get("cut")) or len(text) > TRACKED_TEXT_CAP,
        })
    return capped


def save_snapshot(*, attorney_id: str, session_id: str, document_id: str, moment: str,
                  current_text: str, original_text: str, tracked_changes: list[dict],
                  tracked_total: int, client: dict, backend_build: str) -> None:
    """Store both views of the document and the capture's row. RAISES — the
    route decides what a failure means (nothing, to the attorney).

    tracked_total is never trusted below what arrived; -1 (unreadable) keeps
    no list."""
    current_sha = save_version(document_id, current_text, attorney_id=attorney_id, source="pane")
    original_sha = save_version(document_id, original_text, attorney_id=attorney_id, source="pane")
    total = -1 if tracked_total < 0 else max(tracked_total, len(tracked_changes))
    items = [] if total < 0 else cap_tracked_changes(tracked_changes)
    with get_pool().connection() as conn:
        conn.execute(
            """INSERT INTO document_snapshot
               (timestamp, attorney_id, session_id, document_id, moment, current_sha256,
                original_sha256, tracked_changes, tracked_total,
                office_platform, office_version, pane_build, backend_build)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (datetime.now(timezone.utc).isoformat(), attorney_id, session_id, document_id,
             moment, current_sha, original_sha, Jsonb(items), total,
             client.get("office_platform", ""), client.get("office_version", ""),
             client.get("pane_build", ""), backend_build),
        )


def documents_with_copies(*, since: str = "", attorney_id: str = "", document_id: str = "",
                          limit: int = 50) -> list[str]:
    """The documents with SP4 data in scope — at least one stored text or
    capture — the newest `limit` by last activity, oldest of them first."""
    clauses: list[str] = []
    params: list = []
    if since:
        clauses.append("ts >= %s")
        params.append(since)
    if attorney_id:
        clauses.append("attorney_id = %s")
        params.append(attorney_id)
    if document_id:
        clauses.append("document_id = %s")
        params.append(document_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT document_id FROM (
            SELECT document_id, max(ts) AS last FROM (
                SELECT document_id, attorney_id, first_seen AS ts FROM document_version
                UNION ALL
                SELECT document_id, attorney_id, timestamp AS ts FROM document_snapshot
            ) seen {where}
            GROUP BY document_id ORDER BY last DESC LIMIT %s
        ) newest ORDER BY last
    """
    with get_pool().connection() as conn:
        return [row[0] for row in conn.execute(sql, [*params, limit]).fetchall()]


def observations(document_id: str) -> list[dict]:
    """Every sighting of the document's text — its turns and its captures —
    oldest first. Sightings, not versions, give the order: a document that goes
    A → B → A has two versions and three sightings, the last of them A.

    A row with no name of its own — every capture, and a turn that came without
    one — carries its attorney's latest non-empty `user_name`: the newest turn
    that has one, as feedback_report --activity names an attorney, failing that
    the newest flag. So a document the pane saw and the model never did is still
    named. Each attorney's name is looked up once, not once per row."""
    sql = """
        WITH seen AS (
            SELECT timestamp, 'turn' AS kind, doc_sha256 AS sha256, turn_id, session_id,
                   attorney_id, user_name, NULL::jsonb AS tracked_changes, 0 AS tracked_total
              FROM turn_log WHERE document_id = %s AND doc_sha256 <> ''
            UNION ALL
            SELECT timestamp, moment, current_sha256, '', session_id,
                   attorney_id, '', tracked_changes, tracked_total
              FROM document_snapshot WHERE document_id = %s
        ), named AS (
            SELECT a.attorney_id,
                   COALESCE(
                       (SELECT t.user_name FROM turn_log t
                         WHERE t.attorney_id = a.attorney_id AND t.user_name <> ''
                         ORDER BY t.timestamp DESC, t.id DESC LIMIT 1),
                       (SELECT f.user_name FROM feedback f
                         WHERE f.attorney_id = a.attorney_id AND f.user_name <> ''
                         ORDER BY f.timestamp DESC, f.id DESC LIMIT 1)) AS user_name
              FROM (SELECT DISTINCT attorney_id FROM seen WHERE user_name = '') a
        )
        SELECT s.timestamp, s.kind, s.sha256, s.turn_id, s.session_id, s.attorney_id,
               COALESCE(NULLIF(s.user_name, ''), n.user_name, '') AS user_name,
               s.tracked_changes, s.tracked_total
          FROM seen s LEFT JOIN named n ON n.attorney_id = s.attorney_id
         ORDER BY s.timestamp
    """
    with get_pool().connection() as conn:
        cur = conn.execute(sql, (document_id, document_id))
        names = [column.name for column in cur.description]
        return [dict(zip(names, values)) for values in cur.fetchall()]


def versions(document_id: str) -> dict[str, dict]:
    """sha256 → the stored text of each version of the document."""
    with get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT sha256, text, chars, truncated, first_seen, source "
            "FROM document_version WHERE document_id = %s", (document_id,)).fetchall()
    return {r[0]: {"text": r[1], "chars": r[2], "truncated": r[3], "first_seen": r[4],
                   "source": r[5]} for r in rows}


def applied_events(document_id: str) -> list[dict]:
    """The document's Apply events that carry their text, oldest first."""
    with get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT timestamp, turn_id, action, applied FROM interaction_event "
            "WHERE document_id = %s AND applied IS NOT NULL ORDER BY timestamp, id",
            (document_id,)).fetchall()
    return [{"timestamp": r[0], "turn_id": r[1], "action": r[2], "applied": r[3]} for r in rows]


def captures_for_sessions(session_ids: list[str]) -> list[dict]:
    """The captures made in these pane sessions, oldest first — for --turns."""
    with get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT timestamp, session_id, document_id, moment, current_sha256, tracked_total "
            "FROM document_snapshot WHERE session_id = ANY(%s) ORDER BY timestamp, id",
            (list(session_ids),)).fetchall()
    keys = ("timestamp", "session_id", "document_id", "moment", "current_sha256", "tracked_total")
    return [dict(zip(keys, r)) for r in rows]


def _scope(document_id: str, attorney_id: str) -> tuple[str, str]:
    """The one column to delete by — never built from input, only these two."""
    if bool(document_id) == bool(attorney_id):
        raise ValueError("give exactly one of document_id / attorney_id")
    return ("document_id", document_id) if document_id else ("attorney_id", attorney_id)


def delete_document_data(*, document_id: str = "", attorney_id: str = "",
                         dry_run: bool = True) -> dict[str, int]:
    """SP4's copies for one document or one attorney: counted, and deleted
    unless dry_run. `applied` is set to NULL rather than the event deleted —
    the Apply still happened; only the applied text goes, and the event's short
    excerpts (target_ref, detail, request) stay. A version belongs to whoever
    first sent it."""
    column, value = _scope(document_id, attorney_id)
    counts_sql = {
        "document_version": f"SELECT count(*) FROM document_version WHERE {column} = %s",
        "document_snapshot": f"SELECT count(*) FROM document_snapshot WHERE {column} = %s",
        "interaction_event.applied":
            f"SELECT count(*) FROM interaction_event WHERE {column} = %s AND applied IS NOT NULL",
    }
    with get_pool().connection() as conn:
        counts = {name: conn.execute(sql, (value,)).fetchone()[0]
                  for name, sql in counts_sql.items()}
        if not dry_run:
            with conn.transaction():
                conn.execute(f"DELETE FROM document_version WHERE {column} = %s", (value,))
                conn.execute(f"DELETE FROM document_snapshot WHERE {column} = %s", (value,))
                conn.execute(f"UPDATE interaction_event SET applied = NULL "
                             f"WHERE {column} = %s AND applied IS NOT NULL", (value,))
    return counts


def untouched_counts(*, document_id: str = "", attorney_id: str = "") -> dict[str, int | None]:
    """What delete_document_data leaves for the same scope — it is not a full
    erasure. None: that table cannot be scoped this way (reviews are keyed by
    document only, the audit log by attorney only). interaction_event counts the
    rows that still hold a short excerpt in target_ref, detail or request."""
    column, value = _scope(document_id, attorney_id)
    out: dict[str, int | None] = {}
    with get_pool().connection() as conn:
        for table in ("review_store", "conversation_store", "conversation_summary",
                      "feedback", "turn_log"):
            if table == "review_store" and column == "attorney_id":
                out[table] = None
                continue
            out[table] = conn.execute(
                f"SELECT count(*) FROM {table} WHERE {column} = %s", (value,)).fetchone()[0]
        out["interaction_event (text excerpts)"] = conn.execute(
            f"SELECT count(*) FROM interaction_event WHERE {column} = %s "
            "AND (target_ref <> '' OR detail <> '' OR request <> '')", (value,)).fetchone()[0]
        if column == "document_id":
            out["audit_log"] = None
        else:
            out["audit_log"] = conn.execute(
                "SELECT count(*) FROM audit_log WHERE user_id = %s", (value,)).fetchone()[0]
    return out
