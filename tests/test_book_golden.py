# tests/test_book_golden.py
"""The golden file: chapters 1 and 5 of Circular 92 (Dec. 2025), parsed, as one row per node (spec D11).

`ch1_ch5_parse` (conftest) parses the session's ch.1 + ch.5 lines once. The fixture file was generated
by `python -m ingest.books.golden` and cross-checked against the prototype's chunk list before it was
committed (task 7's report); a change to the parse shows up here as the first row and field it moved.
"""
from pathlib import Path

import pytest

from ingest.books import golden
from ingest.books.circular92 import parse_lines
from ingest.books.golden import GOLDEN_PDF_PAGES, GOLDEN_SCOPES, diff_rows, golden_rows, read_rows
from ingest.books.overrides import load_overrides
from ingest.books.registry import get_book
from ingest.books.tree import xref_resolution

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "books" / "circ92-ch1-ch5.jsonl"
PREFIX = "circ92-2025-12:"


@pytest.fixture(scope="module")
def idx(ch1_ch5_parse):
    return ch1_ch5_parse.tree.index()


@pytest.fixture(scope="module")
def rows(ch1_ch5_parse):
    return {row["ref_id"]: row for row in golden_rows(ch1_ch5_parse.tree, GOLDEN_SCOPES)}


def test_golden_ch1_ch5(ch1_ch5_parse):
    actual = golden_rows(ch1_ch5_parse.tree, GOLDEN_SCOPES)
    assert (difference := diff_rows(read_rows(FIXTURE), actual)) is None, difference


def test_known_bugs_fixed(idx):
    # ch.5 n.8: "section 2323, chapter 113 of title 18" is 18 U.S.C.
    assert "usc18-s2323" in idx["ch5-n8"].xrefs
    assert "usc17-s2323" not in idx["ch5-n8"].xrefs
    # §504(c): "section 45 of the Act entitled “…” (…; 15 U.S.C. 1127)" is the U.S.C. section, and
    # no half-resolved Act reference is left beside it.
    under = list(idx["usc17-s504-c"].walk())
    assert "usc15-s1127" in {target for node in under for target in node.xrefs}
    assert not [raw for node in under for raw in node.xrefs_unparsed if "Act" in raw]
    # §506(a)(3): its list sits at its own level, not one too deep.
    for path in ("a-3-A", "a-3-A-i", "a-3-A-ii", "a-3-B", "a-3-B-i", "a-3-B-ii"):
        assert f"usc17-s506-{path}" in idx


def test_shipped_xref_targets_fix_three_misreadings(idx):
    # §115(d)(1)(C)(iii), p. 76: "clause (i) and clause (ii) of subsection (c)(2)(A)": both are (c)(2)(A)'s.
    # Read alone, clause (i) was §115(d)(1)(C)(i), a node that exists.
    assert idx["usc17-s115-d-1-C-iii"].xrefs[:2] == ["usc17-s115-c-2-A-i", "usc17-s115-c-2-A-ii"]
    assert "usc17-s115-d-1-C-i" not in idx["usc17-s115-d-1-C-iii"].xrefs
    # §121(c), p. 137: the Individuals with Disabilities Education Act's sections; the book prints no location
    assert not [x for x in idx["usc17-s121-c"].xrefs if x.startswith(("usc17-s612", "usc17-s613"))]
    assert "sections 612(a)(23) (C), 613(a)(6)" in idx["usc17-s121-c"].xrefs_unparsed
    # ch.1 n.42, p. 151: the Record Rental Amendment's §4(c); the note prints its number, Pub. L. No. 98-450
    assert "usc17-s4-c" not in idx["ch1-n42"].xrefs
    assert "pl98-450-s4-c" in idx["ch1-n42"].xrefs
    assert not [raw for raw in idx["ch1-n42"].xrefs_unparsed if "4(c)" in raw]


def test_the_whole_parse_uses_every_override_on_its_pages(ch1_ch5_parse, circ92_ch1_ch5_lines):
    # One ledger serves the parse (title17, notes, cross-references, hyphens), so chapter 3's two §304
    # text_owner entries are left unused by chapters 1 and 5, and so is every hyphen entry on another page.
    # Of the three hyphen entries on these pages that are left unused too, the run decides the hyphen by
    # itself: chapters 1 and 5 print "preexisting" and "subparagraphs" solid and never the compound, so only
    # the whole book (which prints "pre-existing" on p. 258 and "sub-paragraphs" on p. 457) needs the entry.
    pages = {line.page for line in circ92_ch1_ch5_lines}
    hyphens = {o.id: o for o in load_overrides(get_book("circ92").overrides_path) if o.kind == "hyphen"}
    assert len(hyphens) == 57
    decided_by_evidence_here = {"hyphen-p9-preexisting", "hyphen-p109-subparagraphs", "hyphen-p155-subparagraphs"}
    other_pages = {i for i, o in hyphens.items() if o.page not in pages}
    assert {o.id for o in ch1_ch5_parse.ledger.unused()} == (
        {"usc17-s304-a-1-B-flush", "usc17-s304-a-1-C-flush"} | other_pages | decided_by_evidence_here
    )
    assert len(other_pages) == 31  # 57 hyphen entries: 26 on these pages (23 used, the 3 above), 31 elsewhere


def test_note_links(idx, rows):
    # ch.5 n.1 sits on the contents entry "509 [Repealed]" (ruling R9), n.8 on the stub's heading.
    assert idx["usc17-s509"].history_notes == ["ch5-n1", "ch5-n8"]
    assert rows[f"{PREFIX}usc17-s509"]["history_notes"] == [f"{PREFIX}ch5-n1", f"{PREFIX}ch5-n8"]


def test_every_note_is_linked_from_what_it_annotates(ch1_ch5_parse, idx):
    notes = [node for node in ch1_ch5_parse.tree.walk() if node.kind == "history_note"]
    assert len(notes) == 77
    for note in notes:
        if note.annotates is not None:
            assert note.path in idx[note.annotates].history_notes
    linked = [path for node in idx.values() for path in node.history_notes]
    assert sorted(linked) == sorted(note.path for note in notes if note.annotates is not None)


def test_effective_date_notes_are_flagged(ch1_ch5_parse, rows):
    flagged = [row["ref_id"] for row in rows.values() if row["effective_date"]]
    assert len(flagged) == 17  # spec D5: 17 of 77 in ch.1 + ch.5, reviewed in the task report
    assert {row["effective_date"] for row in rows.values() if row["kind"] != "history_note"} == {None}


def test_markers_and_notes_per_scope(ch1_ch5_parse):
    assert ch1_ch5_parse.markers["Chapter 1"] == ch1_ch5_parse.notes["Chapter 1"] == list(range(1, 66))
    assert ch1_ch5_parse.markers["Chapter 5"] == ch1_ch5_parse.notes["Chapter 5"] == list(range(1, 13))
    assert ch1_ch5_parse.out_of_sequence == []


def test_rows_are_the_tree_in_document_order(ch1_ch5_parse, rows):
    order = list(rows)
    assert order[0] == f"{PREFIX}usc17-s101"
    # each chapter's notes follow its last section
    assert order.index(f"{PREFIX}ch1-n65") < order.index(f"{PREFIX}usc17-s501")
    assert order[-1] == f"{PREFIX}ch5-n12"
    assert len(order) == sum(1 for _ in ch1_ch5_parse.tree.walk())


def test_row_text_is_the_cleaned_own_text(rows):
    row = rows[f"{PREFIX}usc17-s101-def-work-made-for-hire"]
    # the lead, then the tail after its children: (1), (2), (A), (B) are rows of their own
    assert row["text"].startswith("A “work made for hire” is— by the courts or the Copyright Office.")
    assert "(1) a work prepared" not in row["text"]
    assert rows[f"{PREFIX}usc17-s107"]["text"].startswith("Notwithstanding the provisions of sections 106 and 106A,")


@pytest.fixture(scope="module")
def app_idx(circ92_app_lines):
    """The appendices (PDF 389-481) through the whole back end: the golden file has none of them."""
    return parse_lines(circ92_app_lines, get_book("circ92")).tree.index()


def test_appendix_passages_name_their_own_code(app_idx):
    # An Act's bare "section 301(b)(1)(B)" is its own section (app. F, the PRO-IP Act) ...
    assert "pl110-403-s301-b-1-B" in app_idx["pl110-403-s303-a"].xrefs
    # ... another title's bare section is that title's, and "(relating to …) of title 17" is Title 17 (app. N)
    assert app_idx["usc18-s2319-a"].xrefs[0] == "usc17-s506-a"
    # an appendix note reads its sections in the appendix's code: "amended paragraph 2319(b)(2)" (app. N n.3)
    assert "usc18-s2319-b-2" in app_idx["appN-n3"].xrefs
    assert app_idx["appN-n3"].annotates is not None and "appN-n3" in app_idx[app_idx["appN-n3"].annotates].history_notes


def test_xref_resolution_on_ch1_ch5(ch1_ch5_parse):
    total, resolved, misses = xref_resolution(ch1_ch5_parse.tree)
    assert total == resolved + len(misses)
    assert ("ch5-n8", "pl110-403") in misses  # an Act the tree does not hold is a miss, not an error


# --- the comparison and the writer -----------------------------------------------------------------

def _row(ref_id, **over):
    return {"ref_id": ref_id, "kind": "rule", "label": ref_id, "text": "t", **over}


def test_diff_rows_names_the_first_differing_ref_id_and_field():
    expected = [_row("a"), _row("b"), _row("c")]
    assert diff_rows(expected, [dict(row) for row in expected]) is None
    message = diff_rows(expected, [_row("a"), _row("b", kind="definition", text="u"), _row("c", text="v")])
    assert "'b'" in message and "kind" in message
    assert "'rule'" in message and "'definition'" in message
    assert "'c'" not in message


def test_diff_rows_names_a_missing_or_an_extra_row():
    expected = [_row("a"), _row("b")]
    assert "'b'" in diff_rows(expected, [_row("a")]) and "missing" in diff_rows(expected, [_row("a")])
    assert "'x'" in diff_rows(expected, [_row("a"), _row("x"), _row("b")])
    extra = diff_rows(expected, [*expected, _row("c")])
    assert "'c'" in extra and "extra" in extra


def test_diff_rows_names_a_field_only_one_side_has():
    assert "pages" in diff_rows([_row("a", pages=[1, 2])], [_row("a")])


def test_main_writes_the_golden_file(tmp_path, monkeypatch, ch1_ch5_parse, circ92_pdf):
    calls = []

    def parse(pdf_path, book, pdf_pages):
        calls.append((Path(pdf_path), book.id, tuple(pdf_pages)))
        return ch1_ch5_parse

    monkeypatch.setattr(golden, "parse_circular92", parse)
    out = tmp_path / "golden.jsonl"
    assert golden.main([str(circ92_pdf), str(out)]) == 0
    assert calls == [(circ92_pdf, "circ92", tuple(GOLDEN_PDF_PAGES))]
    assert out.read_bytes() == FIXTURE.read_bytes()
