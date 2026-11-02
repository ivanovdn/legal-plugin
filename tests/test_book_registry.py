# tests/test_book_registry.py
import hashlib
import json
from pathlib import Path

import pytest

from ingest.books.overrides import OVERRIDE_KINDS, Override, OverrideLedger, load_overrides
from ingest.books.registry import (
    BOOKS,
    PdfHashMismatch,
    UnknownBook,
    book_for_ref,
    get_book,
    verify_pdf,
)

FIXTURE = Path(__file__).parent / "fixtures" / "books" / "circular-92-2025-12.pdf"


def _entry(**over):
    entry = {
        "id": "lvl-1",
        "kind": "label_level",
        "page": 204,
        "match": "(3)",
        "value": "2",
        "reason": "§506(a)(3): list set one level too deep, p. 204",
    }
    entry.update(over)
    return entry


def _write(tmp_path, entries):
    path = tmp_path / "overrides.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def test_registry_entry():
    book = BOOKS["circ92"]
    assert book.id == "circ92"
    assert book.title == (
        "Copyright Law of the United States and Related Laws Contained in "
        "Title 17 of the United States Code"
    )
    assert book.short_title == "Circular 92"
    assert book.edition_id == "2025-12"
    assert book.edition_label == "Dec. 2025"
    assert book.jurisdiction == "US"
    assert book.front_end == "circular92"
    assert book.licence == "public domain, U.S. government work"
    assert book.source == "https://www.copyright.gov/title17/title17.pdf"
    assert book.ref_prefix == "circ92-2025-12"


def test_registry_entry_is_frozen():
    with pytest.raises(AttributeError):
        BOOKS["circ92"].edition_id = "2030-01"


def test_unregistered_book_is_refused():
    with pytest.raises(UnknownBook):
        get_book("nope")
    assert issubclass(UnknownBook, LookupError)


def test_get_book_returns_the_registered_entry():
    assert get_book("circ92") is BOOKS["circ92"]


def test_hash_mismatch_is_refused(tmp_path):
    other = tmp_path / "other.pdf"
    other.write_bytes(b"%PDF-1.4 not the book")
    actual = hashlib.sha256(other.read_bytes()).hexdigest()
    book = get_book("circ92")
    with pytest.raises(PdfHashMismatch) as excinfo:
        verify_pdf(book, other)
    assert book.pdf_sha256 in str(excinfo.value)
    assert actual in str(excinfo.value)
    assert issubclass(PdfHashMismatch, ValueError)


def test_fixture_matches_registry():
    verify_pdf(get_book("circ92"), FIXTURE)


def test_book_for_ref():
    assert book_for_ref("circ92-2025-12:usc17-s107").id == "circ92"
    with pytest.raises(UnknownBook):
        book_for_ref("x-2020-01:a")


def test_book_for_ref_needs_the_whole_prefix():
    # same book, other edition; and a bare book id with no edition
    with pytest.raises(UnknownBook):
        book_for_ref("circ92-2022-12:usc17-s107")
    with pytest.raises(UnknownBook):
        book_for_ref("circ92:usc17-s107")
    # the prefix ends at the colon: an edition that merely starts the same way is not a match
    with pytest.raises(UnknownBook):
        book_for_ref("circ92-2025-120:usc17-s107")


def test_shipped_overrides_path_is_absolute_and_exists():
    path = get_book("circ92").overrides_path
    assert path.is_absolute()
    assert path.name == "overrides-2025-12.json"
    assert path.is_file()


def test_shipped_overrides_load():
    load_overrides(get_book("circ92").overrides_path)


def test_override_needs_a_reason(tmp_path):
    with pytest.raises(ValueError, match="reason"):
        load_overrides(_write(tmp_path, [_entry(reason="")]))
    with pytest.raises(ValueError, match="reason"):
        load_overrides(_write(tmp_path, [_entry(reason="   ")]))


def test_override_unknown_kind_is_refused(tmp_path):
    with pytest.raises(ValueError, match="kind"):
        load_overrides(_write(tmp_path, [_entry(kind="indent")]))


def test_override_duplicate_id_is_refused(tmp_path):
    with pytest.raises(ValueError, match="lvl-1"):
        load_overrides(_write(tmp_path, [_entry(), _entry(match="(4)")]))


def test_override_missing_field_is_refused(tmp_path):
    entry = _entry()
    del entry["match"]
    with pytest.raises(ValueError, match="match"):
        load_overrides(_write(tmp_path, [entry]))


@pytest.mark.parametrize(
    "field, bad",
    [("id", 5), ("kind", 5), ("match", 5), ("value", 5), ("reason", None)],
)
def test_override_text_fields_must_be_strings(tmp_path, field, bad):
    # without the guard a number in match or value loads as if it were text,
    # and a null reason or id dies later with an AttributeError instead of a ValueError
    with pytest.raises(ValueError, match=f"field '{field}' must be a string"):
        load_overrides(_write(tmp_path, [_entry(**{field: bad})]))


@pytest.mark.parametrize("blank", ["", "   "])
def test_override_id_must_not_be_blank(tmp_path, blank):
    with pytest.raises(ValueError, match="field 'id' is empty"):
        load_overrides(_write(tmp_path, [_entry(id=blank)]))


def test_override_entry_must_be_an_object(tmp_path):
    with pytest.raises(ValueError, match="expected an object, got str"):
        load_overrides(_write(tmp_path, ["x"]))


def test_override_page_must_be_an_integer(tmp_path):
    with pytest.raises(ValueError, match="page"):
        load_overrides(_write(tmp_path, [_entry(page="204")]))
    # JSON true is a Python int; it is not a page
    with pytest.raises(ValueError, match="page"):
        load_overrides(_write(tmp_path, [_entry(page=True)]))


def test_override_match_must_not_be_empty(tmp_path):
    # an empty label_level prefix would match every line on its page
    with pytest.raises(ValueError, match="match"):
        load_overrides(_write(tmp_path, [_entry(match="")]))


def test_override_hyphen_value_is_join_or_keep(tmp_path):
    ok = _entry(id="h-1", kind="hyphen", match="co-|operate", value="join")
    assert load_overrides(_write(tmp_path, [ok]))[0].value == "join"
    with pytest.raises(ValueError, match="value"):
        load_overrides(_write(tmp_path, [{**ok, "value": "maybe"}]))


def test_overrides_file_must_be_a_list(tmp_path):
    with pytest.raises(ValueError, match="list"):
        load_overrides(_write(tmp_path, {"id": "x"}))


def test_load_overrides_reads_every_field(tmp_path):
    loaded = load_overrides(_write(tmp_path, [_entry()]))
    assert loaded == [
        Override(
            id="lvl-1",
            kind="label_level",
            page=204,
            match="(3)",
            value="2",
            reason="§506(a)(3): list set one level too deep, p. 204",
        )
    ]
    assert OVERRIDE_KINDS == {"label_level", "hyphen", "xref_target", "text_owner", "note_home"}


def test_override_text_owner_value_is_a_path(tmp_path):
    ok = _entry(id="o-1", kind="text_owner", page=177, match="the proprietor of", value="usc17-s304-a-1-B")
    assert load_overrides(_write(tmp_path, [ok]))[0].value == "usc17-s304-a-1-B"
    for bad in ("", "usc17 s304", "-usc17-s304", "usc17-s304-", "§304(a)"):
        with pytest.raises(ValueError, match="value"):
            load_overrides(_write(tmp_path, [{**ok, "value": bad}]))


def test_override_note_home_names_a_note_and_a_node(tmp_path):
    ok = _entry(id="h-1", kind="note_home", page=148, match="ch1-n15", value="usc17-s101-def-international-agreement")
    assert load_overrides(_write(tmp_path, [ok]))[0].match == "ch1-n15"
    assert load_overrides(_write(tmp_path, [{**ok, "match": "appB-n2"}]))[0].match == "appB-n2"
    for bad in ("ch1-15", "ch1 n15", "usc17-s101", "n15", "appBB-n2", "ch1-n15 "):
        with pytest.raises(ValueError, match="match"):
            load_overrides(_write(tmp_path, [{**ok, "match": bad}]))
    for bad in ("", "usc17 s101", "§101"):
        with pytest.raises(ValueError, match="value"):
            load_overrides(_write(tmp_path, [{**ok, "value": bad}]))


def test_ledger_reports_unused():
    a = Override("a", "label_level", 10, "(3)", "2", "because")
    b = Override("b", "xref_target", 11, "section 2323", "usc18-s2323", "because")
    ledger = OverrideLedger([a, b])
    assert ledger.unused() == [a, b]
    assert ledger.find("xref_target", 11, "section 2323") is b
    assert ledger.unused() == [a]


def test_ledger_find_matches_kind_page_and_text():
    a = Override("a", "label_level", 10, "(3)", "2", "because")
    h = Override("h", "hyphen", 10, "co-|operate", "join", "because")
    ledger = OverrideLedger([a, h])
    assert ledger.find("label_level", 11, "(3) text") is None
    assert ledger.find("hyphen", 10, "(3)") is None
    assert ledger.find("hyphen", 10, "co-|operate") is h
    # a label_level match is a line prefix; the other kinds match whole
    assert ledger.find("label_level", 10, "(3) the work is infringing") is a
    assert ledger.find("hyphen", 10, "co-|operate and more") is None
    assert ledger.unused() == []


def test_ledger_find_matches_a_text_owner_by_line_prefix():
    o = Override("o", "text_owner", 177, "the proprietor of such", "usc17-s304-a-1-B", "because")
    ledger = OverrideLedger([o])
    assert ledger.find("text_owner", 177, "the proprietor of such copyright shall be") is o
    assert ledger.find("text_owner", 177, "of such copyright") is None


def test_ledger_find_matches_a_note_home_whole():
    o = Override("o", "note_home", 146, "ch1-n1", "usc17-s101", "because")
    ledger = OverrideLedger([o])
    assert ledger.find("note_home", 146, "ch1-n15") is None  # n.1's override is not n.15's
    assert ledger.find("note_home", 147, "ch1-n1") is None
    assert ledger.find("note_home", 146, "ch1-n1") is o


def test_ledger_with_no_overrides_finds_nothing():
    ledger = OverrideLedger([])
    assert ledger.find("label_level", 1, "(a)") is None
    assert ledger.unused() == []
