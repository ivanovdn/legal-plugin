# memory/db.py
"""Postgres connection pool for the relational stores (audit, review, conversation).

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
        duration_ms BIGINT NOT NULL DEFAULT 0
    )
    """,
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
    """Create all store tables + indexes if absent. Idempotent."""
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
