# ingest/books/circular92/text.py
"""Circular 92 text cleaning: from lines to running text.

Line-end hyphens (join or keep?), number breaks ("105-" / "304"), small-caps acronyms with
stray spaces, and the character-level clean-up. Joining is where the decision is made; what a
line looks like on the page is layout.py's business.
"""
import re
import unicodedata
from collections.abc import Sequence

from ingest.books.circular92.layout import RawLine
from ingest.books.models import TextLine
from ingest.books.overrides import OverrideLedger

SOFT_HYPHEN = "\u00ad"

_USC_RE = re.compile(r"U\s?\.S\s?\.C\s?\.")
# A parenthetical that is one token with a stray space inside: "( DMCA )", "512( g )", "(h )".
_PAREN_TOKEN_RE = re.compile(r"\((?:\s+([^\s()]+)\s*|([^\s()]+)\s+)\)")
_SPACE_BEFORE_PUNCTUATION_RE = re.compile(r"\s+([.,;:])")
_EM_DASH_RE = re.compile(r"\s+—\s*")
_BLANKS_RE = re.compile(r"[ \t]+")
_WORD_END_RE = re.compile(r"[^\W_]+(?:[-’'][^\W_]+)*-$")
_WORD_END_SCAN = 120  # no word is longer; the text so far can be a whole chapter
_EDGE_NON_WORD_RE = re.compile(r"^\W+|\W+$")
_NEXT_WORD_RE = re.compile(r"[^\W_]+(?:[-’'][^\W_]+)*")


def fix_small_caps(text: str) -> str:
    """Close the stray spaces the book's small-caps acronyms leave: "U .S .C ." and "( DMCA )".

    The book sets acronyms in a smaller size or in small caps, so the PDF cuts the word where the
    type changes. layout.py glues the cuts that sit edge to edge; this closes the spaces that are
    inside one word of the PDF ("U .S .C ." is a single word) and the ones just inside the
    parentheses round a one-token acronym.
    """
    text = _USC_RE.sub("U.S.C.", text)
    return _PAREN_TOKEN_RE.sub(lambda m: f"({m.group(1) or m.group(2)})", text)


def _form(token: str) -> str:
    """A token as evidence: no punctuation around it, no case."""
    return _EDGE_NON_WORD_RE.sub("", token).casefold()


def _is_digit(char: str) -> bool:
    return "0" <= char <= "9"


class HyphenDecider:
    """Decides what a hyphen at the end of a line is: a break to join, a compound to keep, or a number.

    The evidence is the run of lines given here. A form found elsewhere in the text is attested:
    "spaces" attests that "spac-/es" is a break, "non-dramatic" that "non-/dramatic" is a compound.
    The pieces of a broken word attest nothing: the last word of a line that ends in a hyphen, and
    the first word of the line after it. Evidence for one form decides. Evidence for both or
    neither goes to the overrides file; with no override it defaults to join and is listed in
    `undecided`, for the dry-run report.
    """

    def __init__(self, lines: Sequence[RawLine], ledger: OverrideLedger):
        self._ledger = ledger
        self._attested: set[str] = set()
        after_break = False
        for line in lines:
            tokens = line.text.split()
            if after_break:
                tokens = tokens[1:]
            after_break = line.text.rstrip().endswith("-")
            if after_break:
                tokens = tokens[:-1]
            for token in tokens:
                if form := _form(token):
                    self._attested.add(form)
        self._undecided: dict[str, None] = {}

    @property
    def undecided(self) -> list[str]:
        """The breaks no evidence decided and no override covers, as `p.<printed page> <word->|<next word>`."""
        return list(self._undecided)

    def decide(self, word_end: str, next_word: str, page: int) -> str:
        """`"join"`, `"keep"` or `"number"` for `word_end` (ends with a hyphen) before `next_word`, on printed `page`.

        A hyphen after a digit is a number's ("105-" / "304", "12-" / "month"), and one before a
        capital or a digit is the author's ("Trade-" / "Related"): a typesetter breaks a word
        between syllables, so neither is a break, and no evidence is needed to keep them.
        """
        if not word_end.endswith("-"):
            raise ValueError(f"a line-end hyphen needs a word ending in one, got {word_end!r}")
        if _is_digit(word_end[-2:-1]):
            return "number"
        if not next_word[:1].islower():
            return "keep"
        joined = _form(word_end[:-1] + next_word) in self._attested
        compound = _form(word_end + next_word) in self._attested
        if joined != compound:
            return "join" if joined else "keep"
        match = f"{word_end}|{next_word}"
        if override := self._ledger.find("hyphen", page, match):
            return override.value
        self._undecided[f"p.{page} {match}"] = None
        return "join"


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace(SOFT_HYPHEN, "")
    return _SPACE_BEFORE_PUNCTUATION_RE.sub(r"\1", fix_small_caps(text)).strip()


def _word_end(out: str) -> str:
    """The hyphenated word the text so far ends with, without what runs into it: "Proceed-" of "terms.—Proceed-"."""
    return _WORD_END_RE.search(out, max(0, len(out) - _WORD_END_SCAN)).group()


def clean_join(lines: Sequence[TextLine], hyphens: HyphenDecider) -> str:
    """Lines -> running text. A line-end hyphen is a break ("spac-" + "es"), a compound
    ("non-" + "dramatic") or part of a number ("105-" + "304"); `hyphens` says which."""
    out, out_page = "", 0
    for line in lines:
        text = _clean(line.text)
        if not text:
            continue
        if not out:
            out, out_page = text, line.page
            continue
        if out.endswith("-") and out[-2:-1].isalnum() and text[0].isalnum():
            verdict = hyphens.decide(_word_end(out), _NEXT_WORD_RE.match(text).group(), out_page)
            out = out[:-1] + text if verdict == "join" else out + text
        else:
            out += " " + text
        out_page = line.page
    return _BLANKS_RE.sub(" ", _EM_DASH_RE.sub("—", out)).strip()
