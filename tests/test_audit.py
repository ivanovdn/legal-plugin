# tests/test_audit.py
from memory.audit import write_audit_log
from memory.db import get_pool


def test_write_audit_log_inserts_a_record():
    write_audit_log(
        session_id="sess-001", user_id="attorney-1", skill_name="legal_research",
        task_type="research", request_summary="What are indemnification standards?",
        risk_level="low", review_status="not_required", review_notes="", duration_ms=1200,
    )
    with get_pool().connection() as conn:
        rows = conn.execute("SELECT * FROM audit_log").fetchall()
    assert len(rows) == 1


def test_write_audit_log_multiple():
    for i in range(3):
        write_audit_log(
            session_id=f"sess-{i}", user_id="attorney-1", skill_name="compliance_check",
            task_type="compliance", request_summary=f"Check {i}",
            risk_level="low", review_status="not_required", review_notes="", duration_ms=500,
        )
    with get_pool().connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    assert count == 3
