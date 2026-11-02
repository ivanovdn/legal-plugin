# ingest/books/circular92/notes.py
"""Circular 92 (Dec. 2025), front end part 3: the numbered amendment-history notes.

Each chapter and appendix ends with its notes ("<scope> · Notes"), numbered from 1, and each note
explains one footnote marker in the scope's text. A note is a node of its own kind, history_note:
amendment history, not law (the history-notes decision). It keeps a link to where its marker sits,
`annotates`: the path of the node whose own text holds the marker, or the section whose heading
holds it, or the section a table-of-contents entry names (ruling R9). A marker on a chapter's or an
appendix's title annotates nothing (None). A note that says when a provision takes or took effect
is flagged (`effective_date`, spec D5).

A note starts on a line "<n>. " (or "<n> . ") where n is the next number expected and the line is
set near the margin (x0 < 70: note 1 sits at the margin, later notes one indent in); every other
line continues the note before it (the prototype's rule; the research report, section 1).
"""
import re
from collections.abc import Iterator, Sequence

from ingest.books.circular92.appendices import APPENDICES, APPENDIX_SCOPE, appendix_heading_marks
from ingest.books.circular92.layout import RawLine
from ingest.books.circular92.text import HyphenDecider, clean_join
from ingest.books.circular92.title17 import (
    JURISDICTION,
    PARA_INDENT,
    SECTION_HEADING_RE,
    SOURCE_TYPE,
    section_heading_marks,
    section_path,
)
from ingest.books.models import Node, TextLine

EFFECTIVE_DATE_RE = re.compile(r"tak(e|es|en) effect|took effect|effective (date|upon|on)", re.I)

NOTE_START_RE = re.compile(r"(\d{1,3})\s?\.\s")
NOTE_START_MAX_X = 70.0
CHAPTER_SCOPE = "Chapter "
# A table-of-contents entry opens with the section number ("104a Copyright in restored works ……").
CONTENTS_ENTRY_RE = re.compile(r"(\d+[A-Za-z]?)\s")
CONTENTS_KINDS = frozenset({"toc", "body"})  # an entry that wraps has no leaders on its first line, so reads as body


def _runs(lines: Sequence[RawLine]) -> list[tuple[str, int, list[RawLine]]]:
    """(scope, number, lines) of each note, in order."""
    runs: list[tuple[str, int, list[RawLine]]] = []
    expected: dict[str, int] = {}
    for line in lines:
        if line.kind != "note":
            continue
        number = expected.get(line.scope, 1)
        m = NOTE_START_RE.match(line.text)
        if m and int(m.group(1)) == number and line.x0 < NOTE_START_MAX_X:
            runs.append((line.scope, number, [line]))
            expected[line.scope] = number + 1
        elif runs and runs[-1][0] == line.scope:
            runs[-1][2].append(line)
        else:
            raise ValueError(f"{line.scope}, p. {line.page}: a note line before the first note: {line.text!r}")
    return runs


def _contents_marks(lines: Sequence[RawLine]) -> Iterator[tuple[str, int, str | None, RawLine]]:
    """(scope, marker, the path of the section named, line) for each marker on a chapter title or in
    a chapter's table of contents (the lines before its first § heading).

    An entry names its section by its number, matched case-insensitively ("104a" is §104A: paths
    are lower case). The line an entry wraps onto is set further in and belongs to the entry; any
    other line there names no section. A title names none either."""
    started: set[str] = set()
    scope = entry = entry_x = None
    for line in lines:
        if line.scope != scope:
            scope, entry, entry_x = line.scope, None, None
        if line.kind == "title":
            for n in line.marks:
                yield line.scope, n, None, line
            continue
        if not line.scope.startswith(CHAPTER_SCOPE) or line.scope in started:
            continue
        if line.kind == "heading" and SECTION_HEADING_RE.match(line.text):
            started.add(line.scope)
            continue
        if line.kind not in CONTENTS_KINDS:
            continue
        if m := CONTENTS_ENTRY_RE.match(line.text):
            entry, entry_x = section_path(m.group(1)), line.x0
        elif entry_x is None or line.x0 <= entry_x + PARA_INDENT:
            entry, entry_x = None, None
        for n in line.marks:
            yield line.scope, n, entry, line


def _marker_sites(lines: Sequence[RawLine], placed: Sequence[Node]) -> dict[tuple[str, int], str | None]:
    """(scope, marker) -> the path of what the marker annotates (None: a chapter or appendix title).

    Every marker lifted from `lines` must have a place: on a placed node's own lines, on a section
    heading, on a title or in a table of contents. One that has none is a parse error, raised, so a
    note cannot lose its link unseen."""
    nodes = {node.path: node for root in placed for node in root.walk()}
    sites: dict[tuple[str, int], str | None] = {}

    def put(scope: str, number: int, path: str | None) -> None:
        key = (scope, number)
        if key in sites and sites[key] != path:
            raise ValueError(f"{scope}: marker {number} sits in two places, {sites[key]} and {path}")
        sites[key] = path

    for node in nodes.values():
        for line in node.own_lines():
            for n in line.marks:
                put(node.scope, n, node.path)
    for path, marks in {**section_heading_marks(lines), **appendix_heading_marks(lines)}.items():
        if marks and path not in nodes:
            raise ValueError(f"the heading of {path} holds markers {marks}, but {path} was not placed")
        for n in marks:
            put(nodes[path].scope, n, path)
    for scope, n, path, line in _contents_marks(lines):
        if path is not None and path not in nodes:
            raise ValueError(f"{scope}, p. {line.page}: marker {n} is on the contents entry for {path}, "
                             f"which was not placed")
        put(scope, n, path)
    for line in lines:
        for n in line.marks:
            if (line.scope, n) not in sites:
                raise ValueError(f"{line.scope}, p. {line.page}: marker {n} sits on no placed node: {line.text!r}")
    return sites


def _scope_facts(scope: str) -> tuple[str, str, str, str, str | None]:
    """(path prefix, label prefix, source type, jurisdiction, appendix) for a scope's notes."""
    if scope.startswith(APPENDIX_SCOPE):
        letter = scope.removeprefix(APPENDIX_SCOPE)
        spec = APPENDICES[letter]
        return f"app{letter}", f"app. {letter}", spec.source_type, spec.jurisdiction, letter
    number = scope.removeprefix(CHAPTER_SCOPE)
    return f"ch{number}", f"ch. {number}", SOURCE_TYPE, JURISDICTION, None


def _note(scope: str, number: int, raw: list[RawLine], annotates: str | None, hyphens: HyphenDecider) -> Node:
    first = raw[0]
    texts = [first.text[NOTE_START_RE.match(first.text).end():], *(line.text for line in raw[1:])]
    starts = [True] + [line.x0 > before.x0 + PARA_INDENT for before, line in zip(raw, raw[1:])]
    lead = [TextLine(text=text, page=line.page, marks=[], para_start=start)
            for text, line, start in zip(texts, raw, starts)]
    path_prefix, label_prefix, source_type, jurisdiction, appendix = _scope_facts(scope)
    return Node(
        path=f"{path_prefix}-n{number}",
        kind="history_note",
        source_type=source_type,
        jurisdiction=jurisdiction,
        label=f"{label_prefix} n.{number}",
        heading="",
        scope=scope,
        lead=lead,
        appendix=appendix,
        note_number=number,
        annotates=annotates,
        effective_date=EFFECTIVE_DATE_RE.search(clean_join(lead, hyphens)) is not None,
    )


def history_notes(lines: list[RawLine], placed: list[Node], hyphens: HyphenDecider) -> list[Node]:
    """The notes of every chapter and appendix in `lines`, in order, each linked to its marker's place.

    `placed` holds the roots built from the same lines (Title 17's sections, the appendices' nodes):
    a marker's place is looked up among them. A note whose marker is not in `lines` annotates None.
    """
    sites = _marker_sites(lines, placed)
    return [_note(scope, number, raw, sites.get((scope, number)), hyphens) for scope, number, raw in _runs(lines)]
