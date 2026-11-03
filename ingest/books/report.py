# ingest/books/report.py
"""The dry-run report: what a parsed and chunked book looks like, and whether it clears the bar (spec D11).

The golden file covers chapters 1 and 5; for the rest of the book this is the check, and a real ingest
refuses unless it passes (ruling 15). The bar:

  - every one of Title 17's 152 section headings is a node (a repealed section is one too);
  - all 192 footnote markers are linked to their notes: the same numbers, in order, in every scope;
  - all 20 appendices are parsed, each with the `source_type` the edition table gives it;
  - no label line is left out of sequence (every one either follows its rule or has an override);
  - no chunk is over the token budget (a chunk the counter put past the embedding window counts as over);
  - no override is unused: a whole-body run must consume every hand fix the edition ships.

Reported, never gated: the cross-reference resolution rate and its misses (a Public Law target is never a
node, so the all-targets rate is low by construction; Title 17's is the one to read), and the line-end
hyphens no evidence decided (they default to "join").

The numbers are Circular 92 (Dec. 2025)'s. A second book brings its own expectations here.
"""
import random
from collections import Counter
from collections.abc import Sequence

from ingest.books.chunker import OVER_WINDOW
from ingest.books.circular92 import ParseResult
from ingest.books.circular92.appendices import APPENDICES
from ingest.books.citations import render_citation
from ingest.books.models import BookChunk
from ingest.books.registry import BookEntry
from ingest.books.tree import xref_resolution

BAR_EXPECTED = {"title17_sections": 152, "markers": 192, "appendices": 20}
SAMPLE_SIZE = 20
TITLE17_TARGET = "usc17-"  # a cross-reference target that names a Title 17 section, as a node path

TSV_COLUMNS = ("ref_id", "chunk_key", "label", "pages", "kind", "source_type", "tokens", "xrefs", "citation")


def _rate(resolved: int, total: int) -> float | None:
    return resolved / total if total else None


def _marker_report(markers: dict[str, list[int]], notes: dict[str, list[int]]) -> dict:
    """Markers lifted from the text against the notes found, scope by scope: a marker is linked when its
    scope has a note of that number (each note once), and a scope whose two lists differ at all is listed."""
    scopes = list(dict.fromkeys([*markers, *notes]))
    linked = sum(sum((Counter(markers.get(s, [])) & Counter(notes.get(s, []))).values()) for s in scopes)
    mismatches = [
        {"scope": s, "markers": markers.get(s, []), "notes": notes.get(s, [])}
        for s in scopes if markers.get(s, []) != notes.get(s, [])
    ]
    return {"total": sum(len(v) for v in markers.values()), "linked": linked, "scope_mismatches": mismatches}


def _appendix_types(parsed: ParseResult) -> dict[str, str]:
    """Each appendix the tree holds, with the source type of its sections (`a|b` if they disagree)."""
    found: dict[str, set[str]] = {}
    for root in parsed.tree.nodes:
        if root.appendix is not None and root.kind != "history_note":
            found.setdefault(root.appendix, set()).add(root.source_type)
    return {letter: "|".join(sorted(types)) for letter, types in sorted(found.items())}


def _xref_report(parsed: ParseResult) -> dict:
    total, resolved, misses = xref_resolution(parsed.tree)
    t17_total = sum(
        1 for node in parsed.tree.walk() for target in node.xrefs if target.startswith(TITLE17_TARGET)
    )
    t17_missed = sum(1 for _, target in misses if target.startswith(TITLE17_TARGET))
    return {
        "total": total,
        "resolved": resolved,
        "rate": _rate(resolved, total),
        "title17": {
            "total": t17_total, "resolved": t17_total - t17_missed, "rate": _rate(t17_total - t17_missed, t17_total),
        },
        "misses": [[from_path, target] for from_path, target in misses],
    }


def build_report(parsed: ParseResult, chunks: Sequence[BookChunk], *, budget: int) -> dict:
    """Every number of the dry run, and the bar's verdict on them."""
    book = parsed.tree.book
    sections = sum(
        1 for root in parsed.tree.nodes if root.appendix is None and root.kind != "history_note"
    )
    markers = _marker_report(parsed.markers, parsed.notes)
    appendices = _appendix_types(parsed)
    over_budget = [
        {"chunk_key": c.chunk_key, "tokens": c.tokens} for c in chunks if c.tokens == OVER_WINDOW or c.tokens > budget
    ]
    counted = [c for c in chunks if c.tokens != OVER_WINDOW]
    largest = max(counted, key=lambda c: c.tokens, default=None)
    unused = [override.id for override in parsed.ledger.unused()]

    bar = {
        "title17_sections": sections == BAR_EXPECTED["title17_sections"],
        "markers": (
            markers["total"] == BAR_EXPECTED["markers"]
            and markers["linked"] == markers["total"]
            and not markers["scope_mismatches"]
        ),
        "appendices": (
            len(appendices) == BAR_EXPECTED["appendices"]
            and appendices == {letter: spec.source_type for letter, spec in APPENDICES.items()}
        ),
        "out_of_sequence": not parsed.out_of_sequence,
        "over_budget": not over_budget,
        "overrides_unused": not unused,
    }
    return {
        "book": book.id,
        "edition": book.edition_id,
        "budget": budget,
        "chunks": len(chunks),
        "title17_sections": sections,
        "markers": markers,
        "appendices": appendices,
        "out_of_sequence": list(parsed.out_of_sequence),
        "xref_resolution": _xref_report(parsed),
        "chunks_by_kind": dict(Counter(c.kind for c in chunks)),
        "largest_chunk_tokens": largest.tokens if largest else 0,
        "largest_chunk_key": largest.chunk_key if largest else None,
        "over_budget": over_budget,
        "overrides_unused": unused,
        "hyphens_undecided": list(parsed.hyphens.undecided),
        "bar_expected": BAR_EXPECTED,
        "bar": bar,
        "bar_passed": all(bar.values()),
    }


def sample_chunks(chunks: Sequence[BookChunk], seed: int, n: int = SAMPLE_SIZE) -> list[BookChunk]:
    """`n` chunks drawn with `random.Random(seed)`, in book order: what a person checks against the printed
    page. The seed is printed and stored in the report, so the draw can be made again from the chunk list."""
    picks = random.Random(seed).sample(range(len(chunks)), min(n, len(chunks)))
    return [chunks[i] for i in sorted(picks)]


def _cell(text: str) -> str:
    """One TSV cell: a tab or a line break would end it, so each becomes a space."""
    return " ".join(str(text).split())


def chunk_row(chunk: BookChunk, book: BookEntry) -> list[str]:
    return [
        _cell(chunk.ref_id),
        _cell(chunk.chunk_key),
        _cell(chunk.label),
        str(chunk.page_start) if chunk.page_start == chunk.page_end else f"{chunk.page_start}-{chunk.page_end}",
        chunk.kind,
        chunk.source_type,
        str(chunk.tokens),
        _cell(", ".join(chunk.xrefs)),
        _cell(render_citation(
            label=chunk.label, kind=chunk.kind, source_type=chunk.source_type, appendix=chunk.appendix,
            page_start=chunk.page_start, page_end=chunk.page_end, book=book,
        )),
    ]


def chunks_tsv(chunks: Sequence[BookChunk], book: BookEntry, *, with_text: bool = False) -> str:
    """The chunk list as TSV, one row per chunk. `with_text` adds the embedded text as a last column (its
    line breaks shown as ⏎), for the person who checks it against the page."""
    header = [*TSV_COLUMNS, "text"] if with_text else list(TSV_COLUMNS)
    lines = ["\t".join(header)]
    for chunk in chunks:
        row = chunk_row(chunk, book)
        if with_text:
            row.append(" ⏎ ".join(_cell(part) for part in chunk.text.split("\n")))
        lines.append("\t".join(row))
    return "\n".join(lines) + "\n"
