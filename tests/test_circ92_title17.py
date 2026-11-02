# tests/test_circ92_title17.py
"""Circular 92 front end, part 2: classified lines -> the Title 17 tree.

The tests on the real PDF share one parse of chapters 1 and 5 (conftest: `ch1_ch5_title17`).
A rule the fixture cannot pin on its own (an override, a cross-reference that wraps onto a new
line, flush text after a list) runs on synthetic lines in the shape layout.py returns them.
"""
import pytest

from ingest.books.circular92.labels import SEQ, expected_next, resolve, resolve_level
from ingest.books.circular92.layout import RawLine, extract_lines
from ingest.books.circular92.text import HyphenDecider, clean_join
from ingest.books.circular92.title17 import UnitBuilder, section_heading_marks, title17_nodes, x_level
from ingest.books.overrides import Override, OverrideLedger, load_overrides
from ingest.books.registry import get_book

CH1 = ["101", "102", "103", "104", "104a", "105", "106", "106a", "107", "108", "109", "110", "111",
       "112", "113", "114", "115", "116", "117", "118", "119", "120", "121", "121a", "122"]
CH5 = [str(n) for n in range(501, 514)]


def _index(nodes):
    return {n.path: n for s in nodes for n in s.walk()}


def _text(lines, hyphens=None):
    return clean_join(lines, hyphens or HyphenDecider([], OverrideLedger([])))


@pytest.fixture(scope="module")
def idx(ch1_ch5_nodes):
    return _index(ch1_ch5_nodes)


S304_OWNERS = {"usc17-s304-a-1-B-flush", "usc17-s304-a-1-C-flush"}  # the shipped text_owner overrides
NOTE_HOMES = {"ch1-n15-home", "ch1-n22-home", "ch1-n25-home"}  # the shipped note_home overrides (notes.py)


@pytest.fixture(scope="module")
def ch3(circ92_pdf):
    """Chapter 3 (PDF 195-206), parsed with the shipped overrides: (index, out_of_sequence, ledger)."""
    lines = extract_lines(circ92_pdf, range(195, 207))
    ledger = OverrideLedger(load_overrides(get_book("circ92").overrides_path))
    nodes, out_of_sequence = title17_nodes(lines, HyphenDecider(lines, ledger), ledger)
    return _index(nodes), out_of_sequence, ledger


# --- synthetic lines ---------------------------------------------------------------------------

def _line(text, x0=54.2, page=30, *, kind="body", marks=(), scope="Chapter 1"):
    font = ("TheMixOsF-Bold", 10.0) if kind == "heading" else ("MinionC", 10.2)
    return RawLine(pdf_page=page + 22, page=page, scope=scope, x0=x0, top=0.0, text=text,
                   marks=list(marks), kind=kind, font=font, fonts=[font[0]])


def _heading(text, page=30, scope="Chapter 1"):
    return _line(text, 53.7, page, kind="heading", scope=scope)


def _parse(*lines, overrides=()):
    ledger = OverrideLedger(list(overrides))
    nodes, out_of_sequence = title17_nodes(list(lines), HyphenDecider(lines, ledger), ledger)
    return _index(nodes), out_of_sequence, ledger


def _texts(lines):
    return [line.text for line in lines]


# --- labels ------------------------------------------------------------------------------------

def test_indent_levels():
    # A unit's first line sits at x = 54 + 12 * level (research report): (a) 66 … (aa) 126.
    assert [x_level(x) for x in (54.0, 66.0, 78.0, 90.0, 102.0, 114.0, 126.0)] == [0, 1, 2, 3, 4, 5, 6]
    assert [x_level(x) for x in (53.3, 65.3, 77.3, 90.5)] == [0, 1, 2, 3]  # set a little off


def test_label_sequences():
    assert SEQ[1][:3] == ["a", "b", "c"] and SEQ[1][26] == "aa"
    assert SEQ[2][:3] == ["1", "2", "3"]
    assert SEQ[3][:3] == ["A", "B", "C"]
    assert SEQ[4][:5] == ["i", "ii", "iii", "iv", "v"] and SEQ[4][8] == "ix" and SEQ[4][-1] == "xxx"
    assert SEQ[5][:4] == ["I", "II", "III", "IV"]
    assert SEQ[6][:2] == ["aa", "bb"]
    assert SEQ[7][:2] == ["AA", "BB"]


@pytest.mark.parametrize("label,stack,x,expected", [
    ("i", {1: "h"}, 4, 1),                   # sequence says letter i after h
    ("i", {1: "a", 2: "3", 3: "A"}, 5, 4),   # roman i under (A), even though set one level deep
    ("A", {1: "a", 2: "3"}, 4, 3),           # unambiguous class beats the indent
    ("v", {1: "u"}, 4, 1), ("ii", {1: "a", 2: "1", 3: "A", 4: "i"}, 4, 4)])
def test_resolve_level(label, stack, x, expected):
    assert resolve_level(label, stack, x) == expected


@pytest.mark.parametrize("label,stack,x,expected", [
    ("i", {1: "h", 2: "1", 3: "A"}, 4, 4),   # both fit: the indent decides
    ("i", {1: "h", 2: "1", 3: "A"}, 1, 1),
    ("x", {1: "a"}, 4, 4),                   # neither fits: the indent decides
    ("x", {1: "a"}, 2, 1),
    ("I", {1: "a", 2: "1", 3: "A", 4: "i"}, 3, 5),  # (I) after (A): subclause; letter I would need H before it
])
def test_resolve_level_ties_go_to_the_indent(label, stack, x, expected):
    assert resolve_level(label, stack, x) == expected


def test_resolve_says_when_no_rule_placed_the_label():
    assert resolve("i", {1: "h", 2: "1", 3: "A"}, 4) == (4, True)
    # (I) fits as the letter after (H) and as the subclause under (ii); the indent sits between.
    assert resolve("I", {1: "a", 2: "1", 3: "H", 4: "ii"}, 4) == (3, False)
    assert resolve("q1", {}, 2) == (2, False)  # no sequence holds it


def test_expected_next():
    assert expected_next({}, 4) == "i"
    assert expected_next({1: "z"}, 1) == "aa"
    assert expected_next({4: "xxx"}, 4) is None  # the end of the sequence
    assert expected_next({1: "A"}, 1) is None  # an override put a label at a level not its own


# --- the fixture: chapters 1 and 5 -------------------------------------------------------------

def test_sections_in_ch1_ch5(ch1_ch5_nodes):
    assert [n.path for n in ch1_ch5_nodes] == [f"usc17-s{num}" for num in CH1 + CH5]
    assert len(ch1_ch5_nodes) == 25 + 13


def test_every_node_is_title_17_in_its_chapter(ch1_ch5_nodes):
    for section in ch1_ch5_nodes:
        scope = "Chapter 1" if section.path.removeprefix("usc17-s") in CH1 else "Chapter 5"
        for node in section.walk():
            assert (node.source_type, node.jurisdiction, node.scope) == ("usc_title", "US", scope)
            assert node.kind in ("rule", "definition", "repealed")


def test_fair_use(idx, ch1_ch5_hyphens):
    s107 = idx["usc17-s107"]
    assert s107.label == "17 U.S.C. §107"
    assert s107.heading == "Limitations on exclusive rights: Fair use"
    assert s107.kind == "rule" and s107.pages() == (20, 20)
    assert [c.path for c in s107.children] == [f"usc17-s107-{n}" for n in range(1, 5)]
    assert idx["usc17-s107-4"].label == "17 U.S.C. §107(4)"
    own = _text(s107.own_lines(), ch1_ch5_hyphens)
    assert own.startswith("Notwithstanding the provisions of sections 106 and 106A, the fair use")


def test_flush_text_after_a_list_is_the_parents_tail(idx):
    s107 = idx["usc17-s107"]
    assert _texts(s107.tail)[0].startswith("The fact that a work is unpublished")
    assert _texts(idx["usc17-s107-4"].all_lines())[-1] == "righted work."
    # The line before the drop ends the item ("misidentification,"), so the flush text starts at the drop.
    assert _texts(idx["usc17-s512-f-2"].all_lines())[-1] == "misidentification,"
    assert _texts(idx["usc17-s512-f"].tail)[0].startswith("shall be liable for any damages")


def test_text_between_two_children_is_the_earlier_childs_tail(idx):
    # §104A(d)(2)'s flush text is set as (d)'s, then (d)(3) follows: it can only be (2)'s.
    d2, d = idx["usc17-s104a-d-2"], idx["usc17-s104a-d"]
    assert _texts(d2.tail)[0].startswith("In the event that notice is provided under both")
    assert d.tail == []
    assert [c.path for c in d.children][-1] == "usc17-s104a-d-4"


def test_section_title_continues_on_the_next_heading_line(idx):
    assert idx["usc17-s108"].heading == "Limitations on exclusive rights: Reproduction by libraries and archives"
    assert idx["usc17-s108"].pages()[0] == 20


def test_section_letter_is_a_capital_in_the_label_and_lowercase_in_the_path(idx):
    assert idx["usc17-s106a"].label == "17 U.S.C. §106A"
    assert idx["usc17-s106a-a-1-B"].label == "17 U.S.C. §106A(a)(1)(B)"


def test_definitions(idx, ch1_ch5_hyphens):
    s101 = idx["usc17-s101"]
    assert len(s101.children) == 54 and all(c.kind == "definition" for c in s101.children)
    assert s101.children[0].path == "usc17-s101-def-anonymous-work"
    assert s101.children[-1].path == "usc17-s101-def-wto-agreement"
    assert _text(s101.lead, ch1_ch5_hyphens) == (
        "Except as otherwise provided in this title, as used in this title, the following terms "
        "and their variant forms mean the following:")
    joint = idx["usc17-s101-def-joint-work"]
    assert joint.term == "joint work" and joint.label == "17 U.S.C. §101 (“joint work”)"
    assert joint.heading == "" and joint.pages() == (4, 4)
    assert _text(joint.lead, ch1_ch5_hyphens).startswith("A “joint work” is a work prepared by two")
    assert idx["usc17-s101-def-audiovisual-works"].term == "Audiovisual works"


def test_definitions_have_their_own_subdivisions(idx):
    five = idx["usc17-s101-def-international-agreement-5"]
    assert five.kind == "definition"
    assert five.lead[0].marks == [13]
    assert five.label == "17 U.S.C. §101 (“international agreement”)(5)"
    for path in ("usc17-s101-def-work-made-for-hire-1", "usc17-s101-def-work-made-for-hire-2",
                 "usc17-s101-def-united-states-work-1-D"):
        assert path in idx


def test_a_definitions_own_paragraph_restarts_its_subdivisions_and_leads_them(idx):
    # "A work of visual art does not include— (A)(i) …": (A) is the definition's, not (2)'s, and
    # the paragraph that introduces it is its lead, not (2)'s tail.
    assert "usc17-s101-def-work-of-visual-art-A-i" in idx
    assert "usc17-s101-def-work-of-visual-art-2-A" not in idx
    assert _texts(idx["usc17-s101-def-work-of-visual-art-A"].lead) == ["A work of visual art does not include—"]
    assert idx["usc17-s101-def-work-of-visual-art-2"].tail == []
    hire = idx["usc17-s101-def-work-made-for-hire"]
    assert [c.path.removeprefix(hire.path) for c in hire.children] == ["-1", "-2", "-A", "-B"]
    assert _texts(idx["usc17-s101-def-work-made-for-hire-A"].lead)[0].startswith("In determining whether any work")
    # "by the courts or the Copyright Office. Paragraph (2) …" runs on into the drop: flush text.
    assert _texts(hire.tail)[0].startswith("by the courts or the Copyright Office.")


def test_a_list_inside_flush_text_stays_text(idx):
    # §110's flush text after (11): "… accreditation— (A) … (B) …", then more flush paragraphs.
    s110 = idx["usc17-s110"]
    assert s110.children[-1].path == "usc17-s110-11" and s110.children[-1].children == []
    assert "usc17-s110-A" not in idx and "usc17-s110-11-A" not in idx
    tail = _texts(s110.tail)
    assert tail[0].startswith("The exemptions provided under paragraph (5)")
    assert "(A) with respect to an institution providing post-secondary education, shall" in tail
    assert tail[-1] == "this section."


def test_label_case_kept(idx):
    assert "usc17-s122-a-4-B" in idx
    assert idx["usc17-s122-a-4-B"].label == "17 U.S.C. §122(a)(4)(B)"
    assert idx["usc17-s122-a-4-B"].pages()[0] == 141


def test_inline_labels_after_heading_dash(idx):
    assert "usc17-s506-a" in idx and "usc17-s506-a-1" in idx
    assert idx["usc17-s506-a"].heading == "Criminal Infringement"
    assert idx["usc17-s506-a-1"].heading == "In general"
    assert idx["usc17-s512-a"].heading == "Transitory Digital Network Communications"  # ". —"
    # "(c) Exceptions.—(1) The modification …" is cut where (1) starts.
    c, c1 = idx["usc17-s106a-c"], idx["usc17-s106a-c-1"]
    assert _texts(c.lead) == ["(c) Exceptions.—"] and c.heading == "Exceptions"
    assert _texts(c1.lead)[0].startswith("(1) The modification of a work of visual art")
    assert c1.lead[0].page == 19 and not c1.lead[0].para_start


def test_labels_on_a_line_may_be_spaced(idx):
    # "(3) (A) In a case of infringement …" (§504(c)(3), p. 203)
    assert _texts(idx["usc17-s504-c-3-A"].lead)[0].startswith("(3) (A) In a case of infringement")
    assert [c.path for c in idx["usc17-s504-c-3"].children] == [
        "usc17-s504-c-3-A", "usc17-s504-c-3-B", "usc17-s504-c-3-C"]


def test_506_a_3_fixed(idx):
    for tail in ("A", "A-i", "A-ii", "B", "B-i", "B-ii"):
        assert f"usc17-s506-a-3-{tail}" in idx
    assert not any(p.startswith("usc17-s506-a-3-A-i-") for p in idx)
    assert [c.path for c in idx["usc17-s506-a-3"].children] == ["usc17-s506-a-3-A", "usc17-s506-a-3-B"]


def test_repealed_stub(idx):
    stub = idx["usc17-s509"]
    assert stub.kind == "repealed" and stub.label == "17 U.S.C. §509" and stub.heading == "[Repealed]"
    assert _texts(stub.lead) == ["[Repealed]"] and stub.children == [] and stub.tail == []
    assert stub.lead[0].marks == [8]  # ch.5 n.8 annotates it
    assert stub.pages() == (206, 206)


def test_a_repealed_subdivision_is_repealed(idx):
    repealed = [n for n in idx.values() if n.kind == "repealed" and "-" in n.path.removeprefix("usc17-")]
    assert [(n.path, _texts(n.lead)) for n in repealed] == [("usc17-s114-i", ["[(i) Repealed.]"])]


def test_a_chapter_opener_never_joins_the_last_section(idx):
    # Chapter 5's title and contents (printed p. 199) come after chapter 1's notes: they are not §122.
    assert idx["usc17-s122"].pages() == (139, 146)
    assert idx["usc17-s501"].pages()[0] == 200


def test_section_marks_stay_on_their_lines(idx):
    # A footnote marker lifted from a body line stays with that line, for the note links.
    assert idx["usc17-s101-def-architectural-work"].lead[-1].marks == [3]


def test_heading_markers_by_section(circ92_ch1_ch5_lines):
    # A heading is no node's text line, so a note on it is linked through this (Task 6).
    marks = section_heading_marks(circ92_ch1_ch5_lines)
    assert list(marks)[:2] == ["usc17-s101", "usc17-s102"] and len(marks) == 38
    assert marks["usc17-s101"] == [2] and marks["usc17-s506"] == [6] and marks["usc17-s505"] == []
    assert marks["usc17-s108"] == [41]  # on the line the title wraps onto
    assert marks["usc17-s509"] == [8]


def test_no_unplaced_labels(ch1_ch5_title17):
    assert ch1_ch5_title17[1] == []


# --- the rules, on synthetic lines -------------------------------------------------------------

def test_para_start_is_an_indent_of_more_than_six():
    idx, _, _ = _parse(
        _heading("§ 900 · Test"),
        _line("Except as provided in this section, the owner may—", 66.0),
        _line("do the following:", 54.3),
        _line("(1) reproduce the work; and", 78.0),
        _line("(2) distribute copies of the work to the public by sale or", 78.0),
        _line("other transfer of ownership, rental, lease, lending, or any", 66.2),
        _line("other means.", 66.4),  # the jitter of a justified line is no indent
    )
    lines = idx["usc17-s900"].all_lines()
    assert [line.para_start for line in lines] == [True, False, True, False, False, False]


def test_a_sections_text_starts_a_paragraph_even_under_a_wrapped_title():
    idx, _, _ = _parse(
        _heading("§ 900 · Limitations on exclusive rights:"),
        _line("Reproduction by libraries and archives", 90.3, kind="heading"),
        _line("(a) Except as otherwise provided in this title, it is not an infringement", 66.0),
    )
    assert idx["usc17-s900"].heading == "Limitations on exclusive rights: Reproduction by libraries and archives"
    assert idx["usc17-s900-a"].lead[0].para_start


def test_a_cross_reference_wrapped_onto_a_new_line_is_not_a_label():
    idx, oos, _ = _parse(
        _heading("§ 900 · Test"),
        _line("(a) The rights in this section are subject to subsection", 66.0),
        _line("(c)(1) and to the limits of this title.", 54.2),
        _line("(b) The owner may not waive the rights under paragraphs (1) and", 66.0),
        _line("(2) of subsection (a).", 54.2),
        _line("(c) A transfer is not a waiver.", 66.0),
    )
    assert [c.path for c in idx["usc17-s900"].children] == ["usc17-s900-a", "usc17-s900-b", "usc17-s900-c"]
    assert _texts(idx["usc17-s900-a"].lead)[1].startswith("(c)(1) and to the limits")
    assert oos == []


def test_a_label_after_a_line_ending_in_a_section_number_or_label_is_a_cross_reference():
    # PDF p. 332: "… sections 112(e)(1) and 114(d)" / "(2) during the 3 years …", where (2) is the
    # next paragraph in sequence but the reference is 114(d)(2).
    idx, oos, _ = _parse(
        _heading("§ 900 · Test"),
        _line("(a) A transmission is exempt if—", 66.0),
        _line("(1) the entity pays royalties as required under sections 112(e)(1) and 114(d)", 78.0),
        _line("(2) during the 3 years before that date; and", 78.0),
        _line("(2) the entity gives notice.", 78.0),
    )
    assert [c.path for c in idx["usc17-s900-a"].children] == ["usc17-s900-a-1", "usc17-s900-a-2"]
    assert _texts(idx["usc17-s900-a-1"].lead)[1].startswith("(2) during the 3 years")
    assert oos == []


def test_or_after_a_label_list_admits_only_the_next_label_in_sequence():
    # PDF p. 344: "… under paragraph (1), (2), or" / "(3) by 1 or more claimants …" inside (6).
    idx, oos, _ = _parse(
        _heading("§ 900 · Test"),
        _line("(1) A claim.", 78.0),
        _line("(2) A defense.", 78.0),
        _line("(3) A counterclaim.", 78.0),
        _line("(4) A single claim permitted under paragraph (1), (2), or", 78.0),
        _line("(3) by 1 or more claimants.", 66.0),
        _line("(5) A notice served under section 104A(d)(2)(A), or", 78.0),
        _line("(6) a notice served by mail.", 78.0),
    )
    assert _texts(idx["usc17-s900-4"].lead) == [
        "(4) A single claim permitted under paragraph (1), (2), or", "(3) by 1 or more claimants."]
    assert "usc17-s900-6" in idx  # ", or" after a label, then the next label in sequence: an item
    assert oos == []


def test_a_conjunction_wrapped_onto_its_own_line_still_closes_the_item():
    # PDF p. 330: "… as applicable;" / "and" / "(bb) the rights owner’s …"
    idx, oos, _ = _parse(
        _heading("§ 900 · Test"),
        _line("(a) The amount is the difference between—", 66.0),
        _line("(1) the receipts for all such transmissions covered under the license, as applicable;", 78.0),
        _line("and", 66.3),
        _line("(2) the payments for outside legal expenses, as applicable;", 78.0),
        _line("or", 66.3),
        _line("(3) the amounts paid to artists.", 78.0),
    )
    assert [c.path for c in idx["usc17-s900-a"].children] == ["usc17-s900-a-1", "usc17-s900-a-2", "usc17-s900-a-3"]
    assert _texts(idx["usc17-s900-a-1"].lead)[-1] == "and"
    assert oos == []


def test_a_conjunction_wrapped_in_ch1_ch5_closes_the_item(idx):
    # §122(j)(2) p. 146, §503(a)(1) p. 201: "…;" / "and" / "(B) …", "(C) …"
    assert _texts(idx["usc17-s503-a-1-C"].lead)[0].startswith("(C) of records documenting the manufacture")
    assert any(p.endswith("-B") and _texts(n.lead)[0].startswith("(B) includes a television broadcast station")
               for p, n in idx.items() if p.startswith("usc17-s122-j-"))


def test_an_indented_label_read_as_a_cross_reference_is_reported():
    # A wrapped cross-reference sits under the line before; an indented one is a unit the rules missed.
    idx, oos, _ = _parse(
        _heading("§ 900 · Test"),
        _line("(a) The rights under this section are subject to subsection", 66.0),
        _line("(b) shall apply to every work.", 78.0),
    )
    assert "usc17-s900-a-b" not in idx and "usc17-s900-b" not in idx
    assert oos == ["p.30: (b) shall apply to every work."]


def test_a_label_after_and_is_a_cross_reference_even_in_sequence():
    idx, _, _ = _parse(
        _heading("§ 900 · Test"),
        _line("(a) The rights under subsections (a) and", 66.0),
        _line("(b) apply to every work.", 54.2),
    )
    assert list(idx) == ["usc17-s900", "usc17-s900-a"]


def test_a_second_drop_after_flush_text_flushes_again():
    idx, _, _ = _parse(
        _heading("§ 900 · Test"),
        _line("(1) The owner shall pay—", 78.0),
        _line("(A) an amount equal to—", 90.0),
        _line("(i) the receipts; and", 102.0),
        _line("(ii) the payments,", 102.0),
        _line("less any credit, as determined by", 78.3),  # (A)'s flush text
        _line("the collective;", 78.2),
        _line("the remainder is paid to the artists.", 66.1),  # one level up again: (1)'s
    )
    assert _texts(idx["usc17-s900-1-A"].tail) == ["less any credit, as determined by", "the collective;"]
    assert _texts(idx["usc17-s900-1"].tail) == ["the remainder is paid to the artists."]


def test_a_quoted_term_on_a_list_items_line_does_not_start_a_definition():
    idx, _, _ = _parse(
        _heading("§ 101 · Definitions"),
        _line("As used in this title:", 54.0),
        _line("A “work” is—", 65.3),
        _line("(1) a “thing” of value; or", 66.0),  # a first line set too shallow, at the definitions' indent
        _line("(2) a service.", 66.0),
    )
    assert "usc17-s101-def-thing" not in idx
    assert [c.path for c in idx["usc17-s101-def-work"].children] == ["usc17-s101-def-work-1", "usc17-s101-def-work-2"]


def test_a_label_followed_by_of_this_is_a_cross_reference():
    idx, _, _ = _parse(
        _heading("§ 900 · Test"),
        _line("(a) The owner may act.", 66.0),
        _line("(1) of this section does not apply to a transfer.", 54.2),
    )
    assert "usc17-s900-a-1" not in idx
    assert len(idx["usc17-s900-a"].lead) == 2


def test_a_label_after_a_list_item_ending_in_a_comma_starts_an_item():
    idx, _, _ = _parse(
        _heading("§ 900 · Test"),
        _line("(a) Nothing in this section—", 66.0),
        _line("(1) shall be considered to have legal significance,", 78.0),
        _line("(2) shall be interpreted as approval.", 78.0),
    )
    assert "usc17-s900-a-2" in idx


def test_a_label_after_a_sentence_end_starts_a_block_even_out_of_sequence():
    idx, oos, _ = _parse(
        _heading("§ 900 · Test"),
        _line("(a) The owner may act.", 66.0),
        _line("(c) The owner may not act.", 66.0),
    )
    assert "usc17-s900-c" in idx
    assert oos == ["p.30: (c) The owner may not act."]


def test_an_indented_label_starts_a_block_even_out_of_sequence():
    idx, oos, _ = _parse(
        _heading("§ 900 · Test"),
        _line("(a) The owner may act if the", 66.0),
        _line("(c) other party consents", 78.0),
    )
    assert "usc17-s900-a-c" not in idx and "usc17-s900-c" in idx
    assert oos == ["p.30: (c) other party consents"]


def test_a_label_level_override_is_applied_before_the_rule():
    lines = (
        _heading("§ 900 · Test"),
        _line("(a) The owner may act—", 66.0),
        _line("(1) in person; or", 78.0),
        _line("(i) by an agent.", 102.0),
    )
    idx, oos, _ = _parse(*lines)
    assert "usc17-s900-a-1-i" in idx and oos == []
    override = Override(id="t1", kind="label_level", page=30, match="(i) by an", value="1", reason="test")
    idx, oos, ledger = _parse(*lines, overrides=[override])
    assert "usc17-s900-i" in idx and "usc17-s900-a-1-i" not in idx
    assert oos == [] and ledger.unused() == []


def test_a_repeated_label_is_reported_and_kept_as_text():
    idx, oos, _ = _parse(
        _heading("§ 900 · Test"),
        _line("(a) The owner may act.", 66.0),
        _line("(b) The owner may not act.", 66.0),
        _line("(a) The owner may act again.", 66.0),
    )
    assert _texts(idx["usc17-s900-b"].lead) == ["(b) The owner may not act.", "(a) The owner may act again."]
    assert oos == ["p.30: (a) The owner may act again."]


def test_a_flush_paragraphs_first_line_moves_with_it():
    idx, _, _ = _parse(
        _heading("§ 900 · Test"),
        _line("The factors to be considered shall include—", 66.0),
        _line("(1) the purpose of the use; and", 78.0),
        _line("(2) the effect of the use upon the market for the", 78.0),
        _line("work.", 66.1),
        _line("The fact that a work is unpublished shall not itself bar a finding of", 65.5),
        _line("fair use.", 54.5),
    )
    assert _texts(idx["usc17-s900-2"].lead) == ["(2) the effect of the use upon the market for the", "work."]
    assert _texts(idx["usc17-s900"].tail) == [
        "The fact that a work is unpublished shall not itself bar a finding of", "fair use."]


def test_a_wrapped_heading_can_carry_inline_labels_on_its_second_line():
    idx, oos, _ = _parse(
        _heading("§ 900 · Test"),
        _line("(a) Violations Regarding Circumvention of Technological Mea-", 66.0),
        _line("sures.—(1)(A) No person shall circumvent a technological measure", 54.2),
        _line("that effectively controls access.", 54.2),
        _line("(B) The prohibition takes effect later.", 90.0),
    )
    a, a1a = idx["usc17-s900-a"], idx["usc17-s900-a-1-A"]
    assert _texts(a.lead) == ["(a) Violations Regarding Circumvention of Technological Mea-", "sures.—"]
    assert a.heading == "Violations Regarding Circumvention of Technological Measures"
    assert _texts(a1a.lead) == ["(1)(A) No person shall circumvent a technological measure",
                                "that effectively controls access."]
    assert idx["usc17-s900-a-1"].lead == [] and "usc17-s900-a-1-B" in idx
    assert oos == []


def test_lines_outside_a_chapters_sections_are_skipped():
    idx, _, _ = _parse(
        _line("Chapter 9", 54.2, kind="title", scope="Chapter 9"),
        _line("901 Definitions 264", 58.0, scope="Chapter 9"),  # a contents line read as body
        _heading("§ 901 · Definitions", scope="Chapter 9"),
        _line("(a) As used in this chapter—", 66.0, scope="Chapter 9"),
        _line("Chapter 9 · Notes", 54.2, kind="notes_heading", scope="Chapter 9"),
        _line("1 Note text.", 54.2, kind="note", scope="Chapter 9"),
        _line("1001 Definitions 278", 58.0, scope="Chapter 10"),
        _line("Appendix A text", 54.2, scope="Appendix A"),
    )
    assert list(idx) == ["usc17-s901", "usc17-s901-a"]
    assert _texts(idx["usc17-s901-a"].lead) == ["(a) As used in this chapter—"]


def test_a_lowercase_section_letter_is_cited_as_a_capital():
    idx, _, _ = _parse(_heading("§ 121a · Reproduction"), _line("Text.", 66.0))
    assert idx["usc17-s121a"].label == "17 U.S.C. §121A"


def test_only_chapter_sections_are_title_17():
    idx, _, _ = _parse(
        _heading("§ 901 · Definitions", scope="Chapter 9"),
        _line("Text of 901.", 66.0, scope="Chapter 9"),
        _heading("§ 2318 · Trafficking in counterfeit labels", page=400, scope="Appendix N"),
        _line("(a) Whoever knowingly traffics—", 66.0, page=400, scope="Appendix N"),
    )
    assert list(idx) == ["usc17-s901"]


def test_a_subchapter_heading_does_not_continue_the_title_before_it():
    idx, _, _ = _parse(
        _heading("§ 509 · [Repealed]"),
        _line("subchapter b—remedies", 53.8, kind="heading"),
        _heading("§ 510 · Remedies"),
        _line("Text.", 66.0),
    )
    assert idx["usc17-s509"].heading == "[Repealed]" and idx["usc17-s509"].kind == "repealed"


def test_a_section_heading_without_text_is_an_error():
    with pytest.raises(ValueError, match=r"17 U.S.C. §900 \(p. 30\) has a heading and no text"):
        _parse(_heading("§ 900 · Test"), _heading("§ 901 · Next"), _line("Text.", 66.0))


def test_labels_that_do_not_go_deeper_along_a_line_are_reported_and_kept_as_text():
    idx, oos, _ = _parse(
        _heading("§ 900 · Test"),
        _line("(a) The owner may act.", 66.0),
        _line("(1)(b) The owner may not act.", 78.0),
    )
    assert list(idx) == ["usc17-s900", "usc17-s900-a"]
    assert oos == ["p.30: (1)(b) The owner may not act."]


def test_a_flush_line_two_indents_left_does_not_take_the_line_before():
    idx, _, _ = _parse(
        _heading("§ 900 · Test"),
        _line("(a) The owner may—", 66.0),
        _line("(1) act—", 78.0),
        _line("(A) in person, or by an agent whom the owner", 90.0),
        _line("appoints in writing for the", 78.2),
        _line("purpose, if the agent consents.", 54.2),
    )
    assert _texts(idx["usc17-s900-a-1-A"].lead)[-1] == "appoints in writing for the"
    assert _texts(idx["usc17-s900-a"].tail) == ["purpose, if the agent consents."]


def test_inline_labels_need_the_units_first_dash():
    idx, _, _ = _parse(
        _heading("§ 900 · Test"),
        _line("(a) In General.—The term “agent” means a person who acts for", 66.0),
        _line("another, as in “Agency.—(1) Scope.” and the like.", 54.2),
    )
    assert "usc17-s900-a-1" not in idx and len(idx["usc17-s900-a"].lead) == 2


def test_a_defined_term_is_normalized():
    idx, _, _ = _parse(
        _heading("§ 101 · Definitions"),
        _line("As used in this title:", 54.0),
        _line("The term “\ufb01nancial gain” includes receipt of anything of value.", 65.5),
    )
    gain = idx["usc17-s101-def-financial-gain"]
    assert gain.term == "financial gain" and gain.label == "17 U.S.C. §101 (“financial gain”)"


def test_a_split_lines_marker_goes_with_the_text_after_the_dash():
    idx, _, _ = _parse(
        _heading("§ 900 · Test"),
        _line("(c) Exceptions.—(1) The modification of a work is not a distortion.", 66.0, marks=[5]),
    )
    assert idx["usc17-s900-c"].lead[0].marks == [] and idx["usc17-s900-c-1"].lead[0].marks == [5]


def test_contents_and_titles_never_join_a_section():
    idx, _, _ = _parse(
        _heading("§ 900 · Test"),
        _line("(a) The owner may act.", 66.0),
        _line("Subject Matter and Scope of Copyright", 54.2, kind="title"),
        _line("901 Definitions ......................... 264", 58.0, kind="toc"),
        _line("(b) The owner may not act.", 66.0),
    )
    assert _texts(idx["usc17-s900-a"].lead) == ["(a) The owner may act."]
    assert "usc17-s900-b" in idx


# --- text_owner overrides (ruling R8) ------------------------------------------------------------

def _owner(match, value, oid="o1"):
    return Override(id=oid, kind="text_owner", page=30, match=match, value=value, reason="test")


def test_a_text_owner_override_puts_the_line_and_its_continuation_in_the_owners_tail():
    # §304(a)(1)(B)'s shape: (a)'s flush text is set at (2)'s continuation indent.
    lines = (
        _heading("§ 900 · Test"),
        _line("(a) In the case of—", 66.0),
        _line("(1) any posthumous work, or", 78.0),
        _line("(2) any work copyrighted by an employer", 78.0),
        _line("for whom it is made for hire,", 66.3),
        _line("the proprietor shall be entitled to a re-", 66.2),
        _line("newal of the copyright for 67 years.", 66.2),
        _line("(b) Other works.", 66.0),
    )
    idx, _, _ = _parse(*lines)  # by layout alone, all of it is (2)'s
    assert _texts(idx["usc17-s900-a-2"].lead)[-1] == "newal of the copyright for 67 years."
    idx, oos, ledger = _parse(*lines, overrides=[_owner("the proprietor shall", "usc17-s900-a")])
    assert _texts(idx["usc17-s900-a"].tail) == [
        "the proprietor shall be entitled to a re-", "newal of the copyright for 67 years."]
    assert _texts(idx["usc17-s900-a-2"].lead) == ["(2) any work copyrighted by an employer", "for whom it is made for hire,"]
    assert "usc17-s900-b" in idx and oos == [] and ledger.unused() == []


def test_lines_under_owned_text_keep_its_indent():
    # §304(a)(1)(C): the owned line sits two indents left of the open clause; its next line follows it.
    idx, _, _ = _parse(
        _heading("§ 900 · Test"),
        _line("(1) In the case of any other work—", 78.0),
        _line("(A) the author,", 90.0),
        _line("(i) if living, or", 102.0),
        _line("(ii) if not living,", 102.0),
        _line("shall be entitled to a renewal and extension of the copyright for", 66.5),
        _line("a further term of 67 years.", 66.3),
        overrides=[_owner("shall be entitled to a renewal", "usc17-s900-1-A")],
    )
    assert _texts(idx["usc17-s900-1-A"].tail) == [
        "shall be entitled to a renewal and extension of the copyright for", "a further term of 67 years."]
    assert idx["usc17-s900-1"].tail == []


def test_units_below_the_owner_close_as_after_flush_text():
    idx, _, _ = _parse(
        _heading("§ 900 · Test"),
        _line("(a) In the case of—", 66.0),
        _line("(1) any work, or", 78.0),
        _line("(2) any work made for hire,", 78.0),
        _line("the owner shall be entitled to", 78.0),
        _line("(A) a renewal; and", 90.0),  # a list inside the owner's text, not (2)'s
        overrides=[_owner("the owner shall be entitled", "usc17-s900-a")],
    )
    assert _texts(idx["usc17-s900-a"].tail) == ["the owner shall be entitled to", "(A) a renewal; and"]
    assert "usc17-s900-a-2-A" not in idx


@pytest.mark.parametrize("owner", ["usc17-s900-d", "usc17-s900-b"])  # no such unit; a closed one
def test_a_text_owner_that_is_not_an_open_unit_is_an_error(owner):
    with pytest.raises(ValueError, match="override 'o1'"):
        _parse(
            _heading("§ 900 · Test"),
            _line("(a) The owner may act.", 66.0),
            _line("(b) The owner may not act.", 66.0),
            _line("(c) The owner shall act—", 66.0),
            _line("(1) at once;", 78.0),
            _line("the act being in writing.", 66.0),
            overrides=[_owner("the act being", owner)],
        )


def test_section_304_a_1_flush_text_has_its_owners(ch3):
    idx, oos, _ = ch3
    b, c = idx["usc17-s304-a-1-B"], idx["usc17-s304-a-1-C"]
    assert _texts(b.tail) == ["the proprietor of such copyright shall be entitled to a renewal and ex-",
                              "tension of the copyright in such work for the further term of 67 years."]
    assert _texts(idx["usc17-s304-a-1-B-ii"].all_lines())[-1] == "such work is made for hire,"
    assert _texts(c.tail) == ["shall be entitled to a renewal and extension of the copyright in such work for",
                              "a further term of 67 years."]
    assert idx["usc17-s304-a-1"].tail == [] and "usc17-s304-a-2-A" in idx
    assert oos == []


def test_every_shipped_override_is_used(ch3, ch1_ch5_title17, ch1_ch5_ledger):
    # The two §304 entries sit on printed p. 177 (chapter 3): the chapter 3 parse uses both, and
    # the chapter 1 + 5 parse leaves only them unused, with the note_home entries, which only the
    # notes read (tests/test_circ92_notes_appendices.py shows the ch. 1 notes use all three).
    assert {o.id for o in ch3[2].unused()} == NOTE_HOMES
    assert {o.id for o in ch1_ch5_ledger.unused()} == S304_OWNERS | NOTE_HOMES


# --- quoted text is the appendices' rule, not Title 17's (ruling R11) -----------------------------

def test_a_line_opening_with_a_quote_is_placed_by_the_title_17_rules():
    # In the appendices this line would open quoted text and keep the next one in (1); Title 17 quotes
    # no amendment, so the flush rule moves both to (a)'s tail.
    idx, _, _ = _parse(
        _heading("§ 900 · Test"),
        _line("(a) In this section, the terms—", 66.0),
        _line("(1) the term", 78.0),
        _line("“copy” has its meaning in section 101; and", 63.3),
        _line("the owner may act.", 54.2),
    )
    assert _texts(idx["usc17-s900-a"].tail) == ["“copy” has its meaning in section 101; and", "the owner may act."]
    assert _texts(idx["usc17-s900-a-1"].lead) == ["(1) the term"]


def test_title_17_never_reads_quoted_text(circ92_ch1_ch5_lines, monkeypatch):
    # Chapter 1's §101 has 11 definitions whose first line opens with a quote mark.
    def refuse(self, raw, para_start):
        raise AssertionError(f"Title 17 read p.{raw.page} {raw.text!r} as quoted text")

    monkeypatch.setattr(UnitBuilder, "_quoted", refuse)
    ledger = OverrideLedger(load_overrides(get_book("circ92").overrides_path))
    title17_nodes(circ92_ch1_ch5_lines, HyphenDecider(circ92_ch1_ch5_lines, ledger), ledger)
