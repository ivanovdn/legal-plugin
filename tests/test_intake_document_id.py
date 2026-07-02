"""intake resolves a document_id from the uploaded contract text."""
from graph.nodes.intake import intake
from memory.document_id import resolve_document_id


def _state(**kw):
    base = {"request": "Review this", "user_id": "word-addin", "uploaded_docs": [],
            "filters": {}, "task_type": "contract_review"}
    base.update(kw)
    return base


def test_intake_sets_document_id_from_uploaded_text():
    text = "STATEMENT OF WORK\n\nAcme and Globex.\n\n1. Scope."
    state = _state(uploaded_docs=[{"text": text}])
    out = intake(state)
    assert out["document_id"] == resolve_document_id(text)


def test_intake_empty_document_id_when_no_doc():
    out = intake(_state(uploaded_docs=[]))
    assert out["document_id"] == ""
