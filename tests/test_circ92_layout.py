# tests/test_circ92_layout.py
"""Circular 92 front end, part 1: PDF -> classified lines, and the text cleaning on top.

The tests on the real PDF extract only the pages they need (pdfplumber takes ~0.15 s a page).
A rule the fixture cannot exercise (a footnote-font digit on a note line, the word-gap glue)
runs on synthetic words in the shape pdfplumber returns them.
"""
import pdfplumber
import pytest

from ingest.books.circular92.layout import (
    APPENDICES_DIVIDER,
    BODY_PDF_PAGES,
    PAGE_OFFSET,
    SCOPE_STARTS,
    WORD_SETTINGS,
    RawLine,
    extract_lines,
    lines_from_words,
)
from ingest.books.circular92.text import HyphenDecider, clean_join, fix_small_caps
from ingest.books.models import TextLine
from ingest.books.overrides import Override, OverrideLedger


# --- synthetic words -------------------------------------------------------------------------

def _word(text, x0, *, font="MinionC", size=10.2, top=100.0, width=None):
    width = 5.0 * len(text) if width is None else width
    return {
        "text": text, "x0": x0, "x1": x0 + width, "top": top, "bottom": top + size,
        "fontname": f"ABCDEF+{font}", "size": size,
    }


def _run(*words):
    """Words laid out left to right with a normal space (2.3 pt) between them."""
    out, x = [], 54.0
    for text, kw in words:
        w = _word(text, x, **kw)
        out.append(w)
        x = w["x1"] + 2.3
    return out


def _body_page(words, pdf_page=60, notes_scopes=None):
    return lines_from_words(words, pdf_page, set() if notes_scopes is None else notes_scopes)


# --- the real PDF ----------------------------------------------------------------------------

def test_the_body_is_pdf_23_to_481_less_the_appendices_divider_and_the_printed_page_is_22_less():
    assert APPENDICES_DIVIDER == range(387, 389)
    assert list(BODY_PDF_PAGES) == [*range(23, 387), *range(389, 482)]
    assert all(p in BODY_PDF_PAGES for p in (23, 385, 386, 389, 481))
    assert not any(p in BODY_PDF_PAGES for p in (22, 387, 388, 482, 486))
    assert PAGE_OFFSET == 22
    assert [pdf for pdf, _ in SCOPE_STARTS] == sorted(pdf for pdf, _ in SCOPE_STARTS)
    assert SCOPE_STARTS[0] == (23, "Chapter 1") and SCOPE_STARTS[-1] == (481, "Appendix T")
    assert len(SCOPE_STARTS) == 35


@pytest.mark.parametrize("pdf,name", SCOPE_STARTS)
def test_scope_starts_open_with_their_title(circ92_pdf, pdf, name):
    lines = extract_lines(circ92_pdf, [pdf])
    assert [l.text for l in lines if l.kind == "title"][0] == name


def test_printed_page_is_pdf_minus_22(circ92_pdf):
    lines = extract_lines(circ92_pdf, [42])
    assert lines and all(l.pdf_page == 42 and l.page == 20 for l in lines)


def test_running_head_dropped_and_marker_lifted(circ92_pdf):
    lines = extract_lines(circ92_pdf, [24])
    first = [l for l in lines if l.kind != "toc"][0]
    assert first.text == "§101 · Definitions" and first.marks == [2] and first.kind == "heading"
    # the running head ("§101 Subject Matter and Scope") and the folio are gone
    assert not any("Subject Matter and Scope" in l.text for l in lines)
    assert not any(l.font[0].startswith("TheMixOsF") and l.font[1] < 8 for l in lines)


def test_marker_lifted_at_any_size(circ92_pdf):
    # Appendix G's marker 2 is set at 10.2 pt, the others at 10.5
    lines = extract_lines(circ92_pdf, [428])
    marked = [l for l in lines if l.marks]
    assert [(l.marks, l.kind) for l in marked] == [([2], "body")]
    assert marked[0].text.endswith("December 31, 2014.")


def test_note_lines_keep_digits(circ92_pdf):
    lines = extract_lines(circ92_pdf, [446])
    heading = [l for l in lines if l.kind == "notes_heading"]
    assert [l.text for l in heading] == ["Appendix M · Notes"]
    notes = [l for l in lines if l.kind == "note"]
    assert any("Pub. L. No. 116-26, 134 Stat. 1182" in l.text for l in notes)  # the book prints 116-26
    assert all(l.marks == [] for l in notes)
    # everything above the notes heading is not a note
    assert not any(l.kind == "note" and l.top < heading[0].top for l in lines)


@pytest.mark.parametrize("page,why", [
    (0, "front matter"), (22, "front matter"),
    (387, "appendices divider"), (388, "appendices divider"),
    (482, "back matter"), (486, "back matter"), (487, "back matter"),
])
def test_a_page_outside_the_body_is_refused_with_its_reason(circ92_pdf, page, why):
    with pytest.raises(ValueError, match=rf"PDF page {page} is not body: it is .*{why}"):
        extract_lines(circ92_pdf, [page])


def test_a_range_over_the_divider_cannot_bring_it_back(circ92_pdf):
    with pytest.raises(ValueError, match="PDF page 387 is not body"):
        extract_lines(circ92_pdf, range(385, 390))


def test_the_divider_is_the_appendices_part_title_and_contents(circ92_pdf):
    # edition data, like the scope starts: PDF 387 opens with the "Appendices" title, 388 goes on with the contents
    with pdfplumber.open(circ92_pdf) as pdf:
        first = lines_from_words(pdf.pages[386].extract_words(**WORD_SETTINGS), 387, set())
        second = lines_from_words(pdf.pages[387].extract_words(**WORD_SETTINGS), 388, set())
    assert (first[0].kind, first[0].text) == ("title", "Appendices")
    assert first[1].text == "appendix page"
    assert any(l.text.startswith("n Title 18—Crimes") or l.text.startswith("o Title 28—Judiciary") for l in first + second)
    assert not any(l.kind == "title" for l in second)


def test_chapter_15_ends_at_pdf_385_and_no_note_is_a_title(circ92_pdf):
    lines = extract_lines(circ92_pdf, [384, 385, 386, 389])  # 386 is blank
    chapter_15 = [l for l in lines if l.scope == "Chapter 15"]
    assert max(l.pdf_page for l in chapter_15) == 385
    assert chapter_15[-1].kind == "note" and chapter_15[-1].text.endswith("2176–2199.")
    assert not any(l.kind == "note" and l.font[0] == "TheMixOsF-ExtraBold" for l in lines)
    assert [(l.scope, l.kind, l.text) for l in lines if l.pdf_page == 389][:1] == [("Appendix A", "title", "Appendix A")]


def test_scopes(circ92_pdf):
    assert {l.scope for l in extract_lines(circ92_pdf, [221])} == {"Chapter 5"}
    assert {l.scope for l in extract_lines(circ92_pdf, [479])} == {"Appendix S"}
    assert {l.scope for l in extract_lines(circ92_pdf, [187])} == {"Chapter 1"}  # 188 is blank
    assert {l.scope for l in extract_lines(circ92_pdf, [189])} == {"Chapter 2"}


def test_line_kinds_on_the_first_chapter_pages(circ92_pdf):
    by_text = {l.text: l for l in extract_lines(circ92_pdf, [23, 24])}
    assert by_text["Chapter 1"].kind == "title" and by_text["Chapter 1"].marks == [1]
    assert by_text["Subject Matter and Scope of Copyright"].kind == "title"
    assert by_text["section page"].kind == "toc"
    assert by_text["§101 · Definitions"].kind == "heading"
    toc = [l for l in by_text.values() if l.kind == "toc"]
    assert any(l.text.startswith("101 Definitions") for l in toc)
    body = [l for l in extract_lines(circ92_pdf, [24]) if l.kind == "body"]
    assert body[0].text.startswith("Except as otherwise provided in this title")
    assert body[0].font == ("MinionC", 10.2) and body[0].fonts == ["MinionC"]


def test_a_word_split_by_a_size_change_is_one_word(circ92_pdf):
    # "106" + "A" is set at two sizes, so pdfplumber returns two words; the book prints "106A"
    texts = [l.text for l in extract_lines(circ92_pdf, [42])]
    assert any("sections 106 and 106A," in t for t in texts)


def test_a_word_cut_inside_one_typeface_is_one_word(circ92_pdf):
    assert any("accordance with the following procedures:" in l.text for l in extract_lines(circ92_pdf, [58]))


def test_small_caps_glued_to_the_roman_that_follows(circ92_pdf):
    # a small-caps word and the roman after it are cut 0.15-0.3 pt apart: no space before "(" or a closing quote
    texts = [l.text for l in extract_lines(circ92_pdf, [457])]
    assert any("section 3663a(c)(1)(A)(ii) of this title" in t for t in texts)  # was "3663a (c)(1)(A)(ii)"
    texts = [l.text for l in extract_lines(circ92_pdf, [409])]
    assert any("by striking “Whoever” and inserting" in t for t in texts)  # was "“Whoever ”"
    texts = [l.text for l in extract_lines(circ92_pdf, [464])]
    assert any("“Random Assignment of Rate Court Proceedings” to the end" in t for t in texts)
    texts = [l.text for l in extract_lines(circ92_pdf, [179])]
    assert any("by inserting “in General” after" in t for t in texts)


# --- line building, on synthetic words ------------------------------------------------------

def test_marker_is_lifted_from_body_lines_but_a_digit_on_a_note_line_is_text():
    notes_heading = _run(("Chapter", {"font": "TheMixOsF-Bold", "size": 10.0}), ("1", {"font": "TheMixOsF-Bold", "size": 10.0}),
                         ("·", {"font": "TheMixOsF-Bold", "size": 10.0}), ("Notes", {"font": "TheMixOsF-Bold", "size": 10.0}))
    digit = ("7", {"font": "MinionPro-Regular", "size": 10.5})

    def line(top):
        words = _run(("See", {}), ("section", {}), digit, ("below.", {}))
        for w in words:
            w["top"], w["bottom"] = top, top + 10.2
        return words

    for w in notes_heading:
        w["top"], w["bottom"] = 200.0, 210.0
    seen = set()
    lines = lines_from_words(line(100.0) + notes_heading + line(300.0), 60, seen)
    body, heading, note = lines
    assert (body.kind, body.text, body.marks) == ("body", "See section below.", [7])
    assert heading.kind == "notes_heading" and seen == {"Chapter 1"}
    assert (note.kind, note.text, note.marks) == ("note", "See section 7 below.", [])


def test_a_marker_is_one_to_three_digits_in_the_footnote_font_only():
    marker = {"font": "MinionPro-Regular", "size": 10.5}
    words = _run(("one", {}), ("12", marker), ("two", {}), ("123", marker), ("three", {}), ("1234", marker),
                 ("four", {}), ("7", {}), ("five", {}), ("8a", marker))
    (line,) = _body_page(words)
    assert line.marks == [12, 123]
    assert line.text == "one two three 1234 four 7 five 8a"


def test_a_notes_heading_makes_every_later_line_in_that_scope_a_note_and_no_other_scope():
    bold = {"font": "TheMixOsF-Bold", "size": 10.0}
    heading = _run(("Chapter", bold), ("1", bold), ("·", bold), ("Notes", bold))
    later_title = _run(("Appendix", {"font": "TheMixOsF-ExtraBold", "size": 13.0}))
    seen = set()
    first = lines_from_words(heading, 60, seen)
    assert first[0].kind == "notes_heading"
    assert [l.kind for l in lines_from_words(later_title, 61, seen)] == ["note"]  # still Chapter 1
    assert [l.kind for l in lines_from_words(later_title, 189, seen)] == ["title"]  # Chapter 2 has no notes yet
    # a bold line that merely mentions Notes is not the heading unless it ENDS "· Notes" ...
    plain = _run(("Notes", bold), ("on", bold), ("use", bold))
    assert lines_from_words(plain, 189, set())[0].kind == "heading"
    mentions = _run(("Chapter", bold), ("1", bold), ("·", bold), ("Notes", bold), ("on", bold), ("use", bold))
    assert lines_from_words(mentions, 189, set())[0].kind == "heading"
    # ... and its first word is set in the bold face
    italic = {"font": "MinionC-Italic", "size": 10.0}
    wrong_face = _run(("Chapter", italic), ("1", italic), ("·", italic), ("Notes", italic))
    assert lines_from_words(wrong_face, 189, set())[0].kind == "body"


def test_kinds_by_font():
    def kind_of(font, size, *texts):
        return _body_page(_run(*[(t, {"font": font, "size": size}) for t in texts]))[0].kind

    assert kind_of("TheMixOsF-ExtraBold", 13.0, "Chapter", "5") == "title"
    assert kind_of("TheMixOsF-Bold", 10.0, "§101", "·", "Definitions") == "heading"
    assert kind_of("MinionC-Bold", 10.2, "Heading") == "heading"
    assert kind_of("MinionC-BoldItalic", 10.2, "Heading") == "heading"
    assert kind_of("MinionC-BoldItalic", 8.7, "2319A") == "body"  # under 10 pt: not a heading
    assert kind_of("MinionC", 10.2, "Plain", "words") == "body"


def test_toc_lines():
    dots = {"font": "MinionPro-Regular", "size": 10.5}
    leader = _run(("Definitions", {}), (" . . . . . . . .", dots), ("3", {}))
    assert _body_page(leader)[0].kind == "toc"  # a leader word of dots and spaces ...
    three = _run(("a", {}), (" .", dots), (" .", dots), (" .", dots), ("5", {}))
    assert _body_page(three)[0].kind == "toc"  # ... or three words of dots
    two = _run(("a", {}), (" .", dots), (" .", dots), ("5", {}))
    assert _body_page(two)[0].kind == "body"
    glued = _run(("Ownership", {}), ("....................", {}), ("168", {}))
    assert _body_page(glued)[0].kind == "toc"  # six dots in a row
    assert _body_page(_run(("section", {"font": "MinionSC", "size": 10.0}), ("page", {"font": "MinionSC", "size": 10.0})))[0].kind == "toc"


def test_a_small_marker_font_word_overlapping_the_line_joins_it():
    # "U .S .C ." is MinionPro-Regular 9.4 pt and sits 2.5 pt lower than the text around it
    low = _word("U .S .C .", 120.0, font="MinionPro-Regular", size=9.4, top=102.5)
    same_font_elsewhere = _word("elsewhere", 120.0, font="MinionC", size=9.4, top=102.5)
    base = _run(("(47", {}), ("x", {}))
    assert len(_body_page(base + [low])) == 1
    assert len(_body_page(base + [same_font_elsewhere])) == 2  # only that font gets the overlap rule


def test_words_within_two_points_of_the_top_share_a_line():
    a, b, c = _run(("one", {}), ("two", {}), ("three", {}))
    b["top"] = 101.9
    c["top"] = 102.5
    assert [l.text for l in _body_page([a, b, c])] == ["one two", "three"]


def test_running_head_and_folio_are_dropped():
    small = {"font": "TheMixOsF-Plain", "size": 7.5}
    head = _run(("424", small), ("Copyright", {"font": "TheMixOsF-PlainItalic", "size": 7.5}))
    body = _run(("Real", {}), ("text", {}))
    for w in body:
        w["top"], w["bottom"] = 200.0, 210.2
    assert [l.text for l in _body_page(head + body)] == ["Real text"]


def test_words_cut_at_a_font_or_size_change_are_glued_but_a_real_space_is_kept():
    glued = [_word("106", 100.0, width=14.6), _word("A", 114.6, size=9.4, width=6.4), _word(",", 121.1, width=2.5)]
    assert _body_page(glued)[0].text == "106A,"
    # a word cut with no change of type at all: "pro" + "cedures:" (PDF 58), 0 and 0.1 pt apart
    plain = [_word("pro", 100.0, width=14.1), _word("cedures:", 114.1, width=32.5)]
    assert _body_page(plain)[0].text == "procedures:"
    plain = [_word("copy", 100.0, width=19.5), _word("right", 119.6, width=20.2)]
    assert _body_page(plain)[0].text == "copyright"
    # the narrowest real word space with no change of type is 0.22 pt ("(d)" then "For", p. 159)
    tight = [_word("(d)", 100.0, width=11.8), _word("For", 112.02, width=14.1)]
    assert _body_page(tight)[0].text == "(d) For"
    # a marker between two words does not glue them
    words = [_word("features.", 100.0, width=30.0), _word("3", 130.0, font="MinionPro-Regular", size=10.5, width=5.0),
             _word("Next", 137.0, width=20.0)]
    assert [(l.text, l.marks) for l in _body_page(words)] == [("features. Next", [3])]


def test_at_a_change_of_type_a_wider_cut_is_still_one_word_but_a_real_space_is_not():
    def pair(gap, *, second_font="MinionC", second_size=10.2, second="”"):
        return [_word("Whoever", 100.0, font="MinionSC", width=35.0), _word(second, 135.0 + gap, font=second_font, size=second_size, width=4.0)]

    # small caps into roman, 0.16-0.29 pt apart (PDF 409 "Whoever" + "”"): one word
    assert _body_page(pair(0.16))[0].text == "Whoever”"
    assert _body_page(pair(0.29))[0].text == "Whoever”"
    # a size change alone is a change of type
    assert _body_page(pair(0.2, second_font="MinionSC", second_size=9.4))[0].text == "Whoever”"
    # the same gap with no change of type is a space, and so is a mixed number's: "1" + "3/8" (0.38 pt, PDF 306)
    same = [_word("one", 100.0, width=15.0), _word("two", 115.2, width=15.0)]
    assert _body_page(same)[0].text == "one two"
    assert _body_page(pair(0.38, second="3/8"))[0].text == "Whoever 3/8"
    assert _body_page(pair(0.31))[0].text == "Whoever ”"


def test_overlapping_boxes_are_not_one_word():
    # a box that overlaps its neighbour by more than a point is a different word, not a cut one
    words = [_word("alpha", 100.0, width=25.0), _word("beta", 120.0, width=20.0)]
    assert _body_page(words)[0].text == "alpha beta"


def test_punctuation_after_a_gap_closes_up():
    words = _run(("shall", {}), (",", {}), ("and", {}), (";", {}))
    assert _body_page(words)[0].text == "shall, and;"


def test_line_fields():
    words = _run(("(a)", {"font": "MinionC", "size": 10.2}), ("Heading.", {"font": "MinionSC", "size": 10.2}), ("text", {}))
    (line,) = _body_page(words, pdf_page=60)
    assert isinstance(line, RawLine)
    assert (line.pdf_page, line.page, line.scope) == (60, 38, "Chapter 1")
    assert line.x0 == 54.0 and line.top == 100.0
    assert line.font == ("MinionC", 10.2)  # the first word's, without the subset prefix
    assert line.fonts == ["MinionC", "MinionSC"]  # every font on the line, first use first


# --- text cleaning --------------------------------------------------------------------------

def _raw(text, page=5):
    return RawLine(pdf_page=page + PAGE_OFFSET, page=page, scope="Chapter 1", x0=54.0, top=100.0, text=text,
                   marks=[], kind="body", font=("MinionC", 10.2), fonts=["MinionC"])


def _tl(text, page=5):
    return TextLine(text=text, page=page)


def _decider(*line_texts, overrides=()):
    ledger = OverrideLedger(list(overrides))
    return HyphenDecider([_raw(t) for t in line_texts], ledger), ledger


def _hyphen_override(match, value, page=5):
    return Override(id=f"h-{match}", kind="hyphen", page=page, match=match, value=value, reason="test")


def test_number_breaks_rejoin():
    d, _ = _decider()
    assert clean_join([_tl("Pub. L. No. 105-"), _tl("304, 112 Stat.")], d) == "Pub. L. No. 105-304, 112 Stat."
    assert d.decide("105-", "304", 5) == "number"
    assert d.undecided == []


def test_line_end_hyphen_joins_when_safe():
    d, _ = _decider("the spac-", "es and elements", "arrangement of the spaces and more")
    assert clean_join([_tl("the spac-"), _tl("es and elements")], d) == "the spaces and elements"
    assert d.decide("spac-", "es", 5) == "join" and d.undecided == []


def test_line_end_hyphen_keeps_compound():
    d, _ = _decider("a work of non-dramatic literature here", "and a non-")
    assert d.decide("non-", "dramatic", 5) == "keep"
    assert clean_join([_tl("a non-"), _tl("dramatic work")], d) == "a non-dramatic work"
    assert d.undecided == []


def test_undecided_hyphen_uses_override():
    lines = ["a hyph-", "en b"]
    with_override, ledger = _decider(*lines, overrides=[_hyphen_override("hyph-|en", "keep")])
    assert clean_join([_tl("a hyph-"), _tl("en b")], with_override) == "a hyph-en b"
    assert with_override.undecided == [] and ledger.unused() == []

    without, _ = _decider(*lines)
    assert clean_join([_tl("a hyph-"), _tl("en b")], without) == "a hyphen b"
    assert without.undecided == ["p.5 hyph-|en"]


def test_evidence_for_both_forms_goes_to_the_override_too():
    d, _ = _decider("a co-owner here", "a coowner here", "x co-")
    assert d.decide("co-", "owner", 5) == "join" and d.undecided == ["p.5 co-|owner"]
    d2, ledger = _decider("a co-owner here", "a coowner here", overrides=[_hyphen_override("co-|owner", "keep")])
    assert d2.decide("co-", "owner", 5) == "keep" and d2.undecided == [] and ledger.unused() == []


def test_a_decided_hyphen_leaves_its_override_unused_so_a_stale_one_is_reported():
    d, ledger = _decider("the spaces here", overrides=[_hyphen_override("spac-|es", "keep")])
    assert d.decide("spac-", "es", 5) == "join"
    assert [o.id for o in ledger.unused()] == ["h-spac-|es"]


def test_override_is_matched_on_its_own_page():
    d, ledger = _decider(overrides=[_hyphen_override("hyph-|en", "keep", page=9)])
    assert d.decide("hyph-", "en", 5) == "join"  # page 5, the override is for page 9
    assert d.decide("hyph-", "en", 9) == "keep"
    assert d.undecided == ["p.5 hyph-|en"] and ledger.unused() == []


def test_a_whole_word_at_the_start_or_end_of_a_line_is_evidence():
    d, _ = _decider("a b c spaces", "spaces d e", "f g non-dramatic")
    assert d.decide("spac-", "es", 5) == "join" and d.undecided == []
    assert d.decide("non-", "dramatic", 5) == "keep" and d.undecided == []


def test_the_pieces_of_a_broken_word_are_not_evidence():
    # "spaces-" ends a line and "spaces" opens the line after a hyphen: pieces of other breaks, not the word
    d, _ = _decider("a b spaces-", "owner c d")
    assert d.decide("spac-", "es", 5) == "join" and d.undecided == ["p.5 spac-|es"]
    d2, _ = _decider("a b x-", "spaces d e")
    assert d2.decide("spac-", "es", 5) == "join" and d2.undecided == ["p.5 spac-|es"]
    # ... while a line that merely follows an unbroken one gives its first word
    d3, _ = _decider("a b x", "spaces d e")
    assert d3.decide("spac-", "es", 5) == "join" and d3.undecided == []


def test_evidence_ignores_case_and_punctuation_around_a_word():
    d, _ = _decider("see (Non-Dramatic), here", "and Spaces; too")
    assert d.decide("non-", "dramatic", 5) == "keep"
    assert d.decide("spac-", "es", 5) == "join"


def test_decide_needs_a_word_that_ends_in_a_hyphen():
    d, _ = _decider()
    with pytest.raises(ValueError, match="ending in one"):
        d.decide("spaces", "x", 5)


def test_the_same_undecided_break_is_listed_once():
    d, _ = _decider()
    for _ in range(3):
        d.decide("hyph-", "en", 5)
    assert d.undecided == ["p.5 hyph-|en"]


def test_clean_join_uses_the_page_of_the_line_that_holds_the_hyphen():
    d, ledger = _decider(overrides=[_hyphen_override("hyph-|en", "keep", page=7)])
    assert clean_join([_tl("first", page=6), _tl("a hyph-", page=7), _tl("en b", page=8)], d) == "first a hyph-en b"
    assert ledger.unused() == []


def test_the_word_a_hyphen_ends_is_only_the_word():
    # "terms.—Proceed-" is one token of the page; the break is in "Proceed-"
    d, _ = _decider("the proceedings here")
    assert clean_join([_tl("see terms.—Proceed-"), _tl("ings here")], d) == "see terms.—Proceedings here"
    assert d.undecided == []


def test_clean_join_plain_lines_and_empty():
    d, _ = _decider()
    assert clean_join([], d) == ""
    assert clean_join([_tl("one two"), _tl("three")], d) == "one two three"
    assert clean_join([_tl("  one  "), _tl("two  words ")], d) == "one two words"


def test_a_hyphen_after_a_digit_or_before_a_capital_or_digit_is_not_a_break():
    d, _ = _decider()
    # a number's hyphen: "12-month", "105-304"
    assert clean_join([_tl("the 12-"), _tl("month period")], d) == "the 12-month period"
    assert d.decide("12-", "month", 5) == "number"
    # no syllable break leads into a capital letter or a digit: the hyphen is the author's, so there
    # is no break and no evidence is needed
    assert clean_join([_tl("Trade-"), _tl("Related Aspects")], d) == "Trade-Related Aspects"
    assert clean_join([_tl("Id. at 1501A-"), _tl("530. The Act")], d) == "Id. at 1501A-530. The Act"
    assert d.decide("Trade-", "Related", 5) == "keep" and d.decide("Rule-", "5", 5) == "keep"
    assert d.undecided == []


def test_a_hyphen_that_ends_a_line_after_a_closing_bracket_or_dash_is_left_alone():
    d, _ = _decider()
    assert clean_join([_tl("(a)-"), _tl("text")], d) == "(a)- text"


def test_clean_join_cleans_each_line_as_the_prototype_did():
    d, _ = _decider()
    assert clean_join([_tl("(47 U .S .C . 153) and the \ufb01nal"), _tl("rule")], d) == "(47 U.S.C. 153) and the final rule"  # fi ligature
    assert clean_join([_tl("a soft\u00adhyphen")], d) == "a softhyphen"
    assert clean_join([_tl("shall , and ;")], d) == "shall, and;"
    assert clean_join([_tl("the Act ( DMCA ), Pub. L.")], d) == "the Act (DMCA), Pub. L."
    # em dashes close up, also across a line break
    assert clean_join([_tl("Short title. —This section"), _tl("and more —")], d) == "Short title.—This section and more—"
    assert clean_join([_tl("Short title."), _tl("—This section")], d) == "Short title.—This section"
    assert clean_join([_tl("the term — a work")], d) == "the term—a work"
    # a blank line is no line: the hyphen before it still meets the next one
    assert clean_join([_tl("a hyph-"), _tl("  "), _tl("en b")], d) == "a hyphen b"


def test_small_caps():
    assert fix_small_caps("U .S .C .") == "U.S.C."
    assert fix_small_caps("( DMCA )") == "(DMCA)"
    assert fix_small_caps("Act ( DMCA ), Pub. L.") == "Act (DMCA), Pub. L."
    assert fix_small_caps("Section 512( g ) of") == "Section 512(g) of"
    assert fix_small_caps("( h) and (i )") == "(h) and (i)"
    assert fix_small_caps("(47 U .S .C . 153)") == "(47 U.S.C. 153)"
    # a parenthetical of several words keeps its spaces, and a real one is untouched
    assert fix_small_caps("( see this note ) and (a)") == "( see this note ) and (a)"
    assert fix_small_caps("plain text (as written)") == "plain text (as written)"
