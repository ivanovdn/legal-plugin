# ingest/books/circular92/__init__.py
"""Circular 92 (Dec. 2025): the PDF to one BookTree.

The front end (layout.py, text.py, title17.py, appendices.py, notes.py) reads the pages into section
nodes and notes; this assembles them in document order (each chapter's or appendix's sections, then
its notes), gives every node its cross-references (xrefs.py, read from where the node sits) and every
node its notes (tree.link_notes). One override ledger serves the whole parse, so what it reports
unused is what no part of the parse consumed.
"""
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from ingest.books.circular92.appendices import APPENDICES, DELETED_DEFINITION, appendix_nodes
from ingest.books.circular92.layout import BODY_PDF_PAGES, RawLine, extract_lines
from ingest.books.circular92.notes import history_notes
from ingest.books.circular92.text import HyphenDecider, clean_join
from ingest.books.circular92.title17 import DEFINITIONS_SECTION, TITLE, title17_nodes
from ingest.books.models import BookTree, Node, TextLine
from ingest.books.overrides import OverrideLedger, load_overrides
from ingest.books.registry import BookEntry
from ingest.books.tree import link_notes, own_text
from ingest.books.xrefs import XrefContext, find_xrefs, label_level


@dataclass
class ParseResult:
    tree: BookTree
    ledger: OverrideLedger
    hyphens: HyphenDecider
    out_of_sequence: list[str]  # label lines no rule or override placed, Title 17's then the appendices'
    markers: dict[str, list[int]]  # per scope: the footnote markers lifted from its text, in reading order
    notes: dict[str, list[int]]  # per scope: the numbers of its notes, in order


def _appendix_code(letter: str) -> dict:
    """Whose sections an appendix's bare "section N" names: an Act's own, another title's, or Title 17's
    (TRIPs and Appendix T, the definition §101 no longer holds)."""
    spec = APPENDICES[letter]
    if spec.source_type == "uncodified_act":
        return {"title": TITLE, "act": spec.prefix}
    if spec.source_type == "usc_title" and letter != DELETED_DEFINITION[0]:
        return {"title": int(spec.prefix.removeprefix("usc"))}
    return {"title": TITLE}


def _root_context(root: Node) -> XrefContext:
    """The context of a section (or article, or note) node: its section and whose code its bare sections are.
    A note has no section; nor has a TRIPs article, whose paragraphs ("1.") give nothing to be relative to."""
    top = {"path_labels": (), "label_levels": ()}
    if root.appendix is None:  # a Title 17 section, or a chapter's note
        section = None if root.kind == "history_note" else root.path.split("-s", 1)[1]
        return XrefContext(title=TITLE, section=section, **top)
    code = _appendix_code(root.appendix)
    if root.kind == "history_note" or APPENDICES[root.appendix].source_type == "treaty":
        return XrefContext(section=None, **code, **top)
    if root.appendix == DELETED_DEFINITION[0]:
        return XrefContext(section=DEFINITIONS_SECTION, term=root.term, **code, **top)
    return XrefContext(section=root.path.removeprefix(f"{APPENDICES[root.appendix].prefix}-s"), **code, **top)


def _contexts(node: Node, ctx: XrefContext) -> Iterator[tuple[Node, XrefContext]]:
    """Each node under `node` with its context: its labels below the section (below a §101 definition, the
    definition's term and labels from it), each at the level the U.S. Code's sequences give it."""
    yield node, ctx
    for child in node.children:
        if child.kind == "definition" and child.term is not None:
            child_ctx = replace(ctx, term=child.term, path_labels=(), label_levels=())
        else:
            label = child.path.rsplit("-", 1)[1]
            level = label_level(label, ctx.label_levels[-1] if ctx.label_levels else 0)
            child_ctx = replace(ctx, path_labels=(*ctx.path_labels, label), label_levels=(*ctx.label_levels, level))
        yield from _contexts(child, child_ctx)


def _set_xrefs(roots: Sequence[Node], join: Callable[[Sequence[TextLine]], str], ledger: OverrideLedger
               ) -> None:
    for root in roots:
        for node, ctx in _contexts(root, _root_context(root)):
            pages = tuple(sorted({line.page for line in node.own_lines()}))
            node.xrefs, node.xrefs_unparsed = find_xrefs(own_text(node, join), replace(ctx, pages=pages), ledger)


def _in_document_order(scopes: Sequence[str], sections: Sequence[Node], notes: Sequence[Node]) -> list[Node]:
    """Each scope's sections, then its notes, scope by scope as the pages run."""
    return [node for scope in scopes for group in (sections, notes) for node in group if node.scope == scope]


def parse_lines(lines: list[RawLine], book: BookEntry) -> ParseResult:
    """The whole parse from extracted lines (parse_circular92 extracts them; a test reuses its own)."""
    ledger = OverrideLedger(load_overrides(book.overrides_path))
    hyphens = HyphenDecider(lines, ledger)
    sections, out_of_sequence = title17_nodes(lines, hyphens, ledger)
    sections += appendix_nodes(lines, hyphens, ledger, out_of_sequence=out_of_sequence)
    notes = history_notes(lines, sections, hyphens, ledger)

    def join(text_lines: Sequence[TextLine]) -> str:
        return clean_join(text_lines, hyphens)

    _set_xrefs([*sections, *notes], join, ledger)
    scopes = list(dict.fromkeys(line.scope for line in lines))
    tree = BookTree(book, _in_document_order(scopes, sections, notes), join=join)
    link_notes(tree)
    return ParseResult(
        tree=tree,
        ledger=ledger,
        hyphens=hyphens,
        out_of_sequence=out_of_sequence,
        markers={scope: [m for line in lines if line.scope == scope for m in line.marks] for scope in scopes},
        notes={scope: [note.note_number for note in notes if note.scope == scope] for scope in scopes},
    )


def parse_circular92(pdf_path: Path, book: BookEntry, pdf_pages: Iterable[int] = BODY_PDF_PAGES) -> ParseResult:
    """Circular 92's pages (the body by default) parsed into one BookTree, with what the report needs."""
    return parse_lines(extract_lines(pdf_path, pdf_pages), book)
