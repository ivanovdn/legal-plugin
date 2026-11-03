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


def test_write_audit_log_persists_user_name():
    write_audit_log(
        session_id="s-name", user_id="uuid-1", skill_name="research",
        task_type="research", request_summary="who signs?", user_name="Dmytro Ivanov",
    )
    with get_pool().connection() as conn:
        row = conn.execute(
            "SELECT user_name FROM audit_log WHERE user_id = 'uuid-1'"
        ).fetchone()
    assert row[0] == "Dmytro Ivanov"


def test_the_audit_row_stores_its_turn_id():
    write_audit_log(session_id="s-t", user_id="u", skill_name="research",
                    task_type="research", request_summary="q", turn_id="turn-7")
    with get_pool().connection() as conn:
        (turn_id,) = conn.execute(
            "SELECT turn_id FROM audit_log WHERE session_id = 's-t'").fetchone()
    assert turn_id == "turn-7"


def test_sources_column():
    """D9: the turn's sources read back as the dict written; a row written without them holds {}."""
    sources = {
        "doc_ids": ["d1"],
        "ref_ids_retrieved": ["circ92-2025-12:usc17-s107"],
        "ref_ids_cited": ["circ92-2025-12:usc17-s107-1", "usc17-s108"],
    }
    write_audit_log(session_id="s-src", user_id="u", skill_name="research",
                    task_type="research", request_summary="q", sources=sources)
    write_audit_log(session_id="s-none", user_id="u", skill_name="research",
                    task_type="research", request_summary="q")
    with get_pool().connection() as conn:
        rows = dict(conn.execute("SELECT session_id, sources FROM audit_log").fetchall())
    assert rows == {"s-src": sources, "s-none": {}}


def test_a_row_from_before_the_sources_column_holds_an_empty_object():
    """The column's default, which every audit row written before D9 takes when the ALTER adds it."""
    with get_pool().connection() as conn:
        conn.execute(
            """INSERT INTO audit_log (timestamp, session_id, user_id, skill_name, task_type, request_summary)
               VALUES ('2026-11-01T00:00:00+00:00', 's-old', 'u', 'research', 'research', 'q')"""
        )
        (sources,) = conn.execute("SELECT sources FROM audit_log WHERE session_id = 's-old'").fetchone()
    assert sources == {}
