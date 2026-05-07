# tests/test_parsers.py
from pathlib import Path
from ingest.chunk_models import LegalChunk


def test_docx_parser_returns_legal_chunks(tmp_path):
    """DOCX parser returns list of LegalChunk objects."""
    from docx import Document

    doc = Document()
    doc.add_heading("Section 1: Definitions", level=1)
    doc.add_paragraph("This agreement defines the terms used herein.")
    doc.add_heading("Section 2: Obligations", level=1)
    doc.add_paragraph("The contractor shall deliver services as described.")
    filepath = tmp_path / "test_contract.docx"
    doc.save(str(filepath))

    from ingest.parsers.docx_parser import parse_docx

    chunks = parse_docx(
        filepath=filepath,
        client_id="client-abc",
        jurisdiction="US-DE",
        doc_type="contract",
        sensitivity="confidential",
    )

    assert len(chunks) > 0
    assert all(isinstance(c, LegalChunk) for c in chunks)
    assert all(c.client_id == "client-abc" for c in chunks)
    assert all(c.jurisdiction == "US-DE" for c in chunks)
    assert all(c.doc_type == "contract" for c in chunks)
    assert all(c.sensitivity == "confidential" for c in chunks)
    assert all(c.doc_filename == "test_contract.docx" for c in chunks)


def test_docx_parser_chunk_has_text(tmp_path):
    """Each chunk has non-empty text."""
    from docx import Document

    doc = Document()
    doc.add_heading("Important Clause", level=1)
    doc.add_paragraph("The parties hereby agree to binding arbitration for all disputes arising under this agreement.")
    filepath = tmp_path / "test.docx"
    doc.save(str(filepath))

    from ingest.parsers.docx_parser import parse_docx

    chunks = parse_docx(
        filepath=filepath,
        client_id="internal",
        jurisdiction="US",
        doc_type="policy",
        sensitivity="internal",
    )

    assert all(len(c.text.strip()) > 0 for c in chunks)
