# ingest/books/circular92/appendices.py
"""Circular 92 (Dec. 2025), front end part 3: appendices A-T as trees of labelled nodes.

The appendices hold law other than Title 17: the provisions of Acts that do not amend the title
(A-M, Q, R), sections of other titles of the U.S. Code (N, O, P), Part II Section 6 of the TRIPs
Agreement (S, Articles 35-38), and the definition of "Berne Convention work" that the DMCA deleted
from §101 (T). What each appendix is, and how it is cited, is edition data: APPENDICES.

A section opens on a heading in one of five styles, "Sec. 101 · Short Title.", "Section 1 · Short
Title.", "Sec. 1. Short Title", "§ 2318 · Trafficking …" and "Article 35 · Relation to IPIC Treaty";
Appendix A sets its section numbers in small caps at the start of their text instead ("Sec. 102.
This Act …"). A heading line right after a heading line continues it. "Title …", "Part …",
"Chapter …", "Subtitle …" and "DIVISION …" headings, and TRIPs' "Section 6: …", are context: they
end the section before them and are no node's text. "* * * * * * *" marks text the book leaves out, so a label after it may skip
ahead. A heading-font line after text is text: the quoted headings of an amendment, or the book's
bracketed "[To assist the reader, …]".

A section's text is placed as Title 17's is (title17.UnitBuilder: labelled subdivisions by their
characters and sequence, the indent breaking ties). TRIPs numbers its paragraphs "1." rather than
"(1)", so an article's paragraphs are split here. Appendix T is one node (ruling 6).
"""
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace

from ingest.books.circular92.layout import RawLine
from ingest.books.circular92.text import HyphenDecider, clean_join
from ingest.books.circular92.title17 import PARA_INDENT, UnitBuilder, set_unit_headings
from ingest.books.models import Node, TextLine
from ingest.books.overrides import OverrideLedger


@dataclass(frozen=True)
class AppendixSpec:
    letter: str
    source_type: str  # usc_title, uncodified_act or treaty
    jurisdiction: str
    prefix: str  # paths start with it: "pl105-304", "usc18", "trips"; Appendix T's is its whole path
    act_name: str | None = None  # as cited; TRIPs too ("TRIPs Agreement")
    pub_law: str | None = None
    year: int | None = None
    reason: str = ""  # where the book's own print is not what is used here


def _act(letter: str, act_name: str, pub_law: str, year: int, reason: str = "") -> AppendixSpec:
    return AppendixSpec(letter, "uncodified_act", "US", f"pl{pub_law}", act_name, pub_law, year, reason)


def _title(letter: str, title: int) -> AppendixSpec:
    return AppendixSpec(letter, "usc_title", "US", f"usc{title}")


# Appendix T is the definition the WIPO treaties Act deleted from §101 (ruling 6): one node, kind
# `repealed`, cited as a definition of §101 and reached by lookup only.
DELETED_DEFINITION = ("T", "Berne Convention work")

APPENDICES: dict[str, AppendixSpec] = {spec.letter: spec for spec in (
    _act("A", "Copyright Act of 1976", "94-553", 1976,
         "note 1 prints Pub. L. No. 94-533 (printed p. 370, checked on the page image); the Act is "
         "Pub. L. 94-553, 90 Stat. 2541, as the book prints it elsewhere (app. N n.1, app. O n.4)"),
    _act("B", "Digital Millennium Copyright Act", "105-304", 1998),
    _act("C", "Copyright Royalty and Distribution Reform Act of 2004", "108-419", 2004),
    _act("D", "Satellite Home Viewer Extension and Reauthorization Act of 2004", "108-447", 2004),
    _act("E", "Intellectual Property Protection and Courts Amendments Act of 2004", "108-482", 2004),
    _act("F", "Prioritizing Resources and Organization for Intellectual Property Act of 2008", "110-403", 2008),
    _act("G", "Satellite Television Extension and Localism Act of 2010", "111-175", 2010),
    _act("H", "Unlocking Consumer Choice and Wireless Competition Act", "113-144", 2014),
    _act("I", "STELA Reauthorization Act of 2014", "113-200", 2014),
    _act("J", "Marrakesh Treaty Implementation Act", "115-261", 2018,
         "the book gives Appendix J no note, so it prints no Public Law number here; the Act is "
         "Pub. L. 115-261, 132 Stat. 3667, as ch. 1 nn.63-64 print it"),
    _act("K", "Orrin G. Hatch–Bob Goodlatte Music Modernization Act", "115-264", 2018,
         "note 1 prints Pub. L. No. 115-262 (printed p. 420, checked on the page image); the Act is "
         "Pub. L. 115-264, 132 Stat. 3676, as the book prints it elsewhere (app. O n.1)"),
    _act("L", "Satellite Television Community Protection and Promotion Act of 2019", "116-94", 2019),
    _act("M", "Copyright Alternative in Small-Claims Enforcement Act of 2020", "116-260", 2020,
         "note 1 prints Pub. L. No. 116-26 (printed p. 424, checked on the page image); the Act is "
         "Pub. L. 116-260, 134 Stat. 1182, as the book prints it elsewhere"),
    _title("N", 18),
    _title("O", 28),
    _title("P", 44),
    _act("Q", "Berne Convention Implementation Act of 1988", "100-568", 1988),
    _act("R", "Uruguay Round Agreements Act", "103-465", 1994),
    AppendixSpec("S", "treaty", "international", "trips", act_name="TRIPs Agreement"),
    AppendixSpec("T", "usc_title", "US", "usc17-s101-def-berne-convention-work",
                 reason="ruling 6: the definition §101 no longer holds is one repealed node, reached by lookup"),
)}

APPENDIX_SCOPE = "Appendix "
SKIPPED_KINDS = frozenset({"title", "toc", "note", "notes_heading"})

SECTION_HEADING_RE = re.compile(r"(?:Sec\.|Section|§|Article)\s*(\d+[A-Za-z]?)\s*[·.]\s*(.+)")
RUN_IN_SECTION_RE = re.compile(r"Sec\.\s*(\d+[A-Za-z]?)\.\s+(.+)")
RUN_IN_FONT = "MinionSC"  # Appendix A's "Sec. 102." is set in small caps; a "Sec. 102." in roman is text
CONTEXT_RE = re.compile(r"(?:Title|Part|Chapter|Subtitle|DIVISION)\s|Section\s+\d+\s*:")
ELISION_RE = re.compile(r"\*(?:\s*\*)+")
TREATY_PARAGRAPH_RE = re.compile(r"(\d{1,2})\.\s")


@dataclass
class _Section:
    number: str  # as printed, and cited: the appendices print a section letter as a capital ("2319A")
    scope: str
    heading: list[RawLine]  # the heading line and the lines it wraps onto; none for a run-in number
    body: list[RawLine] = field(default_factory=list)  # with the elision lines, which say where text is left out
    run_in: RawLine | None = None  # Appendix A: the line whose start is the section number


def _is_elision(line: RawLine) -> bool:
    return line.kind == "heading" and ELISION_RE.fullmatch(line.text) is not None


def _by_scope(lines: Sequence[RawLine]) -> Iterator[tuple[AppendixSpec, list[RawLine]]]:
    """The appendices' lines, appendix by appendix, in order."""
    groups: dict[str, list[RawLine]] = {}
    for line in lines:
        if line.scope.startswith(APPENDIX_SCOPE):
            groups.setdefault(line.scope, []).append(line)
    for scope, scope_lines in groups.items():
        yield APPENDICES[scope.removeprefix(APPENDIX_SCOPE)], scope_lines


def _sections(scope_lines: Sequence[RawLine]) -> list[_Section]:
    """One appendix's lines, grouped by the section they belong to.

    A heading line right after a section's heading line continues its title; after a context
    heading no section is open, so a heading line there continues the context."""
    sections: list[_Section] = []
    current: _Section | None = None
    in_heading = False
    for line in scope_lines:
        if line.kind in SKIPPED_KINDS:
            in_heading = False
            continue
        if line.kind == "heading":
            if m := SECTION_HEADING_RE.fullmatch(line.text):
                current = _Section(number=m.group(1), scope=line.scope, heading=[line])
                sections.append(current)
                in_heading = True
                continue
            if ELISION_RE.fullmatch(line.text):
                if current is not None:
                    current.body.append(line)
            elif CONTEXT_RE.match(line.text):
                current = None
            elif in_heading:
                current.heading.append(line)
                continue
            elif current is not None:  # after text: an amendment's quoted heading, or the book's bracket
                current.body.append(line)
            in_heading = False
            continue
        in_heading = False
        if (m := RUN_IN_SECTION_RE.fullmatch(line.text)) and line.font[0] == RUN_IN_FONT:
            current = _Section(number=m.group(1), scope=line.scope, heading=[], run_in=line)
            current.body.append(replace(line, text=m.group(2)))
            sections.append(current)
            continue
        if current is None:
            raise ValueError(f"{line.scope}, p. {line.page}: text outside any section: {line.text!r}")
        current.body.append(line)
    return sections


def _label(spec: AppendixSpec, number: str, labels: tuple[str, ...] = ()) -> str:
    sub = "".join(f"({label})" for label in labels)
    if spec.source_type == "uncodified_act":
        return f"{spec.act_name}, Pub. L. No. {spec.pub_law}, §{number}{sub} ({spec.year})"
    if spec.source_type == "treaty":
        return f"{spec.act_name}, art. {number}{sub}"
    return f"{spec.prefix.removeprefix('usc')} U.S.C. §{number}{sub}"


def _path(spec: AppendixSpec, number: str) -> str:
    if spec.source_type == "treaty":
        return f"{spec.prefix}-art{number}"
    return f"{spec.prefix}-s{number.lower()}"


def _heading(section: _Section, hyphens: HyphenDecider) -> str:
    """The section's title as printed, its heading lines joined, without the full stop that closes it."""
    if not section.heading:
        return ""
    first, *rest = section.heading
    lines = [TextLine(SECTION_HEADING_RE.fullmatch(first.text).group(2), first.page)]
    lines += [TextLine(line.text, line.page) for line in rest]
    return clean_join(lines, hyphens).removesuffix(".")


def _para_starts(body: Sequence[RawLine]) -> list[bool]:
    """A line starts a paragraph when it is set more than PARA_INDENT right of the line before; the
    first line, and the first after an elision, always do."""
    starts, before = [], None
    for line in body:
        if _is_elision(line):
            starts.append(False)
            before = None
            continue
        starts.append(before is None or line.x0 > before.x0 + PARA_INDENT)
        before = line
    return starts


def _text_line(line: RawLine, para_start: bool) -> TextLine:
    return TextLine(text=line.text, page=line.page, marks=list(line.marks), para_start=para_start)


def _place_paragraphs(article: Node, spec: AppendixSpec, number: str, body: Sequence[RawLine],
                      starts: Sequence[bool]) -> None:
    """A TRIPs article: a paragraph opens on "1.", "2." … in turn, at the start of a paragraph; text
    before the first is the article's own."""
    current, expected = article, 1
    for line, para_start in zip(body, starts):
        if _is_elision(line):
            continue
        m = TREATY_PARAGRAPH_RE.match(line.text)
        if m and para_start and int(m.group(1)) == expected:
            current = Node(
                path=f"{article.path}-{expected}",
                kind="rule",
                source_type=spec.source_type,
                jurisdiction=spec.jurisdiction,
                label=_label(spec, number, (str(expected),)),
                heading="",
                scope=article.scope,
                appendix=spec.letter,
            )
            article.children.append(current)
            expected += 1
        current.lead.append(_text_line(line, para_start))


def _section_node(spec: AppendixSpec, section: _Section, hyphens: HyphenDecider, ledger: OverrideLedger,
                  out_of_sequence: list[str]) -> Node:
    node = Node(
        path=_path(spec, section.number),
        kind="rule",
        source_type=spec.source_type,
        jurisdiction=spec.jurisdiction,
        label=_label(spec, section.number),
        heading=_heading(section, hyphens),
        scope=section.scope,
        appendix=spec.letter,
    )
    if not any(not _is_elision(line) for line in section.body):
        page = (section.heading or [section.run_in])[0].page
        raise ValueError(f"{node.label} (p. {page}) has a heading and no text: was its page left out?")
    starts = _para_starts(section.body)
    if spec.source_type == "treaty":
        _place_paragraphs(node, spec, section.number, section.body, starts)
        return node
    builder = UnitBuilder(node, "rule", ledger, out_of_sequence,
                          label_of=lambda labels: _label(spec, section.number, labels), quotations=True)
    for line, para_start in zip(section.body, starts):
        if _is_elision(line):
            builder.elide()
        else:
            builder.feed(line, para_start)
    set_unit_headings(node, hyphens)
    return node


def _deleted_definition(spec: AppendixSpec, scope_lines: Sequence[RawLine], hyphens: HyphenDecider) -> Node:
    """Appendix T: the whole text is one repealed node, cited as the §101 definition it was."""
    _, term = DELETED_DEFINITION
    titles = [line for line in scope_lines if line.kind == "title"][1:]  # after "Appendix T"
    body = [line for line in scope_lines if line.kind not in SKIPPED_KINDS]
    if not body:
        raise ValueError(f"Appendix {spec.letter} has no text: was its page left out?")
    return Node(
        path=spec.prefix,
        kind="repealed",
        source_type=spec.source_type,
        jurisdiction=spec.jurisdiction,
        label=f"17 U.S.C. §101 (“{term}”)",
        heading=clean_join([TextLine(line.text, line.page) for line in titles], hyphens),
        scope=body[0].scope,
        lead=[_text_line(line, para_start) for line, para_start in zip(body, _para_starts(body))],
        appendix=spec.letter,
        term=term,
    )


def appendix_heading_marks(lines: Sequence[RawLine]) -> dict[str, list[int]]:
    """The footnote markers on each appendix section's heading (its heading line and the lines it wraps
    onto), by section path, in order. A heading is no node's text line, so the note links read its
    markers here, as they read Title 17's from title17.section_heading_marks."""
    marks: dict[str, list[int]] = {}
    for spec, scope_lines in _by_scope(lines):
        if spec.letter == DELETED_DEFINITION[0]:
            continue
        for section in _sections(scope_lines):
            marks[_path(spec, section.number)] = [m for line in section.heading for m in line.marks]
    return marks


def appendix_nodes(lines: list[RawLine], hyphens: HyphenDecider, ledger: OverrideLedger, *,
                   out_of_sequence: list[str] | None = None) -> list[Node]:
    """The appendices' sections (articles, for TRIPs), in order, each with its subdivisions; Appendix T
    as its one node. Each label line no rule (and no override) placed is appended to `out_of_sequence`,
    when given, as `p.<page>: <line text>` (as title17_nodes reports them)."""
    collected = [] if out_of_sequence is None else out_of_sequence
    nodes: list[Node] = []
    for spec, scope_lines in _by_scope(lines):
        if spec.letter == DELETED_DEFINITION[0]:
            nodes.append(_deleted_definition(spec, scope_lines, hyphens))
            continue
        for section in _sections(scope_lines):
            nodes.append(_section_node(spec, section, hyphens, ledger, collected))
    return nodes
