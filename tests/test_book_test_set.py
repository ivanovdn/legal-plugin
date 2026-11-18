# tests/test_book_test_set.py
"""The book retrieval test set: the files `evals.books.run_retrieval` scores (spec
docs/superpowers/specs/2026-11-18-book-retrieval-test-set-design.md).

The runner cannot see these faults, and each one would read as a finding about the retrieval:

- a gold that is grammatical but names no node of the book, or a node of a kind its question's search leaves
  out (a history note under `search`, a repealed stub under either): the question is a miss forever;
- a question that names a section, or one that appears twice: either inflates the score;
- a Law Stack Exchange question without its link and licence, which the licence forbids.
"""
import re
from pathlib import Path

import pytest

from evals.books.run_retrieval import QUESTION_TYPES, load_questions
from ingest.books.ref_ids import parse_reference
from rag.book_search import kind_filter

SETS = Path(__file__).resolve().parent.parent / "evals" / "books" / "sets"

# Each file: the question types it may hold, and the least and most questions it may have.
SET_FILES = {
    "lse-search.txt": (("search",), 55, 65),
    "lse-none.txt": (("none",), 12, 18),
    "citations.txt": (("citation",), 12, 18),
    "written.txt": (("search", "history"), 12, 18),
}

SPEC = "docs/superpowers/specs/2026-11-18-book-retrieval-test-set-design.md"

# Each question type the runner searches for, and whether it searches with the history notes included
# (evals.books.run_retrieval: `history` passes include_history_notes=True, `search` the default).
SEARCHES_NOTES = {"search": False, "history": True}

LSE_SEARCH_RE = re.compile(
    r"^# lse:(\d+) https://law\.stackexchange\.com/q/\1 · CC BY-SA (?:3\.0|4\.0) · cited: .+ · (?:answers|governs) · gold: .+$"
)
LSE_NONE_RE = re.compile(r"^# lse:(\d+) https://law\.stackexchange\.com/q/\1 · CC BY-SA (?:3\.0|4\.0) · tags: .+ · none: .+$")
# Each Law Stack Exchange file, and the provenance line every question in it carries.
LSE_FILES = {"lse-search.txt": LSE_SEARCH_RE, "lse-none.txt": LSE_NONE_RE}
DROPPED_RE = re.compile(r"^# dropped lse:(\d+) · .+$")
WRITTEN_RE = re.compile(r"^# written by Claude · (search|history) · gold: .+$")
# The files whose questions are searched for, not looked up: none of them may name a section.
SEARCHED_FILES = ("lse-search.txt", "written.txt")
SECTION_RE = re.compile(r"§|\bU\.?\s?S\.?\s?C\b|\bsection\s+\d|\b\d{3}[A-C]?\([a-z0-9]+\)", re.I)
# Each Law Stack Exchange file's walk line, which a later batch continues from: the last rank walked of the
# pool's size, and what became of the ranks walked. Only the search file splits its takes into answers/governs.
WALKED_RE = {
    "lse-search.txt": re.compile(
        r"^# Walked: ranks 1–(\d+) of 193; taken (\d+), dropped (\d+)\. Of those taken: answers (\d+), governs (\d+)\.$"
    ),
    "lse-none.txt": re.compile(r"^# Walked: ranks 1–(\d+) of 182; taken (\d+), dropped (\d+)\.$"),
}
# The files whose gold a second, blind reader labelled again from the book's text (citations.txt's gold is what
# parse_reference returns, so there is nothing to read). Each header carries the agreement line, and each entry's
# comment the verdict: `agreed`, or the second reader's gold and how the disagreement was settled. The owner's
# check (Task 8) may follow the verdict.
SECOND_READ_FILES = ("lse-search.txt", "lse-none.txt", "written.txt")
SECOND_READING_HEADER_RE = re.compile(
    r"^# Second reading \(fresh agent, book text only\): agreed on (\d+) of (\d+) \(\d+%\)$"
)
SECOND_READING_RE = re.compile(r" · second reading: (agreed|.+ — settled: .+?)(?: · owner's check: .+)?$")


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
        if _is_question(line) or line.startswith(("# lse:", "# dropped ", "# written by Claude ·")):
            break
        if line.startswith("#"):
            header.append(line)
    return header


@pytest.mark.parametrize("name", SET_FILES)
def test_each_set_file_holds_only_its_types(name):
    types = SET_FILES[name][0]
    assert set(types) <= set(QUESTION_TYPES)
    assert {q.type for q in load_questions(SETS / name)} <= set(types)


@pytest.mark.parametrize("name", SET_FILES)
def test_each_set_file_has_its_size(name):
    _, least, most = SET_FILES[name]
    assert least <= len(load_questions(SETS / name)) <= most


@pytest.mark.parametrize("name", SET_FILES)
def test_every_gold_names_a_node_of_the_book(name, circ92_whole_parse):
    index = circ92_whole_parse.tree.index()
    missing = [(q.text, g) for q in load_questions(SETS / name) for g in q.gold if g.rpartition(":")[2] not in index]
    assert missing == []


def _searched_kinds(include_history_notes: bool) -> set[str]:
    """The node kinds `search_book_chunks` returns, read off its own filter."""
    (condition,) = kind_filter(include_history_notes).must
    assert condition.key == "kind"
    return set(condition.match.any)


@pytest.mark.parametrize("name", SET_FILES)
def test_every_gold_is_a_kind_its_search_returns(name, circ92_whole_parse):
    """A gold of a kind the question's search leaves out (a history note under `search`, a repealed stub under
    either) can never be found: the question is a miss forever."""
    index = circ92_whole_parse.tree.index()
    unsearched = [
        (q.text, g, index[path].kind)
        for q in load_questions(SETS / name)
        if q.type in SEARCHES_NOTES
        for g in q.gold
        if (path := g.rpartition(":")[2]) in index
        and index[path].kind not in _searched_kinds(SEARCHES_NOTES[q.type])
    ]
    assert unsearched == []


@pytest.mark.parametrize("name", SET_FILES)
def test_a_history_question_has_a_note_among_its_golds(name, circ92_whole_parse):
    """A `history` question is there for an answer that lives in a history note."""
    index = circ92_whole_parse.tree.index()
    noteless = [
        q.text
        for q in load_questions(SETS / name)
        if q.type == "history"
        and not any((n := index.get(g.rpartition(":")[2])) and n.kind == "history_note" for g in q.gold)
    ]
    assert noteless == []


def test_every_citation_question_names_its_gold():
    for q in load_questions(SETS / "citations.txt"):
        assert len(q.gold) == 1 and parse_reference(q.text) == q.gold[0], q.text


def test_no_question_appears_twice():
    texts = [q.text for name in SET_FILES for q in load_questions(SETS / name)]
    assert sorted({t for t in texts if texts.count(t) > 1}) == []


def test_lse_questions_carry_their_source_and_licence():
    ids = []
    for name, provenance in LSE_FILES.items():
        path = SETS / name
        bad = [(above, question) for above, question in _entries(path) if not provenance.match(above)]
        assert bad == [], name

        lines = path.read_text(encoding="utf-8").splitlines()
        taken = [provenance.match(above).group(1) for above, _ in _entries(path)]
        dropped_lines = [line for line in lines if line.startswith("# dropped")]
        assert [line for line in dropped_lines if not DROPPED_RE.match(line)] == [], name
        ids += taken + [DROPPED_RE.match(line).group(1) for line in dropped_lines]

    # A candidate is taken or dropped once, in one file: no id twice, among the taken, across taken and
    # dropped, or across the two files.
    assert sorted({i for i in ids if ids.count(i) > 1}) == []


@pytest.mark.parametrize("name", SEARCHED_FILES)
def test_no_searched_question_names_a_section(name):
    named = [q.text for q in load_questions(SETS / name) if SECTION_RE.search(q.text)]
    assert named == []


def test_written_questions_say_who_wrote_them():
    path = SETS / "written.txt"
    assert any("written by Claude" in line for line in _header(path))
    for above, question in _entries(path):
        match = WRITTEN_RE.match(above)
        assert match, question
        assert match.group(1) == question.rpartition("|")[2].strip(), question


def test_written_has_both_types():
    assert {q.type for q in load_questions(SETS / "written.txt")} == {"search", "history"}


@pytest.mark.parametrize("name", LSE_FILES)
def test_lse_headers_name_the_source_revision_and_licence(name):
    header = "\n".join(_header(SETS / name))
    for needed in (
        "ymoslem/Law-StackExchange",
        "ab2dbaad9a71b6550316994c8fcd69f24f7f16e6",
        "CC BY-SA 4.0",
        "questions copied verbatim; gold labels added",
        "random.Random(20261118)",
        SPEC,
    ):
        assert needed in header, needed


@pytest.mark.parametrize("name", WALKED_RE)
def test_lse_headers_record_the_walk(name):
    """The next batch starts at the rank after the last one walked, so the header must say it, and say it
    right: every rank walked was either taken or dropped, and the counts are the file's own."""
    path = SETS / name
    walked = [m for line in _header(path) if (m := WALKED_RE[name].match(line))]
    assert len(walked) == 1, f"expected one '# Walked: ranks 1–N of <pool size>; …' line in the header of {name}"
    last, taken, dropped, *split = (int(n) for n in walked[0].groups())

    comments = [above for above, _ in _entries(path)]
    lines = path.read_text(encoding="utf-8").splitlines()
    assert taken == len(comments)
    assert dropped == sum(line.startswith("# dropped ") for line in lines)
    assert last == taken + dropped
    if split:  # the search file's takes, split by what their gold does
        answers, governs = split
        assert answers == sum(" · answers · " in c for c in comments)
        assert governs == sum(" · governs · " in c for c in comments)


@pytest.mark.parametrize("name", SECOND_READ_FILES)
def test_the_second_reading_is_recorded(name):
    """Every entry carries the second reading's verdict, and the header's agreement line is the file's own count:
    agreed on N of M, where M is the file's questions and N the entries whose verdict is `agreed`."""
    path = SETS / name
    lines = [m for line in _header(path) if (m := SECOND_READING_HEADER_RE.match(line))]
    assert len(lines) == 1, (
        f"expected one '# Second reading (fresh agent, book text only): agreed on N of M (P%)' line in the header "
        f"of {name}"
    )
    agreed, total = (int(n) for n in lines[0].groups())

    entries = _entries(path)
    unrecorded = [question for above, question in entries if not SECOND_READING_RE.search(above)]
    assert unrecorded == []
    assert total == len(load_questions(path))
    assert agreed == sum(SECOND_READING_RE.search(above).group(1) == "agreed" for above, _ in entries)
