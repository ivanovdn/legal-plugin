# ingest/parsers/pdf_parser.py
"""PDF parser using pdfplumber. Extracts text and chunks by section headings."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pdfplumber

from config import get_settings
from ingest.chunk_models import LegalChunk

_HEADING_RE = re.compile(
    r"^(?:section|article|chapter|part|clause)\s+\d+",
    re.IGNORECASE,
)


def _split_into_sections(text: str) -> list[tuple[str, str]]:
    """Split text into (heading, body) tuples by section headings."""
    lines = text.split("\n")
    sections: list[tuple[str, str]] = []
    current_heading = ""
    current_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if _HEADING_RE.match(stripped):
            if current_lines:
                body = "\n".join(current_lines).strip()
                if body:
                    sections.append((current_heading, body))
            current_heading = stripped
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        body = "\n".join(current_lines).strip()
        if body:
            sections.append((current_heading, body))

    return sections


def _chunk_text(text: str, max_tokens: int) -> list[str]:
    """Split text into chunks at sentence boundaries, respecting max_tokens."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for sentence in sentences:
        words = len(sentence.split())
        if current_len + words > max_tokens and current:
            chunks.append(" ".join(current))
            current = []
            current_len = 0
        current.append(sentence)
        current_len += words

    if current:
        chunks.append(" ".join(current))

    return chunks


def parse_pdf_text(
    text: str,
    filename: str,
    client_id: str,
    jurisdiction: str,
    doc_type: str,
    sensitivity: str,
) -> list[LegalChunk]:
    """Parse extracted PDF text into LegalChunks."""
    settings = get_settings()
    doc_id = str(uuid.uuid4())
    doc_title = Path(filename).stem.replace("_", " ").title()
    now = datetime.now(timezone.utc).isoformat()

    sections = _split_into_sections(text)
    if not sections:
        sections = [("", text)]

    chunks: list[LegalChunk] = []
    chunk_index = 0

    for heading, body in sections:
        text_chunks = _chunk_text(body, settings.chunk_max_tokens)
        for chunk_text in text_chunks:
            word_count = len(chunk_text.split())
            if word_count < settings.chunk_min_tokens:
                continue

            chunks.append(LegalChunk(
                chunk_id=str(uuid.uuid4()),
                doc_id=doc_id,
                doc_title=doc_title,
                doc_filename=filename,
                doc_type=doc_type,
                client_id=client_id,
                jurisdiction=jurisdiction,
                sensitivity=sensitivity,
                section=heading,
                text=chunk_text,
                char_count=len(chunk_text),
                chunk_index=chunk_index,
                chunk_strategy="section-based",
                last_updated=now,
            ))
            chunk_index += 1

    return chunks


def parse_pdf(
    filepath: Path | str,
    client_id: str,
    jurisdiction: str,
    doc_type: str,
    sensitivity: str,
) -> list[LegalChunk]:
    """Parse a PDF file into LegalChunks."""
    filepath = Path(filepath)
    with pdfplumber.open(filepath) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
    full_text = "\n".join(pages)
    return parse_pdf_text(
        text=full_text,
        filename=filepath.name,
        client_id=client_id,
        jurisdiction=jurisdiction,
        doc_type=doc_type,
        sensitivity=sensitivity,
    )
