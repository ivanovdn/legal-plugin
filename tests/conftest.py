# tests/conftest.py
"""Shared test fixtures: an ephemeral Postgres for the store layer.

A single container is started for the whole test session; every test runs
against clean tables (truncated before each test). Docker must be running.
"""
import os

import pytest
from testcontainers.postgres import PostgresContainer


@pytest.fixture(scope="session", autouse=True)
def _pg_container():
    import memory.db as db
    from config import get_settings

    with PostgresContainer("postgres:17") as pg:
        # testcontainers defaults to the psycopg2 driver in the URL; psycopg 3
        # wants a plain postgresql:// DSN.
        dsn = pg.get_connection_url().replace("+psycopg2", "")
        os.environ["DATABASE_URL"] = dsn
        get_settings.cache_clear()
        db.reset_pool()
        db.init_db()
        yield pg
        db.reset_pool()
        os.environ.pop("DATABASE_URL", None)


@pytest.fixture(autouse=True)
def _clean_tables(_pg_container):
    import memory.db as db
    with db.get_pool().connection() as conn:
        conn.execute(
            "TRUNCATE audit_log, review_store, conversation_store RESTART IDENTITY"
        )
    yield
