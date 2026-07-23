"""End-to-end: a review persisted via intake+memory_writer is retrievable by document_id."""
import graph.nodes.memory_writer as mw
from graph.nodes.intake import intake
from memory.review_store import init_review_db, load_latest_review


def test_review_persisted_on_review_turn_is_recallable_by_document_id(monkeypatch, tmp_path):
    db = str(tmp_path / "legal.db")
    # Point the review store at a temp db and force re-init on it (the module-level
    # guard may already be True from earlier tests, which would skip table creation).
    # The audit log is unaffected — it now writes to the shared Postgres pool.
    from config import get_settings
    monkeypatch.setattr(get_settings(), "sqlite_path", db, raising=False)
    monkeypatch.setattr(mw, "_review_db_initialized", False)

    text = "STATEMENT OF WORK\n\nAcme and Globex.\n\n1. Scope: build a site."
    # intake resolves document_id from uploaded_docs (the same way both tabs do)
    state = {
        "request": "Review this contract.", "user_id": "word-addin",
        "uploaded_docs": [{"text": text}], "filters": {},
        "task_type": "contract_review",
    }
    state = intake(state)
    doc_id = state["document_id"]
    assert doc_id  # non-empty

    # memory_writer persists the markdown review keyed to that document_id
    state.update({
        "session_id": "s1", "risk_level": "low", "attorney_notes": "",
        "llm_response": "# Review\nFinding: IP clause is Red.",
        "contract_type_detected": "sow", "report": {"response": "# Review\nFinding: IP clause is Red."},
        "awaiting_review": False,
    })
    mw.memory_writer(state)

    # The chat path would load it by the SAME document_id
    latest = load_latest_review(db, doc_id)
    assert latest is not None
    assert "IP clause is Red" in latest["markdown"]
    assert latest["contract_type"] == "sow"
