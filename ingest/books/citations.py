# ingest/books/citations.py
"""Turn a stored passage into the citation an attorney reads, and check what a model wrote.

The model cites `ref_id: <id>` and never writes a page or an edition; code builds the citation
from stored fields. Six formats (the five of the citations decision plus treaties):

    17 U.S.C. §122(f)(1) — Circular 92 (Dec. 2025), p. 143                         statute
    17 U.S.C. §101 (“joint work”) — Circular 92 (Dec. 2025), p. 4                  definition
    18 U.S.C. §2319 — Circular 92 (Dec. 2025), app. N, p. 428                      another title
    <Act>, Pub. L. No. 105-304, §101 (1998) — Circular 92 (Dec. 2025), app. B, p. 371   uncodified Act
    Circular 92 (Dec. 2025), ch. 1 n.3, p. 146 (amendment history, not statutory text)  history note
    TRIPs Agreement, art. 38(1) — Circular 92 (Dec. 2025), app. S, p. 458 (treaty, not U.S. law)  treaty
"""
import re
from collections.abc import Mapping

from ingest.books.models import KINDS, SOURCE_TYPES
from ingest.books.ref_ids import replace_cited_ref_ids
from ingest.books.registry import BookEntry

UNVERIFIED = "[unverified reference: {ref_id}]"

_STATED_PAGE_RE = re.compile(
    r"\b(?:pp?\.\s?\d+(?:\s?[–-]\s?\d+)?|pages?\s+\d+(?:\s?[–-]\s?\d+)?)", re.I | re.ASCII
)


def page_span(start: int, end: int) -> str:
    """`p. 20`, `pp. 143–44` (the end page keeps its last two digits when only those differ), `pp. 99–101`."""
    if end < start:
        raise ValueError(f"pages run backwards: {start} to {end}")
    if start == end:
        return f"p. {start}"
    first, last = str(start), str(end)
    if first[:-2] == last[:-2]:  # equal beyond the last two digits (so the same number of digits past two)
        last = last[-2:]
    return f"pp. {first}–{last}"


def render_citation(
    *,
    label: str,
    kind: str,
    source_type: str,
    appendix: str | None,
    page_start: int,
    page_end: int,
    book: BookEntry,
) -> str:
    if kind not in KINDS:
        raise ValueError(f"kind {kind!r} is not one of {KINDS}")
    if source_type not in SOURCE_TYPES:
        raise ValueError(f"source_type {source_type!r} is not one of {SOURCE_TYPES}")
    where = f"{book.short_title} ({book.edition_label})"
    pages = page_span(page_start, page_end)
    if kind == "history_note":  # the label already names its chapter or appendix
        return f"{where}, {label}, {pages} (amendment history, not statutory text)"
    citation = f"{label} — {where}, {f'app. {appendix}, ' if appendix else ''}{pages}"
    if kind == "repealed":
        return f"{citation} (repealed)"
    if source_type == "treaty":
        return f"{citation} (treaty, not U.S. law)"
    return citation


def render_response(text: str, citations: Mapping[str, str]) -> str:
    """Replace each cited id, with its `ref_id:` marker and any backticks around either, by its citation,
    or by UNVERIFIED when `citations` has no entry for it."""
    return replace_cited_ref_ids(
        text, lambda ref_id: citations.get(ref_id) or UNVERIFIED.format(ref_id=ref_id)
    )


def find_stated_pages(text: str) -> list[str]:
    """Page references written in the text (`p. 12`, `pp. 12–13`, `page 40`), in order."""
    return _STATED_PAGE_RE.findall(text)
