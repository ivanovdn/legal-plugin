# tests/test_ingest_book.py
"""The ingest command and its dry-run report (spec D10, the D11 bar; ruling 15).

The parse is faked with a small hand-built ParseResult that meets the bar (152 Title 17 sections, 192
markers with their notes, the 20 appendices), the counter counts words and the embedder returns small
vectors: no Ollama, and no whole-book parse. The registry check runs against the real fixture PDF.
The dry run's real numbers come from running it on the whole book (docs/shipped-log.md, Task 13).
"""
import json
import logging
import os
import random
import re
import subprocess
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

import ingest.books as books
from config import get_settings
from ingest.books import parse_book
from ingest.books.chunker import chunk_tree
from ingest.books.circular92 import ParseResult
from ingest.books.circular92.appendices import APPENDICES
from ingest.books.circular92.text import HyphenDecider
from ingest.books.citations import render_citation
from ingest.books.models import BookChunk, BookTree, Node, TextLine
from ingest.books.overrides import Override, OverrideLedger
from ingest.books.registry import get_book
from ingest.books.report import BAR_EXPECTED, build_report, sample_chunks
from rag.book_store import count_book_points, ensure_book_collection, upsert_book_chunks
from rag.embeddings import EmbeddingInputTooLong
from scripts import ingest_book
from scripts.ingest_book import DEPLOY_LOGS_DIR, main

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOK = get_book("circ92")
BUDGET = 800
DIM = 768
TSV_COLUMNS = ["ref_id", "chunk_key", "label", "pages", "kind", "source_type", "tokens", "xrefs", "citation"]
SCOPE_NOTES = {"Chapter 1": list(range(1, 101)), "Chapter 2": list(range(1, 93))}  # 192 in all


def words(text: str) -> int:
    return len(text.split())


def _line(text: str, page: int) -> TextLine:
    return TextLine(text=text, page=page, para_start=True)


def _section(n: int, **kw) -> Node:
    fields = dict(
        path=f"usc17-s{100 + n}", kind="rule", source_type="usc_title", jurisdiction="US",
        label=f"17 U.S.C. §{100 + n}", heading=f"Heading {n}", scope="Chapter 1",
        lead=[_line(f"The text of section {100 + n}.", 10 + n)],
    )
    fields.update(kw)
    return Node(**fields)


def _appendix(letter: str, source_type: str | None = None) -> Node:
    spec = APPENDICES[letter]
    repealed = letter == "T"
    return Node(
        path=spec.prefix if repealed else f"{spec.prefix}-s1",
        kind="repealed" if repealed else "rule",
        source_type=source_type or spec.source_type, jurisdiction="US",
        label=f"Appendix {letter} section 1", heading=f"Appendix {letter}", scope=f"Appendix {letter}",
        appendix=letter, lead=[_line(f"The text of appendix {letter}.", 400 + ord(letter) - ord("A"))],
    )


def make_parsed(
    *, sections=152, drop_appendix=None, appendix_types=None, out_of_sequence=(), unused=(), undecided=(),
    notes=None, markers=None, xref_nodes=None,
) -> ParseResult:
    """A parse that meets the bar, with whatever the test breaks."""
    roots = [_section(i) for i in range(sections)]
    roots.append(Node(
        path="ch1-n1", kind="history_note", source_type="usc_title", jurisdiction="US",
        label="Chapter 1 n.1", heading="", scope="Chapter 1", lead=[_line("Pub. L. 94-553.", 12)],
        note_number=1, annotates="usc17-s100",
    ))
    types = appendix_types or {}
    roots += [_appendix(letter, types.get(letter)) for letter in APPENDICES if letter != drop_appendix]
    for path, changes in (xref_nodes or {}).items():
        next(r for r in roots if r.path == path).__dict__.update(changes)
    ledger = OverrideLedger([
        Override(id=name, kind="label_level", page=1, match="(a)", value="1", reason="r") for name in unused
    ])
    hyphens = HyphenDecider([], ledger)
    hyphens._undecided.update(dict.fromkeys(undecided))
    return ParseResult(
        tree=BookTree(BOOK, roots), ledger=ledger, hyphens=hyphens,
        out_of_sequence=list(out_of_sequence),
        markers={k: list(v) for k, v in (SCOPE_NOTES if markers is None else markers).items()},
        notes={k: list(v) for k, v in (SCOPE_NOTES if notes is None else notes).items()},
    )


def chunks_of(parsed: ParseResult) -> list[BookChunk]:
    return chunk_tree(parsed.tree, budget=BUDGET, count_tokens=words)


# --- the report and its bar ---------------------------------------------------------------------------


def test_report_bar():
    parsed = make_parsed(xref_nodes={"usc17-s100": {
        "xrefs": ["usc17-s101", "usc17-s999", "pl110-403"], "xrefs_unparsed": ["the Foo Act"],
    }})
    chunks = chunks_of(parsed)
    report = build_report(parsed, chunks, budget=BUDGET)

    assert BAR_EXPECTED == {"title17_sections": 152, "markers": 192, "appendices": 20}
    assert report["title17_sections"] == 152
    assert report["markers"] == {"total": 192, "linked": 192, "scope_mismatches": []}
    assert report["appendices"] == {letter: spec.source_type for letter, spec in APPENDICES.items()}
    assert len(report["appendices"]) == 20
    assert report["out_of_sequence"] == []
    assert report["overrides_unused"] == []
    assert report["hyphens_undecided"] == []
    assert report["over_budget"] == []
    assert report["chunks_by_kind"] == dict(Counter(c.kind for c in chunks))
    assert report["chunks_by_kind"]["history_note"] == 1
    assert report["largest_chunk_tokens"] == max(c.tokens for c in chunks) > 0
    assert report["budget"] == BUDGET and report["chunks"] == len(chunks)
    assert (report["book"], report["edition"]) == ("circ92", "2025-12")
    assert report["bar"] == {
        "title17_sections": True, "markers": True, "appendices": True,
        "out_of_sequence": True, "over_budget": True, "overrides_unused": True, "hyphens_undecided": True,
        "node_citations": True,
    }
    assert report["node_citation_mismatches"] == []
    assert report["bar_passed"] is True


def test_report_xref_resolution_by_target_type():
    """Title 17's rate and all targets' rate, side by side: a whole Public Law target is never a node, so the
    second is low by construction and gates nothing."""
    parsed = make_parsed(xref_nodes={"usc17-s100": {
        "xrefs": ["usc17-s101", "usc17-s999", "pl110-403"], "xrefs_unparsed": ["the Foo Act"],
    }})
    report = build_report(parsed, chunks_of(parsed), budget=BUDGET)
    xref = report["xref_resolution"]
    assert (xref["total"], xref["resolved"], xref["rate"]) == (4, 1, 0.25)
    assert xref["title17"] == {"total": 2, "resolved": 1, "rate": 0.5}
    assert xref["misses"] == [
        ["usc17-s100", "usc17-s999"], ["usc17-s100", "pl110-403"], ["usc17-s100", "unparsed: the Foo Act"],
    ]
    assert report["bar_passed"] is True  # reported, never gated


def test_report_xref_rate_without_references():
    parsed = make_parsed()
    xref = build_report(parsed, chunks_of(parsed), budget=BUDGET)["xref_resolution"]
    assert xref["total"] == 0 and xref["rate"] is None and xref["title17"]["rate"] is None


@pytest.mark.parametrize("item, build", [
    ("title17_sections", lambda: make_parsed(sections=151)),
    ("title17_sections", lambda: make_parsed(sections=153)),
    ("markers", lambda: make_parsed(markers={"Chapter 1": list(range(1, 100)), "Chapter 2": list(range(1, 93))},
                                    notes={"Chapter 1": list(range(1, 100)), "Chapter 2": list(range(1, 93))})),
    ("markers", lambda: make_parsed(markers={"Chapter 1": list(range(1, 102)), "Chapter 2": list(range(1, 93))},
                                    notes={"Chapter 1": list(range(1, 102)), "Chapter 2": list(range(1, 93))})),
    ("markers", lambda: make_parsed(notes={"Chapter 1": list(range(1, 100)), "Chapter 2": list(range(1, 93))})),
    ("markers", lambda: make_parsed(
        markers={"Chapter 1": [*range(1, 99), 100, 99], "Chapter 2": list(range(1, 93))})),
    ("appendices", lambda: make_parsed(drop_appendix="K")),
    ("appendices", lambda: make_parsed(appendix_types={"N": "uncodified_act"})),
    ("out_of_sequence", lambda: make_parsed(out_of_sequence=["p.12 (a) a label no rule placed"])),
    ("overrides_unused", lambda: make_parsed(unused=["stale-override"])),
    ("hyphens_undecided", lambda: make_parsed(undecided=["p.5 spac-|es"])),
], ids=["sections-151", "sections-153", "markers-191", "markers-193", "marker-missing-its-note", "markers-out-of-order",
        "appendix-missing", "appendix-wrong-source-type", "out-of-sequence", "override-unused", "hyphen-undecided"])
def test_each_bar_item_fails_alone(item, build):
    parsed = build()
    report = build_report(parsed, chunks_of(parsed), budget=BUDGET)
    assert report["bar"][item] is False
    assert [name for name, ok in report["bar"].items() if not ok] == [item]
    assert report["bar_passed"] is False


def test_the_appendix_count_is_20_even_when_the_table_and_the_tree_agree_on_fewer(monkeypatch):
    """The edition table and the tree could both lose an appendix together: the bar's own count still holds."""
    short = {letter: spec for letter, spec in APPENDICES.items() if letter != "T"}
    monkeypatch.setattr("ingest.books.report.APPENDICES", short)
    parsed = make_parsed(drop_appendix="T")
    report = build_report(parsed, chunks_of(parsed), budget=BUDGET)
    assert len(report["appendices"]) == 19
    assert report["bar"]["appendices"] is False


def test_report_names_the_scope_whose_markers_and_notes_disagree():
    parsed = make_parsed(markers={"Chapter 1": [*range(1, 99), 100, 99], "Chapter 2": SCOPE_NOTES["Chapter 2"]})
    report = build_report(parsed, chunks_of(parsed), budget=BUDGET)
    assert report["markers"]["total"] == 192
    assert [m["scope"] for m in report["markers"]["scope_mismatches"]] == ["Chapter 1"]
    mismatch = report["markers"]["scope_mismatches"][0]
    assert mismatch["markers"][-2:] == [100, 99] and mismatch["notes"][-2:] == [99, 100]


def test_report_lists_the_unused_overrides_and_undecided_hyphens():
    parsed = make_parsed(unused=["a-stale-one"], undecided=["p.5 spac-|es", "p.9 twenty-|eight"])
    report = build_report(parsed, chunks_of(parsed), budget=BUDGET)
    assert report["overrides_unused"] == ["a-stale-one"]
    assert report["hyphens_undecided"] == ["p.5 spac-|es", "p.9 twenty-|eight"]
    assert report["bar"]["overrides_unused"] is False and report["bar"]["hyphens_undecided"] is False
    assert report["bar_passed"] is False


def _repaged(chunk: BookChunk, first: int, last: int) -> BookChunk:
    return chunk.model_copy(update={"ancestor_pages": [(first, last), *chunk.ancestor_pages[1:]]})


@pytest.mark.parametrize("corrupt", [
    lambda chunks: [_repaged(chunks[3], 1, 999) if i == 3 else c for i, c in enumerate(chunks)],
    lambda chunks: [c.model_copy(update={"label": "17 U.S.C. §999"}) if i == 3 else c for i, c in enumerate(chunks)],
    lambda chunks: [c.model_copy(update={"kind": "definition"}) if i == 3 else c for i, c in enumerate(chunks)],
    lambda chunks: [c for i, c in enumerate(chunks) if i != 3],
], ids=["wrong-pages", "wrong-label", "wrong-kind", "node-held-by-no-chunk"])
def test_a_node_the_chunks_would_cite_wrongly_fails_the_bar_alone(corrupt):
    """Bar item 8 (final review C1): every node of the tree, resolved over the chunk list exactly as
    fetch_nodes resolves it, has the tree's label, kind, source type, appendix and pages. A citation of it
    is rendered from that, so a node it gets wrong reaches the attorney as a verified citation."""
    parsed = make_parsed()
    chunks = chunks_of(parsed)
    node = next(n for n in parsed.tree.walk() if f"{BOOK.ref_prefix}:{n.path}" == chunks[3].ref_id)

    report = build_report(parsed, corrupt(chunks), budget=BUDGET)

    assert [name for name, ok in report["bar"].items() if not ok] == ["node_citations"]
    assert report["bar_passed"] is False
    (mismatch,) = report["node_citation_mismatches"]
    assert mismatch["ref_id"] == chunks[3].ref_id
    assert mismatch["tree"] == {"label": node.label, "kind": node.kind, "source_type": node.source_type,
                                "appendix": node.appendix, "pages": list(node.pages())}
    assert mismatch["chunks"] != mismatch["tree"]


@pytest.mark.parametrize("tokens", [BUDGET + 1, -1], ids=["over-the-budget", "over-the-embedding-window"])
def test_a_chunk_over_the_budget_fails_the_bar(tokens):
    """-1 is the counter's answer for a text past the embedding window (R4): over budget, not "smallest"."""
    parsed = make_parsed()
    chunks = chunks_of(parsed)
    chunks[7].tokens = tokens
    report = build_report(parsed, chunks, budget=BUDGET)
    assert report["over_budget"] == [{"chunk_key": chunks[7].chunk_key, "tokens": tokens}]
    assert report["bar"]["over_budget"] is False and report["bar_passed"] is False
    # The largest chunk is the largest count there is: one past the window has none, so it is not "largest".
    others = max(c.tokens for c in chunks if c is not chunks[7])
    assert report["largest_chunk_tokens"] == (tokens if tokens > 0 else others)


def test_a_chunk_at_the_budget_is_within_it():
    parsed = make_parsed()
    chunks = chunks_of(parsed)
    chunks[0].tokens = BUDGET
    report = build_report(parsed, chunks, budget=BUDGET)
    assert report["over_budget"] == [] and report["largest_chunk_tokens"] == BUDGET


# --- the 20-chunk sample --------------------------------------------------------------------------------


def test_sample_is_seeded_sorted_and_twenty():
    chunks = chunks_of(make_parsed())
    assert len(chunks) > 100
    sample = sample_chunks(chunks, seed=42)
    assert len(sample) == 20
    expected = sorted(random.Random(42).sample(range(len(chunks)), 20))
    assert [c.chunk_key for c in sample] == [chunks[i].chunk_key for i in expected]
    assert [c.chunk_key for c in sample_chunks(chunks, seed=42)] == [c.chunk_key for c in sample]
    assert [c.chunk_key for c in sample_chunks(chunks, seed=43)] != [c.chunk_key for c in sample]


def test_sample_of_a_short_book_is_all_of_it():
    chunks = chunks_of(make_parsed(sections=5, drop_appendix=None))[:7]
    assert [c.chunk_key for c in sample_chunks(chunks, seed=1)] == [c.chunk_key for c in chunks]


# --- refusals ----------------------------------------------------------------------------------------


@pytest.fixture
def no_parse(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("the book was parsed")
    monkeypatch.setattr(ingest_book, "parse_book", boom)


def test_unregistered_book_refused(capsys, circ92_pdf, no_parse):
    assert main(["nope", str(circ92_pdf)]) == 2
    assert "nope" in capsys.readouterr().err


def test_hash_mismatch_refused(capsys, tmp_path, no_parse):
    other = tmp_path / "other.pdf"
    other.write_bytes(b"%PDF-1.4 not circular 92")
    assert main(["circ92", str(other)]) == 2
    err = capsys.readouterr().err
    assert "sha256" in err and "circ92" in err


def test_missing_pdf_refused(capsys, tmp_path, no_parse):
    assert main(["circ92", str(tmp_path / "absent.pdf")]) == 2
    assert "absent.pdf" in capsys.readouterr().err


def test_parse_book_dispatches_on_the_front_end(monkeypatch, circ92_pdf):
    sentinel = object()
    calls = []
    monkeypatch.setattr(books, "parse_circular92", lambda pdf, book: calls.append((pdf, book)) or sentinel)
    assert parse_book(BOOK, circ92_pdf) is sentinel
    assert calls == [(circ92_pdf, BOOK)]
    with pytest.raises(ValueError, match="no-such-front-end"):
        parse_book(replace(BOOK, front_end="no-such-front-end"), circ92_pdf)


# --- the command ---------------------------------------------------------------------------------------


@pytest.fixture
def settings_env():
    """Set settings through the environment, and clear the cache after the environment is put back."""
    with pytest.MonkeyPatch.context() as env:
        def put(**values):
            for key, value in values.items():
                env.setenv(key.upper(), str(value))
            get_settings.cache_clear()
        put(book_chunk_max_tokens=BUDGET)
        yield put
    get_settings.cache_clear()


@pytest.fixture
def fake_counter(monkeypatch):
    monkeypatch.setattr(ingest_book, "ollama_token_counter", lambda: words)


@pytest.fixture
def fake_parse(monkeypatch, fake_counter, settings_env):
    parsed = make_parsed()
    monkeypatch.setattr(ingest_book, "parse_book", lambda book, pdf: parsed)
    return parsed


@pytest.fixture
def fake_parse_failing(monkeypatch, fake_counter, settings_env):
    parsed = make_parsed(out_of_sequence=["p.12 (a) a label no rule placed"])
    monkeypatch.setattr(ingest_book, "parse_book", lambda book, pdf: parsed)
    return parsed


@pytest.fixture
def no_qdrant(monkeypatch):
    """Anything the command would do to Qdrant, or to the embedder that feeds it, fails the test."""
    def boom(*args, **kwargs):
        raise AssertionError("the command reached Qdrant or the embedder")
    for name in ("ensure_book_collection", "delete_book_edition", "upsert_book_chunks", "count_book_points",
                 "embed_texts"):
        monkeypatch.setattr(ingest_book, name, boom)


@pytest.fixture
def deploy_logs(monkeypatch, tmp_path):
    path = tmp_path / "data" / "deploy-logs"  # not there yet: the command makes it
    monkeypatch.setattr(ingest_book, "DEPLOY_LOGS_DIR", path)
    return path


def test_the_dry_run_writes_to_data_deploy_logs():
    """CLAUDE.md rule 7: a working file goes where the image and git leave it out."""
    assert DEPLOY_LOGS_DIR.as_posix() == "data/deploy-logs"


def test_dry_run_writes_files(circ92_pdf, deploy_logs, fake_parse, no_qdrant, capsys):
    assert main(["circ92", str(circ92_pdf), "--dry-run"]) == 0

    stamp = r"circ92-2025-12-\d{8}T\d{6}Z"
    names = sorted(p.name for p in deploy_logs.iterdir())
    assert len(names) == 4
    prefixes = {re.fullmatch(rf"({stamp})-(chunks\.jsonl|chunks\.tsv|report\.json|sample20\.tsv)", n) for n in names}
    assert None not in prefixes
    assert len({m.group(1) for m in prefixes}) == 1  # one run, one stamp
    stem = prefixes.pop().group(1)

    chunks = chunks_of(fake_parse)
    rows = [json.loads(line) for line in (deploy_logs / f"{stem}-chunks.jsonl").read_text().splitlines()]
    assert [BookChunk.model_validate(row).chunk_key for row in rows] == [c.chunk_key for c in chunks]

    tsv = (deploy_logs / f"{stem}-chunks.tsv").read_text().splitlines()
    assert tsv[0].split("\t") == TSV_COLUMNS
    assert len(tsv) == len(chunks) + 1
    first = dict(zip(TSV_COLUMNS, tsv[1].split("\t")))
    c = chunks[0]
    assert first == {
        "ref_id": c.ref_id, "chunk_key": c.chunk_key, "label": c.label, "pages": str(c.page_start),
        "kind": c.kind, "source_type": c.source_type, "tokens": str(c.tokens), "xrefs": "",
        "citation": render_citation(
            label=c.label, kind=c.kind, source_type=c.source_type, appendix=c.appendix,
            page_start=c.page_start, page_end=c.page_end, book=BOOK),
    }

    report = json.loads((deploy_logs / f"{stem}-report.json").read_text())
    assert report["bar_passed"] is True and report["chunks"] == len(chunks)
    seed = report["sample"]["seed"]
    assert isinstance(seed, int)
    drawn = [c.chunk_key for c in sample_chunks(chunks, seed)]
    assert report["sample"]["chunk_keys"] == drawn  # the stored seed re-draws the stored sample

    sample = (deploy_logs / f"{stem}-sample20.tsv").read_text().splitlines()
    assert sample[0].split("\t") == [*TSV_COLUMNS, "text"]
    assert [row.split("\t")[1] for row in sample[1:]] == drawn and len(drawn) == 20

    out = capsys.readouterr().out
    assert f"seed {seed}" in out and "bar passed" in out.lower()
    for name in report["bar"]:
        assert name in out


def test_chunk_rows_in_the_tsv_carry_pages_and_xrefs(circ92_pdf, deploy_logs, monkeypatch, fake_counter, settings_env,
                                                     no_qdrant):
    parsed = make_parsed(xref_nodes={"usc17-s100": {"xrefs": ["usc17-s101", "pl110-403"]}})
    parsed.tree.nodes[0].lead.append(_line("It runs onto the next page.", 11))  # 10 -> 11
    monkeypatch.setattr(ingest_book, "parse_book", lambda book, pdf: parsed)
    main(["circ92", str(circ92_pdf), "--dry-run"])
    tsv = next(deploy_logs.glob("*-chunks.tsv")).read_text().splitlines()
    first = dict(zip(TSV_COLUMNS, tsv[1].split("\t")))
    assert first["pages"] == "10-11" and first["xrefs"] == "usc17-s101, pl110-403"


def test_dry_run_exit_is_one_when_the_bar_fails_and_files_still_written(
        circ92_pdf, deploy_logs, fake_parse_failing, no_qdrant, capsys):
    assert main(["circ92", str(circ92_pdf), "--dry-run"]) == 1
    report = json.loads(next(deploy_logs.glob("*-report.json")).read_text())
    assert report["bar_passed"] is False and report["bar"]["out_of_sequence"] is False
    assert len(list(deploy_logs.iterdir())) == 4
    out = capsys.readouterr().out
    assert "FAIL" in out and "out_of_sequence" in out


def test_dry_run_needs_no_vector_dimension(circ92_pdf, deploy_logs, fake_parse, no_qdrant, settings_env):
    settings_env(qdrant_vector_dim=0)
    assert main(["circ92", str(circ92_pdf), "--dry-run"]) == 0


def test_ingest_refuses_when_bar_fails(circ92_pdf, fake_parse_failing, no_qdrant, settings_env, capsys):
    settings_env(qdrant_vector_dim=DIM)
    assert main(["circ92", str(circ92_pdf)]) == 1  # no_qdrant: one touch of Qdrant would raise
    err = capsys.readouterr().err
    assert "out_of_sequence" in err and "--dry-run" in err


def test_ingest_refuses_without_a_vector_dimension_before_parsing(circ92_pdf, no_parse, no_qdrant, settings_env, capsys):
    settings_env(qdrant_vector_dim=0)
    assert main(["circ92", str(circ92_pdf)]) == 1
    assert "QDRANT_VECTOR_DIM" in capsys.readouterr().err


def vector_of(text: str, dim: int = DIM) -> list[float]:
    """A vector the fake embedder returns: the text's length first, so vector i is traceably chunk i's."""
    return [float(len(text))] + [1.0] * (dim - 1)


class Recorder:
    """The Qdrant functions and the embedder, faked in the command's namespace, in the order they are called."""

    def __init__(self, monkeypatch, *, count=None, embed_error_at=None, dim=DIM, drop=0):
        self.calls: list[str] = []
        self.batches: list[list[str]] = []
        self.upserted: tuple[list, list] | None = None
        self._count, self._embed_error_at, self._dim, self._drop = count, embed_error_at, dim, drop
        monkeypatch.setattr(ingest_book, "ensure_book_collection", lambda: self.calls.append("ensure") or True)
        monkeypatch.setattr(ingest_book, "delete_book_edition",
                            lambda book_id, edition_id: self.calls.append(f"delete {book_id} {edition_id}"))
        monkeypatch.setattr(ingest_book, "embed_texts", self._embed)
        monkeypatch.setattr(ingest_book, "upsert_book_chunks", self._upsert)
        monkeypatch.setattr(ingest_book, "count_book_points", self._count_points)

    def _embed(self, texts):
        self.calls.append("embed")
        self.batches.append(list(texts))
        if self._embed_error_at is not None:
            raise EmbeddingInputTooLong(self._embed_error_at, 9999)
        return [vector_of(t, self._dim) for t in texts][self._drop:]

    def _upsert(self, chunks, vectors, **kw):
        self.calls.append("upsert")
        self.upserted = (chunks, vectors)

    def _count_points(self, book_id, edition_id):
        self.calls.append(f"count {book_id} {edition_id}")
        return len(self.upserted[0]) if self._count is None else self._count


def test_ingest_steps_in_order(circ92_pdf, fake_parse, monkeypatch, settings_env, caplog):
    settings_env(qdrant_vector_dim=DIM)
    rec = Recorder(monkeypatch)
    ticks = iter(range(0, 10_000, 3))  # a clock that moves 3 s a look
    monkeypatch.setattr(ingest_book.time, "monotonic", lambda: float(next(ticks)))
    chunks = chunks_of(fake_parse)
    with caplog.at_level(logging.INFO, logger=ingest_book.logger.name):
        assert main(["circ92", str(circ92_pdf)]) == 0

    # R16a: the collection, every chunk embedded, and only then the edition's old points deleted.
    assert rec.calls[0] == "ensure"
    assert set(rec.calls[1:-3]) == {"embed"} and "embed" in rec.calls
    assert rec.calls[-3:] == ["delete circ92 2025-12", "upsert", "count circ92 2025-12"]
    assert [len(b) for b in rec.batches] == [32] * (len(chunks) // 32) + [len(chunks) % 32]
    assert [t for b in rec.batches for t in b] == [c.text for c in chunks]  # the header + body: what is embedded
    up_chunks, up_vectors = rec.upserted
    assert [c.chunk_key for c in up_chunks] == [c.chunk_key for c in chunks]
    assert up_vectors == [vector_of(c.text) for c in chunks]  # vector i belongs to chunk i

    summary = [r.getMessage() for r in caplog.records if "ingested" in r.getMessage()]
    assert len(summary) == 1
    assert "circ92" in summary[0] and "2025-12" in summary[0] and f"{len(chunks)} chunks" in summary[0]
    assert "history_note 1" in summary[0] and f"largest chunk {max(c.tokens for c in chunks)} tokens" in summary[0]
    seconds = re.search(r"; (\d+\.\d) s$", summary[0])
    assert seconds and float(seconds.group(1)) > 0  # the run's own length, not a placeholder


def test_ingest_fails_when_the_point_count_is_not_the_chunk_count(circ92_pdf, fake_parse, monkeypatch, settings_env,
                                                                  capsys):
    settings_env(qdrant_vector_dim=DIM)
    Recorder(monkeypatch, count=len(chunks_of(fake_parse)) - 1)
    assert main(["circ92", str(circ92_pdf)]) == 1
    err = capsys.readouterr().err
    assert str(len(chunks_of(fake_parse))) in err and "point" in err


def test_ingest_stops_on_a_chunk_past_the_embedding_window(circ92_pdf, fake_parse, monkeypatch, settings_env, capsys):
    settings_env(qdrant_vector_dim=DIM)
    rec = Recorder(monkeypatch, embed_error_at=3)
    assert main(["circ92", str(circ92_pdf)]) == 1
    assert rec.upserted is None and not [c for c in rec.calls if c.startswith("delete")]
    assert chunks_of(fake_parse)[3].chunk_key in capsys.readouterr().err  # item 3 of the first batch


def test_ingest_and_reingest(circ92_pdf, book_qdrant, fake_parse, monkeypatch, fake_counter):
    chunks = chunks_of(fake_parse)
    monkeypatch.setattr(ingest_book, "embed_texts",
                        lambda texts: [[1.0 if i == hash_slot(t) else 0.0 for i in range(DIM)] for t in texts])

    assert main(["circ92", str(circ92_pdf)]) == 0
    assert count_book_points("circ92", "2025-12") == len(chunks)

    # A point of this edition the new run does not write (a chunk the cut no longer makes) must go.
    stale = chunks[0].model_copy(update={"chunk_key": f"{chunks[0].ref_id}~stale"})
    upsert_book_chunks([stale], [[1.0] * DIM])
    assert count_book_points("circ92", "2025-12") == len(chunks) + 1

    assert main(["circ92", str(circ92_pdf)]) == 0
    assert count_book_points("circ92", "2025-12") == len(chunks)


def hash_slot(text: str) -> int:
    return sum(map(ord, text)) % DIM


# --- R16a: embed everything before deleting anything -------------------------------------------------------


def _plant_earlier_ingest(fake_parse) -> None:
    """A point of this edition from an earlier ingest, in the real collection."""
    ensure_book_collection()
    first = chunks_of(fake_parse)[0]
    upsert_book_chunks([first.model_copy(update={"chunk_key": f"{first.ref_id}~earlier"})], [[1.0] * DIM])
    assert count_book_points("circ92", "2025-12") == 1


def _embedder(monkeypatch, *, fail_on_batch=None, error=None, dim=DIM, drop=0):
    """The embedder, faked: the Nth call raises `error`, every other returns vectors `dim` long."""
    state = {"calls": 0}

    def embed(texts):
        state["calls"] += 1
        if state["calls"] == fail_on_batch:
            raise error
        return [vector_of(t, dim) for t in texts][drop:]

    monkeypatch.setattr(ingest_book, "embed_texts", embed)
    return state


def test_an_ollama_failure_midway_leaves_the_earlier_ingest_in_place(circ92_pdf, book_qdrant, fake_parse, monkeypatch):
    _plant_earlier_ingest(fake_parse)
    _embedder(monkeypatch, fail_on_batch=2, error=httpx.ConnectError("Ollama is down"))
    with pytest.raises(httpx.ConnectError):
        main(["circ92", str(circ92_pdf)])
    assert count_book_points("circ92", "2025-12") == 1  # the point the delete would have taken


def test_a_chunk_past_the_window_leaves_the_earlier_ingest_in_place(circ92_pdf, book_qdrant, fake_parse, monkeypatch,
                                                                   capsys):
    _plant_earlier_ingest(fake_parse)
    _embedder(monkeypatch, fail_on_batch=3, error=EmbeddingInputTooLong(0, 9999))
    assert main(["circ92", str(circ92_pdf)]) == 1
    assert count_book_points("circ92", "2025-12") == 1
    err = capsys.readouterr().err
    assert chunks_of(fake_parse)[64].chunk_key in err  # item 0 of the third batch
    assert "already deleted" not in err and "untouched" in err


@pytest.mark.parametrize("dim, drop", [(3, 0), (DIM + 1, 0), (DIM, 1)], ids=["too-short", "too-long", "one-short"])
def test_vectors_of_the_wrong_size_or_number_are_refused_before_the_delete(
        circ92_pdf, book_qdrant, fake_parse, monkeypatch, capsys, dim, drop):
    _plant_earlier_ingest(fake_parse)
    _embedder(monkeypatch, dim=dim, drop=drop)
    assert main(["circ92", str(circ92_pdf)]) == 1
    assert count_book_points("circ92", "2025-12") == 1
    err = capsys.readouterr().err
    assert ("QDRANT_VECTOR_DIM" in err) if drop == 0 else ("vectors for" in err)


def test_a_clean_run_replaces_the_earlier_ingest_after_embedding(circ92_pdf, book_qdrant, fake_parse, monkeypatch):
    _plant_earlier_ingest(fake_parse)
    _embedder(monkeypatch)
    assert main(["circ92", str(circ92_pdf)]) == 0
    assert count_book_points("circ92", "2025-12") == len(chunks_of(fake_parse))  # the earlier point is gone


# --- the logging an operator reads ----------------------------------------------------------------------------


def test_the_token_counter_logs_every_250th_request(caplog):
    asked = []

    def stub(text):
        asked.append(text)
        return None if text == "past the window" else len(text)

    counted = ingest_book._with_progress(stub)
    assert ingest_book.COUNT_PROGRESS_EVERY == 250
    with caplog.at_level(logging.INFO, logger=ingest_book.logger.name):
        assert [counted("ab") for _ in range(249)] == [2] * 249
        assert caplog.records == []
        assert counted("past the window") is None  # the 250th: the stub's answer passes through
        assert [r.getMessage() for r in caplog.records if "250 times" in r.getMessage()]
        for _ in range(250):
            counted("ab")
    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == 2 and "500 times" in messages[1]
    assert "repeats included" in messages[0] and "passages" not in messages[0]  # requests, not distinct texts
    assert len(asked) == 500


def test_embedding_logs_its_progress(fake_parse, monkeypatch, caplog):
    chunks = chunks_of(fake_parse)
    monkeypatch.setattr(ingest_book, "embed_texts", lambda texts: [vector_of(t) for t in texts])
    monkeypatch.setattr(ingest_book, "EMBED_PROGRESS_EVERY", 2)
    with caplog.at_level(logging.INFO, logger=ingest_book.logger.name):
        assert len(ingest_book._embed_all(chunks)) == len(chunks)
    batches = -(-len(chunks) // 32)
    assert [r.getMessage() for r in caplog.records] == [  # after the 2nd, 4th and 6th batch
        f"embedded 64 of {len(chunks)} chunks", f"embedded 128 of {len(chunks)} chunks",
        f"embedded {len(chunks)} of {len(chunks)} chunks",
    ] and batches == 6


def _fresh_interpreter(snippet: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", snippet], capture_output=True, text=True, cwd=REPO_ROOT, env=os.environ.copy(),
        timeout=180,
    )


_STUB_MAIN = (
    "import logging\n"
    "import scripts.ingest_book as m\n"
    "def main(argv=None):\n"
    "    m.logger.info('MARKER-INFO')\n"
    "    logging.getLogger('httpx').info('MARKER-HTTPX')\n"
    "    return 0\n"
    "m.main = main\n"
)


def test_the_command_configures_logging_so_info_reaches_stderr():
    """A successful ingest prints nothing but its INFO lines. Run in a fresh interpreter on purpose: under pytest
    the root logger has pytest's handler and caplog forces the level, so a caplog test passes whether or not the
    command configures logging at all (tests/test_logging_config.py)."""
    proc = _fresh_interpreter(_STUB_MAIN + "m.cli()\n")
    assert proc.returncode == 0, proc.stderr
    assert "MARKER-INFO" in proc.stderr
    assert "MARKER-HTTPX" not in proc.stderr  # one httpx line per /api/embed request would bury ours


def test_without_configuring_logging_the_info_lines_are_lost():
    """The control that makes the test above mean something: a fresh interpreter that does not call the command's
    entry point sees no INFO at all (the root logger has no handler)."""
    proc = _fresh_interpreter(_STUB_MAIN + "m.main()\n")
    assert proc.returncode == 0, proc.stderr
    assert "MARKER-INFO" not in proc.stderr


def test_configure_logging_puts_a_handler_on_the_root_at_info_and_quiets_httpx():
    proc = _fresh_interpreter(
        "import json, logging\n"
        "import scripts.ingest_book as m\n"
        "m._configure_logging()\n"
        "root = logging.getLogger()\n"
        "print(json.dumps({'handlers': len(root.handlers), 'level': root.level,\n"
        "                  'httpx': logging.getLogger('httpx').level}))\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {"handlers": 1, "level": logging.INFO, "httpx": logging.WARNING}


def test_run_as_a_module_the_command_configures_logging_and_refuses_an_unknown_book(tmp_path):
    """The `__main__` block itself: it must go through `cli()`, or the INFO line that opens every run is lost."""
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.ingest_book", "no-such-book", str(tmp_path / "x.pdf")],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=180,
    )
    assert proc.returncode == 2
    assert re.search(r" INFO ingest_book no-such-book .*x\.pdf", proc.stderr)
    assert "refused: book 'no-such-book' is not registered" in proc.stderr
