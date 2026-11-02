# ingest/books/circular92/title17.py
"""Circular 92 (Dec. 2025), front end part 2: Title 17's sections as a tree of labelled nodes.

Chapters 1-15 hold the statute. A `§ <number> · <title>` heading opens a section, and its body
lines are placed under its labelled subdivisions, (a)(1)(A)(i)(I)(aa)(AA) (labels.py), and §101's
under its definitions. A node keeps its own lines, the lead before its first child and the tail
after its last, as TextLines with their printed page, footnote markers and paragraph starts.
Turning them into running text is clean_join's job, done by whoever reads them.

The layout, measured on the whole of Title 17 (research report, 2026-10-30): a unit's first line
sits at x = 54 + 12 * level, (a) 66, (1) 78, (A) 90, (i) 102, and its other lines one level less.
A label at the start of a line opens a unit, unless the line wrap carried a cross-reference
there. Text that drops below the open unit's other lines is the flush text after a list, and
belongs to a unit above it.
"""
import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from ingest.books.circular92.labels import is_expected, resolve
from ingest.books.circular92.layout import RawLine
from ingest.books.circular92.text import HyphenDecider, clean_join
from ingest.books.models import Node, TextLine
from ingest.books.overrides import Override, OverrideLedger
from ingest.books.ref_ids import slugify_term

TITLE = 17
SOURCE_TYPE = "usc_title"
JURISDICTION = "US"
CHAPTER_SCOPE = "Chapter"  # the scopes that hold Title 17's sections; appendices have their own front end
SKIPPED_KINDS = frozenset({"toc", "title", "note", "notes_heading"})

SECTION_HEADING_RE = re.compile(r"§\s*(\d+[A-Za-z]?)\s*·\s*(.*)")
REPEALED_TITLE = "[Repealed]"

INDENT_ORIGIN = 54.0  # x of an unindented line
INDENT_STEP = 12.0  # one level of indent
PARA_INDENT = 6.0  # a line set more than this right of the line before starts a paragraph

# Labels at the start of a line: "(a)", "(1)(A)", "(3) (A)", "[(i) Repealed.]". Group 1 ends at
# the last label's ")", so what follows can be read as a cross-reference or not.
LEAD_RE = re.compile(r"^\[?(\([A-Za-z0-9]{1,5}\)(?:\s?\([A-Za-z0-9]{1,5}\))*)")
LABEL_RE = re.compile(r"\(([A-Za-z0-9]{1,5})\)")
# Labels inline after a heading's dash: "(c) Exceptions.—(1) The …", or on a wrapped heading's
# next line, "sures.—(1)(A) No person …".
INLINE_RE = re.compile(r"^[^—]{0,160}?[.:]\s?—\s?((?:\([A-Za-z0-9]{1,5}\))+)")
INLINE_WINDOW = 3  # a heading wraps onto at most this many lines
# A label at the start of a line is a cross-reference the wrap carried there when the line before
# ends naming one ("… under subsection" / "(c)(1) and …", "… sections 112(e)(1) and 114(d)" /
# "(2) during …") or the label goes on as one ("(2) of this section", "(b), (c), and (h)"),
# unless the line before closes a list item ("; or").
XREF_TAIL_RE = re.compile(
    r"(\b(sections?|subsections?|paragraphs?|subparagraphs?|clauses?|subclauses?|items?|and|or)"
    r"|\d[A-Za-z]?|\([A-Za-z0-9]{1,5}\))\s*$",
    re.I,
)
# A list item ends "; or", ", and", or with the conjunction wrapped onto a line of its own.
ITEM_END_RE = re.compile(r"(^|[;,])\s?(or|and)\s*$")
XREF_AFTER_RE = re.compile(
    r"^(,|\)|\s+(and|or|through)\s+\(|"
    r"\s+of\s+(this|such|that|section|subsection|paragraph|subparagraph|clause|subclause|title|chapter)\b)"
)
# The line before ends a sentence, a heading or a list item: a label after it opens a unit.
END_OK_RE = re.compile(r"([.:;—\]”]|[;,]\s?(or|and))\s*$")
# … except ", or" after a label, which also ends a list of references ("paragraph (1), (2), or" /
# "(3) by …"): there only the sequence can admit the label.
LABEL_LIST_END_RE = re.compile(r"\([A-Za-z0-9]{1,5}\),?\s+(or|and)\s*$")
# A line that runs on into the next one: it ends no sentence, clause or list item.
CLOSED_RE = re.compile(r"[.,;:—\]”]\s*$")
# A labelled unit's small-caps heading: "(a) Criminal Infringement.—", read on cleaned text (no
# space before the dash). A heading holds no opening quote: a ".—" after one is the heading of the
# quoted text ("(A) by striking “Whoever” and inserting “Offense.—”"), not the unit's own.
HEADING_RE = re.compile(r"^\[?(?:\([A-Za-z0-9]{1,5}\)\s?)+\s*([^“‘]{3,120}?)\.—")
HEADING_LINES = 2
REPEALED_UNIT_RE = re.compile(r"^\[\([A-Za-z0-9]{1,5}\)\s*Repealed\.?\]$")

# Quoted text: the words an amendment inserts (in the appendices) open with a quote mark on each of
# their paragraphs and close once, at the end ("… paid when the order is filed.”."). Title 17 quotes
# no amendment: of its 15 lines that open with a quote mark, 14 start §101 definitions (the restart
# rule reads them first) and one wraps a quoted term (p. 303); its tree is the same with the rule.
QUOTE_OPEN_RE = re.compile(r"[“‘\"]")
QUOTE_CLOSE_RE = re.compile(r"(”|’’)[.;,:]*(\s+(and|or))?\s*$")

# §101: a definition starts at the definitions' indent, with a quoted term near the start of its
# first line, after a line that closes the one before. 54 definitions, after the intro sentence.
DEFINITIONS_SECTION = "101"
DEFINITION_X = (62.0, 67.0)
TERM_RE = re.compile(r"“\s?([^”’]+?)\s?(”|’’)")
TERM_WINDOW = 70
DEFINITION_AFTER_RE = re.compile(r"[.:;—]\s*$")


def x_level(x0: float) -> int:
    """The level a first line set at `x0` sits at."""
    return round((x0 - INDENT_ORIGIN) / INDENT_STEP)


def section_path(number: str) -> str:
    """`106A` -> `usc17-s106a`: section numbers are lowercased in paths, labels keep their case."""
    return f"usc{TITLE}-s{number.lower()}"


@dataclass
class _Section:
    number: str  # as cited: a section letter is a capital ("106A")
    scope: str
    heading: list[RawLine]  # the § line, then the lines its title wraps onto
    body: list[RawLine] = field(default_factory=list)


def _cited_number(number: str) -> str:
    return number[:-1] + number[-1].upper()  # small caps can extract a section letter in lower case


def _sections(lines: Sequence[RawLine]) -> list[_Section]:
    """The chapters' body lines, grouped by the § heading they follow.

    A chapter opens with its title and contents, and its notes follow its last section: neither
    belongs to a section. A heading line that is not a § heading continues the title when it
    is indented under it; otherwise it is a subchapter heading."""
    sections: list[_Section] = []
    current: _Section | None = None
    in_heading = False
    scope = None
    for line in lines:
        if line.scope != scope:
            scope, current, in_heading = line.scope, None, False
        if not line.scope.startswith(CHAPTER_SCOPE) or line.kind in SKIPPED_KINDS:
            in_heading = False
            continue
        if line.kind == "heading":
            if m := SECTION_HEADING_RE.match(line.text):
                current = _Section(number=_cited_number(m.group(1)), scope=line.scope, heading=[line])
                sections.append(current)
                in_heading = True
            elif in_heading and line.x0 > current.heading[0].x0 + PARA_INDENT:
                current.heading.append(line)
            continue
        in_heading = False
        if current is not None:
            current.body.append(line)
    return sections


@dataclass
class _Last:
    """The line most recently placed (always in the current unit): a flush paragraph's first line
    may have to move."""

    line: TextLine
    node: Node
    raw: RawLine


class UnitBuilder:
    """Places the body lines of one section, or of one §101 definition, in a tree under `root`.

    The units take the root's source type, jurisdiction and appendix; `label_of` cites a unit from
    its labels (by default the root's label followed by "(a)(1)…"). The appendices' sections are
    built by it too (appendices.py)."""

    def __init__(self, root: Node, kind: str, ledger: OverrideLedger, out_of_sequence: list[str],
                 paragraphs_restart: bool = False,
                 label_of: Callable[[tuple[str, ...]], str] | None = None):
        self.root = root
        self.kind = kind
        self.ledger = ledger
        self.out_of_sequence = out_of_sequence
        self.label_of = label_of or (lambda labels: root.label + "".join(f"({label})" for label in labels))
        # In a definition, a new paragraph at the definition's own indent is the definition's, and
        # it introduces the list that follows it ("A work of visual art does not include—").
        self.paragraphs_restart = paragraphs_restart
        self.lead_in = False  # the root's tail is such a paragraph, waiting for its list
        self.nodes: dict[tuple[str, ...], Node] = {(): root}
        self.stack: dict[int, str] = {}  # the open units: level -> label
        # After flush text, the owner's last child, closed: the next label in sequence still follows it.
        self.closed: dict[int, str] = {}
        self.current = root
        self.block: list[RawLine] = []  # the lines of the unit or paragraph being read
        self.block_x: float | None = None  # the x of its first line
        self.prev: RawLine | None = None
        self.last: _Last | None = None
        self.elided = False  # an elision ("* * *") came before the line now being read
        self.quoting = False  # inside quoted text that has not closed yet

    # --- reading one line ----------------------------------------------------------------------

    def elide(self) -> None:
        """Text left out here ("* * *", in the appendices): the next line follows no line, so a label
        on it is read as a label, and the sequence it takes up again may skip ahead."""
        self.prev, self.last, self.elided, self.quoting = None, None, True, False

    def feed(self, raw: RawLine, para_start: bool) -> None:
        self._feed(raw, para_start)
        self.elided = False

    def _feed(self, raw: RawLine, para_start: bool) -> None:
        as_text = False
        if m := self._label_start(raw):
            if self._open(raw, m, para_start):
                self.prev = raw
                return
            as_text = True  # refused (and reported), or a list inside flush text
        elif LEAD_RE.match(raw.text) and self.prev is not None and raw.x0 > self.prev.x0 + PARA_INDENT:
            # Read as a wrapped cross-reference, yet indented like a unit: a wrapped reference sits
            # under the line before (so do all 86 refused in Title 17), so report it.
            self.out_of_sequence.append(f"p.{raw.page}: {raw.text}")
        if owner := self.ledger.find("text_owner", raw.page, raw.text):
            self._own(raw, para_start, owner)
        elif self.paragraphs_restart and self._restarts(raw):
            self._paragraph(raw, para_start)
        elif self.quoting or QUOTE_OPEN_RE.match(raw.text):
            self._quoted(raw, para_start)
        elif self.block_x is not None and raw.x0 < self.block_x - INDENT_STEP - PARA_INDENT:
            self._flush(raw, para_start)
        else:
            self._continue(raw, para_start, inline=not as_text)
        self.prev = raw

    def _label_start(self, raw: RawLine) -> re.Match | None:
        m = LEAD_RE.match(raw.text)
        if m is None:
            return None
        if XREF_AFTER_RE.match(raw.text[m.end(1):]):
            return None
        prev = self.prev
        if prev is None:
            return m
        if XREF_TAIL_RE.search(prev.text) and not ITEM_END_RE.search(prev.text):
            return None
        closes = END_OK_RE.search(prev.text) is not None and not LABEL_LIST_END_RE.search(prev.text)
        if closes or raw.x0 > prev.x0 + PARA_INDENT:
            return m
        first = LABEL_RE.findall(m.group(1))[0]
        view = self._view()
        level, _ = resolve(first, view, x_level(raw.x0))
        return m if is_expected(first, view, level) else None

    def _restarts(self, raw: RawLine) -> bool:
        return (
            DEFINITION_X[0] <= raw.x0 <= DEFINITION_X[1]
            and not raw.text.startswith("(")
            and self.prev is not None
            and DEFINITION_AFTER_RE.search(self.prev.text) is not None
        )

    # --- units -------------------------------------------------------------------------------

    def _view(self) -> dict[int, str]:
        """What the sequence sees: the open units and the closed sibling."""
        return {**self.closed, **self.stack}

    def _place(self, raw: RawLine, labels: list[str], first_x_level: int, override: int | None
               ) -> dict[int, str] | None:
        """The open units after these labels open, or None when the line opens nothing.

        None when a label would reopen a unit already there, or would not sit below the label
        before it on the line (both reported), and when the first label lies deeper than the
        closed sibling: after flush text, a label that cannot be the next child of the text's
        owner is a list inside that text (§110's "accreditation— (A) … (B) …"), so it stays text.
        A label no rule (and no override) placed is reported in `out_of_sequence`; after an elision the
        first label may skip ahead without being reported, as the labels it skips were left out.
        """
        stack, closed = dict(self.stack), dict(self.closed)
        unplaced = False
        level = 0
        for i, label in enumerate(labels):
            above, view = level, {**closed, **stack}
            if i == 0 and override is not None:
                level = override
            else:
                level, decided = resolve(label, view, first_x_level if i == 0 else above + 1)
                skipped = i == 0 and self.elided
                unplaced |= not decided or not (skipped or is_expected(label, view, level))
            if i == 0 and closed and level > max(closed):
                return None
            stack = {k: v for k, v in stack.items() if k < level}
            stack[level] = label
            closed = {}
            if (i > 0 and level <= above) or self._labels(stack) in self.nodes:
                self.out_of_sequence.append(f"p.{raw.page}: {raw.text}")
                return None
        if unplaced:
            self.out_of_sequence.append(f"p.{raw.page}: {raw.text}")
        return stack

    def _open(self, raw: RawLine, m: re.Match, para_start: bool) -> bool:
        leading = LABEL_RE.findall(m.group(1))
        labels, cut = list(leading), None
        if inline := INLINE_RE.match(raw.text[m.end(1):]):
            labels += LABEL_RE.findall(inline.group(1))
            cut = m.end(1) + inline.start(1)
        override = self.ledger.find("label_level", raw.page, raw.text)
        stack = self._place(raw, labels, x_level(raw.x0), int(override.value) if override else None)
        if stack is None:
            return False
        opened = self._make_units(stack, len(labels))
        if cut is None:
            self._add(opened[-1], self._text_line(raw, raw.text, para_start, raw.marks), raw)
        else:  # the heading is the unit's text; what follows its dash, its first child's
            head = self._text_line(raw, raw.text[:cut].rstrip(), para_start, [])
            self._add(opened[len(leading) - 1], head, raw, lead=True)
            self._add(opened[-1], self._text_line(raw, raw.text[cut:], False, raw.marks), raw)
        self.stack, self.closed, self.current = stack, {}, opened[-1]
        self.block, self.block_x = [raw], raw.x0
        self.quoting = False
        return True

    def _make_units(self, stack: dict[int, str], count: int) -> list[Node]:
        """Create the `count` deepest units of `stack`, shallowest first."""
        labels = self._labels(stack)
        return [self._unit(labels[:depth]) for depth in range(len(labels) - count + 1, len(labels) + 1)]

    def _unit(self, labels: tuple[str, ...]) -> Node:
        parent = next(self.nodes[labels[:k]] for k in range(len(labels) - 1, -1, -1) if labels[:k] in self.nodes)
        node = Node(
            path=self.root.path + "".join(f"-{label}" for label in labels),
            kind=self.kind,
            source_type=self.root.source_type,
            jurisdiction=self.root.jurisdiction,
            label=self.label_of(labels),
            heading="",
            scope=self.root.scope,
            appendix=self.root.appendix,
        )
        if parent.tail:
            # Text between two children: a definition's new paragraph leads the list it introduces;
            # anything else can only be the earlier child's flush text, set at the wrong level.
            if self.lead_in and parent is self.root:
                node.lead.extend(parent.tail)
            else:
                parent.children[-1].tail.extend(parent.tail)
            parent.tail.clear()
        self.lead_in = False
        parent.children.append(node)
        self.nodes[labels] = node
        return node

    # --- lines that open no unit -------------------------------------------------------------

    def _continue(self, raw: RawLine, para_start: bool, inline: bool = True) -> None:
        if self.block_x is None:
            self.block_x = raw.x0
        self.block.append(raw)
        found = INLINE_RE.match(raw.text) if inline else None
        if (
            found
            and len(self.block) <= INLINE_WINDOW
            and not any("—" in line.text for line in self.block[:-1])
        ):
            labels = LABEL_RE.findall(found.group(1))
            stack = self._place(raw, labels, max(self.stack, default=0) + 1, None)
            if stack is not None:
                cut = found.start(1)
                self._add(self.current, self._text_line(raw, raw.text[:cut].rstrip(), para_start, []), raw)
                opened = self._make_units(stack, len(labels))
                self._add(opened[-1], self._text_line(raw, raw.text[cut:], False, raw.marks), raw)
                self.stack, self.current = stack, opened[-1]
                return
        self._add(self.current, self._text_line(raw, raw.text, para_start, raw.marks), raw)

    def _quoted(self, raw: RawLine, para_start: bool) -> None:
        """Quoted text is set to the indents of the law it amends, not to this one's: it is the current
        unit's text, whatever its indent, until a line closes the quotation (or a label opens a unit),
        and a flush line after it never takes its last line along."""
        self.block.append(raw)
        self._add(self.current, self._text_line(raw, raw.text, para_start, raw.marks), raw)
        self.quoting = QUOTE_CLOSE_RE.search(raw.text) is None
        self.last = None

    def _flush(self, raw: RawLine, para_start: bool) -> None:
        """A line below the open unit's other lines: the text after a list, at the level of a unit
        above. The unit is the deepest one open at the level the line's indent belongs to; the
        line before moves with it when it is the paragraph's first line (one indent to the right,
        and running on into this one)."""
        owner_level = x_level(raw.x0) + 1
        self._close_below(owner_level)
        owner = self.nodes[self._labels(self.stack)]
        last = self.last
        if (
            last is not None
            and PARA_INDENT < last.raw.x0 - raw.x0 < INDENT_STEP + PARA_INDENT
            and not CLOSED_RE.search(last.raw.text)
        ):
            placed = last.node.tail if last.node.children else last.node.lead
            assert placed[-1] is last.line
            placed.pop()
            self._add(owner, last.line, last.raw)
        self.current = owner
        self.block, self.block_x = [raw], INDENT_ORIGIN + INDENT_STEP * owner_level
        self._add(owner, self._text_line(raw, raw.text, para_start, raw.marks), raw)

    def _own(self, raw: RawLine, para_start: bool, override: Override) -> None:
        """A `text_owner` override: the line is the text of the unit at `override.value`, which must be
        open here (the current unit or one above it), or the text would land out of order.

        The owner then reads as after flush text: the units below it close, and the paragraph's
        indent is this line's. So the lines that continue it (any non-label line not set more than
        INDENT_STEP + PARA_INDENT left of it) follow it into the owner, until a label opens a unit
        or the text drops below.
        """
        open_labels = self._labels(self.stack)
        labels = next((key for key, node in self.nodes.items() if node.path == override.value), None)
        if labels is None or open_labels[: len(labels)] != labels:
            raise ValueError(
                f"override {override.id!r} (p. {raw.page}): {override.value!r} is not a unit open at "
                f"its line in {self.root.label}"
            )
        self._close_below(sorted(self.stack)[len(labels) - 1] if labels else 0)
        self.current = self.nodes[labels]
        self.block, self.block_x = [raw], raw.x0
        self._add(self.current, self._text_line(raw, raw.text, para_start, raw.marks), raw)

    def _paragraph(self, raw: RawLine, para_start: bool) -> None:
        self.stack, self.closed, self.current = {}, {}, self.root
        self.lead_in = bool(self.root.children)
        self.block, self.block_x = [raw], raw.x0
        self._add(self.root, self._text_line(raw, raw.text, para_start, raw.marks), raw)

    # --- helpers -----------------------------------------------------------------------------

    def _close_below(self, level: int) -> None:
        """Close the open units below `level`; the first of them stays as the closed sibling."""
        below = sorted(k for k in self.stack if k > level)
        self.closed = {below[0]: self.stack[below[0]]} if below else {}
        self.stack = {k: v for k, v in self.stack.items() if k <= level}

    @staticmethod
    def _labels(stack: dict[int, str]) -> tuple[str, ...]:
        return tuple(stack[level] for level in sorted(stack))

    @staticmethod
    def _text_line(raw: RawLine, text: str, para_start: bool, marks: list[int]) -> TextLine:
        return TextLine(text=text, page=raw.page, marks=list(marks), para_start=para_start)

    def _add(self, node: Node, line: TextLine, raw: RawLine, lead: bool = False) -> None:
        (node.tail if node.children and not lead else node.lead).append(line)
        self.last = _Last(line=line, node=node, raw=raw)


def _section_node(section: _Section, hyphens: HyphenDecider) -> Node:
    first, *rest = section.heading
    title_lines = [TextLine(SECTION_HEADING_RE.match(first.text).group(2), first.page)]
    title_lines += [TextLine(line.text, line.page) for line in rest]
    title = clean_join(title_lines, hyphens)
    repealed = title == REPEALED_TITLE
    node = Node(
        path=section_path(section.number),
        kind="repealed" if repealed else "rule",
        source_type=SOURCE_TYPE,
        jurisdiction=JURISDICTION,
        label=f"{TITLE} U.S.C. §{section.number}",
        heading=title,
        scope=section.scope,
    )
    if repealed:  # the stub's text is its heading, so it has a page and a body to search
        node.lead.append(TextLine(REPEALED_TITLE, first.page, [m for line in section.heading for m in line.marks]))
    elif not section.body:
        raise ValueError(f"{node.label} (p. {first.page}) has a heading and no text: was its page left out?")
    return node


def _para_starts(section: _Section) -> list[bool]:
    """A body line starts a paragraph when it is set more than PARA_INDENT right of the line before;
    the section's first line always does (the line before it is the heading)."""
    return [True] + [line.x0 > before.x0 + PARA_INDENT for before, line in zip(section.body, section.body[1:])]


def _definition_start(line: RawLine, prev: RawLine | None) -> re.Match | None:
    if not DEFINITION_X[0] <= line.x0 <= DEFINITION_X[1] or line.text.startswith("("):
        return None
    if prev is not None and not DEFINITION_AFTER_RE.search(prev.text):
        return None
    return TERM_RE.search(line.text[:TERM_WINDOW])


def _place_definitions(section: Node, body: list[RawLine], starts: list[bool], ledger: OverrideLedger,
                       out_of_sequence: list[str]) -> None:
    """§101: the intro sentence is the section's lead; each definition is a child, with its own units."""
    builder = UnitBuilder(section, "rule", ledger, out_of_sequence)
    prev = None
    for line, para_start in zip(body, starts):
        if term_match := _definition_start(line, prev):
            term = unicodedata.normalize("NFKC", term_match.group(1)).strip()
            definition = Node(
                path=f"{section.path}-def-{slugify_term(term)}",
                kind="definition",
                source_type=SOURCE_TYPE,
                jurisdiction=JURISDICTION,
                label=f"{section.label} (“{term}”)",
                heading="",
                scope=section.scope,
                term=term,
            )
            section.children.append(definition)
            builder = UnitBuilder(definition, "definition", ledger, out_of_sequence, paragraphs_restart=True)
        builder.feed(line, para_start)
        prev = line


def set_unit_headings(section: Node, hyphens: HyphenDecider) -> None:
    """Each unit under `section` whose lead opens "(a) Heading.—" gets that heading; a childless
    "[(x) Repealed.]" unit is kind `repealed`."""
    for node in section.walk():
        if node is section:
            continue
        if node.lead and not node.children and REPEALED_UNIT_RE.match(" ".join(line.text for line in node.lead)):
            node.kind = "repealed"
        if node.lead and (m := HEADING_RE.match(clean_join(node.lead[:HEADING_LINES], hyphens))):
            node.heading = m.group(1).strip()


def section_heading_marks(lines: Sequence[RawLine]) -> dict[str, list[int]]:
    """The footnote markers on each section's heading (its § line and the lines its title wraps
    onto), by section path, in order. A heading is not a node's text line, so the note links read
    its markers here; a repealed stub also carries them on its one line."""
    return {section_path(s.number): [m for line in s.heading for m in line.marks] for s in _sections(lines)}


def title17_nodes(lines: list[RawLine], hyphens: HyphenDecider, ledger: OverrideLedger
                  ) -> tuple[list[Node], list[str]]:
    """Title 17's sections, in order, each with its subdivisions (or §101 its definitions), and
    `out_of_sequence`: each label line no rule or override placed, as `p.<page>: <line text>` (a
    label the sequence does not expect, one that would reopen a unit, and an indented label read
    as a cross-reference).

    A `label_level` override (match: the line's start; value: the level of its first label) is
    applied before the rules.
    """
    nodes: list[Node] = []
    out_of_sequence: list[str] = []
    for section in _sections(lines):
        node = _section_node(section, hyphens)
        starts = _para_starts(section)
        if section.number == DEFINITIONS_SECTION:
            _place_definitions(node, section.body, starts, ledger, out_of_sequence)
        else:
            builder = UnitBuilder(node, "rule", ledger, out_of_sequence)
            for line, para_start in zip(section.body, starts):
                builder.feed(line, para_start)
        set_unit_headings(node, hyphens)
        nodes.append(node)
    return nodes, out_of_sequence
