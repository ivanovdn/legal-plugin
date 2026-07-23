"""End-to-end: a review persisted via intake+memory_writer is retrievable by document_id."""
import graph.nodes.memory_writer as mw
from graph.nodes.intake import intake
from memory.review_store import load_latest_review


def test_review_persisted_on_review_turn_is_recallable_by_document_id():
    text = "STATEMENT OF WORK\n\nAcme and Globex.\n\n1. Scope: build a site."
    state = {
        "request": "Review this contract.", "user_id": "word-addin",
        "uploaded_docs": [{"text": text}], "filters": {},
        "task_type": "contract_review",
    }
    state = intake(state)
    doc_id = state["document_id"]
    assert doc_id

    state.update({
        "session_id": "s1", "risk_level": "low", "attorney_notes": "",
        "llm_response": "# Review\nFinding: IP clause is Red.",
        "contract_type_detected": "sow",
        "report": {"response": "# Review\nFinding: IP clause is Red."},
        "awaiting_review": False,
    })
    mw.memory_writer(state)

    latest = load_latest_review(doc_id)
    assert latest is not None
    assert "IP clause is Red" in latest["markdown"]
    assert latest["contract_type"] == "sow"
