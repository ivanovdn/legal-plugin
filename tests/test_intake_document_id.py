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


def test_intake_prefers_client_supplied_document_id():
    # A client-supplied id (e.g. the Office settings UUID) wins over the hash,
    # so the id is stable even when the document text changes.
    text = "STATEMENT OF WORK\n\nAcme and Globex.\n\n1. Scope."
    state = _state(uploaded_docs=[{"text": text}], document_id="client-uuid-123")
    out = intake(state)
    assert out["document_id"] == "client-uuid-123"


def test_intake_falls_back_to_hash_when_client_id_empty():
    text = "MASTER SERVICES AGREEMENT\n\nA and B.\n\n1. Term."
    state = _state(uploaded_docs=[{"text": text}], document_id="")
    out = intake(state)
    assert out["document_id"] == resolve_document_id(text)
