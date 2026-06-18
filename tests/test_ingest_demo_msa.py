# tests/test_ingest_demo_msa.py
"""The demo MSA ingest tags the doc as doc_type=msa and clears prior MSAs."""
from __future__ import annotations

import scripts.ingest_demo_msa as mod


def test_tags_document_as_msa(monkeypatch, tmp_path):
    fake = tmp_path / "msa.docx"
    fake.write_text("x")
    monkeypatch.setattr(mod, "_MSA_PATH", fake)
    monkeypatch.setattr(mod, "scroll_by_filter", lambda **kw: [])
    captured: dict = {}
    monkeypatch.setattr(mod, "ingest_document", lambda **kw: captured.update(kw) or 7)

    rc = mod.main()

    assert rc == 0
    assert captured["doc_type"] == "msa"
    assert captured["client_id"] == "internal"
    assert captured["collection"] == "legal_docs"
    assert captured["filepath"] == fake


def test_clears_existing_msas_before_ingest(monkeypatch, tmp_path):
    fake = tmp_path / "msa.docx"
    fake.write_text("x")
    monkeypatch.setattr(mod, "_MSA_PATH", fake)
    monkeypatch.setattr(mod, "scroll_by_filter", lambda **kw: [
        {"doc_id": "old-1"}, {"doc_id": "old-1"}, {"doc_id": "old-2"},
    ])
    deleted: list = []
    monkeypatch.setattr(mod, "delete_document", lambda doc_id, collection: deleted.append(doc_id))
    monkeypatch.setattr(mod, "ingest_document", lambda **kw: 3)

    mod.main()

    assert sorted(deleted) == ["old-1", "old-2"]


def test_missing_file_returns_error_code(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_MSA_PATH", tmp_path / "nope.docx")
    monkeypatch.setattr(mod, "scroll_by_filter", lambda **kw: [])
    assert mod.main() == 1
