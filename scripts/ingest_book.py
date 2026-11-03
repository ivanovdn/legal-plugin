"""Admin command: ingest one registered reference book into the book collection (spec D10).

    uv run python -m scripts.ingest_book <book_id> <pdf_path> [--dry-run]

It refuses an unregistered id and a PDF that is not the registered edition (exit 2), parses and chunks
the book, and builds the report (ingest/books/report.py). With --dry-run it writes the chunk list, the
report and a 20-chunk sample to data/deploy-logs/ and stops: nothing reaches Qdrant. Without it, a book
that does not clear the report's bar is refused (exit 1, ruling 15); one that does replaces the book's
points for this edition: the collection if missing, then EVERY chunk embedded (truncation off) and each
vector checked against QDRANT_VECTOR_DIM, and only then the old points deleted and the new ones
upserted. Embedding first means an Ollama failure, a chunk past the model's window or a vector of the
wrong size leaves the previous ingest as it was (ruling R16a). The command then checks that the
collection holds exactly the chunks it wrote (exit 1 if not).

Exit codes: 0 done, 1 the bar failed or the ingest check failed, 2 refused.
"""
import argparse
import json
import logging
import secrets
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from config import get_settings
from ingest.books import parse_book
from ingest.books.chunker import chunk_tree
from ingest.books.models import BookChunk
from ingest.books.registry import BookEntry, PdfHashMismatch, UnknownBook, get_book, verify_pdf
from ingest.books.report import build_report, chunks_tsv, sample_chunks
from ingest.books.tokens import TokenCounter, ollama_token_counter
from rag.book_store import count_book_points, delete_book_edition, ensure_book_collection, upsert_book_chunks
from rag.embeddings import EmbeddingInputTooLong, embed_texts

logger = logging.getLogger(__name__)

# Dry-run files go where git and the backend image leave them out (CLAUDE.md rule 7).
DEPLOY_LOGS_DIR = Path("data/deploy-logs")
EMBED_BATCH = 32
COUNT_PROGRESS_EVERY = 250  # requests to the token counter between progress lines
EMBED_PROGRESS_EVERY = 20  # embedding batches between progress lines


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def _with_progress(count: TokenCounter) -> Callable[[str], int | None]:
    """The counter, logging every COUNT_PROGRESS_EVERY requests: a whole book asks it about a thousand times,
    for about a minute. The count is requests, repeats included: the cache sits inside the real counter."""
    started = time.monotonic()
    asked = 0

    def counted(text: str) -> int | None:
        nonlocal asked
        asked += 1
        if asked % COUNT_PROGRESS_EVERY == 0:
            logger.info("token counter asked %d times so far (%.0f s; repeats included)",
                        asked, time.monotonic() - started)
        return count(text)

    return counted


def _kinds(report: dict) -> str:
    return ", ".join(f"{kind} {n}" for kind, n in report["chunks_by_kind"].items())


def _pct(rate: float | None) -> str:
    return "n/a" if rate is None else f"{rate:.1%}"


def _bar_details(report: dict) -> dict[str, str]:
    """For each bar item, the numbers a reader needs to see why it holds or not."""
    expected, markers = report["bar_expected"], report["markers"]
    return {
        "title17_sections": f"{report['title17_sections']} section nodes (expected {expected['title17_sections']})",
        "markers": (
            f"{markers['total']} markers (expected {expected['markers']}), {markers['linked']} linked, "
            f"{len(markers['scope_mismatches'])} scopes where markers and notes differ"
            + "".join(f" [{m['scope']}]" for m in markers["scope_mismatches"])
        ),
        "appendices": f"{len(report['appendices'])} appendices (expected {expected['appendices']}) with their source types",
        "out_of_sequence": f"{len(report['out_of_sequence'])} label lines no rule or override placed"
        + "".join(f"\n      {line}" for line in report["out_of_sequence"][:10]),
        "over_budget": f"{len(report['over_budget'])} chunks over the {report['budget']}-token budget"
        + "".join(f"\n      {c['chunk_key']}: {c['tokens']}" for c in report["over_budget"][:10]),
        "overrides_unused": f"{len(report['overrides_unused'])} overrides nothing consumed"
        + "".join(f"\n      {override_id}" for override_id in report["overrides_unused"][:10]),
        "hyphens_undecided": f"{len(report['hyphens_undecided'])} line-end hyphens no evidence or override decided"
        + "".join(f"\n      {hyphen}" for hyphen in report["hyphens_undecided"][:10]),
    }


def _print_bar(report: dict, *, failed_only: bool = False, file: TextIO | None = None) -> None:
    details = _bar_details(report)
    for name, ok in report["bar"].items():
        if failed_only and ok:
            continue
        print(f"  {'PASS' if ok else 'FAIL'}  {name}: {details[name]}", file=file)


def _print_summary(report: dict, files: list[Path], seed: int) -> None:
    xref = report["xref_resolution"]
    t17 = xref["title17"]
    print(f"{report['book']} {report['edition']}: {report['chunks']} chunks ({_kinds(report)})")
    print(f"largest chunk {report['largest_chunk_tokens']} tokens ({report['largest_chunk_key']}), "
          f"budget {report['budget']}")
    print("bar:")
    _print_bar(report)
    print(f"xref resolution: Title 17 targets {_pct(t17['rate'])} ({t17['resolved']}/{t17['total']}), "
          f"all targets {_pct(xref['rate'])} ({xref['resolved']}/{xref['total']}), "
          f"{len(xref['misses'])} misses (listed in the report; reported, not gated)")
    print(f"sample: seed {seed}, {len(report['sample']['chunk_keys'])} chunks to check against the printed page")
    for path in files:
        print(f"  {path}")
    print(f"bar passed: {'yes' if report['bar_passed'] else 'NO'}")


def _write_dry_run_files(book: BookEntry, chunks: list[BookChunk], report: dict) -> list[Path]:
    """<book>-<edition>-<UTC stamp>-{chunks.jsonl, chunks.tsv, report.json, sample20.tsv} in DEPLOY_LOGS_DIR."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    DEPLOY_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    stem = DEPLOY_LOGS_DIR / f"{book.ref_prefix}-{stamp}"
    sample = sample_chunks(chunks, report["sample"]["seed"])
    files = {
        "chunks.jsonl": "".join(chunk.model_dump_json() + "\n" for chunk in chunks),
        "chunks.tsv": chunks_tsv(chunks, book),
        "report.json": json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        "sample20.tsv": chunks_tsv(sample, book, with_text=True),
    }
    paths = []
    for suffix, content in files.items():
        path = Path(f"{stem}-{suffix}")
        path.write_text(content, encoding="utf-8")
        paths.append(path)
    return paths


def _dry_run(book: BookEntry, chunks: list[BookChunk], report: dict) -> int:
    seed = secrets.randbelow(2**32)
    report["sample"] = {"seed": seed, "chunk_keys": [c.chunk_key for c in sample_chunks(chunks, seed)]}
    files = _write_dry_run_files(book, chunks, report)
    _print_summary(report, files, seed)
    return 0 if report["bar_passed"] else 1


def _embed_all(chunks: list[BookChunk]) -> list[list[float]] | None:
    """One vector per chunk, from the text that is stored with it, embedded EMBED_BATCH at a time.
    None (after saying why) when a chunk is longer than the embedding model's window: nothing is cut.
    Nothing is deleted yet, so the previous ingest is untouched."""
    vectors: list[list[float]] = []
    for number, start in enumerate(range(0, len(chunks), EMBED_BATCH), start=1):
        batch = chunks[start:start + EMBED_BATCH]
        try:
            vectors += embed_texts([chunk.text for chunk in batch])
        except EmbeddingInputTooLong as exc:
            _err(f"failed: chunk {batch[exc.index].chunk_key} ({exc.chars} chars) is longer than the embedding "
                 "model's window and was not cut. Nothing was deleted: this edition's earlier points are "
                 "untouched. Fix the chunking, then run the ingest again.")
            return None
        if number % EMBED_PROGRESS_EVERY == 0:
            logger.info("embedded %d of %d chunks", len(vectors), len(chunks))
    return vectors


def _wrong_vectors(chunks: list[BookChunk], vectors: list[list[float]], dim: int) -> str | None:
    """Why these vectors cannot be written (one per chunk, each QDRANT_VECTOR_DIM long), or None."""
    if len(vectors) != len(chunks):
        return f"the embedding model returned {len(vectors)} vectors for {len(chunks)} chunks"
    for chunk, vector in zip(chunks, vectors):
        if len(vector) != dim:
            return (f"the embedding model returned a vector of {len(vector)} numbers for chunk "
                    f"{chunk.chunk_key}, but QDRANT_VECTOR_DIM is {dim}")
    return None


def _ingest(book: BookEntry, chunks: list[BookChunk], report: dict, started: float) -> int:
    ensure_book_collection()
    # Embed everything before the edition's points are deleted: a failure from here to the delete leaves
    # the previous ingest as it was.
    vectors = _embed_all(chunks)
    if vectors is None:
        return 1
    if problem := _wrong_vectors(chunks, vectors, get_settings().qdrant_vector_dim):
        _err(f"failed: {problem}. Nothing was deleted or written: this edition's earlier points are untouched. "
             "Set QDRANT_VECTOR_DIM to the embedding model's dimension (a collection made with another size "
             "has to be recreated), then run the ingest again.")
        return 1
    delete_book_edition(book.id, book.edition_id)
    upsert_book_chunks(chunks, vectors)
    stored = count_book_points(book.id, book.edition_id)
    if stored != len(chunks):
        _err(f"failed: the collection holds {stored} points for {book.id} {book.edition_id} after the ingest, "
             f"expected {len(chunks)} (one per chunk).")
        return 1
    logger.info(
        "ingested %s %s: %d chunks (%s); largest chunk %d tokens; %.1f s",
        book.id, book.edition_id, len(chunks), _kinds(report), report["largest_chunk_tokens"],
        time.monotonic() - started,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.ingest_book", description="Ingest one registered reference book."
    )
    parser.add_argument("book_id", help="a registered book (ingest/books/registry.py)")
    parser.add_argument("pdf_path", type=Path, help="the book's PDF: the registered edition, byte for byte")
    parser.add_argument("--dry-run", action="store_true", help="write the chunk list and report, touch nothing else")
    opts = parser.parse_args(argv)
    logger.info("ingest_book %s %s%s", opts.book_id, opts.pdf_path, " (dry run)" if opts.dry_run else "")

    try:
        book = get_book(opts.book_id)
    except UnknownBook as exc:
        _err(f"refused: {exc}")
        return 2
    if not opts.pdf_path.is_file():
        _err(f"refused: there is no PDF at {opts.pdf_path}")
        return 2
    try:
        verify_pdf(book, opts.pdf_path)
    except PdfHashMismatch as exc:
        _err(f"refused: {exc}")
        return 2

    settings = get_settings()
    if not opts.dry_run and settings.qdrant_vector_dim == 0:
        _err("refused: QDRANT_VECTOR_DIM is 0, so the book collection's dense vector has no size. Set it to the "
             "embedding model's dimension in .env (the value the other collections were made with), then run again.")
        return 1

    started = time.monotonic()
    logger.info("parsing %s %s from %s", book.id, book.edition_id, opts.pdf_path)
    parsed = parse_book(book, opts.pdf_path)
    logger.info("parsed in %.0f s; counting tokens to chunk", time.monotonic() - started)
    budget = settings.book_chunk_max_tokens
    chunks = chunk_tree(parsed.tree, budget=budget, count_tokens=_with_progress(ollama_token_counter()))
    report = build_report(parsed, chunks, budget=budget)

    if opts.dry_run:
        return _dry_run(book, chunks, report)
    if not report["bar_passed"]:
        _err(f"refused: {book.id} {book.edition_id} does not clear the bar, so nothing was ingested "
             f"(run with --dry-run for the full report):")
        _print_bar(report, failed_only=True, file=sys.stderr)
        return 1
    return _ingest(book, chunks, report, started)


def _configure_logging() -> None:
    """Uvicorn is not here to do it: without a handler on the root logger every INFO line is discarded."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per /api/embed request buries ours


def cli() -> None:
    """The entry point of `python -m scripts.ingest_book`: logging first, because a successful ingest's
    only output is its INFO lines (tests/test_ingest_book.py runs this in a fresh interpreter)."""
    _configure_logging()
    sys.exit(main())


if __name__ == "__main__":
    cli()
