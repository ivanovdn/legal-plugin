# ingest/books/__init__.py
"""Reference books: parse, chunk, store (spec docs/superpowers/specs/2026-10-30-reference-books-design.md).

`parse_book` is the one door from a registry entry to a parsed tree: the entry's `front_end` names the
reader that knows its layout.
"""
from pathlib import Path

from ingest.books.circular92 import ParseResult, parse_circular92
from ingest.books.registry import BookEntry


def parse_book(book: BookEntry, pdf_path: Path) -> ParseResult:
    """The whole book (its body pages) parsed by the front end the registry entry names."""
    if book.front_end == "circular92":
        return parse_circular92(pdf_path, book)
    raise ValueError(f"book {book.id!r} names front end {book.front_end!r}, which does not exist")
