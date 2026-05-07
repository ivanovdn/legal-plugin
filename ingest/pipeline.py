# ingest/pipeline.py
"""Ingest pipeline: parse -> chunk -> embed -> upsert to Qdrant (+ BM25)."""

from __future__ import annotations

import logging
from pathlib import Path

from config import get_settings
from ingest.parsers.docx_parser import parse_docx
from ingest.parsers.pdf_parser import parse_pdf
from rag.embeddings import embed_texts
from rag.vector_store import upsert_chunks

logger = logging.getLogger(__name__)

PARSERS = {
    ".docx": parse_docx,
    ".pdf": parse_pdf,
}


def ingest_document(
    filepath: Path | str,
    client_id: str,
    jurisdiction: str,
    doc_type: str,
    sensitivity: str,
    collection: str,
) -> int:
    """Parse, embed, and upsert a single document. Returns chunk count."""
    filepath = Path(filepath)
    settings = get_settings()

    ext = filepath.suffix.lower()
    parser = PARSERS.get(ext)
    if parser is None:
        raise ValueError(f"Unsupported file type: {ext}. Supported: {list(PARSERS)}")

    chunks = parser(
        filepath=filepath,
        client_id=client_id,
        jurisdiction=jurisdiction,
        doc_type=doc_type,
        sensitivity=sensitivity,
    )

    if not chunks:
        logger.warning("No chunks produced from %s", filepath)
        return 0

    logger.info("Parsed %d chunks from %s", len(chunks), filepath.name)

    texts = [c.text for c in chunks]
    embeddings = embed_texts(texts)

    upsert_chunks(chunks, embeddings, collection=collection)
    logger.info("Upserted %d chunks to %s", len(chunks), collection)

    if settings.bm25_enabled:
        from rag.bm25_index import get_bm25_index
        index = get_bm25_index()
        for chunk in chunks:
            index.add(chunk.chunk_id, chunk.model_dump())
        logger.info("Added %d chunks to BM25 index", len(chunks))

    return len(chunks)
