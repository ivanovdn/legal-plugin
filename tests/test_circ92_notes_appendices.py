# tests/test_circ92_notes_appendices.py
"""Circular 92 front end, part 3: the history notes, and appendices A-T.

The chapter notes run on the session's parse of chapters 1 and 5 (conftest: `ch1_ch5_nodes`); the
appendices on PDF 389-481, extracted once a session (`circ92_app_lines`). A rule the fixture cannot
pin on its own runs on synthetic lines in the shape layout.py returns them.
"""
import re

import pytest

from ingest.books.circular92.appendices import (
    APPENDICES,
    DELETED_DEFINITION,
    appendix_heading_marks,
    appendix_nodes,
)
from ingest.books.circular92.layout import RawLine
from ingest.books.circular92.notes import EFFECTIVE_DATE_RE, history_notes
from ingest.books.circular92.text import HyphenDecider
from ingest.books.circular92.title17 import title17_nodes
from ingest.books.overrides import Override, OverrideLedger, load_overrides
from ingest.books.ref_ids import slugify_term
from ingest.books.registry import get_book

LETTERS = [chr(c) for c in range(ord("A"), ord("T") + 1)]
S304_OWNERS = {"usc17-s304-a-1-B-flush", "usc17-s304-a-1-C-flush"}  # text_owner: a chapter 3 parse uses them


def _index(roots):
    return {node.path: node for root in roots for node in root.walk()}


def _texts(lines):
    return [line.text for line in lines]


@pytest.fixture(scope="module")
def notes_ledger():
    """The shipped overrides, for the ch.1 + ch.5 notes only (the session ledger is the Title 17 parse's)."""
    return OverrideLedger(load_overrides(get_book("circ92").overrides_path))


@pytest.fixture(scope="module")
def ch1_ch5_notes(circ92_ch1_ch5_lines, ch1_ch5_nodes, ch1_ch5_hyphens, notes_ledger):
    return history_notes(circ92_ch1_ch5_lines, ch1_ch5_nodes, ch1_ch5_hyphens, notes_ledger)


@pytest.fixture(scope="module")
def ch_notes(ch1_ch5_notes):
    return {note.path: note for note in ch1_ch5_notes}


@pytest.fixture(scope="module")
def app_lines(circ92_app_lines):
    return circ92_app_lines


@pytest.fixture(scope="module")
def app_parse(app_lines):
    """The appendices parsed with the shipped overrides: (roots, out_of_sequence, hyphens)."""
    ledger = OverrideLedger(load_overrides(get_book("circ92").overrides_path))
    hyphens = HyphenDecider(app_lines, ledger)
    out_of_sequence: list[str] = []
    roots = appendix_nodes(app_lines, hyphens, ledger, out_of_sequence=out_of_sequence)
    return roots, out_of_sequence, hyphens, ledger


@pytest.fixture(scope="module")
def app_nodes(app_parse):
    return app_parse[0]


@pytest.fixture(scope="module")
def app(app_nodes):
    return _index(app_nodes)


@pytest.fixture(scope="module")
def app_notes(app_lines, app_parse):
    roots, _, hyphens, ledger = app_parse
    return history_notes(app_lines, roots, hyphens, ledger)


@pytest.fixture(scope="module")
def app_note(app_notes):
    return {note.path: note for note in app_notes}


# --- synthetic lines ---------------------------------------------------------------------------

def _line(text, x0=54.2, page=371, *, kind="body", marks=(), scope="Appendix B", font=None):
    if font is None:
        font = {"heading": ("MinionC-BoldItalic", 10.2), "note": ("MinionC", 9.0),
                "title": ("TheMixOsF-ExtraBold", 13.0)}.get(kind, ("MinionC", 10.2))
    return RawLine(pdf_page=page + 22, page=page, scope=scope, x0=x0, top=0.0, text=text,
                   marks=list(marks), kind=kind, font=font, fonts=[font[0]])


def _heading(text, page=371, scope="Appendix B", marks=()):
    return _line(text, 54.1, page, kind="heading", scope=scope, marks=marks)


def _note(text, x0=65.6, page=376, scope="Appendix B"):
    return _line(text, x0, page, kind="note", scope=scope)


def _notes_heading(scope="Appendix B", page=376):
    return _line(f"{scope} · Notes", 53.9, page, kind="notes_heading", scope=scope,
                 font=("TheMixOsF-Bold", 10.0))


def _parse(*lines):
    """Appendix lines -> (index, out_of_sequence, notes)."""
    lines = list(lines)
    ledger = OverrideLedger([])
    hyphens = HyphenDecider(lines, ledger)
    out_of_sequence: list[str] = []
    roots = appendix_nodes(lines, hyphens, ledger, out_of_sequence=out_of_sequence)
    return _index(roots), out_of_sequence, history_notes(lines, roots, hyphens, ledger)


def _chapter(*lines, overrides=()):
    """Chapter lines -> (index, notes)."""
    lines = list(lines)
    ledger = OverrideLedger(list(overrides))
    hyphens = HyphenDecider(lines, ledger)
    roots, _ = title17_nodes(lines, hyphens, ledger)
    return _index(roots), history_notes(lines, roots, hyphens, ledger)


# --- the notes of chapters 1 and 5 -------------------------------------------------------------

def test_note_counts(ch1_ch5_notes):
    numbers: dict[str, list[int]] = {}
    for note in ch1_ch5_notes:
        numbers.setdefault(note.scope, []).append(note.note_number)
    assert numbers == {"Chapter 1": list(range(1, 66)), "Chapter 5": list(range(1, 13))}
    assert [note.path for note in ch1_ch5_notes][:2] == ["ch1-n1", "ch1-n2"]
    assert ch1_ch5_notes[-1].path == "ch5-n12"


def test_note_annotates(ch_notes):
    assert ch_notes["ch5-n8"].annotates == "usc17-s509"
    assert ch_notes["ch5-n8"].label == "ch. 5 n.8"


def test_effective_date_flag(ch1_ch5_notes, ch_notes):
    # The prototype said §101(5): the marker sits in paragraph (5) of the definition.
    assert ch_notes["ch1-n13"].effective_date
    assert ch_notes["ch1-n13"].annotates == "usc17-s101-def-international-agreement-5"
    assert len(ch1_ch5_notes) == 77 and sum(note.effective_date for note in ch1_ch5_notes) == 17


def test_a_note_is_amendment_history_of_its_chapter(ch_notes):
    note = ch_notes["ch1-n3"]
    assert (note.kind, note.source_type, note.jurisdiction) == ("history_note", "usc_title", "US")
    assert (note.scope, note.appendix, note.note_number, note.heading) == ("Chapter 1", None, 3, "")
    assert note.label == "ch. 1 n.3" and note.children == []
    assert note.pages() == (146, 147)


def test_a_note_drops_its_number_and_keeps_its_paragraphs(ch_notes):
    lead = ch_notes["ch1-n63"].lead
    assert lead[0].text.startswith("The Legislative Branch Appropriations Act, 1997, added section 121.")
    starts = [line.text for line in lead if line.para_start]  # n.63 runs to three paragraphs
    assert [text[:24] for text in starts] == [
        "The Legislative Branch A", "The Individuals with Dis", "In 2018, the Marrakesh T"]
    assert all(line.marks == [] for line in lead)


@pytest.mark.parametrize("path, annotates", [
    ("ch1-n1", None),  # the chapter title
    ("ch1-n2", "usc17-s101"),  # a section heading
    ("ch1-n41", "usc17-s108"),  # the line a section title wraps onto
    ("ch5-n1", "usc17-s509"),  # a table-of-contents entry names its section (ruling R9)
    ("ch5-n2", "usc17-s513"),  # … also on the line the entry wraps onto
    ("ch5-n12", "usc17-s513"),
    ("ch1-n32", "usc17-s104a-h-1-C"),  # a unit's own text
])
def test_a_note_annotates_where_its_marker_sits(ch_notes, path, annotates):
    assert ch_notes[path].annotates == annotates


@pytest.mark.parametrize("path, definition", [
    ("ch1-n15", "international-agreement"),  # "… by adding the definition of “international agreement.”"
    ("ch1-n22", "united-states-work"),  # "… changing it to a definition for “United States work,” …"
    ("ch1-n25", "work-of-visual-art"),  # "… by adding the definition of “work of visual art.”"
])
def test_a_note_about_a_whole_definition_names_it(ch_notes, path, definition):
    # Its marker closes the definition's last item, so the marker's place alone would name that item
    # (ruling R10: a note_home override). Notes whose marker closes an item they are about stay put.
    assert ch_notes[path].annotates == f"usc17-s101-def-{definition}"
    assert ch_notes["ch1-n27"].annotates == "usc17-s101-def-wto-agreement"


def test_every_note_home_override_is_used(ch1_ch5_notes, notes_ledger):
    # This ledger saw only the notes; the text_owner entries are chapter 3's (tests/test_circ92_title17.py).
    assert {o.id for o in notes_ledger.unused()} == S304_OWNERS


def _home(match, value, page=266, oid="h1"):
    return Override(id=oid, kind="note_home", page=page, match=match, value=value, reason="test")


_HOMED = (
    _line("§ 901 · Definitions", 53.7, 264, kind="heading", scope="Chapter 9"),
    _line("(a) The owner may act.", 66.0, 264, scope="Chapter 9"),
    _line("(b) The owner may not act.", 66.0, 264, scope="Chapter 9", marks=[1]),
    _notes_heading("Chapter 9", 266),
    _note("1. The Act added section 901, and its note runs", 53.7, 266, scope="Chapter 9"),
    _note("onto the next page.", 54.1, 267, scope="Chapter 9"),  # the override names the page it starts on
)


def test_a_note_home_override_replaces_where_the_marker_sits():
    _, notes = _chapter(*_HOMED)
    assert notes[0].annotates == "usc17-s901-b"
    _, notes = _chapter(*_HOMED, overrides=[_home("ch9-n1", "usc17-s901")])
    assert notes[0].annotates == "usc17-s901"
    _, notes = _chapter(*_HOMED, overrides=[_home("ch9-n1", "usc17-s901", page=265)])  # another page: not this note
    assert notes[0].annotates == "usc17-s901-b"


def test_a_note_home_naming_no_placed_node_is_an_error():
    with pytest.raises(ValueError, match="override 'h1'.*usc17-s999"):
        _chapter(*_HOMED, overrides=[_home("ch9-n1", "usc17-s999")])


# --- the appendices ----------------------------------------------------------------------------

def test_every_appendix_parsed(app_nodes):
    for letter in LETTERS:
        nodes = [node for root in app_nodes for node in root.walk() if node.appendix == letter]
        assert nodes, letter
        assert {node.source_type for node in nodes} == {APPENDICES[letter].source_type}, letter


def test_trips(app):
    for article in range(35, 39):
        node = app[f"trips-art{article}"]
        assert (node.source_type, node.jurisdiction, node.appendix) == ("treaty", "international", "S")
    assert app["trips-art38"].label == "TRIPs Agreement, art. 38"
    assert app["trips-art37"].heading == "Acts Not Requiring the Authorization of the Right Holder"


def test_trips_paragraphs_are_numbered_without_parentheses(app):
    assert [child.path for child in app["trips-art37"].children] == ["trips-art37-1", "trips-art37-2"]
    assert [child.path for child in app["trips-art38"].children] == [f"trips-art38-{n}" for n in (1, 2, 3)]
    assert app["trips-art35"].children == [] and app["trips-art36"].children == []
    paragraph = app["trips-art38-1"]
    assert paragraph.label == "TRIPs Agreement, art. 38(1)"
    assert (paragraph.source_type, paragraph.jurisdiction) == ("treaty", "international")
    assert paragraph.lead[0].text.startswith("1. In Members requiring registration")
    assert app["trips-art38"].lead == []


def test_other_titles(app):
    for path in ("usc18-s2318", "usc18-s2319", "usc28-s137", "usc44-s2117"):
        assert path in app and app[path].source_type == "usc_title"
    assert app["usc18-s2319"].label == "18 U.S.C. §2319"
    assert app["usc18-s2319a"].label == "18 U.S.C. §2319A"
    assert app["usc28-s1338"].heading == (
        "Patents, plant variety protection, copyrights, mask works, designs, trademarks, and unfair competition")
    assert app["usc18-s2318-a-1-A-vii"].label == "18 U.S.C. §2318(a)(1)(A)(vii)"


def test_acts(app):
    assert app["pl105-304-s101"].label == "Digital Millennium Copyright Act, Pub. L. No. 105-304, §101 (1998)"
    unit = app["pl105-304-s105-b-1-A"]
    assert unit.label == "Digital Millennium Copyright Act, Pub. L. No. 105-304, §105(b)(1)(A) (1998)"
    assert (unit.kind, unit.source_type, unit.jurisdiction, unit.appendix, unit.scope) == (
        "rule", "uncodified_act", "US", "B", "Appendix B")
    assert app["pl105-304-s105-a"].heading == "In General"


def test_appendix_t(app, app_nodes):
    node = app["usc17-s101-def-berne-convention-work"]
    assert (node.kind, node.appendix, node.source_type, node.jurisdiction) == ("repealed", "T", "usc_title", "US")
    assert node.term == "Berne Convention work" and node.children == []
    assert node.heading == "Definition of “Berne Convention Work”"  # the appendix's title
    assert node.label == "17 U.S.C. §101 (“Berne Convention work”)"
    assert node.lead[0].text.startswith("The WIPO Copyright and Performances")
    assert node.lead[-1].text == "of publication are within 30 days of one another."
    assert [line.marks for line in node.lead if line.marks] == [[1]]
    assert [root.path for root in app_nodes if root.appendix == "T"] == [node.path]
    letter, term = DELETED_DEFINITION
    assert APPENDICES[letter].prefix == f"usc17-s101-def-{slugify_term(term)}"


def test_section_heading_styles(app):
    assert app["pl105-304-s1"].heading == "Short Title"  # Section 1 · Short Title.
    assert app["pl105-304-s405"].heading == "Scope of Exclusive Rights in Sound Recordings; Ephemeral Recordings"
    assert app["pl113-144-s2"].heading == (  # Sec. 2. Repeal of … (and its wrapped line)
        "Repeal of Existing Rule and Additional Rulemaking by Librarian of Congress")
    assert app["pl110-403-s205"].heading == "Trafficking in Counterfeit Goods or Services"  # Sec. 205. in a · Act
    assert app["pl100-568-s1"].heading == "Short Title and References to Title 17, United States Code"  # Sec.1 ·
    assert app["usc18-s2318"].heading == (
        "Trafficking in counterfeit labels, illicit labels, or counterfeit documentation or packaging")
    assert app["trips-art35"].heading == "Relation to IPIC Treaty"


def test_run_in_sections(app):
    # Appendix A sets its sections' numbers in small caps at the start of their text: "Sec. 102. This Act …"
    assert [path for path in app if re.fullmatch(r"pl94-553-s\d+", path)] == [
        f"pl94-553-s{n}" for n in range(102, 116)]
    section = app["pl94-553-s102"]
    assert section.heading == "" and section.lead[0].text.startswith("This Act becomes effective on January 1, 1978")
    assert app["pl94-553-s113-a"].lead[0].text.startswith("(a) The Librarian of Congress")
    assert app["pl94-553-s105-a-1"].lead[0].text.startswith("(a)(1) Section 505 of title 44")  # as Title 17's


def test_context_headings_are_neither_sections_nor_text(app):
    # "Title I — Approval of, and General Provisions Relating to," wraps onto "the Uruguay Round
    # Agreements" between §2 and §101 of the URAA.
    assert "the Uruguay Round Agreements" not in _texts(app["pl103-465-s2"].all_lines())
    assert not any(path.startswith("pl105-304-s6") for path in app)  # "Section 6: Layout-Designs …" is app. S's


def test_editorial_brackets_and_quoted_text_stay_in_their_unit(app):
    assert "[To assist the reader, section 2113 of title 44, now designated section 2117," in _texts(
        app["pl94-553-s105-b"].lead)
    quoted = _texts(app["pl105-304-s406-a"].all_lines())
    assert "‘‘§4001. Assumption of contractual obligations related to transfers of rights" in quoted
    assert "‘‘(b) Scope of Exclusion of Transfers of Public Performance Rights.—" in quoted
    assert "pl105-304-s406-b" not in app


def test_appendix_paths_are_unique(app_nodes):
    paths = [node.path for root in app_nodes for node in root.walk()]
    assert len(paths) == len(set(paths))


def test_every_appendix_node_has_text(app):
    for node in app.values():
        assert node.pages()  # raises on a node with no lines


def test_no_unplaced_labels_in_the_appendices(app_parse):
    assert app_parse[1] == []


def test_appendix_heading_marks(app_lines):
    marks = appendix_heading_marks(app_lines)
    assert marks["pl105-304-s505"] == [2] and marks["pl108-419-s6"] == [2]
    assert marks["usc18-s2318"] == [1] and marks["usc18-s2319a"] == [4]  # on the wrapped line
    assert marks["trips-art37"] == [3] and marks["pl105-304-s1"] == []
    assert "pl94-553-s102" in marks and marks["pl94-553-s102"] == []  # run-in: no heading


def test_the_appendix_table():
    assert list(APPENDICES) == LETTERS
    for letter, spec in APPENDICES.items():
        assert spec.letter == letter
        assert spec.jurisdiction == ("international" if letter == "S" else "US")
        if spec.source_type == "uncodified_act":
            assert spec.prefix == f"pl{spec.pub_law}" and spec.act_name and spec.year
    assert [APPENDICES[letter].prefix for letter in "NOP"] == ["usc18", "usc28", "usc44"]
    assert (APPENDICES["S"].source_type, APPENDICES["S"].prefix) == ("treaty", "trips")


@pytest.mark.parametrize("letter, printed", [("A", "94-533"), ("K", "115-262"), ("M", "116-26")])
def test_a_misprinted_public_law_is_recorded_and_the_real_one_used(letter, printed, app_note):
    spec = APPENDICES[letter]
    note = " ".join(_texts(app_note[f"app{letter}-n1"].lead))
    assert f"Pub. L. No. {printed}," in note and spec.pub_law != printed
    assert printed in spec.reason and spec.pub_law in spec.reason


def test_every_other_act_is_the_one_its_note_1_names(app_note):
    for letter, spec in APPENDICES.items():
        if spec.source_type != "uncodified_act" or letter in "AKM":
            continue
        if letter == "J":  # the book gives Appendix J no note
            assert f"app{letter}-n1" not in app_note and spec.reason
            continue
        note = " ".join(_texts(app_note[f"app{letter}-n1"].lead))
        assert re.search(rf"\b{spec.pub_law.replace('-', r'-\s?')},", note), letter
        assert spec.reason == "", letter


# --- the appendices' notes ---------------------------------------------------------------------

def test_appendix_g_note_2(app_note, app):
    # Marker 2 of Appendix G is set at 10.2 pt, not 10.5 (printed p. 406); it is lifted and linked all the same.
    note = app_note["appG-n2"]
    assert note.annotates == "pl111-175-s107-a"
    assert any(line.page == 406 and line.marks == [2] for line in app[note.annotates].own_lines())


def test_markers_match_notes_in_appendices(app_lines, app_notes):
    for letter in LETTERS:
        scope = f"Appendix {letter}"
        markers = [n for line in app_lines if line.scope == scope for n in line.marks]
        notes = [note.note_number for note in app_notes if note.scope == scope]
        assert markers == notes == list(range(1, len(notes) + 1)), scope
    assert len(app_notes) == 39


@pytest.mark.parametrize("path, annotates", [
    ("appA-n1", None),  # the appendix title
    ("appA-n2", "pl94-553-s102"),
    ("appA-n3", "pl94-553-s105-f"),
    ("appB-n2", "pl105-304-s505"),  # a section heading
    ("appB-n3", "pl105-304-s505"),  # the same section's text
    ("appN-n1", "usc18-s2318"),  # the line a section title wraps onto
    ("appN-n2", "usc18-s2318-b-2"),
    ("appN-n6", "usc18-s2319a-f"),
    ("appO-n2", "usc28-s1338"),
    ("appS-n1", None),
    ("appS-n2", "trips-art36"),
    ("appS-n3", "trips-art37"),
    ("appT-n1", "usc17-s101-def-berne-convention-work"),
])
def test_an_appendix_note_annotates_where_its_marker_sits(app_note, path, annotates):
    assert app_note[path].annotates == annotates


def test_an_appendix_note_takes_its_appendix(app_note):
    note = app_note["appS-n2"]
    assert (note.kind, note.source_type, note.jurisdiction, note.appendix) == (
        "history_note", "treaty", "international", "S")
    assert note.label == "app. S n.2" and note.scope == "Appendix S"
    assert (app_note["appN-n3"].source_type, app_note["appB-n2"].source_type) == ("usc_title", "uncodified_act")


# --- the rules, on synthetic lines -------------------------------------------------------------

def test_effective_date_pattern():
    assert EFFECTIVE_DATE_RE.pattern == r"tak(e|es|en) effect|took effect|effective (date|upon|on)"
    for text in ("shall take effect", "TAKES EFFECT", "had taken effect", "took effect",
                 "the effective date of", "effective upon enactment", "effective on March 1"):
        assert EFFECTIVE_DATE_RE.search(text), text
    for text in ("became effective", "in effect on", "effectively"):
        assert not EFFECTIVE_DATE_RE.search(text), text


def test_effective_date_reads_the_cleaned_text():
    # The phrase can break at a line-end hyphen; the flag reads the note as joined.
    _, _, notes = _parse(
        _notes_heading(),
        _note("1. The amendments shall take ef-", 53.7),
        _note("fect on the date of enactment.", 54.1),
        _note("2. The amendments are in effect.", 65.6),
    )
    assert [note.effective_date for note in notes] == [True, False]


def test_a_note_starts_only_with_the_next_number_near_the_margin():
    _, _, notes = _parse(
        _notes_heading(),
        _note("1. The first note.", 53.7),
        _note("3. A number out of turn is text.", 54.1),
        _note("2. So is the next number set too far in.", 75.0),
        _note("2 . The second note, its period set apart.", 65.6),
    )
    assert [note.note_number for note in notes] == [1, 2]
    assert _texts(notes[0].lead) == ["The first note.", "3. A number out of turn is text.",
                                     "2. So is the next number set too far in."]
    assert _texts(notes[1].lead) == ["The second note, its period set apart."]
    assert [line.para_start for line in notes[0].lead] == [True, False, True]


def test_a_note_line_before_the_first_note_is_an_error():
    with pytest.raises(ValueError, match="before the first note"):
        _parse(_notes_heading(), _note("of the United States Code.", 54.1))


def test_contents_entries_and_their_wrapped_lines_name_their_sections():
    _, notes = _chapter(
        _line("Chapter 9", 54.2, 263, kind="title", scope="Chapter 9", marks=[1]),
        _line("section page", 89.0, 263, kind="toc", scope="Chapter 9", marks=[2]),
        _line("901 Definitions ........................ 264", 58.0, 263, kind="toc", scope="Chapter 9", marks=[3]),
        _line("902A Subject matter of protection, and", 58.0, 263, scope="Chapter 9"),  # matched in any case
        _line("its long title ......................... 265", 89.0, 263, kind="toc", scope="Chapter 9", marks=[4]),
        _line("§ 901 · Definitions", 53.7, 264, kind="heading", scope="Chapter 9"),
        _line("(a) The owner may act.", 66.0, 264, scope="Chapter 9"),
        _line("§ 902A · Subject matter of protection", 53.7, 265, kind="heading", scope="Chapter 9"),
        _line("The owner may not act.", 66.0, 265, scope="Chapter 9"),
        _notes_heading("Chapter 9", 266),
        *(_note(f"{n}. Note {n}.", 65.6, 266, scope="Chapter 9") for n in range(1, 5)),
    )
    assert [note.annotates for note in notes] == [None, None, "usc17-s901", "usc17-s902a"]


def test_a_contents_line_set_at_the_entries_margin_names_no_section():
    _, notes = _chapter(
        _line("901 Definitions ........................ 264", 58.0, 263, kind="toc", scope="Chapter 9"),
        _line("Subchapter A ........................... 264", 58.0, 263, kind="toc", scope="Chapter 9", marks=[1]),
        _line("§ 901 · Definitions", 53.7, 264, kind="heading", scope="Chapter 9"),
        _line("The owner may act.", 66.0, 264, scope="Chapter 9"),
        _notes_heading("Chapter 9", 266),
        _note("1. Note 1.", 65.6, 266, scope="Chapter 9"),
    )
    assert notes[0].annotates is None


def test_a_contents_entry_for_a_section_that_was_not_placed_is_an_error():
    with pytest.raises(ValueError, match="contents entry for usc17-s903"):
        _chapter(
            _line("903 Ownership ........................ 266", 58.0, 263, kind="toc", scope="Chapter 9", marks=[1]),
            _line("§ 901 · Definitions", 53.7, 264, kind="heading", scope="Chapter 9"),
            _line("The owner may act.", 66.0, 264, scope="Chapter 9"),
        )


def test_one_marker_number_in_two_places_is_an_error():
    with pytest.raises(ValueError, match="marker 1 sits in two places"):
        _chapter(
            _line("§ 901 · Definitions", 53.7, 264, kind="heading", scope="Chapter 9"),
            _line("(a) The owner may act.", 66.0, 264, scope="Chapter 9", marks=[1]),
            _line("(b) The owner may not act.", 66.0, 264, scope="Chapter 9", marks=[1]),
        )


def test_a_marker_on_the_heading_of_a_section_not_placed_is_an_error():
    lines = [_line("§ 901 · Definitions", 53.7, 264, kind="heading", scope="Chapter 9", marks=[1]),
             _line("The owner may act.", 66.0, 264, scope="Chapter 9")]
    ledger = OverrideLedger([])
    with pytest.raises(ValueError, match="usc17-s901 was not placed"):
        history_notes(lines, [], HyphenDecider(lines, ledger), ledger)


def test_a_marker_on_a_line_no_node_holds_is_an_error():
    lines = [_line("§ 901 · Definitions", 53.7, 264, kind="heading", scope="Chapter 9"),
             _line("The owner may act.", 66.0, 264, scope="Chapter 9", marks=[1])]
    ledger = OverrideLedger([])
    with pytest.raises(ValueError, match="marker 1"):
        history_notes(lines, [], HyphenDecider(lines, ledger), ledger)


def test_an_elision_lets_the_next_label_skip_ahead():
    lines = [
        _line("The Act", 52.9, kind="title", font=("TheMixOsF-ExtraBold", 13.0)),
        _heading("Sec. 101 · Effective Date."),
        _line("(a) In General.—This title takes effect on enactment.", 66.0),
        _heading("* * * * * * *"),
        _line("(c) Other Amendments.—This title amends nothing.", 66.0),
    ]
    idx, oos, _ = _parse(*lines)
    assert "pl105-304-s101-c" in idx and oos == []
    assert idx["pl105-304-s101-c"].lead[0].para_start  # the first line after a gap starts a paragraph
    _, oos, _ = _parse(*lines[:3], lines[4])  # no elision: (c) after (a) is out of sequence
    assert oos == ["p.371: (c) Other Amendments.—This title amends nothing."]
    _, oos, _ = _parse(*lines, _line("(e) A later skip is reported.", 66.0))  # the gap excuses one label
    assert oos == ["p.371: (e) A later skip is reported."]


def test_a_label_after_an_elision_is_not_read_as_a_wrapped_cross_reference():
    idx, oos, _ = _parse(
        _heading("Sec. 101 · Amendments."),
        _line("(a) Compensation.—(1) Section 3(d) of title 35 is amended under section", 66.0),
        _heading("* * * * * * *"),
        _line("(3) Section 5314 of title 5 is amended.", 78.0),
    )
    assert "pl105-304-s101-a-3" in idx and oos == []


def test_heading_lines_wrap_and_context_lines_stay_out():
    idx, _, _ = _parse(
        _heading("Title I — Approval of, and General Provisions"),
        _heading("Relating to, the Agreements"),
        _heading("Sec. 101 · Provisions Relating to the Commissioner"),
        _heading("and the Register of Copyrights."),
        _line("This section amends title 35.", 65.5),
        _heading("Title II — Online Copyright Infringement"),
        _heading("Liability Limitation"),
        _heading("Sec. 201 · Short Title."),
        _line("This title may be cited as the Act.", 65.5),
    )
    assert idx["pl105-304-s101"].heading == "Provisions Relating to the Commissioner and the Register of Copyrights"
    assert _texts(idx["pl105-304-s101"].lead) == ["This section amends title 35."]
    assert _texts(idx["pl105-304-s201"].lead) == ["This title may be cited as the Act."]


def test_a_heading_line_after_text_is_text():
    idx, _, _ = _parse(
        _heading("Sec. 406 · Assumption of Obligations."),
        _line("(a) In General.—Part VI of title 28 is amended by adding:", 66.0),
        _line("‘‘Chapter 180—Assumption of Certain Contractual Obligations", 64.3, kind="heading",
              font=("MinionC-Bold", 10.2)),
        _line("‘‘§4001. Assumption of contractual obligations", 64.3, kind="heading", font=("MinionC-Bold", 10.2)),
        _line("in motion pictures", 54.2, kind="heading", font=("MinionC-Bold", 10.2)),
        _line("‘‘(a) Assumption of Obligations.—In the case of a transfer.", 64.4),
    )
    assert _texts(idx["pl105-304-s406-a"].lead)[1:] == [
        "‘‘Chapter 180—Assumption of Certain Contractual Obligations",
        "‘‘§4001. Assumption of contractual obligations", "in motion pictures",
        "‘‘(a) Assumption of Obligations.—In the case of a transfer."]
    assert list(idx) == ["pl105-304-s406", "pl105-304-s406-a"]


@pytest.mark.parametrize("before", [(), (_heading("Sec. 101 · Short Title."), _line("This title may be cited.", 65.5))])
def test_text_outside_any_section_is_an_error(before):
    # A context heading ends the section before it: text after it, before the next section, is in none.
    with pytest.raises(ValueError, match="outside any section"):
        _parse(*before, _heading("Title I — WIPO Treaties Implementation"), _line("This Act may be cited.", 65.5))


def test_a_section_heading_with_no_text_is_an_error():
    with pytest.raises(ValueError, match="no text"):
        _parse(_heading("Sec. 101 · Short Title."), _heading("Sec. 102 · Effective Date."),
               _line("This title takes effect.", 65.5))


def test_a_run_in_section_needs_its_small_caps_number():
    idx, _, _ = _parse(
        _line("Sec. 102. This Act becomes effective.", 66.2, scope="Appendix A", font=("MinionSC", 10.2)),
        _line("Sec. 103. is text in roman type, the end of §102.", 66.2, scope="Appendix A"),
    )
    assert list(idx) == ["pl94-553-s102"]
    assert _texts(idx["pl94-553-s102"].lead) == ["This Act becomes effective.",
                                                 "Sec. 103. is text in roman type, the end of §102."]


def test_section_marks_on_a_heading_annotate_the_section():
    _, _, notes = _parse(
        _line("The Act", 52.9, kind="title", marks=[1]),
        _heading("Sec. 505 · Effective Date.", marks=[2]),
        _line("The amendments take effect on enactment.", 53.5, marks=[3]),
        _notes_heading(),
        *(_note(f"{n}. Note {n}.") for n in (1, 2, 3)),
    )
    assert [note.annotates for note in notes] == [None, "pl105-304-s505", "pl105-304-s505"]


def test_treaty_paragraphs_start_only_in_turn_and_at_a_paragraph_start():
    idx, _, _ = _parse(
        _heading("Article 37 · Acts Not Requiring Authorization", 457, "Appendix S"),
        _line("1. Notwithstanding Article 36 above, no Member shall", 65.7, 457, scope="Appendix S"),
        _line("2. a line that wraps on a number is text.", 54.1, 457, scope="Appendix S"),
        _line("3. A paragraph out of turn is text too,", 65.6, 457, scope="Appendix S"),
        _line("and so is its next line.", 54.1, 457, scope="Appendix S"),
        _line("2. The conditions shall apply.", 65.6, 457, scope="Appendix S"),
    )
    assert [child.path for child in idx["trips-art37"].children] == ["trips-art37-1", "trips-art37-2"]
    assert _texts(idx["trips-art37-1"].lead)[1:] == [
        "2. a line that wraps on a number is text.", "3. A paragraph out of turn is text too,",
        "and so is its next line."]
    assert idx["trips-art37-2"].label == "TRIPs Agreement, art. 37(2)"


def test_quoted_text_stays_in_its_unit_whatever_its_indent(app):
    # An amendment's quoted words are set to the indents of the law they amend (here 18 U.S.C. 2320(h),
    # one indent left of the paragraph that inserts them): they are that paragraph's text.
    idx, _, _ = _parse(
        _heading("Sec. 205. Trafficking."),
        _line("(a) In General.—Section 2320 of title 18 is amended—", 66.0),
        _line("(1) by adding at the end the following:", 78.0),
        _line("“(h) Transshipment.—No goods shall be trans-", 63.3),
        _line("shipped through the United States.”.", 54.2),
        _line("(b) Restitution.—Section 2320(b) is amended.", 66.0),
    )
    assert _texts(idx["pl105-304-s205-a-1"].lead)[1:] == [
        "“(h) Transshipment.—No goods shall be trans-", "shipped through the United States.”."]
    assert idx["pl105-304-s205-a"].tail == [] and "pl105-304-s205-b" in idx
    assert _texts(app["pl110-403-s205-a-2"].lead)[1].startswith("“(h) Transshipment and Exportation.—")
    assert app["pl110-403-s205-a"].tail == []


def test_a_quotation_closes_and_flush_text_after_it_leaves_it_in_place():
    idx, _, _ = _parse(
        _heading("Sec. 205. Trafficking."),
        _line("(a) The section is amended—", 66.0),
        _line("(1) by adding at the end the following:", 78.0),
        _line("“(h) The new subsection.’’; and", 63.3),
        _line("the amendment takes effect on enactment.", 54.2),
    )
    assert _texts(idx["pl105-304-s205-a-1"].lead) == [
        "(1) by adding at the end the following:", "“(h) The new subsection.’’; and"]
    assert _texts(idx["pl105-304-s205-a"].tail) == ["the amendment takes effect on enactment."]


def test_a_dash_after_a_quote_is_not_a_units_heading(app):
    idx, _, _ = _parse(
        _heading("Sec. 205. Trafficking."),
        _line("(a) In General.—Section 2320 is amended—", 66.0),
        _line("(1) by striking “Whoever” and inserting “Offense.—”", 78.0),
    )
    assert idx["pl105-304-s205-a"].heading == "In General" and idx["pl105-304-s205-a-1"].heading == ""
    assert app["pl110-403-s205-a-1-A"].heading == "" and app["pl110-403-s205-a-1-C"].heading == ""


def test_a_label_ends_a_quotation_that_did_not_close():
    # A line can open with a quoted term without opening a quotation; the next label ends it all the same.
    idx, _, _ = _parse(
        _heading("§ 2318 · Trafficking", 425, "Appendix N"),
        _line("(b) As used in this section—", 66.0, 425, scope="Appendix N"),
        _line("(1) the terms “copy” and", 78.0, 425, scope="Appendix N"),
        _line("“phonorecord” have their meanings in title 17;", 63.3, 425, scope="Appendix N"),
        _line("(2) the term “traffic” has its meaning in section 2320,", 78.0, 425, scope="Appendix N"),
        _line("and these definitions apply to this section.", 54.2, 425, scope="Appendix N"),
    )
    assert _texts(idx["usc18-s2318-b-1"].lead)[1] == "“phonorecord” have their meanings in title 17;"
    assert _texts(idx["usc18-s2318-b"].tail) == ["and these definitions apply to this section."]
