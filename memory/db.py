# memory/db.py
"""Postgres connection pool for the relational stores (audit, review,
conversation, feedback, interaction_event).

One app-wide psycopg pool built from config.database_url. Connections are
autocommit — the stores issue single-statement writes/reads, so no explicit
transaction management is needed. init_db() creates every store table
(idempotent) and is called once at startup (api/main.py lifespan) and by the
test fixture.
"""
from __future__ import annotations

import logging

from psycopg_pool import ConnectionPool

from config import get_settings

logger = logging.getLogger(__name__)

_pool: ConnectionPool | None = None

_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        id BIGSERIAL PRIMARY KEY,
        timestamp TEXT NOT NULL,
        session_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        skill_name TEXT NOT NULL,
        task_type TEXT NOT NULL,
        request_summary TEXT NOT NULL,
        risk_level TEXT NOT NULL DEFAULT 'low',
        review_status TEXT NOT NULL DEFAULT 'not_required',
        review_notes TEXT NOT NULL DEFAULT '',
        duration_ms BIGINT NOT NULL DEFAULT 0,
        user_name TEXT NOT NULL DEFAULT ''
    )
    """,
    "ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS user_name TEXT NOT NULL DEFAULT ''",
    """
    CREATE TABLE IF NOT EXISTS review_store (
        id BIGSERIAL PRIMARY KEY,
        timestamp TEXT NOT NULL,
        document_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        contract_type TEXT NOT NULL DEFAULT '',
        review_markdown TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_review_doc ON review_store (document_id, id)",
    """
    CREATE TABLE IF NOT EXISTS conversation_store (
        id BIGSERIAL PRIMARY KEY,
        timestamp TEXT NOT NULL,
        document_id TEXT NOT NULL,
        attorney_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_conv ON conversation_store (document_id, attorney_id, id)",
    """
    CREATE TABLE IF NOT EXISTS feedback (
        id BIGSERIAL PRIMARY KEY,
        timestamp TEXT NOT NULL,
        turn_id TEXT NOT NULL DEFAULT '',
        trace_id TEXT NOT NULL DEFAULT '',
        session_id TEXT NOT NULL DEFAULT '',
        document_id TEXT NOT NULL DEFAULT '',
        attorney_id TEXT NOT NULL,
        user_name TEXT NOT NULL DEFAULT '',
        surface TEXT NOT NULL,
        -- what was flagged: 'finding' | 'edit' | 'reply' | '' (unattached).
        -- NOT the same vocabulary as interaction_event.target_kind below.
        target_kind TEXT NOT NULL DEFAULT '',
        target_ref TEXT NOT NULL DEFAULT '',
        comment TEXT NOT NULL,
        snapshot JSONB
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_feedback_turn ON feedback (turn_id)",
    """
    CREATE TABLE IF NOT EXISTS interaction_event (
        id BIGSERIAL PRIMARY KEY,
        timestamp TEXT NOT NULL,
        turn_id TEXT NOT NULL DEFAULT '',
        session_id TEXT NOT NULL DEFAULT '',
        document_id TEXT NOT NULL DEFAULT '',
        attorney_id TEXT NOT NULL,
        surface TEXT NOT NULL,
        action TEXT NOT NULL,
        -- the edit ACTION at record time ('replace'/'insert'/'delete'/
        -- 'replace_all'), NOT the feedback.target_kind vocabulary above —
        -- same column name, deliberately different meaning per table.
        target_kind TEXT NOT NULL DEFAULT '',
        target_ref TEXT NOT NULL DEFAULT '',
        detail TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_event_turn ON interaction_event (turn_id, action)",
    # The attorney's own question, on the chat counter events only. Without it,
    # "did we propose edits on a purely factual question?" — the spurious-edit
    # rate, a High-priority roadmap item — needs a manual trace lookup per turn
    # and cannot be computed in SQL at all. Added by ALTER rather than in the
    # CREATE above because CREATE TABLE IF NOT EXISTS will not add a column to
    # a table that already exists (same pattern as audit_log.user_name).
    "ALTER TABLE interaction_event ADD COLUMN IF NOT EXISTS request TEXT NOT NULL DEFAULT ''",
]


def get_pool() -> ConnectionPool:
    """Return the app-wide pool, opening it on first use."""
    global _pool
    if _pool is None:
        dsn = get_settings().database_url
        _pool = ConnectionPool(dsn, min_size=1, max_size=10,
                               kwargs={"autocommit": True}, open=True)
        logger.info("Postgres pool opened")
    return _pool


def init_db() -> None:
    """Create all five store tables + indexes if absent (audit_log, review_store,
    conversation_store, feedback, interaction_event). Idempotent."""
    with get_pool().connection() as conn:
        for stmt in _STATEMENTS:
            conn.execute(stmt)
    logger.info("Store schema initialized")


def reset_pool() -> None:
    """Close and drop the pool so a new DSN (e.g. in tests) takes effect on next get_pool()."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
