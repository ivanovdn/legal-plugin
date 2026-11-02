# ingest/books/registry.py
"""The books we ingest, and the only door into the book collection.

An entry names one edition of one book: the ingest command refuses an id that is
not here and a PDF whose hash is not the one recorded here. A new edition is a new
entry (and a new overrides file), never an edit of the old one.
"""
import hashlib
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class UnknownBook(LookupError):
    pass


class PdfHashMismatch(ValueError):
    pass


@dataclass(frozen=True)
class BookEntry:
    id: str
    title: str
    short_title: str
    edition_id: str
    edition_label: str
    jurisdiction: str
    source: str
    licence: str
    pdf_sha256: str
    front_end: str
    overrides_path: Path

    @property
    def ref_prefix(self) -> str:
        """What every `ref_id` of this book and edition starts with, before the colon."""
        return f"{self.id}-{self.edition_id}"


BOOKS: dict[str, BookEntry] = {
    "circ92": BookEntry(
        id="circ92",
        title=(
            "Copyright Law of the United States and Related Laws Contained in "
            "Title 17 of the United States Code"
        ),
        short_title="Circular 92",
        edition_id="2025-12",
        edition_label="Dec. 2025",
        jurisdiction="US",
        source="https://www.copyright.gov/title17/title17.pdf",
        licence="public domain, U.S. government work",
        pdf_sha256="d999c79131965a23b2128e20bd5a3e84a2af2a72279fd07676635c256aacd59e",
        front_end="circular92",
        overrides_path=REPO_ROOT / "ingest" / "books" / "circular92" / "overrides-2025-12.json",
    ),
}


def get_book(book_id: str) -> BookEntry:
    try:
        return BOOKS[book_id]
    except KeyError:
        raise UnknownBook(
            f"book {book_id!r} is not registered; registered: {sorted(BOOKS)}"
        ) from None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_pdf(book: BookEntry, pdf_path: Path) -> None:
    """Raise PdfHashMismatch unless the file is the edition this entry was written for."""
    actual = _sha256(Path(pdf_path))
    if actual != book.pdf_sha256:
        raise PdfHashMismatch(
            f"{pdf_path} is not the registered PDF for {book.id} {book.edition_id}: "
            f"expected sha256 {book.pdf_sha256}, got {actual}"
        )


def book_for_ref(ref_id: str) -> BookEntry:
    """The book a `ref_id` belongs to, by its `<id>-<edition_id>:` prefix."""
    for book in BOOKS.values():
        if ref_id.startswith(f"{book.ref_prefix}:"):
            return book
    raise UnknownBook(f"no registered book for ref_id {ref_id!r}")
