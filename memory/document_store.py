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
