# tests/test_book_xrefs.py
"""Cross-references (ingest/books/xrefs.py) and the tree's back end (ingest/books/tree.py).

The resolver's cases are ported from the prototype's xref.py (research vault, 2026-10-30), with its
two known bugs fixed (ch.5 n.8's "section 2323, chapter 113 of title 18"; §504(c)'s Act whose U.S.C.
citation sits past its long name). Targets are paths (ruling 11); a reference that names law but
yields no path is kept as its raw text (ruling R1).
"""
import pytest

from ingest.books.models import BookTree, Node, TextLine
from ingest.books.overrides import Override, OverrideLedger
from ingest.books.registry import get_book
from ingest.books.tree import link_notes, own_text, xref_resolution
from ingest.books.xrefs import XrefContext, find_xrefs, label_level

BOOK = get_book("circ92")


def _ctx(section="504", labels=(), levels=(), **kw):
    return XrefContext(title=17, section=section, path_labels=tuple(labels), label_levels=tuple(levels), **kw)


NOTE = XrefContext(title=17, section=None, path_labels=(), label_levels=())


# --- the brief's cases ----------------------------------------------------------------------------

def test_relative_subsection():
    text = "(1) Except as provided by clause (2) of this subsection, the copyright owner may elect"
    assert find_xrefs(text, _ctx("504", ("c", "1"), (1, 2))) == (["usc17-s504-c-2"], [])


def test_explicit_parent():
    # The parent is a reference of its own too, as in the prototype.
    text = "(3) The rights described in paragraph (1) of subsection (a) shall not apply"
    assert find_xrefs(text, _ctx("106a", ("c", "3"), (1, 2))) == (["usc17-s106a-a-1", "usc17-s106a-a"], [])


def test_explicit_parent_with_a_list():
    text = "The rights described in paragraphs (1) and (2) of subsection (a) shall not apply"
    assert find_xrefs(text, _ctx("106a", ("c", "3"), (1, 2)))[0] == [
        "usc17-s106a-a-1", "usc17-s106a-a-2", "usc17-s106a-a",
    ]


def test_other_title():
    text = "shall be punished as provided under section 2319 of title 18, if the infringement was committed"
    assert find_xrefs(text, _ctx("506", ("a", "1"), (1, 2))) == (["usc18-s2319"], [])


def test_title_after_chapter():
    # ch.5 n.8: the prototype read "section 2323" as 17 U.S.C., because a chapter sits before the title.
    text = ("In lieu of this provision, refer to section 2323, chapter 113 of title 18, United States Code, "
            "entitled, “Forfeiture, Destruction and Restitution.” Section 2323 is included in Appendix N.")
    assert find_xrefs(text, NOTE) == (["usc18-s2323"], [])


def test_usc_in_parentheses():
    # §504(c)(3)(C): the Act's name is too long for a short look-ahead, so the prototype kept a
    # half-resolved "§45 of an Act named nearby" beside the U.S.C. citation.
    text = ("(C) For purposes of this paragraph, the term “domain name” has the meaning given that term in "
            "section 45 of the Act entitled “An Act to provide for the registration and protection of trademarks "
            "used in commerce, to carry out the provisions of certain international conventions, and for other "
            "purposes” approved July 5, 1946 (commonly referred to as the “Trademark Act of 1946”; 15 U.S.C. 1127).")
    assert find_xrefs(text, _ctx("504", ("c", "3", "C"), (1, 2, 3))) == (["usc15-s1127"], [])


def test_public_law():
    assert find_xrefs("Pub. L. No. 110-403, 122 Stat. 4256, 4260.", NOTE) == (["pl110-403"], [])
    paths, unparsed = find_xrefs("as amended by section 101 of Public Law 105-304, the rule", NOTE)
    assert paths == ["pl105-304-s101", "pl105-304"]
    assert unparsed == []


def test_definition_relative():
    # §101 "work made for hire": the text before (A) and (B) names the definition's own paragraph (2).
    ctx = _ctx("101", ("A",), (3,), term="work made for hire")
    text = "In determining whether any work is eligible to be considered a work made for hire under paragraph (2),"
    assert find_xrefs(text, ctx) == (["usc17-s101-def-work-made-for-hire-2"], [])


def test_named_act_without_number():
    raw = "section 1011(d) of the Intellectual Property and Communications Omnibus Reform Act of 1999"
    paths, unparsed = find_xrefs(f"neither the amendment contained in {raw}, as enacted", NOTE)
    assert paths == []
    assert unparsed == [raw]


# --- the other rules ------------------------------------------------------------------------------

def test_section_lists_and_ranges():
    paths, _ = find_xrefs("the rights under sections 106 and 106A, and sections 107 through 122", _ctx("101"))
    # a range names its two ends: what lies between is not known from the text
    assert paths == ["usc17-s106", "usc17-s106a", "usc17-s107", "usc17-s122"]


def test_labels_continue_the_item_before():
    text = "as provided in paragraphs (1)(A), (B), and (C)(i) and section 111(d)(1) through (3)"
    paths, _ = find_xrefs(text, _ctx("119", ("a",), (1,)))
    assert paths == [
        "usc17-s119-a-1-A", "usc17-s119-a-1-B", "usc17-s119-a-1-C-i",
        "usc17-s111-d-1", "usc17-s111-d-3",
    ]


def test_a_section_label_keeps_its_case_and_the_number_is_lowercased():
    assert find_xrefs("under section 104A(h)(6)(B)", _ctx("101"))[0] == ["usc17-s104a-h-6-B"]


def test_a_spaced_label_list_continues():
    paths, _ = find_xrefs("an agreement described in section 101(d) (7), (12), or (13) shall", _ctx(act="pl103-465"))
    assert paths == ["pl103-465-s101-d-7", "pl103-465-s101-d-12", "pl103-465-s101-d-13"]


def test_capitalized_subdivision_is_a_reference_but_a_capitalized_section_is_not():
    # "Paragraph (1) shall not apply" opens a sentence of the statute; a capitalized "Section N" opens
    # sentences in the notes that name another title's or another Act's section (ch.5 n.8, ch.1 n.37).
    assert find_xrefs("(2) Exception.—Paragraph (1) shall not apply", _ctx("512", ("g", "2"), (1, 2)))[0] == [
        "usc17-s512-g-1"
    ]
    assert find_xrefs("Section 2323 is included in Appendix N to this volume.", NOTE) == ([], [])


def test_code_of_federal_regulations_is_unparsed():
    raw = "section 76.55(e) of title 47, Code of Federal Regulations"
    assert find_xrefs(f"as defined in {raw} (as in effect on September 18, 1993)", _ctx("111")) == ([], [raw])


def test_named_act_with_its_codification_in_parentheses():
    text = ("is exempt from taxation under section 501 of the Internal Revenue Code of 1986 (26 U.S.C. 501); and "
            "under section 182(a) of the Trade Act of 1974 (19 U.S.C. 2242(a)) and section 34(d) of the Trademark Act")
    paths, unparsed = find_xrefs(text, _ctx("114"))
    assert paths == ["usc26-s501", "usc19-s2242-a"]
    assert unparsed == ["section 34(d) of the Trademark Act"]


def test_act_names_with_lower_case_words_and_amendments_are_acts():
    # §121(c) and ch.1 n.42: neither is a section of Title 17.
    text = ("described in section 674(e) of the Individuals with Disabilities Education Act that contain, and "
            "repealed section 4(c) of the Record Rental Amendment of 1984. Pub. L. No. 103-182")
    assert find_xrefs(text, _ctx("121", ("c",), (1,))) == (["pl103-182"], [
        "section 674(e) of the Individuals with Disabilities Education Act",
        "section 4(c) of the Record Rental Amendment of 1984",
    ])


def test_a_paren_citation_after_the_next_reference_is_not_taken():
    # The U.S.C. citation belongs to the reference it follows, not to an earlier one.
    text = "under section 2 of such Act and section 1103 of the Trade Act (19 U.S.C. 2903)"
    paths, unparsed = find_xrefs(text, _ctx("114"))
    assert paths == ["usc19-s2903"]
    assert unparsed == ["section 2 of such Act"]


def test_a_child_of_an_unparsed_section_is_not_guessed():
    text = "references in paragraphs (2) through (11) of section 34(d) of the Trademark Act to section 32 of such Act"
    paths, unparsed = find_xrefs(text, _ctx("503", ("a", "3"), (1, 2)))
    assert paths == []
    assert unparsed == ["section 34(d) of the Trademark Act", "section 32 of such Act"]


def test_explicit_section_parent_takes_its_qualifier():
    text = "packaging described in paragraph (4) or (5) of section 2318(b) of title 18, United States Code"
    assert find_xrefs(text, _ctx("103", ("a", "2", "A"), (1, 2, 3), act="pl108-482"))[0] == [
        "usc18-s2318-b-4", "usc18-s2318-b-5", "usc18-s2318-b",
    ]


def test_an_act_names_its_own_sections():
    ctx = _ctx("303", ("a",), (1,), act="pl110-403")
    text = ("referred to in section 301(b)(1)(B), any violation of section 32(1)(a) of this Act or of "
            "section 107 of title 17, United States Code, as amended by this subsection or subsection (b)")
    assert find_xrefs(text, ctx)[0] == [
        "pl110-403-s301-b-1-B", "pl110-403-s32-1-a", "usc17-s107", "pl110-403-s303-b",
    ]


def test_another_title_names_its_own_sections_and_title_17_explicitly():
    ctx = XrefContext(title=18, section="2319", path_labels=("a",), label_levels=(1,))
    text = "infringes a copyright under section 506(a) of title 17 and section 2318, as provided in subsection (b)"
    assert find_xrefs(text, ctx)[0] == ["usc17-s506-a", "usc18-s2318", "usc18-s2319-b"]


def test_such_title_is_the_title_named_before():
    text = "The repeal of section 114(i) of title 17, United States Code, under section 112(e) or 114(f) of such title"
    assert find_xrefs(text, _ctx("103", act="pl115-264"))[0] == ["usc17-s114-i", "usc17-s112-e", "usc17-s114-f"]


def test_such_title_with_no_title_before_is_unparsed():
    assert find_xrefs("under section 112(e) of such title", _ctx("103", act="pl115-264")) == (
        [], ["section 112(e) of such title"]
    )


def test_this_act_outside_an_act_is_unparsed():
    assert find_xrefs("under section 5 of this Act", _ctx("101")) == ([], ["section 5 of this Act"])


def test_a_note_drops_relative_references():
    # A note has no section of its own, so "subsection (a)" names nothing; a section number still does.
    text = "amended section 503 by revising subsection (a) in its entirety, and section 109(b), as added"
    assert find_xrefs(text, NOTE) == (["usc17-s503", "usc17-s109-b"], [])


def test_a_kind_word_before_a_section_number_names_that_section():
    # The notes cite a unit by its whole citation: "amended subparagraph 803(b)(6)(A)" (ch.8 n.5).
    assert find_xrefs("Act of 2010 amended subparagraph 803(b)(6)(A) by revising", NOTE)[0] == ["usc17-s803-b-6-A"]
    assert find_xrefs("amended paragraphs 804(b) (1)(A) and (B) by substituting", NOTE)[0] == [
        "usc17-s804-b-1-A", "usc17-s804-b-1-B",
    ]
    assert find_xrefs("no longer contained in subsection 2320(e)(2) of Title 18 because", NOTE)[0] == [
        "usc18-s2320-e-2"
    ]
    app_n = XrefContext(title=18, section=None, path_labels=(), label_levels=())
    assert find_xrefs("It also amended paragraphs 2319(c)(2), (d)(3) and (d)(4) by making", app_n)[0] == [
        "usc18-s2319-c-2", "usc18-s2319-d-3", "usc18-s2319-d-4",
    ]
    # a number alone after a kind word names no unit
    assert find_xrefs("as provided in paragraph 2 of the agreement", _ctx("101")) == ([], [])


def test_respectively_may_stand_before_the_parent():
    # §101 "WTO Agreement": the paragraphs are the Uruguay Round Agreements Act's, not the definition's.
    text = ("The terms have the meanings given those terms in paragraphs (9) and (10), respectively, of section 2 "
            "of the Uruguay Round Agreements Act.")
    assert find_xrefs(text, _ctx("101", term="WTO Agreement")) == (
        [], ["section 2 of the Uruguay Round Agreements Act"]
    )


def test_such_names_the_unit_named_before_it():
    # §105(c)(1): "such subsection" is (d)(2), not the passage's own subsection.
    text = ("described in subparagraphs (A) through (L) of subsection (d)(2) and subparagraph (M) of such "
            "subsection when")
    assert find_xrefs(text, _ctx("105", ("c", "1"), (1, 2)))[0] == [
        "usc17-s105-d-2-A", "usc17-s105-d-2-L", "usc17-s105-d-2", "usc17-s105-d-2-M",
    ]
    text = ("brought under subsection (c)(6) of section 1504 and that relates to a claim under subsection (c)(1) "
            "or (c)(4) of such section, qualifies")
    assert find_xrefs(text, _ctx("1507", ("d",), (1,)))[0] == [
        "usc17-s1504-c-6", "usc17-s1504", "usc17-s1504-c-1", "usc17-s1504-c-4",
    ]
    text = "section 5 of the Federal Trade Commission Act (15 U.S.C. 45) to the extent such section 5 applies"
    assert find_xrefs(text, _ctx("119", ("e", "2", "A"), (1, 2, 3))) == (["usc15-s45"], [])
    text = ("For purposes of section 230 of the Communications Act of 1934 (47 U.S.C. 230), subsection (a) shall be "
            "a law under subsection (e) (2) of such section 230.")
    assert find_xrefs(text, _ctx("1401", ("g",), (1,))) == (
        ["usc47-s230", "usc17-s1401-a", "usc47-s230-e-2"], []
    )
    text = "the application of section 108(h) to a claim, the phrase in such section 108(h) shall be construed"
    assert find_xrefs(text, _ctx("1401", ("f", "1", "B"), (1, 2, 3)))[0] == ["usc17-s108-h"]


def test_such_with_nothing_named_before_is_unparsed():
    assert find_xrefs("to the extent such section 5 applies", _ctx("119")) == ([], ["such section 5"])
    assert find_xrefs("section 107 and such section 5 apply", _ctx("119")) == (["usc17-s107"], ["such section 5"])
    assert find_xrefs("and subparagraph (M) of such subsection when", _ctx("105", ("c",), (1,))) == (
        [], ["subparagraph (M) of such subsection"]
    )


def test_a_unit_of_a_definition_in_section_101():
    # §106A(c)(3): the items are the definition's, not §106A(c)(3)'s own.
    text = 'any item described in subparagraph (A) or (B) of the definition of “work of visual art” in section 101, and'
    assert find_xrefs(text, _ctx("106a", ("c", "3"), (1, 2)))[0] == [
        "usc17-s101-def-work-of-visual-art-A", "usc17-s101-def-work-of-visual-art-B", "usc17-s101",
    ]
    text = ("(A) Paragraph (5) of the definition of ‘‘international agreement’’ contained in section 101 of title 17, "
            "United States Code, as amended")
    assert find_xrefs(text, _ctx("105", act="pl105-304"))[0] == [
        "usc17-s101-def-international-agreement-5", "usc17-s101",
    ]


def test_a_unit_of_a_definition_with_no_section_is_unparsed():
    # §104A(h) defines terms too ("restored work"), so without "in section 101" the place is not known.
    raw = "paragraph (5) of the definition of “international agreement”"
    assert find_xrefs(f"requires that {raw} take effect upon entry", NOTE) == ([], [raw])


def test_a_unit_of_the_first_section_of_a_named_act():
    text = ("has the meaning given that term in subsection (a) of the first section of the Clayton Act "
            "(15 U.S.C. 12(a)), except")
    assert find_xrefs(text, _ctx("119", ("e", "2", "A"), (1, 2, 3))) == (["usc15-s12-a"], [])
    raw = "subsection (a) of the first section of the Sherman Act"
    assert find_xrefs(f"defined in {raw}, except", _ctx("119", ("e",), (1,))) == ([], [raw])


def test_a_number_before_percent_ends_the_list():
    text = "in accordance with section 114(f), 50 percent of the performance royalties"
    assert find_xrefs(text, _ctx("1401")) == (["usc17-s114-f"], [])


def test_a_parenthetical_may_stand_before_the_qualifier():
    ctx = XrefContext(title=18, section="2319", path_labels=("a",), label_levels=(1,))
    text = "(a) Any person who violates section 506(a) (relating to criminal offenses) of title 17 shall be punished"
    assert find_xrefs(text, ctx)[0] == ["usc17-s506-a"]
    # with no qualifier after it, the parenthetical changes nothing
    assert find_xrefs("under section 2318 (relating to labels) shall", ctx)[0] == ["usc18-s2318"]


def test_and_chapter_may_stand_before_the_title():
    text = "The provisions of sections 118, 304(b), and chapter 8 of title 17, as amended by the first section"
    assert find_xrefs(text, _ctx("102", act="pl94-553"))[0] == ["usc17-s118", "usc17-s304-b"]


def test_resolutions_and_trips_are_not_title_17():
    assert find_xrefs("the competition established under section 3 of House Resolution 77, 113th Congress",
                      _ctx("708")) == ([], ["section 3 of House Resolution 77"])
    assert find_xrefs("Part II, section 6, of TRIPs protects semiconductor chip products", NOTE) == (
        [], ["section 6, of TRIPs"]
    )


def test_a_section_list_of_bare_labels_is_unparsed():
    # ch.1 n.37 prints "added sections (3) and (4)": no section number, so no path.
    assert find_xrefs("and added sections (3) and (4).", NOTE) == ([], ["sections (3) and (4)"])


def test_inline_usc_citation_and_dedup_in_order():
    text = "under 17 U.S.C. 512(c) and section 512(c), see also 47 U.S.C. §338 and 17 U.S.C. 512(c)."
    assert find_xrefs(text, _ctx("1201"))[0] == ["usc17-s512-c", "usc47-s338"]


def test_item_and_subitem_levels():
    paths, _ = find_xrefs("under item (bb) of subclause (II)", _ctx("114", ("f", "1", "A", "i", "II", "aa"),
                                                                    (1, 2, 3, 4, 5, 6)))
    assert paths == ["usc17-s114-f-1-A-i-II-bb", "usc17-s114-f-1-A-i-II"]
    paths, _ = find_xrefs("under subitem (BB) of item (aa)", _ctx("114", ("f", "1", "A", "i", "II", "aa", "AA"),
                                                                 (1, 2, 3, 4, 5, 6, 7)))
    assert paths == ["usc17-s114-f-1-A-i-II-aa-BB", "usc17-s114-f-1-A-i-II-aa"]


def test_label_level():
    assert label_level("a", 0) == 1
    assert label_level("i", 3) == 4  # a clause under a subparagraph
    assert label_level("i", 0) == 1  # subsection (i)
    assert label_level("I", 2) == 3  # subparagraph (I)
    assert label_level("I", 4) == 5  # subclause (I)
    assert label_level("aa", 5) == 6
    assert label_level("ii", 3) == 4


# --- xref_target overrides ------------------------------------------------------------------------

def _override(page, match, value, oid="x1"):
    return Override(id=oid, kind="xref_target", page=page, match=match, value=value, reason="test")


def test_xref_target_override_names_the_target():
    ledger = OverrideLedger([_override(5, "section 32 of such Act", "usc15-s1114")])
    ctx = _ctx("503", pages=(4, 5))
    assert find_xrefs("to section 32 of such Act shall be read", ctx, ledger) == (["usc15-s1114"], [])
    assert ledger.unused() == []


def test_xref_target_override_needs_its_page_and_whole_text():
    ledger = OverrideLedger([_override(5, "section 32 of such Act", "usc15-s1114")])
    assert find_xrefs("to section 32 of such Act", _ctx("503", pages=(6,)), ledger) == (
        [], ["section 32 of such Act"]
    )
    assert find_xrefs("to section 32 of such Act", _ctx("503", pages=(5,)), None) == ([], ["section 32 of such Act"])
    assert [o.id for o in ledger.unused()] == ["x1"]


def test_xref_target_override_replaces_a_parsed_target():
    ledger = OverrideLedger([_override(9, "section 2323", "usc18-s2323")])
    assert find_xrefs("subject to section 2323, to the extent", _ctx("205", act="pl110-403", pages=(9,)),
                      ledger) == (["usc18-s2323"], [])


# --- the tree's back end --------------------------------------------------------------------------

def _node(path, *, kind="rule", lead=(), tail=(), children=(), **kw):
    return Node(path=path, kind=kind, source_type="usc_title", jurisdiction="US", label=path, heading="",
                scope="Chapter 5", lead=[TextLine(t, 200) for t in lead], tail=[TextLine(t, 201) for t in tail],
                children=list(children), **kw)


def _tree(*roots):
    return BookTree(BOOK, list(roots))


def test_xref_resolution_counts_paths_and_unparsed():
    child = _node("usc17-s506-a", lead=["(a)"], xrefs=["usc17-s506", "usc18-s2319"])
    section = _node("usc17-s506", lead=["text"], children=[child],
                    xrefs=["usc17-s506-a", "usc17-s999"], xrefs_unparsed=["section 2 of such Act"])
    total, resolved, misses = xref_resolution(_tree(section))
    assert (total, resolved) == (5, 2)
    assert misses == [
        ("usc17-s506", "usc17-s999"),
        ("usc17-s506", "unparsed: section 2 of such Act"),
        ("usc17-s506-a", "usc18-s2319"),
    ]


def test_link_notes_fills_history_notes_in_note_order():
    section = _node("usc17-s509", kind="repealed", lead=["[Repealed]"])
    other = _node("usc17-s510", lead=["text"])
    notes = [
        _node("ch5-n1", kind="history_note", lead=["n1"], annotates="usc17-s509", note_number=1),
        _node("ch5-n2", kind="history_note", lead=["n2"], annotates=None, note_number=2),
        _node("ch5-n8", kind="history_note", lead=["n8"], annotates="usc17-s509", note_number=8),
    ]
    tree = _tree(section, other, *notes)
    link_notes(tree)
    link_notes(tree)  # a second run gives the same links, not twice as many
    assert section.history_notes == ["ch5-n1", "ch5-n8"]
    assert other.history_notes == []


def test_link_notes_refuses_a_note_about_a_missing_node():
    note = _node("ch5-n1", kind="history_note", lead=["n1"], annotates="usc17-s999", note_number=1)
    with pytest.raises(ValueError, match="usc17-s999"):
        link_notes(_tree(_node("usc17-s509", lead=["x"]), note))


def test_own_text_joins_the_lead_and_the_tail_apart():
    child = _node("usc17-s101-def-x-1", lead=["(1) one"])
    node = _node("usc17-s101-def-x", lead=["A “x” is—"], tail=["by the courts."], children=[child])
    assert own_text(node, BookTree(BOOK, []).join) == "A “x” is— by the courts."
    assert own_text(child, lambda lines: "|".join(line.text for line in lines)) == "(1) one"
