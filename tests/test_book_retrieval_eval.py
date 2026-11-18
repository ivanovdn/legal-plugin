# tests/test_book_retrieval_eval.py
"""The retrieval eval runner (spec Goal 7, "evals/books/run_retrieval.py").

The runner needs Ollama and Qdrant to run for real, so it is not in scripts/check.sh. What IS checked
here is its arithmetic and its file format, with stub search and lookup: a metric that is off by one
would otherwise be read as a finding about the retrieval.
"""
import inspect
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from evals.books import run_retrieval
from evals.books.run_retrieval import QUESTION_TYPES, Question, is_hit, load_questions, run, score_ranked
from ingest.books.models import BookChunk
from rag.book_search import BookHit, LookupResult, lookup_book_chunks, search_book_chunks

PREFIX = "circ92-2025-12"
S107 = f"{PREFIX}:usc17-s107"
S108 = f"{PREFIX}:usc17-s108"
S512 = f"{PREFIX}:usc17-s512"
CH1N13 = f"{PREFIX}:ch1-n13"


def _chunk(path: str, sub: tuple[str, ...] = ()) -> BookChunk:
    return BookChunk(
        chunk_key=f"{PREFIX}:{path}",
        ref_id=f"{PREFIX}:{path}",
        book="circ92",
        edition="2025-12",
        path=path,
        kind="rule",
        source_type="usc_title",
        jurisdiction="US",
        label=path,
        heading="h",
        page_start=1,
        page_end=1,
        text="h\nb",
        body="b",
        tokens=1,
        order=0,
        sub_ref_ids=[f"{PREFIX}:{s}" for s in sub],
    )


def _ranked(*paths: str) -> list[tuple[str, list[str]]]:
    """What score_ranked takes: (ref_id, sub_ref_ids) in rank order."""
    return [(f"{PREFIX}:{p}", []) for p in paths]


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "questions.txt"
    path.write_text(text, encoding="utf-8")
    return path


# --- the questions file ---------------------------------------------------------------------


def test_load_questions(tmp_path):
    path = _write(
        tmp_path,
        "# a comment\n"
        "\n"
        f"What are the fair use factors? | {S107} | search\n"
        f"  17 U.S.C. §108   |  {S108} , {S512}  | citation\n"
        "Who won the 1998 World Cup? | | none\n",
    )

    assert load_questions(path) == [
        Question("What are the fair use factors?", [S107], "search"),
        Question("17 U.S.C. §108", [S108, S512], "citation"),
        Question("Who won the 1998 World Cup?", [], "none"),
    ]


def test_a_pipe_inside_the_question_stays_in_the_question(tmp_path):
    path = _write(tmp_path, f"Is 'fair use | fair dealing' a thing? | {S107} | search\n")

    assert load_questions(path)[0].text == "Is 'fair use | fair dealing' a thing?"


def test_a_gold_written_as_a_citation_is_read_as_its_path(tmp_path):
    path = _write(tmp_path, "What is fair use? | §107 | search\n")

    assert load_questions(path)[0].gold == ["usc17-s107"]


@pytest.mark.parametrize(
    "line, why",
    [
        (f"only a question | {S107}", "two fields"),
        (f"q | {S107} | lookup", "unknown type"),
        ("q | | search", "a search question with no gold can never be scored"),
        (f"q | {S107} | none", "a none question with a gold contradicts itself"),
        ("q | not a ref id | search", "a gold that names nothing"),
        (f" | {S107} | search", "an empty question"),
    ],
)
def test_a_bad_line_is_refused_with_its_line_number(tmp_path, line, why):
    path = _write(tmp_path, f"# header\n{line}\n")

    with pytest.raises(ValueError, match=r"questions\.txt:2"):
        load_questions(path)


def test_an_empty_file_has_no_questions(tmp_path):
    assert load_questions(_write(tmp_path, "")) == []
    assert load_questions(_write(tmp_path, "# only a comment\n\n")) == []


def test_the_example_file_loads_and_has_one_of_each_type():
    path = Path(run_retrieval.__file__).with_name("questions.example.txt")

    assert sorted(q.type for q in load_questions(path)) == ["citation", "history", "none", "search"]
    assert "NOT a test set" in path.read_text(encoding="utf-8")


# --- is_hit ---------------------------------------------------------------------------------


def test_is_hit():
    assert is_hit(S107, [], S107)  # equal
    assert is_hit(f"{S107}-1", [], S107)  # a chunk of a subsection, the gold being the section
    assert is_hit(f"{S107}-1-A", [], S107)
    assert is_hit(S107, [f"{S107}-1", f"{S107}-2"], f"{S107}-2")  # the gold is a node inside the chunk
    assert not is_hit(S108, [], S107)
    assert not is_hit(S107, [], f"{S107}-1")  # the section's chunk is not a hit for its subsection...
    assert not is_hit(S107, [f"{S107}-1"], f"{S107}-2")  # ...unless that subsection is inside it


def test_is_hit_matches_whole_labels_only():
    assert not is_hit(f"{PREFIX}:usc17-s1010", [], f"{PREFIX}:usc17-s101")


def test_is_hit_compares_like_with_like():
    # the same path in another book or edition is not the gold
    assert not is_hit("circ92-2030-01:usc17-s107", [], S107)
    assert not is_hit("other-2025-12:usc17-s107", [], S107)


def test_is_hit_with_a_bare_path_gold_ignores_the_book():
    assert is_hit(S107, [], "usc17-s107")
    assert is_hit(f"{S107}-1", [], "usc17-s107")
    assert is_hit(S107, [f"{S107}-2"], "usc17-s107-2")
    assert not is_hit(f"{PREFIX}:usc17-s1070", [], "usc17-s107")


# --- the metrics ----------------------------------------------------------------------------


def test_metrics():
    scores = score_ranked(_ranked("usc17-s108", "usc17-s107", "usc17-s512"), [S107])

    assert scores["recall@5"] == 1.0
    assert scores["recall@10"] == 1.0
    assert scores["rr"] == 0.5
    assert scores["ndcg@10"] == pytest.approx(1 / math.log2(3))


def test_a_first_place_hit_scores_one_everywhere():
    scores = score_ranked(_ranked("usc17-s107", "usc17-s108"), [S107])

    assert scores == {"recall@5": 1.0, "recall@10": 1.0, "rr": 1.0, "ndcg@10": pytest.approx(1.0)}


def test_a_miss_scores_zero_everywhere():
    scores = score_ranked(_ranked("usc17-s108", "usc17-s512"), [S107])

    assert scores == {"recall@5": 0.0, "recall@10": 0.0, "rr": 0.0, "ndcg@10": 0.0}


def test_nothing_returned_scores_zero():
    assert score_ranked([], [S107]) == {"recall@5": 0.0, "recall@10": 0.0, "rr": 0.0, "ndcg@10": 0.0}


def test_the_cutoffs_are_inclusive_at_five_and_ten():
    fill = [f"usc17-s{n}" for n in range(200, 220)]

    at_5 = score_ranked(_ranked(*fill[:4], "usc17-s107"), [S107])
    at_6 = score_ranked(_ranked(*fill[:5], "usc17-s107"), [S107])
    at_10 = score_ranked(_ranked(*fill[:9], "usc17-s107"), [S107])
    at_11 = score_ranked(_ranked(*fill[:10], "usc17-s107"), [S107])

    assert (at_5["recall@5"], at_5["recall@10"]) == (1.0, 1.0)
    assert (at_6["recall@5"], at_6["recall@10"]) == (0.0, 1.0)
    assert at_6["ndcg@10"] == pytest.approx(1 / math.log2(7))
    assert (at_10["recall@10"], at_10["ndcg@10"]) == (1.0, pytest.approx(1 / math.log2(11)))
    assert (at_11["recall@10"], at_11["ndcg@10"]) == (0.0, 0.0)
    assert at_11["rr"] == pytest.approx(1 / 11)  # MRR has no cutoff


def test_recall_is_the_share_of_the_golds_found():
    ranked = _ranked("usc17-s107", "usc17-s200", "usc17-s201", "usc17-s202", "usc17-s203", "usc17-s108")

    scores = score_ranked(ranked, [S107, S108])

    assert scores["recall@5"] == 0.5
    assert scores["recall@10"] == 1.0
    assert scores["rr"] == 1.0


def test_ndcg_is_normalised_by_the_best_order_for_that_many_golds():
    # golds at ranks 1 and 3 of two: DCG = 1 + 1/log2(4); the ideal is ranks 1 and 2: 1 + 1/log2(3)
    scores = score_ranked(_ranked("usc17-s107", "usc17-s200", "usc17-s108"), [S107, S108])

    assert scores["ndcg@10"] == pytest.approx((1 + 1 / 2) / (1 + 1 / math.log2(3)))


def test_a_second_chunk_of_the_same_gold_adds_no_gain():
    ranked = [(S107, []), (f"{S107}-1", []), (f"{S107}-2", [])]

    scores = score_ranked(ranked, [S107])

    assert scores["ndcg@10"] == pytest.approx(1.0)
    assert scores["recall@5"] == 1.0


def test_one_chunk_covering_two_golds_cannot_push_ndcg_past_one():
    scores = score_ranked([(S107, [f"{S107}-1"])], [S107, f"{S107}-1"])

    assert scores["recall@5"] == 1.0
    assert 0.0 < scores["ndcg@10"] <= 1.0


def test_ndcg_is_cut_at_ten_and_so_is_its_ideal():
    golds = [f"{PREFIX}:usc17-s{n}" for n in range(300, 312)]  # twelve golds
    scores = score_ranked([(g, []) for g in golds], golds)  # all twelve returned, in order

    assert scores["recall@5"] == pytest.approx(5 / 12)
    assert scores["recall@10"] == pytest.approx(10 / 12)
    assert scores["ndcg@10"] == pytest.approx(1.0)  # the ten places there are to fill are all filled


def test_a_gold_listed_twice_counts_once():
    scores = score_ranked(_ranked("usc17-s107"), [S107, S107])

    assert scores["recall@5"] == 1.0
    assert scores["ndcg@10"] == pytest.approx(1.0)


def test_scoring_with_no_gold_is_refused():
    with pytest.raises(ValueError, match="gold"):
        score_ranked(_ranked("usc17-s107"), [])


# --- run ------------------------------------------------------------------------------------


class _Stubs:
    """A search and a lookup that answer from tables and remember what they were asked."""

    def __init__(self, search: dict[str, list[BookChunk]], lookup: dict[str, LookupResult | None]):
        self.search_table = search
        self.lookup_table = lookup
        self.searched: list[str] = []
        self.searched_with_notes: list[str] = []
        self.looked_up: list[str] = []

    def search(self, query: str, *, include_history_notes: bool = False) -> list[BookHit]:
        self.searched.append(query)
        if include_history_notes:
            self.searched_with_notes.append(query)
        return [BookHit(chunk=c, score=1.0) for c in self.search_table.get(query, [])]

    def lookup(self, reference: str) -> LookupResult | None:
        self.looked_up.append(reference)
        return self.lookup_table.get(reference)


def test_run_with_stubs():
    stubs = _Stubs(
        search={
            "fair use?": [_chunk("usc17-s108"), _chunk("usc17-s107")],  # gold at rank 2
            "library copies?": [_chunk("usc17-s512")],  # a miss
            "who won the cup?": [],  # the book has nothing
            "who is the pope?": [],  # nor about this
            "what is a trademark?": [_chunk("usc17-s107")],  # the book "answers" what it cannot
        },
        lookup={
            # the hit is the second chunk shown: the lookup is scanned whole
            "§107": LookupResult(target=S107, shown=[_chunk("usc17-s108"), _chunk("usc17-s107-1")], more=[]),
            "§108": LookupResult(target=S108, shown=[_chunk("usc17-s107")], more=[]),
            "§999": None,
        },
    )
    questions = [
        Question("fair use?", [S107], "search"),
        Question("library copies?", [S108], "search"),
        Question("§107", [S107], "citation"),  # lookup.shown holds a chunk under the gold: a hit
        Question("§108", [S108], "citation"),  # shown holds only §107: a miss
        Question("§999", [S107], "citation"),  # the reference did not parse: a miss
        Question("who won the cup?", [], "none"),  # search returned []: counts as not found
        Question("what is a trademark?", [], "none"),  # search returned something: does not
        Question("who is the pope?", [], "none"),
    ]

    result = run(questions, search=stubs.search, lookup=stubs.lookup)

    assert result["n"] == 8
    assert result["counts"] == {"search": 2, "citation": 3, "none": 3, "history": 0}
    assert result["recall@5"] == 0.5  # (1.0 + 0.0) / 2
    assert result["recall@10"] == 0.5
    assert result["mrr"] == pytest.approx(0.25)  # (0.5 + 0.0) / 2
    assert result["ndcg@10"] == pytest.approx((1 / math.log2(3)) / 2)
    assert result["citation_hit_rate"] == pytest.approx(1 / 3)
    assert result["not_found_rate"] == pytest.approx(2 / 3)
    # each type goes only where it belongs
    assert stubs.searched == [
        "fair use?", "library copies?", "who won the cup?", "what is a trademark?", "who is the pope?",
    ]
    assert stubs.looked_up == ["§107", "§108", "§999"]


def test_run_records_every_question_and_whether_it_came_out_right():
    stubs = _Stubs(
        search={"a": [_chunk("usc17-s108"), _chunk("usc17-s107")], "b": [_chunk("usc17-s107")], "c": []},
        lookup={"§107": LookupResult(target=S107, shown=[_chunk("usc17-s108")], more=[])},
    )
    questions = [
        Question("a", [S107], "search"),
        Question("b", [S108], "search"),
        Question("§107", [S107], "citation"),
        Question("c", [], "none"),
    ]

    records = run(questions, search=stubs.search, lookup=stubs.lookup)["questions"]

    assert [(r["question"], r["type"], r["ok"]) for r in records] == [
        ("a", "search", True),
        ("b", "search", False),
        ("§107", "citation", False),
        ("c", "none", True),
    ]
    assert records[0]["returned"] == [S108, S107]
    assert records[2]["returned"] == [S108]


def test_a_lookup_that_shows_nothing_is_a_miss():
    stubs = _Stubs(
        search={},
        lookup={"§107": LookupResult(target=S107, shown=[], more=[]), "§108": None},  # parsed, no chunks / unparsed
    )
    questions = [Question("§107", [S107], "citation"), Question("§108", [S108], "citation")]

    result = run(questions, search=stubs.search, lookup=stubs.lookup)

    assert [r["ok"] for r in result["questions"]] == [False, False]
    assert result["citation_hit_rate"] == 0.0


def test_a_search_question_with_a_second_gold_missing_is_not_ok():
    stubs = _Stubs(search={"a": [_chunk("usc17-s107")]}, lookup={})

    record = run([Question("a", [S107, S108], "search")], search=stubs.search, lookup=stubs.lookup)["questions"][0]

    assert record["recall@10"] == 0.5
    assert record["ok"] is False


def test_a_history_question_is_searched_with_history_notes_included():
    stubs = _Stubs(search={"when?": [_chunk("ch1-n13")], "fair use?": [_chunk("usc17-s107")]}, lookup={})
    run([Question("when?", [CH1N13], "history"), Question("fair use?", [S107], "search")],
        search=stubs.search, lookup=stubs.lookup)
    assert stubs.searched_with_notes == ["when?"]
    assert stubs.searched == ["when?", "fair use?"]


def test_history_is_scored_like_search_and_reported_apart():
    stubs = _Stubs(search={"when?": [_chunk("usc17-s108"), _chunk("ch1-n13")], "fair use?": []}, lookup={})
    result = run([Question("when?", [CH1N13], "history"), Question("fair use?", [S107], "search")],
                 search=stubs.search, lookup=stubs.lookup)
    assert result["counts"]["history"] == 1
    assert result["history_recall@5"] == 1.0 and result["history_mrr"] == 0.5
    assert result["recall@5"] == 0.0  # the search mean is over search questions only
    assert result["questions"][0]["type"] == "history"


def test_a_history_question_needs_a_gold(tmp_path):
    with pytest.raises(ValueError, match="needs at least one gold id"):
        load_questions(_write(tmp_path, "when? | | history\n"))


def test_run_with_no_questions_is_all_zeros():
    result = run([], search=_Stubs({}, {}).search, lookup=_Stubs({}, {}).lookup)

    assert result["n"] == 0
    assert result["counts"] == {"search": 0, "citation": 0, "none": 0, "history": 0}
    for key in ("recall@5", "recall@10", "mrr", "ndcg@10", "citation_hit_rate", "not_found_rate",
                "history_recall@5", "history_recall@10", "history_mrr", "history_ndcg@10"):
        assert result[key] == 0.0
    assert result["questions"] == []


def test_run_refuses_an_unknown_type_rather_than_skipping_it():
    stubs = _Stubs({}, {})

    with pytest.raises(ValueError, match="type"):
        run([Question("q", [S107], "lookup")], search=stubs.search, lookup=stubs.lookup)


def test_a_failing_search_stops_the_run_rather_than_scoring_a_miss():
    def search(query: str) -> list[BookHit]:
        raise ConnectionError("qdrant is down")

    with pytest.raises(ConnectionError):
        run([Question("q", [S107], "search")], search=search, lookup=_Stubs({}, {}).lookup)


def test_run_defaults_to_the_real_search_and_lookup():
    params = inspect.signature(run).parameters

    assert params["search"].default is search_book_chunks
    assert params["lookup"].default is lookup_book_chunks


# --- main -----------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def top_k_10(monkeypatch):
    """The shipped default is 8; the runner needs at least ten results a question to report @10 honestly."""
    set_top_k(monkeypatch, 10)


def set_top_k(monkeypatch, top_k: int) -> None:
    monkeypatch.setattr(run_retrieval, "get_settings", lambda: SimpleNamespace(book_search_top_k=top_k))


def test_main_refuses_when_search_returns_fewer_than_ten_results(tmp_path, capsys, monkeypatch):
    asked = []
    monkeypatch.setattr(run_retrieval, "run", lambda qs: asked.append(qs))
    path = _write(tmp_path, f"hit | {S107} | search\n")

    for top_k in (8, 9):
        set_top_k(monkeypatch, top_k)
        assert run_retrieval.main([str(path)]) == 2

        captured = capsys.readouterr()
        assert f"BOOK_SEARCH_TOP_K is {top_k}" in captured.err
        assert "BOOK_SEARCH_TOP_K=10 uv run python -m evals.books.run_retrieval" in captured.err
        assert captured.out == ""
    assert asked == []  # nothing was searched


def test_the_top_k_guard_comes_before_the_file_is_read(tmp_path, capsys, monkeypatch):
    set_top_k(monkeypatch, 8)

    assert run_retrieval.main([str(tmp_path / "nope.txt")]) == 2

    err = capsys.readouterr().err
    assert "BOOK_SEARCH_TOP_K" in err and "No such file" not in err  # the file was never opened


@pytest.mark.parametrize("top_k", [10, 30])
def test_main_runs_at_ten_results_or_more_and_says_which(tmp_path, capsys, monkeypatch, top_k):
    set_top_k(monkeypatch, top_k)
    stubs = _Stubs(search={"hit": [_chunk("usc17-s107")]}, lookup={})
    monkeypatch.setattr(run_retrieval, "run", lambda qs: run(qs, search=stubs.search, lookup=stubs.lookup))

    assert run_retrieval.main([str(_write(tmp_path, f"hit | {S107} | search\n"))]) == 0

    out = capsys.readouterr().out
    assert f"BOOK_SEARCH_TOP_K={top_k}" in out.splitlines()[0]
    assert stubs.searched == ["hit"]


def test_main_on_an_empty_file_says_so_and_exits_zero(tmp_path, capsys):
    assert run_retrieval.main([str(_write(tmp_path, "# nothing yet\n"))]) == 0

    assert "no questions" in capsys.readouterr().out


def test_main_prints_a_per_type_table_overall_metrics_and_the_misses(tmp_path, capsys, monkeypatch):
    stubs = _Stubs(
        search={"hit": [_chunk("usc17-s107")], "miss": [_chunk("usc17-s512")], "none q": []},
        lookup={},
    )
    monkeypatch.setattr(run_retrieval, "run", lambda qs: run(qs, search=stubs.search, lookup=stubs.lookup))
    path = _write(tmp_path, f"hit | {S107} | search\nmiss | {S108} | search\nnone q | | none\n")

    assert run_retrieval.main([str(path)]) == 0

    out = capsys.readouterr().out
    rows = {words[0]: words for line in out.splitlines() if (words := line.split()) and words[0] in QUESTION_TYPES}
    assert rows["search"][1] == "2"
    assert rows["search"][2] == "0.500"  # recall@5
    assert rows["none"][1] == "1"
    assert rows["citation"][1] == "0"
    assert "miss" in out and S108 in out  # the missed question is named with its gold


def test_main_prints_a_history_row(tmp_path, capsys, monkeypatch):
    stubs = _Stubs(search={"when?": [_chunk("ch1-n13")]}, lookup={})
    monkeypatch.setattr(run_retrieval, "run", lambda qs: run(qs, search=stubs.search, lookup=stubs.lookup))
    path = _write(tmp_path, f"when? | {CH1N13} | history\n")

    assert run_retrieval.main([str(path)]) == 0

    out = capsys.readouterr().out
    rows = {words[0]: words for line in out.splitlines() if (words := line.split()) and words[0] in QUESTION_TYPES}
    assert rows["history"][1] == "1" and rows["history"][2] == "1.000"
    assert rows["history"][6] == "-" and rows["history"][7] == "-"  # hit_rate, not_found
    assert rows["search"][2] == "-"


def test_main_refuses_a_bad_file_with_exit_two(tmp_path, capsys):
    assert run_retrieval.main([str(_write(tmp_path, "q | | search\n"))]) == 2

    assert "questions.txt:1" in capsys.readouterr().err


def test_main_refuses_a_missing_file_with_exit_two(tmp_path, capsys):
    assert run_retrieval.main([str(tmp_path / "nope.txt")]) == 2

    assert "nope.txt" in capsys.readouterr().err
