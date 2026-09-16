# tests/conftest.py
"""Shared test fixtures: an ephemeral Postgres for the store layer.

A single container is started for the whole test session; every test runs
against clean tables (truncated before each test). Docker must be running.
"""
import os

os.environ.setdefault("TRACING_ENABLED", "false")

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from testcontainers.postgres import PostgresContainer

import memory.db as db
from config import get_settings


@pytest.fixture(scope="session", autouse=True)
def _pg_container():
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
    with db.get_pool().connection() as conn:
        conn.execute(
            "TRUNCATE audit_log, review_store, conversation_store, "
            "conversation_summary, feedback, interaction_event RESTART IDENTITY"
        )
    yield


_span_exporter = InMemorySpanExporter()


@pytest.fixture(scope="session", autouse=True)
def _otel_test_provider():
    """One recording provider for the whole suite.

    App tracing is off in tests (TRACING_ENABLED=false above), so this is the
    first real set_tracer_provider call and it wins. Spans become real in every
    test file, which is the point: assertions about degradations live wherever
    the code under test lives, not only in test_observability.py.
    """
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(_span_exporter))
    trace.set_tracer_provider(provider)
    yield


@pytest.fixture(autouse=True)
def _clear_spans():
    _span_exporter.clear()
    yield
    _span_exporter.clear()


def spans_by_name(name: str):
    """Finished spans with this name, for assertions."""
    return [s for s in _span_exporter.get_finished_spans() if s.name == name]
