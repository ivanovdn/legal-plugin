# memory/db.py
"""Postgres connection pool for the relational stores (audit, review,
conversation, conversation_summary, feedback, interaction_event, turn_log).

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
    # The request's turn id, so an audit row joins its turn_log row exactly
    # instead of by session and timestamp. '' on rows written before it existed.
    "ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS turn_id TEXT NOT NULL DEFAULT ''",
    "CREATE INDEX IF NOT EXISTS idx_audit_turn ON audit_log (turn_id)",
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
    CREATE TABLE IF NOT EXISTS conversation_summary (
        id BIGSERIAL PRIMARY KEY,
        timestamp TEXT NOT NULL,
        document_id TEXT NOT NULL,
        attorney_id TEXT NOT NULL,
        from_id BIGINT NOT NULL,
        to_id BIGINT NOT NULL,
        content TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_conv_summary ON conversation_summary (document_id, attorney_id, id)",
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
    # What sent the flag (SP3): the Word host and pane build from the request
    # headers, and the backend's own build — a flag from a stale pane is then a
    # property of its row. ALTER, not the CREATE above: the VM's table exists.
    "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS office_platform TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS office_version TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS pane_build TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS backend_build TEXT NOT NULL DEFAULT ''",
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
    # What sent the event (SP3) — same four facts and the same reason as on
    # feedback above. Pane lifecycle events (surface='pane') need them most: a
    # pane_opened without its pane_build cannot show a stale pane.
    "ALTER TABLE interaction_event ADD COLUMN IF NOT EXISTS office_platform TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE interaction_event ADD COLUMN IF NOT EXISTS office_version TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE interaction_event ADD COLUMN IF NOT EXISTS pane_build TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE interaction_event ADD COLUMN IF NOT EXISTS backend_build TEXT NOT NULL DEFAULT ''",
    # Add later turn_log columns with an `ALTER TABLE turn_log ADD COLUMN IF NOT
    # EXISTS …` statement BELOW its CREATE, never only inside it: CREATE TABLE IF
    # NOT EXISTS will not touch a table that already exists (the VM's), and every
    # insert naming the new column would then fail — quietly, into the app log.
    # One row per accepted request to /api/query, its resume and /api/compact —
    # failures included — written by the request handler after the response
    # (api/turn_record.py). `timestamp` is when the request ARRIVED; the other
    # tables stamp when they wrote. Spec:
    # docs/superpowers/specs/2026-10-09-turn-log-design.md
    """
    CREATE TABLE IF NOT EXISTS turn_log (
        id BIGSERIAL PRIMARY KEY,
        timestamp TEXT NOT NULL,
        turn_id TEXT NOT NULL,
        trace_id TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL,
        session_id TEXT NOT NULL DEFAULT '',
        document_id TEXT NOT NULL DEFAULT '',
        attorney_id TEXT NOT NULL,
        user_name TEXT NOT NULL DEFAULT '',
        task_type TEXT NOT NULL DEFAULT '',
        request TEXT NOT NULL DEFAULT '',
        doc_chars INTEGER NOT NULL DEFAULT 0,
        doc_sha256 TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL,
        outcome TEXT NOT NULL,
        degradations JSONB NOT NULL DEFAULT '[]',
        error TEXT NOT NULL DEFAULT '',
        awaiting_review BOOLEAN NOT NULL DEFAULT FALSE,
        contract_type TEXT NOT NULL DEFAULT '',
        contract_type_defaulted BOOLEAN NOT NULL DEFAULT FALSE,
        msa_title TEXT NOT NULL DEFAULT '',
        preferences_chars INTEGER NOT NULL DEFAULT 0,
        doc_kept_pct INTEGER,
        context JSONB,
        duration_ms INTEGER NOT NULL DEFAULT 0,
        llm_calls INTEGER NOT NULL DEFAULT 0,
        llm_load_ms INTEGER NOT NULL DEFAULT 0,
        llm_prompt_ms INTEGER NOT NULL DEFAULT 0,
        llm_gen_ms INTEGER NOT NULL DEFAULT 0,
        tokens_in INTEGER NOT NULL DEFAULT 0,
        tokens_out INTEGER NOT NULL DEFAULT 0,
        llm_detail JSONB NOT NULL DEFAULT '[]',
        response_chars INTEGER NOT NULL DEFAULT 0,
        edits_proposed INTEGER NOT NULL DEFAULT 0,
        preferences_proposed INTEGER NOT NULL DEFAULT 0,
        compaction JSONB,
        office_platform TEXT NOT NULL DEFAULT '',
        office_version TEXT NOT NULL DEFAULT '',
        pane_build TEXT NOT NULL DEFAULT '',
        backend_build TEXT NOT NULL DEFAULT '',
        user_agent TEXT NOT NULL DEFAULT ''
    )
    """,
    # What the turn ran with beyond the code (2026-10-15, api/runtime_facts.py):
    # the VM's settings minus credentials, and the model weights and Ollama
    # version on the inference server — each can change without a deploy.
    "ALTER TABLE turn_log ADD COLUMN IF NOT EXISTS settings JSONB NOT NULL DEFAULT '{}'",
    "ALTER TABLE turn_log ADD COLUMN IF NOT EXISTS model_digest TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE turn_log ADD COLUMN IF NOT EXISTS ollama_version TEXT NOT NULL DEFAULT ''",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_turn_log_turn ON turn_log (turn_id)",
    "CREATE INDEX IF NOT EXISTS idx_turn_log_attorney ON turn_log (attorney_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_turn_log_doc ON turn_log (document_id, id)",
]


def get_pool() -> ConnectionPool:
    """Return the app-wide pool, opening it on first use.

    The pool is bounded on purpose. psycopg_pool waits 30 s for a connection by
    default, and since every store call in the app goes through this one pool,
    that default is what turned an `app-db` outage into a half-minute hang in
    the Word pane — even on the recall reads that already catch their failure
    and degrade correctly, but only after burning the full wait first.
    `timeout` bounds waiting for a pooled connection; `connect_timeout` bounds
    establishing a new one, which a refused connection ends instantly but a
    blackholed host (VPN drop, a VM that stops answering) does not.
    """
    global _pool
    if _pool is None:
        settings = get_settings()
        timeout = settings.db_pool_timeout
        # libpq takes whole seconds and silently treats 1 as 2.
        connect_timeout = max(2, round(timeout))
        _pool = ConnectionPool(
            settings.database_url, min_size=1, max_size=10, timeout=timeout,
            kwargs={"autocommit": True, "connect_timeout": connect_timeout},
            open=True,
        )
        logger.info("Postgres pool opened (timeout=%ss)", timeout)
    return _pool


def init_db() -> None:
    """Create all seven store tables + indexes if absent (audit_log, review_store,
    conversation_store, conversation_summary, feedback, interaction_event, turn_log).
    Idempotent."""
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
