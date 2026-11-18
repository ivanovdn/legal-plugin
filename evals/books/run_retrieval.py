"""Retrieval eval for the reference books: scores `search_books` and `lookup_book` against a question set.

    uv run python -m evals.books.run_retrieval <questions file>

Needs Ollama (the query embedding) and Qdrant (the book collection), so it is NOT part of
`scripts/check.sh` and is not one of the zero-LLM `evals/` runners: `scripts/eval.sh` never runs it. Its
arithmetic is unit-tested with stubs (tests/test_book_retrieval_eval.py).

Why it exists: the chunk budget, the embedding model and `BOOK_MIN_SCORE` are decided on the test set in
`evals/books/sets/` (its baseline is in docs/rag.md, "Measuring retrieval"). This is the thing that reads it.
Run it before and after changing any of the three, on the same file.

Questions file, one per line: `question | gold ref_ids (comma-separated) | type`. `#` lines and blank
lines are skipped. Types:

- `search`: `search_book_chunks(question)`; the ranked chunks are scored against the gold ids.
- `history`: `search_book_chunks(question, include_history_notes=True)`, scored like `search` and reported in
  its own row. For a question whose answer (an effective date, say) lives only in a history note, which
  the default search leaves out.
- `citation`: `lookup_book_chunks(question)`; a hit when a chunk it shows is a hit for a gold id. The shown
  chunks are in document order, not by relevance, so there is no rank to score: the metric is a hit rate.
- `none`: a question the book cannot answer, with no gold. It counts as "not found" when `search` returns
  nothing. That only happens once `BOOK_MIN_SCORE` is set, so with the floor off (0) the rate is 0 by
  construction; the rate is what a floor is calibrated against.

A failing search or lookup (Ollama or Qdrant down) stops the run. Scoring it as a miss would put an outage
into the recall number.

Run with `BOOK_SEARCH_TOP_K=10` or more. `search_book_chunks` returns at most `BOOK_SEARCH_TOP_K` chunks
(8 by default), so under the shipped setting Recall@10 would equal Recall@8 and the nDCG@10 window would be 8
ranks, under a label that says 10: a number that reads as a finding about the retrieval. `main` refuses (exit 2,
before it reads the file or searches) when the setting is below ten, and names the command to run:

    BOOK_SEARCH_TOP_K=10 uv run python -m evals.books.run_retrieval <questions file>

It never changes the setting itself, and the report's first line prints the value the run used.
"""
import argparse
import math
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from config import get_settings
from ingest.books.ref_ids import is_under, parse_reference
from rag.book_search import BookHit, LookupResult, lookup_book_chunks, search_book_chunks

QUESTION_TYPES = ("search", "citation", "none", "history")
_CUTOFFS = (5, 10)


@dataclass
class Question:
    text: str
    gold: list[str]  # full ref_ids, or bare paths; empty exactly for type `none`
    type: str  # one of QUESTION_TYPES


def _refusal(path: Path, lineno: int, why: str) -> ValueError:
    return ValueError(f"{path}:{lineno}: {why}")


def load_questions(path: Path) -> list[Question]:
    """The questions in `path`. ValueError, naming the line, on one that could not be scored.

    The line is split from the right, so the question may hold a `|`. A gold goes through `parse_reference`,
    so a full ref_id or a bare path stands as written and a citation in words (`§107`) becomes its path; one
    that names nothing is refused here, because a gold that can never match would pass as a retrieval miss.
    (A grammatical id that names no node of the book is not caught: that needs Qdrant.)"""
    questions = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        fields = [part.strip() for part in line.rsplit("|", 2)]
        if len(fields) != 3:
            raise _refusal(path, lineno, "expected `question | gold ref_ids | type`")
        text, golds, kind = fields
        if not text:
            raise _refusal(path, lineno, "the question is empty")
        if kind not in QUESTION_TYPES:
            raise _refusal(path, lineno, f"type {kind!r} is not one of {', '.join(QUESTION_TYPES)}")

        gold = []
        for item in (g.strip() for g in golds.split(",")):
            if not item:
                continue
            parsed = parse_reference(item)
            if parsed is None:
                raise _refusal(path, lineno, f"gold {item!r} is neither a ref_id nor a path")
            gold.append(parsed)
        if kind == "none" and gold:
            raise _refusal(path, lineno, "a `none` question has no gold ids (the book cannot answer it)")
        if kind != "none" and not gold:
            raise _refusal(path, lineno, f"a `{kind}` question needs at least one gold id")
        questions.append(Question(text, gold, kind))
    return questions


def _prefix_and_path(ref_id: str) -> tuple[str | None, str]:
    """`circ92-2025-12:usc17-s107` -> (`circ92-2025-12`, `usc17-s107`); a bare path -> (None, path)."""
    prefix, colon, path = ref_id.rpartition(":")
    return (prefix, path) if colon else (None, ref_id)


def _paths(gold: str, ref_id: str) -> tuple[str, str] | None:
    """(gold's path, ref_id's path) when the two are in the same book and edition, else None. A gold with no
    `book-edition:` prefix is a bare path and matches in any book (one book is registered; the prefix is for
    the second)."""
    gold_prefix, gold_path = _prefix_and_path(gold)
    prefix, path = _prefix_and_path(ref_id)
    if gold_prefix is not None and prefix is not None and gold_prefix != prefix:
        return None
    return gold_path, path


def is_hit(ref_id: str, sub_ref_ids: Sequence[str], gold: str) -> bool:
    """Does a returned chunk (its `ref_id` and the nodes inside it) answer for `gold`?

    Yes when the chunk's `ref_id` is the gold or lies under it (a chunk of a subsection, the gold being its
    section), or the gold is one of the nodes inside the chunk. Not the other way round: the chunk of a
    section is no hit for one of its subsections unless that subsection is inside the chunk. Ids are compared
    like with like: same book and edition; a gold given as a bare path (`usc17-s107`) matches in any."""
    if (paths := _paths(gold, ref_id)) and is_under(paths[1], paths[0]):
        return True
    return any((paths := _paths(gold, sub)) and paths[0] == paths[1] for sub in sub_ref_ids)


def score_ranked(ranked: list[tuple[str, list[str]]], gold: list[str]) -> dict:
    """Recall@5, Recall@10, the reciprocal rank and nDCG@10 of one ranked list of `(ref_id, sub_ref_ids)`.

    - `recall@k`: the share of the gold ids some chunk in the top k is a hit for.
    - `rr`: 1 / the rank of the first chunk that is a hit for any gold, 0 when none is. Not cut at 10: the mean
      of it over the questions is the MRR.
    - `ndcg@10`: binary relevance, and a gold is counted ONCE: a chunk gains 1 at its rank when it is a hit for
      a gold no earlier chunk was, so a second chunk of the same gold adds nothing. The ideal list puts one new
      gold at each of the first min(len(gold), 10) ranks. A chunk covering two golds still gains 1, which keeps
      the score at most 1 but means one chunk that is the whole answer to a two-gold question scores below 1.

    A gold listed twice counts once. ValueError on no gold: there is nothing to score."""
    gold = list(dict.fromkeys(gold))
    if not gold:
        raise ValueError("score_ranked needs at least one gold id: a question with none is not scored this way")

    def covered(top: list[tuple[str, list[str]]]) -> set[str]:
        return {g for g in gold if any(is_hit(ref_id, subs, g) for ref_id, subs in top)}

    scores = {f"recall@{k}": len(covered(ranked[:k])) / len(gold) for k in _CUTOFFS}

    credited: set[str] = set()
    first_rank = 0
    dcg = 0.0
    for rank, (ref_id, subs) in enumerate(ranked, start=1):
        new = {g for g in gold if g not in credited and is_hit(ref_id, subs, g)}
        if not new:
            continue
        credited |= new
        first_rank = first_rank or rank
        if rank <= max(_CUTOFFS):
            dcg += 1 / math.log2(rank + 1)
    ideal = sum(1 / math.log2(rank + 1) for rank in range(1, min(len(gold), max(_CUTOFFS)) + 1))

    scores["rr"] = 1 / first_rank if first_rank else 0.0
    scores["ndcg@10"] = dcg / ideal
    return scores


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _search_record(question: Question, hits: list[BookHit], kind: str) -> dict:
    ranked = [(h.chunk.ref_id, h.chunk.sub_ref_ids) for h in hits]
    scores = score_ranked(ranked, question.gold)
    return {
        "question": question.text,
        "type": kind,
        "gold": question.gold,
        "returned": [ref_id for ref_id, _ in ranked],
        **scores,
        "ok": scores["recall@10"] == 1.0,
    }


def _citation_record(question: Question, found: LookupResult | None) -> dict:
    shown = found.shown if found else []
    hit = any(is_hit(c.ref_id, c.sub_ref_ids, g) for c in shown for g in question.gold)
    return {
        "question": question.text,
        "type": "citation",
        "gold": question.gold,
        "returned": [c.ref_id for c in shown],
        "ok": hit,
    }


def _none_record(question: Question, hits: list[BookHit]) -> dict:
    return {
        "question": question.text,
        "type": "none",
        "gold": [],
        "returned": [h.chunk.ref_id for h in hits],
        "ok": not hits,
    }


def run(
    questions: list[Question],
    search: Callable[..., list[BookHit]] = search_book_chunks,
    lookup: Callable[[str], LookupResult | None] = lookup_book_chunks,
) -> dict:
    """Ask every question of its own tool and score the answers.

    Returns `n`, `counts` (questions per type), the means over the `search` questions (`recall@5`,
    `recall@10`, `mrr`, `ndcg@10`), the same four over the `history` questions with a `history_` prefix,
    `citation_hit_rate` (over `citation`), `not_found_rate` (over `none`:
    higher is better) and `questions`, one record per question with what came back and whether it was right
    (`ok`). A metric over no questions is 0.0; `counts` says whether that means anything."""
    records = []
    for question in questions:
        if question.type == "search":
            records.append(_search_record(question, search(question.text), "search"))
        elif question.type == "history":
            records.append(_search_record(question, search(question.text, include_history_notes=True), "history"))
        elif question.type == "citation":
            records.append(_citation_record(question, lookup(question.text)))
        elif question.type == "none":
            records.append(_none_record(question, search(question.text)))
        else:
            raise ValueError(f"question {question.text!r} has type {question.type!r}, not one of {QUESTION_TYPES}")

    def of(kind: str) -> list[dict]:
        return [r for r in records if r["type"] == kind]

    searched = of("search")
    history = of("history")
    return {
        "n": len(records),
        "counts": {kind: len(of(kind)) for kind in QUESTION_TYPES},
        "recall@5": _mean([r["recall@5"] for r in searched]),
        "recall@10": _mean([r["recall@10"] for r in searched]),
        "mrr": _mean([r["rr"] for r in searched]),
        "ndcg@10": _mean([r["ndcg@10"] for r in searched]),
        "history_recall@5": _mean([r["recall@5"] for r in history]),
        "history_recall@10": _mean([r["recall@10"] for r in history]),
        "history_mrr": _mean([r["rr"] for r in history]),
        "history_ndcg@10": _mean([r["ndcg@10"] for r in history]),
        "citation_hit_rate": _mean([float(r["ok"]) for r in of("citation")]),
        "not_found_rate": _mean([float(r["ok"]) for r in of("none")]),
        "questions": records,
    }


# (heading, {question type: key in `run`'s result}); the cells of a type a column has no key for print "-".
_COLUMNS = (
    ("recall@5", {"search": "recall@5", "history": "history_recall@5"}),
    ("recall@10", {"search": "recall@10", "history": "history_recall@10"}),
    ("mrr", {"search": "mrr", "history": "history_mrr"}),
    ("ndcg@10", {"search": "ndcg@10", "history": "history_ndcg@10"}),
    ("hit_rate", {"citation": "citation_hit_rate"}),
    ("not_found", {"none": "not_found_rate"}),
)


def format_report(result: dict, top_k: int) -> str:
    """The questions and the `BOOK_SEARCH_TOP_K` they were searched with, the per-type table (n, then the
    metrics each type has), then every question that came out wrong."""
    lines = [f"{result['n']} questions, BOOK_SEARCH_TOP_K={top_k}", ""]
    lines.append(f"{'type':<9}{'n':>5}" + "".join(f"{heading:>11}" for heading, _ in _COLUMNS))
    for kind in QUESTION_TYPES:
        n = result["counts"][kind]
        cells = (f"{result[keys[kind]]:.3f}" if n and kind in keys else "-" for _, keys in _COLUMNS)
        lines.append(f"{kind:<9}{n:>5}" + "".join(f"{cell:>11}" for cell in cells))

    misses = [r for r in result["questions"] if not r["ok"]]
    if misses:
        lines += ["", f"{len(misses)} missed:"]
        for r in misses:
            lines.append(f"  [{r['type']}] {r['question']}")
            if r["gold"]:
                lines.append(f"      gold:     {', '.join(r['gold'])}")
            lines.append(f"      returned: {', '.join(r['returned'][:10]) or '(nothing)'}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals.books.run_retrieval", description="Score book retrieval against a question set."
    )
    parser.add_argument("questions", type=Path, help="question | gold ref_ids | type, one per line")
    opts = parser.parse_args(argv)

    top_k = get_settings().book_search_top_k
    needed = max(_CUTOFFS)
    if top_k < needed:
        print(
            f"refused: BOOK_SEARCH_TOP_K is {top_k}, so search returns at most {top_k} chunks and "
            f"Recall@{needed} and nDCG@{needed} would be computed over {top_k} results under a label that says "
            f"{needed}. Run with BOOK_SEARCH_TOP_K={needed} (or more): "
            f"BOOK_SEARCH_TOP_K={needed} uv run python -m evals.books.run_retrieval {opts.questions}",
            file=sys.stderr,
        )
        return 2

    try:
        questions = load_questions(opts.questions)
    except (OSError, ValueError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    if not questions:
        print(f"no questions in {opts.questions}")
        return 0
    print(format_report(run(questions), top_k))
    return 0


if __name__ == "__main__":
    sys.exit(main())
