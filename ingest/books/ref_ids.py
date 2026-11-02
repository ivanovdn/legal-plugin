# ingest/books/ref_ids.py
"""The `ref_id` grammar: how a node of a reference book is named, found and looked up.

A `ref_id` is `<book>-<edition>:<path>` (`circ92-2025-12:usc17-s122-f-1`). It names a node
of the parsed tree, never a chunk, so it does not move when the chunk budget does. Its
characters are only `[A-Za-z0-9_:-]`, so it is one token in prose and cannot absorb a
trailing period. Paths:

    usc<title>-s<section, lowercased>[-<label>...]     usc17-s122-a-4-B   (labels keep their case)
    usc17-s101-def-<slug>                              usc17-s101-def-joint-work
    pl<congress>-<number>[-s<section>[-<label>...]]    pl105-304-s101
    trips-art<n>[-<paragraph>...]                      trips-art38-1
    ch<n>-n<m>                                         ch1-n3             (a chapter's note)
    app<Letter>-n<m>                                   appB-n2            (an appendix's note)
"""
import re
import uuid
from collections.abc import Callable

from ingest.books.registry import BookEntry

_BOOK = r"[a-z][a-z0-9]*"
_EDITION = r"\d{4}-\d{2}"
_PATH = r"[A-Za-z0-9_-]*[A-Za-z0-9]"
REF_ID_PATTERN = rf"{_BOOK}-{_EDITION}:{_PATH}"

# An id counts only when nothing that could be part of one stands to its left.
_NOT_AFTER_ID_CHAR = r"(?<![A-Za-z0-9_:-])"
REF_ID_RE = re.compile(_NOT_AFTER_ID_CHAR + REF_ID_PATTERN, re.ASCII)
REF_MARKER_RE = re.compile(rf"ref_id:\s*({REF_ID_PATTERN})", re.ASCII)

# Point ids are uuid5(POINT_NAMESPACE, chunk key): the same chunk is the same point on a re-ingest.
POINT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "legal-plugin/reference-books")

_REF_ID_FULL_RE = re.compile(REF_ID_PATTERN, re.ASCII)
_PARTS_RE = re.compile(rf"({_BOOK})-({_EDITION}):({_PATH})", re.ASCII)

# How a model writes a cited id: bare, in backticks, after a `ref_id:` marker (with or without
# a space), or both. Each alternative has exactly one group, so `lastgroup` names the id.
_MARKER = r"(?<![A-Za-z0-9_])ref_id:"
_CITED_RE = re.compile(
    rf"`(?:{_MARKER}\s*)?(?P<wrapped>{REF_ID_PATTERN})`"  # `id`  `ref_id: id`
    rf"|{_MARKER}\s*`(?P<marker_wrapped>{REF_ID_PATTERN})`"  # ref_id: `id`
    rf"|{_MARKER}\s*(?P<marker>{REF_ID_PATTERN})"  # ref_id: id   ref_id:id
    rf"|{_NOT_AFTER_ID_CHAR}(?P<bare>{REF_ID_PATTERN})",  # id
    re.ASCII,
)


def make_ref_id(book: BookEntry, path: str) -> str:
    ref_id = f"{book.ref_prefix}:{path}"
    if not _REF_ID_FULL_RE.fullmatch(ref_id):
        raise ValueError(f"{ref_id!r} is not a ref_id: the path {path!r} is off the grammar")
    return ref_id


def split_ref_id(ref_id: str) -> tuple[str, str, str]:
    """(book id, edition id, path). ValueError when `ref_id` is not exactly one ref_id."""
    parts = _PARTS_RE.fullmatch(ref_id)
    if parts is None:
        raise ValueError(f"{ref_id!r} is not a ref_id (<book>-<yyyy>-<mm>:<path>)")
    return parts.group(1), parts.group(2), parts.group(3)


def chunk_key(ref_id: str, suffix: str = "") -> str:
    """The key of one chunk of a node: the node's `ref_id`, plus `~lead`, `~tail` or `~pN` when it was split."""
    return f"{ref_id}{suffix}"


def point_id(key: str) -> str:
    return str(uuid.uuid5(POINT_NAMESPACE, key))


def replace_cited_ref_ids(text: str, replace: Callable[[str], str]) -> str:
    """Replace each cited id, with its `ref_id:` marker and any backticks that wrap it, by `replace(id)`."""
    return _CITED_RE.sub(lambda m: replace(m.group(m.lastgroup)), text)


def find_cited_ref_ids(text: str) -> list[str]:
    """Every id the text cites, once each, in the order first seen. Reads the same forms
    `replace_cited_ref_ids` replaces, so a cited id cannot escape being checked and rendered."""
    return list(dict.fromkeys(m.group(m.lastgroup) for m in _CITED_RE.finditer(text)))


def is_under(path: str, ancestor: str) -> bool:
    """True when `path` is `ancestor` or lies below it. Whole labels only: s1010 is not under s101."""
    return path == ancestor or path.startswith(f"{ancestor}-")


def slugify_term(term: str) -> str:
    """The path form of a defined term: `joint work` -> `joint-work`."""
    plain = re.sub(r"['‘’]", "", term.lower())  # an apostrophe joins its word: author's -> authors
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")


# --- parse_reference: what a person or a model types -> a ref_id or a path -------------------

_SEC = r"(?:§§?|sec(?:tion|\.)?)"
_LABELS = r"(?:\s*\(\s*[A-Za-z0-9]+\s*\))*"
_QUOTE = "[\"'‘’“”]"
_FLAGS = re.I | re.ASCII

_PATH_RE = re.compile(
    r"(?:usc\d+-s\d+[a-z]?|pl\d+-\d+|trips-art\d+|ch\d+-n\d+|app[A-Z]-n\d+)(?:-[A-Za-z0-9]+)*", re.ASCII
)
_USC_RE = re.compile(
    rf"(?:(?P<title>\d{{1,2}})\s*U\.?\s?S\.?\s?C\.?\s*{_SEC}?|{_SEC})\s*"
    rf"(?P<section>\d+[A-Za-z]?)(?P<labels>{_LABELS})"
    rf"(?:\s*\(\s*{_QUOTE}(?P<term_paren>.+?){_QUOTE}\s*\)|\s*{_QUOTE}(?P<term>.+?){_QUOTE})?",
    _FLAGS,
)
_PL_RE = re.compile(
    r"Pub(?:lic|\.)?\s*L(?:aw|\.)?\s*(?:No\.?\s*)?(?P<congress>\d+)\s*[-–]\s*(?P<number>\d+)"
    rf"(?:\s*,?\s*{_SEC}\s*(?P<section>\d+[A-Za-z]?)(?P<labels>{_LABELS}))?",
    _FLAGS,
)
_TRIPS_RE = re.compile(
    rf"TRIPS?(?:\s+Agreement)?\s*,?\s*art(?:icle|\.)?\s*(?P<article>\d+)(?P<labels>{_LABELS})", _FLAGS
)
_CH_NOTE_RE = re.compile(r"ch(?:apter|\.)?\s*(?P<chapter>\d+)\s*,?\s*n(?:ote|\.)?\s*(?P<note>\d+)", _FLAGS)
_APP_NOTE_RE = re.compile(
    r"app(?:endix|\.)?\s*(?P<letter>[A-Za-z])\s*,?\s*n(?:ote|\.)?\s*(?P<note>\d+)", _FLAGS
)
_LABEL_RE = re.compile(r"\(\s*([A-Za-z0-9]+)\s*\)")


def _labels(group: str | None) -> str:
    return "".join(f"-{label}" for label in _LABEL_RE.findall(group or ""))


def _clean_reference(reference: str) -> str:
    text = " ".join(reference.split()).strip("`").strip()
    text = re.sub(r"^ref_id:\s*", "", text).strip("`").strip()
    return text.rstrip(".,;")


def parse_reference(reference: str) -> str | None:
    """A full `ref_id`, a bare path, or a citation in words -> a `ref_id` or a path; None when it names nothing.

    Words: `§107`, `section 107`, `17 U.S.C. §122(f)(1)`, `18 U.S.C. 2319`, `section 101 "joint work"`,
    `Pub. L. No. 105-304, §101`, `TRIPs art. 38(1)`, `ch. 1 n.3`, `app. B n.2`. The title defaults to 17.
    A bare number is not a reference: it needs a section sign, `sec.`, `section` or `U.S.C.`.
    """
    text = _clean_reference(reference)
    if _REF_ID_FULL_RE.fullmatch(text) or _PATH_RE.fullmatch(text):
        return text

    if m := _PL_RE.fullmatch(text):
        path = f"pl{m['congress']}-{m['number']}"
        if m["section"]:
            path += f"-s{m['section'].lower()}{_labels(m['labels'])}"
        return path
    if m := _TRIPS_RE.fullmatch(text):
        return f"trips-art{m['article']}{_labels(m['labels'])}"
    if m := _CH_NOTE_RE.fullmatch(text):
        return f"ch{m['chapter']}-n{m['note']}"
    if m := _APP_NOTE_RE.fullmatch(text):
        return f"app{m['letter'].upper()}-n{m['note']}"
    if m := _USC_RE.fullmatch(text):
        path = f"usc{m['title'] or 17}-s{m['section'].lower()}"
        term = m["term"] or m["term_paren"]
        if term is None:
            return path + _labels(m["labels"])
        slug = slugify_term(term)
        if m["labels"] or not slug:
            return None
        return f"{path}-def-{slug}"
    return None
