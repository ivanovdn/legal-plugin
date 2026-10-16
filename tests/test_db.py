# tests/test_db.py
from memory.db import get_pool
from tests.conftest import STORE_TABLES


def test_pool_roundtrips_a_query():
    with get_pool().connection() as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1


def test_init_db_creates_all_tables():
    with get_pool().connection() as conn:
        for table in ("audit_log", "review_store", "conversation_store"):
            row = conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()
            assert row[0] is not None, f"{table} missing"


def test_audit_log_has_user_name_column():
    with get_pool().connection() as conn:
        cols = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'audit_log'"
        ).fetchall()
    assert "user_name" in {c[0] for c in cols}


def test_every_store_table_is_truncated_between_tests():
    """conftest truncates a hardcoded list: a table init_db() creates and the
    list misses would carry rows from one test into the next — and green a lie.
    Mutation: drop a name from STORE_TABLES."""
    with get_pool().connection() as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        ).fetchall()}
    assert tables == set(STORE_TABLES)
