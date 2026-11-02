# ingest/books/circular92/layout.py
"""Circular 92 (Dec. 2025), front end part 1: PDF words -> classified lines.

Everything here reads the typesetting, none of the law: which words share a line, which
lines are running heads (dropped), where the footnote markers are (lifted out of the text
and into `marks`), what kind of line each is, and which chapter or appendix and printed
page it falls on. The tree is built on top of this (title17.py, notes.py, appendices.py).

The rules were measured on the whole book (research report, 2026-10-30) and are fixed
here as constants; a different edition gets its own front end.
"""
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

# The body is PDF 23-481. PDF 1-22 is front matter; 482-486 is a folio page, three blanks
# and the back cover, which would otherwise parse as Appendix T's notes.
BODY_PDF_PAGES = range(23, 482)
PAGE_OFFSET = 22  # printed page = PDF page - 22, on every page that prints a number

# Scope starts are edition data, not detected, so any page range parses on its own; a test
# checks that each listed page opens with its title.
SCOPE_STARTS: tuple[tuple[int, str], ...] = (
    (23, "Chapter 1"), (189, "Chapter 2"), (195, "Chapter 3"), (207, "Chapter 4"), (221, "Chapter 5"),
    (243, "Chapter 6"), (247, "Chapter 7"), (259, "Chapter 8"), (285, "Chapter 9"), (299, "Chapter 10"),
    (313, "Chapter 11"), (315, "Chapter 12"), (331, "Chapter 13"), (347, "Chapter 14"), (359, "Chapter 15"),
    (389, "Appendix A"), (393, "Appendix B"), (399, "Appendix C"), (403, "Appendix D"), (405, "Appendix E"),
    (407, "Appendix F"), (427, "Appendix G"), (431, "Appendix H"), (433, "Appendix I"), (435, "Appendix J"),
    (437, "Appendix K"), (443, "Appendix L"), (445, "Appendix M"), (447, "Appendix N"), (461, "Appendix O"),
    (467, "Appendix P"), (469, "Appendix Q"), (471, "Appendix R"), (479, "Appendix S"), (481, "Appendix T"),
)
FRONT_MATTER = "front matter"

LINE_TOP_TOLERANCE = 2.0  # a word whose top is this close to a line's top is on that line
SUPERSCRIPT_OVERLAP = 2.0  # a small marker-font word sitting higher or lower must overlap the line by more
SUPERSCRIPT_MAX_SIZE = 10.4

# Two words closer than this were one word cut where the typeface or size changes ("106" + "A" at
# 10.2 and 9.4 pt, "(" + "DMCA", "S" + "pecial" in small caps), which pdfplumber returns as two.
# Every pair that close in the book is such a cut (checked over the whole body); the narrowest real
# word space is 0.22 pt ("(d)" then "For", PDF 159), so the threshold sits between them. Boxes that
# overlap by more than a point are not the two halves of one word.
GLUE_GAP = 0.15
MIN_GLUE_GAP = -1.0

FOOTNOTE_FONT = "MinionPro-Regular"  # a digits-only word in this font is a footnote marker, at any size
RUNNING_HEAD_FONT_PREFIX = "TheMixOsF"
RUNNING_HEAD_MAX_SIZE = 8.0
HEADING_FONTS = frozenset({"TheMixOsF-Bold", "MinionC-Bold", "MinionC-BoldItalic"})
HEADING_MIN_SIZE = 10.0
TITLE_FONT = "TheMixOsF-ExtraBold"
NOTES_HEADING_FONT = "TheMixOsF-Bold"

_MARKER_RE = re.compile(r"\d{1,3}")
_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([.,;:])")
_NOTES_HEADING_RE = re.compile(r"·\s*Notes$")
_LEADER_WORD_RE = re.compile(r"[ .]+")
_LEADER_RUN_RE = re.compile(r"\.{6,}")


@dataclass
class RawLine:
    pdf_page: int
    page: int  # the printed page
    scope: str  # "Chapter 5", "Appendix G"
    x0: float  # of the first word that stayed in the text: the indent
    top: float
    text: str  # footnote markers lifted out, running heads gone, words cut at a typeface change glued
    marks: list[int]  # the lifted markers, in reading order (always empty on a note line)
    kind: str  # body, heading, title, toc, note or notes_heading
    font: tuple[str, float]  # font name (no subset prefix) and size of the first word
    fonts: list[str]  # every font name on the line, in order of first use


def scope_of(pdf_page: int) -> str:
    scope = FRONT_MATTER
    for start, name in SCOPE_STARTS:
        if pdf_page >= start:
            scope = name
    return scope


def _font(word: dict) -> str:
    return word["fontname"].split("+")[-1]


def _is_marker(word: dict) -> bool:
    return _font(word) == FOOTNOTE_FONT and _MARKER_RE.fullmatch(word["text"]) is not None


def _group(words: Sequence[dict]) -> list[dict]:
    """Words -> lines: `{"top", "bottom", "words"}`, top to bottom."""
    groups: list[dict] = []
    for word in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        for group in groups:
            if abs(group["top"] - word["top"]) <= LINE_TOP_TOLERANCE or _overlaps(group, word):
                group["words"].append(word)
                group["bottom"] = max(group["bottom"], word["bottom"])
                break
        else:
            groups.append({"top": word["top"], "bottom": word["bottom"], "words": [word]})
    groups.sort(key=lambda g: g["top"])
    return groups


def _overlaps(group: dict, word: dict) -> bool:
    """A small marker-font word (the letter-spaced "U .S .C .") sits a little off the line yet belongs to it."""
    return (
        _font(word) == FOOTNOTE_FONT
        and word["size"] < SUPERSCRIPT_MAX_SIZE
        and word["bottom"] > group["top"] + SUPERSCRIPT_OVERLAP
        and word["top"] < group["bottom"] - SUPERSCRIPT_OVERLAP
    )


def _join(words: Sequence[dict]) -> str:
    out = words[0]["text"]
    for before, word in zip(words, words[1:]):
        gap = word["x0"] - before["x1"]
        out += word["text"] if MIN_GLUE_GAP < gap < GLUE_GAP else " " + word["text"]
    return _SPACE_BEFORE_PUNCTUATION.sub(r"\1", out)


def _is_notes_heading(text: str, first_font: str) -> bool:
    return _NOTES_HEADING_RE.search(text) is not None and first_font == NOTES_HEADING_FONT


def _kind(text: str, words: Sequence[dict], first_font: str, first_size: float, in_notes: bool) -> str:
    if in_notes:
        return "note"
    if first_font == TITLE_FONT:
        return "title"
    if first_font in HEADING_FONTS and first_size >= HEADING_MIN_SIZE:
        return "heading"
    leaders = sum(_LEADER_WORD_RE.fullmatch(w["text"]) is not None for w in words)
    if leaders >= 3 or _LEADER_RUN_RE.search(text) or text.startswith("section page"):
        return "toc"
    return "body"


def lines_from_words(words: Sequence[dict], pdf_page: int, notes_scopes: set[str]) -> list[RawLine]:
    """The lines of one page, from the words pdfplumber returned for it.

    `notes_scopes` holds the scopes whose "· Notes" heading has been seen; pages must come in
    reading order, and a heading found here is added to it. Every line after the heading in
    its scope is a note, and a note line keeps its digits: only a body line has markers.
    """
    scope = scope_of(pdf_page)
    lines: list[RawLine] = []
    for group in _group(words):
        ordered = sorted(group["words"], key=lambda w: w["x0"])
        in_notes = scope in notes_scopes
        if in_notes:
            kept, marks = ordered, []
        else:
            kept = [w for w in ordered if not _is_marker(w)]
            marks = [int(w["text"]) for w in ordered if _is_marker(w)]
        if not kept:
            continue
        first = kept[0]
        first_font, first_size = _font(first), round(first["size"], 1)
        if first_font.startswith(RUNNING_HEAD_FONT_PREFIX) and first_size < RUNNING_HEAD_MAX_SIZE:
            continue
        text = _join(kept)
        if _is_notes_heading(text, first_font):
            notes_scopes.add(scope)
            kind = "notes_heading"
        else:
            kind = _kind(text, kept, first_font, first_size, in_notes)
        fonts = list(dict.fromkeys(_font(w) for w in kept))
        lines.append(
            RawLine(
                pdf_page=pdf_page,
                page=pdf_page - PAGE_OFFSET,
                scope=scope,
                x0=first["x0"],
                top=group["top"],
                text=text,
                marks=marks,
                kind=kind,
                font=(first_font, first_size),
                fonts=fonts,
            )
        )
    return lines


def extract_lines(pdf_path: Path, pdf_pages: Iterable[int] = BODY_PDF_PAGES) -> list[RawLine]:
    """The classified lines of the given PDF pages (1-based, in reading order).

    What a scope's notes are is learnt from the pages given: a page extracted without the page that
    holds its scope's "· Notes" heading reads as body, so extract a notes page together with it.
    """
    notes_scopes: set[str] = set()
    lines: list[RawLine] = []
    with pdfplumber.open(pdf_path) as pdf:
        for number in pdf_pages:
            if not 1 <= number <= len(pdf.pages):
                raise ValueError(f"{pdf_path}: no PDF page {number} (it has {len(pdf.pages)})")
            page = pdf.pages[number - 1]
            words = page.extract_words(
                extra_attrs=["fontname", "size"], keep_blank_chars=False, use_text_flow=True, x_tolerance=3
            )
            lines.extend(lines_from_words(words, number, notes_scopes))
            page.flush_cache()
    return lines
