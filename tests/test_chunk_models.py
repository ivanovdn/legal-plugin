# tests/test_chunk_models.py
from ingest.chunk_models import LegalChunk


def test_legal_chunk_required_fields():
    """LegalChunk can be created with all required fields."""
    chunk = LegalChunk(
        chunk_id="chunk-001",
        doc_id="doc-001",
        doc_title="Service Agreement",
        doc_filename="service_agreement.docx",
        doc_type="contract",
        client_id="client-abc",
        jurisdiction="US-DE",
        sensitivity="confidential",
        text="The parties agree to the following terms.",
    )
    assert chunk.chunk_id == "chunk-001"
    assert chunk.doc_type == "contract"
    assert chunk.client_id == "client-abc"
    assert chunk.sensitivity == "confidential"
    assert chunk.char_count == 0
    assert chunk.chunk_strategy == ""


def test_legal_chunk_optional_fields():
    """LegalChunk optional fields have correct defaults."""
    chunk = LegalChunk(
        chunk_id="c1",
        doc_id="d1",
        doc_title="t",
        doc_filename="f.docx",
        doc_type="policy",
        client_id="internal",
        jurisdiction="EU",
        sensitivity="internal",
        text="test",
    )
    assert chunk.section == ""
    assert chunk.section_number == ""
    assert chunk.clause == ""
    assert chunk.clause_number == ""
    assert chunk.clause_type == ""
    assert chunk.section_display == ""
    assert chunk.chunk_index == 0
    assert chunk.chunk_strategy == ""
    assert chunk.last_updated == ""


def test_legal_chunk_model_dump():
    """model_dump() returns all fields as a dict (used by Qdrant upsert)."""
    chunk = LegalChunk(
        chunk_id="c1",
        doc_id="d1",
        doc_title="t",
        doc_filename="f.docx",
        doc_type="contract",
        client_id="client-x",
        jurisdiction="US-NY",
        sensitivity="public",
        text="hello",
        clause_type="indemnification",
        chunk_strategy="clause-level",
    )
    d = chunk.model_dump()
    assert d["doc_type"] == "contract"
    assert d["client_id"] == "client-x"
    assert d["clause_type"] == "indemnification"
    assert d["chunk_strategy"] == "clause-level"
    assert "text" in d
