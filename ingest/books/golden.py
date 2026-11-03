# ingest/books/golden.py
"""The golden file: the parsed tree of Circular 92's chapters 1 and 5, one row per node (spec D11).

It tests the tree, not the chunks: chunk boundaries depend on the tokenizer, the tree does not. Each
row holds what a reader of the diff needs to see a parse change: the node's ref_id, kind, source type,
label, pages, cross-references, note links, effective-date flag (notes only) and its own text, cleaned
(ruling 10). The rows come from a parse of the WHOLE body, filtered to the two chapters: the hyphen
decider takes its evidence from every line it is given, so a parse of the two chapters alone welds a
compound the whole book keeps ("threedimensional"), and an oracle must not hold text the ingest will not
produce (ruling R17). Regenerate it with

    uv run python -m ingest.books.golden tests/fixtures/books/circular-92-2025-12.pdf \\
        tests/fixtures/books/circ92-ch1-ch5.jsonl

and read the diff before committing it: it is the oracle the parser is tested against.
"""
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from ingest.books.circular92 import parse_circular92
from ingest.books.circular92.layout import BODY_PDF_PAGES
from ingest.books.models import BookTree
from ingest.books.ref_ids import make_ref_id
from ingest.books.registry import get_book, verify_pdf
from ingest.books.tree import own_text

GOLDEN_SCOPES = frozenset({"Chapter 1", "Chapter 5"})
GOLDEN_BOOK = "circ92"


def golden_rows(tree: BookTree, scopes: set[str] | frozenset[str]) -> list[dict]:
    """One row per node of the tree that sits in `scopes`, in document order."""
    rows = []
    for node in tree.walk():
        if node.scope not in scopes:
            continue
        first, last = node.pages()
        rows.append({
            "ref_id": make_ref_id(tree.book, node.path),
            "kind": node.kind,
            "source_type": node.source_type,
            "label": node.label,
            "pages": [first, last],
            "xrefs": list(node.xrefs),
            "history_notes": [make_ref_id(tree.book, path) for path in node.history_notes],
            "effective_date": node.effective_date if node.kind == "history_note" else None,
            "text": own_text(node, tree.join),
        })
    return rows


def write_rows(rows: Sequence[dict], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def diff_rows(expected: Sequence[dict], actual: Sequence[dict]) -> str | None:
    """None when the rows are equal; otherwise what differs first, naming the ref_id and the field."""
    for i, (want, got) in enumerate(zip(expected, actual)):
        if want["ref_id"] != got["ref_id"]:
            if got["ref_id"] not in {row["ref_id"] for row in expected}:
                return f"row {i}: extra row {got['ref_id']!r} before {want['ref_id']!r}"
            return f"row {i}: missing row {want['ref_id']!r} (got {got['ref_id']!r})"
        for field in dict.fromkeys([*want, *got]):
            if want.get(field) != got.get(field):
                return f"{want['ref_id']!r}, field {field!r}: expected {want.get(field)!r}, got {got.get(field)!r}"
    if len(expected) > len(actual):
        more = len(expected) - len(actual) - 1
        return f"row {len(actual)}: missing row {expected[len(actual)]['ref_id']!r} (and {more} more)"
    if len(actual) > len(expected):
        more = len(actual) - len(expected) - 1
        return f"row {len(expected)}: extra row {actual[len(expected)]['ref_id']!r} (and {more} more)"
    return None


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: python -m ingest.books.golden <pdf> <out.jsonl>", file=sys.stderr)
        return 2
    pdf, out = Path(args[0]), Path(args[1])
    book = get_book(GOLDEN_BOOK)
    verify_pdf(book, pdf)  # the oracle comes from the registered edition, or not at all
    parsed = parse_circular92(pdf, book, BODY_PDF_PAGES)
    rows = golden_rows(parsed.tree, GOLDEN_SCOPES)
    write_rows(rows, out)
    print(f"{len(rows)} rows -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
