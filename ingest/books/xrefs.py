# ingest/books/xrefs.py
"""Cross-references: the law a passage names, as paths (ruling 11), read from where the passage sits.

A reference is a kind word and a list ("sections 106 and 106A", "paragraph (2)", "clauses (i)
through (iii)"), a Public Law ("Pub. L. No. 110-403") or a U.S.C. citation ("15 U.S.C. 1127"). A
section is in the passage's own code unless the words after it say otherwise (an aside in
parentheses may stand between): another title ("of title 18"), a Public Law ("of Public Law
105-304"), this title, this Act, such title, a named Act (then only a U.S.C. citation in parentheses
after it gives a path). A subdivision with no section is relative: it sits under the passage's own
units above its level ("clause (2) of this subsection", read in §504(c)(1), is §504(c)(2)), or under
the parent it names ("paragraph (1) of subsection (a)", "subparagraph (A) of the definition of “work
of visual art” in section 101"). "Such" names the unit of that kind named before it in the passage
("subparagraph (M) of such subsection", "such section 5").

A reference that names law yet gives no path (a named Act, the Code of Federal Regulations, "such
Act", a "such" with nothing named before it) is returned as its raw text, `unparsed` (ruling R1). A
relative reference with nowhere to sit (in a note, which has no section) names nothing and is
dropped. Ranges give both ends: what lies between is not known from the text. An `xref_target`
override (match: the raw reference text, on its printed page) names the targets the rules cannot, or
marks the reference unparsed.

Ported from the prototype's xref.py (research vault, 2026-10-30), with its two known bugs fixed: a
chapter between a section and its title ("section 2323, chapter 113 of title 18" is 18 U.S.C.),
and an Act whose name is too long for the prototype's look-ahead to reach its U.S.C. citation
(§504(c)'s "section 45 of the Act entitled “…” … (15 U.S.C. 1127)"). New rules, each found on the
book: labels may be set apart by a space ("section 114(d) (1)"); a subdivision kind may open a
sentence ("Paragraph (1) shall not apply") while "section" may not, as the notes' "Section 2323 is
included in Appendix N" names another title's section; a subdivision kind before a section number
cites that unit ("amended subparagraph 803(b)(6)(A)"); a passage of an Act or of another title
names its own sections, not Title 17's; "such"; a definition's units; an Act named "of the first
section of"; and the asides, "respectively" and "and chapter 8" that may stand before a qualifier.
"""
import re
from collections.abc import Iterator
from dataclasses import dataclass

from ingest.books.labels import levels_of
from ingest.books.overrides import XREF_UNPARSED, OverrideLedger, xref_target_paths
from ingest.books.ref_ids import slugify_term


@dataclass(frozen=True)
class XrefContext:
    """Where a passage sits, which is what its relative references are relative to.

    `title`: the U.S.C. title whose sections a bare "section N" names (unless `act` is given).
    `section`: the section the passage is in, as in its path ("106a"); None for a note.
    `path_labels`, `label_levels`: the units the passage sits in below the section (or below the §101
    definition, `term`), with the level of each (1 subsection, 2 paragraph … 7 subitem).
    `act`: an uncodified Act's path prefix ("pl110-403"), whose own sections a bare "section N" names.
    `pages`: the printed pages the passage is on, where an `xref_target` override is looked up.
    """

    title: int
    section: str | None
    path_labels: tuple[str, ...]
    label_levels: tuple[int, ...]
    term: str | None = None
    act: str | None = None
    pages: tuple[int, ...] = ()


_LAB = r"\([A-Za-z0-9]{1,5}\)"
# One item of a list: a number with its labels, or labels alone. It ends where its word does (never
# inside "1001" or before "(c)"), and a number before "percent" is a share, not a section ("section
# 114(f), 50 percent of the royalties").
_ONE = rf"(?:\d+(?:\.\d+)?[A-Za-z]?(?:\s?{_LAB})*|{_LAB}(?:\s?{_LAB})*)(?![\dA-Za-z(])(?!\s+percent\b)"
_LIST = rf"{_ONE}(?:(?:,\s*(?:and\s+|or\s+)?|\s+(?:and|or|through)\s+){_ONE})*"
# "section" in lower case only: a capitalized "Section N" opens a sentence in the notes, about
# another title's or another Act's section more often than not.
_KIND = r"sections?|[Ss]ub(?:sections?|paragraphs?|clauses?|items?)|[Pp]aragraphs?|[Cc]lauses?|[Ii]tems?"
REF_RE = re.compile(rf"\b({_KIND})\s+({_LIST})")
DEPTH = {"subsection": 1, "paragraph": 2, "subparagraph": 3, "clause": 4, "subclause": 5, "item": 6, "subitem": 7}
_KIND_WORD = "|".join(["section", *DEPTH])  # a parent's kind, as a word

_LABEL_RE = re.compile(r"\(([A-Za-z0-9]{1,5})\)")
_SECTION_ITEM_RE = re.compile(r"(\d+(?:\.\d+)?[A-Za-z]?)")
_SECTION_NUMBER_RE = re.compile(r"\d+[A-Za-z]?")  # what a path can hold: no CFR "76.51"
_ITEM_SPLIT_RE = re.compile(r",\s*(?:and\s+|or\s+)?|\s+(?:and|or)\s+")

# What may follow a section list and says whose sections they are. Each is matched at the list's end.
_CFR_RE = re.compile(r"\s*,?\s*(?:of|in)\s+title\s+(\d+),\s*Code of Federal Regulations")
_OF_PUBLIC_LAW_RE = re.compile(r"\s*,?\s*of\s+(?:Public\s+Law|Pub\.\s*L\.)\s*(?:No\.\s*)?(\d+)\s*[-–]\s*(\d+)")
# "of title 18", "in title 18, United States Code", and a chapter that may stand between: ", chapter 113
# of title 18" (ch.5 n.8; the prototype missed it and read 17 U.S.C.), ", and chapter 8 of title 17".
_OF_TITLE_RE = re.compile(
    r"\s*,?\s*(?:(?:and\s+|of\s+)?chapter\s+\d+[A-Za-z]?\s*,?\s*)?(?:of|in)\s+[Tt]itle\s+(\d+)\b"
    r"(?:,\s*United States Code)?"
)
_OF_THIS_TITLE_RE = re.compile(r"\s*,?\s*of\s+this\s+title\b")
_OF_THIS_ACT_RE = re.compile(r"\s*,?\s*of\s+this\s+Act\b")
_OF_SUCH_TITLE_RE = re.compile(r"\s*,?\s*of\s+such\s+title\b")
_TITLE_NAMED_RE = re.compile(r"\b[Tt]itle\s+(\d+)\b")
# A named Act: capitalized words, with the small words a title holds ("Individuals with Disabilities
# Education Act"), ending in a word that makes it law ("the Record Rental Amendment of 1984" too); a
# resolution of either House; TRIPs.
_OF_ACT_RE = re.compile(
    r"\s*,?\s*of\s+(?:(?:that|such|the)\s+Act\b|(?:the\s+)?[A-Z][A-Za-z’'\-]+"
    r"(?:\s+(?:[A-Z][A-Za-z’'\-]+|of|and|for|the|on|in|to|with|\d{4}))*"
    r"\s+(?:Act|Amendments?|Agreement|Convention|Code|Treaty)(?:\s+of\s+\d{4})?"
    r"|(?:House|Senate)(?:\s+(?:Concurrent|Joint))?\s+Resolution\s+\d+|TRIPs\b)"
)
# An aside between a section and the words that say whose it is: "section 506(a) (relating to criminal
# offenses) of title 17". Not a label: "(a)" is part of the list.
_ASIDE_RE = re.compile(r"\s*\((?![A-Za-z0-9]{1,5}\))[^()]*\)")
# A named Act's U.S.C. citation, closing a parenthetical after its name: "(26 U.S.C. 501)", "(commonly
# referred to as the “Trademark Act of 1946”; 15 U.S.C. 1127)". Looked for up to the next reference or
# the sentence's end, so a long name does not hide it (§504(c); the prototype looked 60 characters on).
_USC_CITE = r"(\d{1,2})\s+U\.S\.C\.\s+§*\s*(\d+[A-Za-z]?)((?:\([A-Za-z0-9]+\))*)"
_PAREN_USC_RE = re.compile(rf"\([^()]*?\b{_USC_CITE}\s*\)")
_SENTENCE_END_RE = re.compile(r"(?<=[a-z0-9)”])\.\s+(?=[A-Z])")
_ACT_WINDOW = 400
_OF_PARENT_RE = re.compile(rf"\s*(?:,\s*respectively,)?\s*of\s+({_KIND_WORD})\s+({_ONE})")
_OF_SUCH_PARENT_RE = re.compile(rf"\s*,?\s*of\s+such\s+({_KIND_WORD})\b")
# A unit of a defined term: "subparagraph (A) or (B) of the definition of “work of visual art” in section
# 101" (§106A(c)(3)). Only §101's definitions have paths of their own; §104A(h) defines terms as its
# paragraphs, so without "in section 101" the unit's place is not known.
_OF_DEFINITION_RE = re.compile(
    r"\s*of\s+the\s+definitions?\s+of\s+(?:“|‘‘|\")(?P<term>[^”’\"]+)(?:”|’’|\")"
    r"(?P<where>\s*,?\s*(?:contained\s+)?in\s+section\s+101\b)?"
)
_OF_FIRST_SECTION_RE = re.compile(r"\s*of\s+the\s+first\s+section\b")  # section 1, of what follows
_SUCH_BEFORE_RE = re.compile(r"\bsuch\s+$")

_PUBLIC_LAW_RE = re.compile(r"\b(?:Pub(?:lic|\.)\s*L(?:aw|\.)\s*(?:No\.\s*)?)(\d{2,3})\s*[-–]\s*(\d{1,4})\b")
_INLINE_USC_RE = re.compile(rf"\b{_USC_CITE}")

_UNPARSED = None  # a code that is known to be one no path names

Labelled = list[tuple[str, int]]
End = tuple[str | None, Labelled]  # (section number or None, [(label, level)…])
Found = tuple[str, list[str], bool]  # (raw text, paths, unparsed?)


@dataclass(frozen=True)
class _Section:
    """The section a passage named last: what "such section" names."""

    number: str
    code: str | None  # a path prefix; None when the section gives no path
    whole: str | None  # the codified path that stands for it, for a named Act's section
    path: str | None  # the unit as cited, with its labels


def label_level(label: str, above: int) -> int:
    """The level of `label` placed under a unit at level `above` (0: under the section): the shallowest
    level below `above` whose sequence holds it. Under (A), (i) is a clause; under nothing, a subsection."""
    deeper = [level for level in levels_of(label) if level > above]
    return min(deeper) if deeper else above + 1


def _first_level(label: str, hint: int) -> int:
    """The level of the first label of a reference, whose kind word suggests `hint`: "clause (i)" is a
    clause, "subsection (i)" a subsection, "clause (2)" (a slip for paragraph) a paragraph all the same."""
    levels = levels_of(label)
    if not levels or hint in levels:
        return hint
    deeper = [level for level in levels if level >= hint]
    return min(deeper) if deeper else max(levels)


def _items(listed: str) -> list[str | tuple[str, str]]:
    """'(1), (2), and (4) through (6)' -> ['(1)', '(2)', ('(4)', '(6)')]."""
    items: list[str | tuple[str, str]] = []
    for part in _ITEM_SPLIT_RE.split(listed):
        part = part.strip()
        if not part:
            continue
        if " through " in part:
            first, last = part.split(" through ", 1)
            items.append((first.strip(), last.strip()))
        else:
            items.append(part)
    return items


def _expand(listed: str, hint: int, section_kind: bool) -> Iterator[End | None]:
    """Each end of each item; None for one that names nothing ("paragraph 2": no label; "sections (3)":
    no number). An item continues the one before it: in "(1)(A), (B)", (B) is (1)(B), and in "section
    111(d)(1) through (3)", (3) is 111(d)(3).

    A subdivision kind before a section number cites the unit whole, as the notes do ("amended
    subparagraph 803(b)(6)(A)"): it then has a number, as a section does."""
    number: str | None = None
    prev: Labelled = []
    for item in _items(listed):
        for end in item if isinstance(item, tuple) else (item,):
            own = None
            if (m := _SECTION_ITEM_RE.match(end)) and (section_kind or _LABEL_RE.search(end, m.end())):
                own, end = m.group(1), end[m.end():]
            labels = _LABEL_RE.findall(end)
            if own is not None:
                number, prev = own, []
            if section_kind and number is None or not section_kind and not labels:
                yield None
                continue
            current: Labelled = []
            for i, label in enumerate(labels):
                if i == 0:
                    level = _first_level(label, 1 if section_kind or own is not None else hint)
                    current = [(lab, lv) for lab, lv in prev if lv < level] if own is None else []
                else:
                    level = label_level(label, current[-1][1])
                current.append((label, level))
            prev = current or prev
            yield number, current


def _join(base: str, labels: list[str]) -> str:
    return base + "".join(f"-{label}" for label in labels)


def _names(labels: Labelled) -> list[str]:
    return [label for label, _ in labels]


def _own_code(ctx: XrefContext) -> str:
    return ctx.act or f"usc{ctx.title}"


def _act_codified(text: str, start: int) -> str | None:
    """A named Act's section codified in a U.S.C. citation closing a parenthetical after it, before the
    next reference or the sentence's end."""
    stop = min(start + _ACT_WINDOW, len(text))
    if (after := REF_RE.search(text, start, stop)) is not None:
        stop = after.start()
    if (end := _SENTENCE_END_RE.search(text, start, stop)) is not None:
        stop = end.start()
    if (m := _PAREN_USC_RE.search(text, start, stop)) is None:
        return None
    return _join(f"usc{m.group(1)}-s{m.group(2).lower()}", _LABEL_RE.findall(m.group(3)))


def _qualifier(text: str, at: int, ctx: XrefContext) -> tuple[str | None, int, str | None] | None:
    """The words at `at` that say whose sections a list names, as (code, their end, a codified path that
    stands for the sections); None when there are none."""
    if m := _CFR_RE.match(text, at):
        return _UNPARSED, m.end(), None
    if m := _OF_PUBLIC_LAW_RE.match(text, at):
        return f"pl{m.group(1)}-{m.group(2)}", m.end(), None
    if m := _OF_TITLE_RE.match(text, at):
        return f"usc{m.group(1)}", m.end(), None
    if m := _OF_THIS_TITLE_RE.match(text, at):
        return _own_code(ctx), m.end(), None
    if m := _OF_THIS_ACT_RE.match(text, at):
        return ctx.act or _UNPARSED, m.end(), None
    if m := _OF_SUCH_TITLE_RE.match(text, at):
        named = _TITLE_NAMED_RE.findall(text, 0, at)
        return (f"usc{named[-1]}" if named else _UNPARSED), m.end(), None
    if m := _OF_ACT_RE.match(text, at):
        return _UNPARSED, m.end(), _act_codified(text, m.end())
    return None


def _section_code(text: str, at: int, ctx: XrefContext) -> tuple[str | None, int, str | None]:
    """Whose sections a list ending at `at` names: (code, end of the words that said so, a codified path
    that stands for them all). The code is a path prefix ("usc18", "pl105-304") or _UNPARSED."""
    if found := _qualifier(text, at, ctx):
        return found
    if (aside := _ASIDE_RE.match(text, at)) and (found := _qualifier(text, aside.end(), ctx)):
        return found
    return _own_code(ctx), at, None


def _relative_root(ctx: XrefContext) -> str | None:
    if ctx.section is None:
        return None
    if ctx.act:
        return f"{ctx.act}-s{ctx.section.lower()}"
    root = f"usc{ctx.title}-s{ctx.section.lower()}"
    return f"{root}-def-{slugify_term(ctx.term)}" if ctx.term else root


class _Reader:
    """Reads one passage's references in order, remembering what each "such" can name."""

    def __init__(self, text: str, ctx: XrefContext):
        self.text = text
        self.ctx = ctx
        self.section: _Section | None = None  # the section named last
        self.units: dict[str, str] = {}  # kind -> the unit of that kind named last

    def read(self, m: re.Match) -> Found:
        kind = m.group(1).lower().rstrip("s")
        found = self._sections(m) if kind == "section" else self._subdivisions(m, kind)
        if kind != "section" and found[1]:
            self.units[kind] = found[1][-1]
        return found

    def _numbered(self, m: re.Match, ends: list[End | None]) -> tuple[Found, str | None, str | None]:
        """A list whose ends carry section numbers: what it names, with its code and codified path."""
        code, end, whole = _section_code(self.text, m.end(), self.ctx)
        raw = self.text[m.start():end].strip()
        if whole is not None:
            return (raw, [whole], False), code, whole
        if code is _UNPARSED or any(e is None or not _SECTION_NUMBER_RE.fullmatch(e[0]) for e in ends):
            return (raw, [], True), _UNPARSED, None
        return (raw, [_join(f"{code}-s{n.lower()}", _names(labels)) for n, labels in ends], False), code, None

    def _sections(self, m: re.Match) -> Found:
        ends = list(_expand(m.group(2), 1, section_kind=True))
        number = ends[0][0] if ends and ends[0] is not None else None
        if such := _SUCH_BEFORE_RE.search(self.text, max(0, m.start() - 12), m.start()):
            raw = self.text[such.start():m.end()]
            prior = self.section
            if prior is None or prior.number != number or (prior.code is None and prior.whole is None):
                return raw, [], True
            if prior.whole is not None:
                return raw, [prior.whole], False
            return raw, [_join(f"{prior.code}-s{n.lower()}", _names(labels)) for n, labels in ends], False
        found, code, whole = self._numbered(m, ends)
        if number is not None:
            self.section = _Section(number, code, whole, found[1][-1] if found[1] else None)
        return found

    def _subdivisions(self, m: re.Match, kind: str) -> Found:
        """Relative to the passage's own units, or to the parent it names (or "such" a parent); with a
        section number, that section's unit."""
        ends = [e for e in _expand(m.group(2), DEPTH[kind], section_kind=False) if e is not None]
        if numbered := [e for e in ends if e[0] is not None]:
            return self._numbered(m, numbered)[0]
        if definition := _OF_DEFINITION_RE.match(self.text, m.end()):
            where = definition.group("where")
            raw = self.text[m.start():definition.start("where") if where else definition.end()].strip()
            if not where:
                return raw, [], True
            base = f"usc17-s101-def-{slugify_term(definition.group('term'))}"
            return raw, [_join(base, _names(labels)) for _, labels in ends], False
        if first := _OF_FIRST_SECTION_RE.match(self.text, m.end()):
            code, end, whole = _qualifier(self.text, first.end(), self.ctx) or (_UNPARSED, first.end(), None)
            raw = self.text[m.start():end].strip()
            if whole is not None:  # a named Act's first section, codified: "(15 U.S.C. 12(a))"
                return raw, [whole], False
            if code is _UNPARSED:
                return raw, [], True
            return raw, [_join(f"{code}-s1", _names(labels)) for _, labels in ends], False
        if such := _OF_SUCH_PARENT_RE.match(self.text, m.end()):
            raw = self.text[m.start():such.end()].strip()
            named = self.section.path if such.group(1) == "section" and self.section else self.units.get(such.group(1))
            if named is None:
                return raw, [], True
            return raw, [_join(named, _names(labels)) for _, labels in ends], False
        parent = _OF_PARENT_RE.match(self.text, m.end())
        raw = self.text[m.start():parent.end() if parent else m.end()].strip()
        if parent and parent.group(1) == "section":
            number_m = _SECTION_ITEM_RE.match(parent.group(2))
            code, _, whole = _section_code(self.text, parent.end(), self.ctx)
            if number_m is None or code is _UNPARSED or whole is not None:
                return raw, [], False  # the parent section's own reference reports it
            base = _join(f"{code}-s{number_m.group(1).lower()}", _LABEL_RE.findall(parent.group(2)))
            return raw, [_join(base, _names(labels)) for _, labels in ends], False
        root = _relative_root(self.ctx)
        if root is None:
            return raw, [], False  # a note: no units for it to sit under
        ctx = self.ctx
        paths = []
        for _, labels in ends:
            if parent:
                above = DEPTH[parent.group(1)]
                anchor = [lab for lab, lv in zip(ctx.path_labels, ctx.label_levels) if lv < above]
                anchor += _LABEL_RE.findall(parent.group(2))
            else:
                top = labels[0][1]
                anchor = [lab for lab, lv in zip(ctx.path_labels, ctx.label_levels) if lv < top]
            paths.append(_join(root, anchor + _names(labels)))
        return raw, paths, False


def _override(raw: str, ctx: XrefContext, ledger: OverrideLedger | None) -> str | None:
    if ledger is None:
        return None
    for page in ctx.pages:
        if found := ledger.find("xref_target", page, raw):
            return found.value
    return None


def find_xrefs(text: str, ctx: XrefContext, ledger: OverrideLedger | None = None) -> tuple[list[str], list[str]]:
    """(target paths, raw texts of references that give no path), each in the order of the text, once each."""
    reader = _Reader(text, ctx)
    found: list[tuple[int, Found]] = [(m.start(), reader.read(m)) for m in REF_RE.finditer(text)]
    for m in _INLINE_USC_RE.finditer(text):
        path = _join(f"usc{m.group(1)}-s{m.group(2).lower()}", _LABEL_RE.findall(m.group(3)))
        found.append((m.start(), (m.group(0), [path], False)))
    for m in _PUBLIC_LAW_RE.finditer(text):
        found.append((m.start(), (m.group(0), [f"pl{m.group(1)}-{m.group(2)}"], False)))

    paths: dict[str, None] = {}
    unparsed: dict[str, None] = {}
    for _, (raw, targets, is_unparsed) in sorted(found, key=lambda f: f[0]):
        if (value := _override(raw, ctx, ledger)) is not None:  # one path, several, or "unparsed" (R12)
            if value == XREF_UNPARSED:
                unparsed[raw] = None
            paths.update(dict.fromkeys(xref_target_paths(value)))
        elif is_unparsed:
            unparsed[raw] = None
        else:
            paths.update(dict.fromkeys(targets))
    return list(paths), list(unparsed)
