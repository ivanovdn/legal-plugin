# tests/test_db.py
def test_pool_roundtrips_a_query():
    from memory.db import get_pool
    with get_pool().connection() as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1


def test_init_db_creates_all_tables():
    from memory.db import get_pool
    with get_pool().connection() as conn:
        for table in ("audit_log", "review_store", "conversation_store"):
            row = conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()
            assert row[0] is not None, f"{table} missing"
