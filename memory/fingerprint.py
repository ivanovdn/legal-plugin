"""The one hash of a document's text, and text Postgres can store.

turn_log keeps a document's fingerprint and document_version keeps its text;
both go through fingerprint() so a turn's doc_sha256 always finds its stored
version (spec 2026-10-15, D2). The hash is of the FULL text, before any cap.
"""
from __future__ import annotations

import hashlib


def fingerprint(text: str) -> tuple[int, str]:
    """(chars, hex SHA-256 of the UTF-8 text); (0, "") for no text.
    surrogatepass, because broken UTF-16 from Word reaches Python as lone
    surrogates that plain UTF-8 refuses to encode."""
    if not text:
        return 0, ""
    return len(text), hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def storable(text: str) -> str:
    """Free text Postgres can store: a lone surrogate becomes '?' and a NUL is
    dropped — TEXT and JSONB refuse both, and the write would be lost."""
    return (text or "").encode("utf-8", "replace").decode("utf-8").replace("\x00", "")
