# tests/test_book_test_set.py
"""The book retrieval test set: the files `evals.books.run_retrieval` scores (spec
docs/superpowers/specs/2026-11-18-book-retrieval-test-set-design.md).

The runner cannot see these faults, and each one would read as a finding about the retrieval:

- a gold that is grammatical but names no node of the book: the question is a miss forever;
- a question that names a section, or one that appears twice: either inflates the score;
- a Law Stack Exchange question without its link and licence, which the licence forbids.
"""
import re
from pathlib import Path

import pytest

from evals.books.run_retrieval import QUESTION_TYPES, load_questions

SETS = Path(__file__).resolve().parent.parent / "evals" / "books" / "sets"

# Each file: the question types it may hold, and the least and most questions it may have.
SET_FILES = {"lse-search.txt": (("search",), 55, 65)}

SPEC = "docs/superpowers/specs/2026-11-18-book-retrieval-test-set-design.md"

LSE_SEARCH_RE = re.compile(
    r"^# lse:(\d+) https://law\.stackexchange\.com/q/\1 · CC BY-SA (?:3\.0|4\.0) · cited: .+ · (?:answers|governs) · gold: .+$"
)
DROPPED_RE = re.compile(r"^# dropped lse:(\d+) · .+$")
SECTION_RE = re.compile(r"§|\bU\.?\s?S\.?\s?C\b|\bsection\s+\d|\b\d{3}[A-C]?\([a-z0-9]+\)", re.I)
# The walk 3b and 3c continue from: the last rank walked, and what became of the ranks walked.
WALKED_RE = re.compile(
    r"^# Walked: ranks 1–(\d+) of 193; taken (\d+), dropped (\d+)\. Of those taken: answers (\d+), governs (\d+)\.$"
)


def _is_question(line: str) -> bool:
    return bool(line.strip()) and not line.lstrip().startswith("#")


def _entries(path: Path) -> list[tuple[str, str]]:
    """`(the line directly above, the question line)` for every question line of `path`, both stripped.
    The line above is "" for a question on the first line."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [
        ((lines[i - 1] if i else "").strip(), line.strip())
        for i, line in enumerate(lines)
        if _is_question(line)
    ]


def _header(path: Path) -> list[str]:
    """The leading `#` lines: everything before the first provenance line, dropped line or question."""
    header = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if _is_question(line) or line.startswith(("# lse:", "# dropped ")):
            break
        if line.startswith("#"):
            header.append(line)
    return header


@pytest.mark.xfail(strict=True, reason="filled to 55+ in Task 3c")
@pytest.mark.parametrize("name", SET_FILES)
def test_each_set_file_has_its_types_and_size(name):
    types, least, most = SET_FILES[name]
    assert set(types) <= set(QUESTION_TYPES)
    questions = load_questions(SETS / name)
    assert {q.type for q in questions} <= set(types)
    assert least <= len(questions) <= most


@pytest.mark.parametrize("name", SET_FILES)
def test_every_gold_names_a_node_of_the_book(name, circ92_whole_parse):
    index = circ92_whole_parse.tree.index()
    missing = [(q.text, g) for q in load_questions(SETS / name) for g in q.gold if g.rpartition(":")[2] not in index]
    assert missing == []


def test_no_question_appears_twice():
    texts = [q.text for name in SET_FILES for q in load_questions(SETS / name)]
    assert sorted({t for t in texts if texts.count(t) > 1}) == []


def test_lse_questions_carry_their_source_and_licence():
    path = SETS / "lse-search.txt"
    bad = [(above, question) for above, question in _entries(path) if not LSE_SEARCH_RE.match(above)]
    assert bad == []

    # A candidate is taken or dropped once: no id twice, among the taken or across taken and dropped.
    lines = path.read_text(encoding="utf-8").splitlines()
    taken = [LSE_SEARCH_RE.match(above).group(1) for above, _ in _entries(path)]
    dropped_lines = [line for line in lines if line.startswith("# dropped")]
    assert [line for line in dropped_lines if not DROPPED_RE.match(line)] == []
    ids = taken + [DROPPED_RE.match(line).group(1) for line in dropped_lines]
    assert sorted({i for i in ids if ids.count(i) > 1}) == []


def test_no_lse_search_question_names_a_section():
    named = [q.text for q in load_questions(SETS / "lse-search.txt") if SECTION_RE.search(q.text)]
    assert named == []


def test_lse_headers_name_the_source_revision_and_licence():
    header = "\n".join(_header(SETS / "lse-search.txt"))
    for needed in (
        "ymoslem/Law-StackExchange",
        "ab2dbaad9a71b6550316994c8fcd69f24f7f16e6",
        "CC BY-SA 4.0",
        "questions copied verbatim; gold labels added",
        "random.Random(20261118)",
        SPEC,
    ):
        assert needed in header, needed


def test_lse_search_header_records_the_walk():
    """The next batch starts at the rank after the last one walked, so the header must say it, and say it
    right: every rank walked was either taken or dropped, and the counts are the file's own."""
    path = SETS / "lse-search.txt"
    walked = [m for line in _header(path) if (m := WALKED_RE.match(line))]
    assert len(walked) == 1, "expected one '# Walked: ranks 1–N of 193; …' line in the header"
    last, taken, dropped, answers, governs = (int(n) for n in walked[0].groups())

    comments = [above for above, _ in _entries(path)]
    lines = path.read_text(encoding="utf-8").splitlines()
    assert taken == len(comments)
    assert dropped == sum(line.startswith("# dropped ") for line in lines)
    assert last == taken + dropped
    assert answers == sum(" · answers · " in c for c in comments)
    assert governs == sum(" · governs · " in c for c in comments)
