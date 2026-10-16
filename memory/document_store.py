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
