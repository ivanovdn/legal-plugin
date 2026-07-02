"""Resolve a stable document identifier from contract text.

Keys the persisted review store. Hashes a NORMALIZED PREAMBLE REGION (title +
parties) rather than the whole body, because the body is actively redlined and a
full-body hash would change on every edit and orphan the stored review.

Interim implementation. The durable upgrade is an Office.js custom document
property (a UUID written into the file on first open); that is a swap of this one
function. See docs/superpowers/specs/2026-06-29-chat-memory-grounding-design.md.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

# Title + parties usually sit in the opening block. 800 chars comfortably covers
# them while staying above the clauses that get redlined.
_PREAMBLE_CHARS = 800


def resolve_document_id(text: str) -> str:
    """SHA-256 hex of the normalized preamble. Empty string for empty input."""
    if not text or not text.strip():
        return ""
    region = text[:_PREAMBLE_CHARS]

    # Find the end of the preamble by looking for the first numbered section.
    # This handles documents where the preamble ends before 800 chars.
    # Fallback: documents without a "\n<digit>." clause (e.g. "ARTICLE"/"Section" headings) use the _PREAMBLE_CHARS prefix; edits beyond it still don't change the id.
    match = re.search(r'\n\d+\.', region)
    if match:
        region = region[:match.start()]

    region = unicodedata.normalize("NFC", region).lower()
    region = re.sub(r"\s+", " ", region).strip()
    return hashlib.sha256(region.encode("utf-8")).hexdigest()
