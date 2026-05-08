# tests/test_audit.py
import sqlite3


def test_init_audit_db_creates_table(tmp_path):
    """init_audit_db creates the audit_log table."""
    from memory.audit import init_audit_db
    db_path = str(tmp_path / "test_legal.db")
    init_audit_db(db_path)

    conn = sqlite3.connect(db_path)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='audit_log'")
    assert cursor.fetchone() is not None
    conn.close()


def test_write_audit_log(tmp_path):
    """write_audit_log inserts a record."""
    from memory.audit import init_audit_db, write_audit_log
    db_path = str(tmp_path / "test_legal.db")
    init_audit_db(db_path)

    write_audit_log(
        db_path=db_path,
        session_id="sess-001",
        user_id="attorney-1",
        skill_name="legal_research",
        task_type="research",
        request_summary="What are indemnification standards?",
        risk_level="low",
        review_status="not_required",
        review_notes="",
        duration_ms=1200,
    )

    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT * FROM audit_log").fetchall()
    assert len(rows) == 1
    conn.close()


def test_write_audit_log_multiple(tmp_path):
    """Multiple audit records can be written."""
    from memory.audit import init_audit_db, write_audit_log
    db_path = str(tmp_path / "test_legal.db")
    init_audit_db(db_path)

    for i in range(3):
        write_audit_log(
            db_path=db_path,
            session_id=f"sess-{i}",
            user_id="attorney-1",
            skill_name="compliance_check",
            task_type="compliance",
            request_summary=f"Check {i}",
            risk_level="low",
            review_status="not_required",
            review_notes="",
            duration_ms=500,
        )

    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    assert count == 3
    conn.close()
