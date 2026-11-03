# tests/test_book_chunker.py
"""The chunker (ingest/books/chunker.py) and the token counter (ingest/books/tokens.py): spec D4,
rulings 9 and 20, R4.

Hand-built trees and a stub counter that counts words: chunk boundaries move with the budget and the
counter, the ref_ids the chunks carry never do. No Ollama here.
"""
import re

import pytest

from ingest.books import tokens
from ingest.books.chunker import HISTORY_NOTE_HEADING, chunk_tree
from ingest.books.models import BookTree, Node, SubRef, TextLine
from ingest.books.ref_ids import chunk_key, point_id
from ingest.books.registry import get_book
from ingest.books.tokens import CachedTokenCounter, ollama_token_counter

BOOK = get_book("circ92")
P = f"{BOOK.ref_prefix}:"
TITLE_122 = "Limitations on exclusive rights: Secondary transmissions of local television programming by satellite"


def words(text: str) -> int:
    return len(text.split())


def _lines(lines, page):
    """Strings (on `page`, the first starting a paragraph) or TextLines, as given."""
    out = []
    for i, line in enumerate(lines):
        out.append(line if isinstance(line, TextLine) else TextLine(text=line, page=page, para_start=i == 0))
    return out


def node(path, *, label=None, heading="", kind="rule", lead=(), children=(), tail=(), page=1, **kw):
    fields = dict(source_type="usc_title", jurisdiction="US", scope="Chapter 1")
    fields.update(kw)
    return Node(
        path=path, kind=kind, label=label or path, heading=heading, lead=_lines(lead, page),
        children=list(children), tail=_lines(tail, page), **fields,
    )


def tree(*roots):
    return BookTree(BOOK, list(roots))


def chunk(chunks, key):
    return next(c for c in chunks if c.chunk_key == key)


def keys(chunks):
    return [c.chunk_key for c in chunks]


def s107():
    return node(
        "usc17-s107", label="17 U.S.C. §107", heading="Limitations on exclusive rights: Fair use", page=20,
        lead=["Notwithstanding the provisions of sections 106 and 106A, the fair use of a copyrighted work",
              "is not an infringement of copyright. The factors to be considered shall include—"],
        children=[
            node("usc17-s107-1", label="17 U.S.C. §107(1)", page=20,
                 lead=["(1) the purpose and character of the use;"]),
            node("usc17-s107-2", label="17 U.S.C. §107(2)", page=21,
                 lead=["(2) the nature of the copyrighted work; and"]),
        ],
    )


def s122(a_lead, *, a_tail=()):
    """§122 with (a) (heading "Secondary Transmissions into Local Markets") and its two paragraphs."""
    return node(
        "usc17-s122", label="17 U.S.C. §122", heading=TITLE_122, page=139,
        children=[node(
            "usc17-s122-a", label="17 U.S.C. §122(a)", heading="Secondary Transmissions into Local Markets",
            page=139, lead=a_lead, tail=a_tail,
            children=[
                node("usc17-s122-a-1", label="17 U.S.C. §122(a)(1)", heading="Within a local market", page=139,
                     lead=["(1) Within a local market.—A secondary transmission into the station’s local",
                           "market shall be subject to statutory licensing under this section."]),
                node("usc17-s122-a-2", label="17 U.S.C. §122(a)(2)", heading="Significantly viewed stations",
                     page=140,
                     lead=["(2) Significantly viewed stations.—A secondary transmission to subscribers",
                           "who receive it under paragraph (1) shall be subject to statutory licensing."]),
            ],
        )],
    )


NON_BARE_A_LEAD = ["(a) Secondary Transmissions into Local Markets.—A secondary transmission of a",
                   "performance or display of a work embodied in a primary transmission shall be subject to—"]


# --- the cut ---------------------------------------------------------------------------------------

def test_small_node_is_one_chunk():
    [c] = chunk_tree(tree(s107()), budget=500, count_tokens=words)

    assert c.chunk_key == c.ref_id == P + "usc17-s107"
    assert c.point_id == point_id(P + "usc17-s107")
    assert (c.book, c.edition, c.path, c.kind, c.source_type, c.jurisdiction) == (
        "circ92", "2025-12", "usc17-s107", "rule", "usc_title", "US")
    assert (c.page_start, c.page_end) == (20, 21)
    assert c.sub_refs == [
        SubRef(ref_id=P + "usc17-s107-1", label="17 U.S.C. §107(1)", page_start=20, page_end=20),
        SubRef(ref_id=P + "usc17-s107-2", label="17 U.S.C. §107(2)", page_start=21, page_end=21),
    ]
    assert c.sub_ref_ids == [P + "usc17-s107-1", P + "usc17-s107-2"]
    # Each paragraph of each node on its own line, in document order; nothing joined across nodes.
    assert c.body == (
        "Notwithstanding the provisions of sections 106 and 106A, the fair use of a copyrighted work "
        "is not an infringement of copyright. The factors to be considered shall include—\n"
        "(1) the purpose and character of the use;\n"
        "(2) the nature of the copyrighted work; and"
    )
    assert c.text == "17 U.S.C. §107 — Limitations on exclusive rights: Fair use\n" + c.body
    assert c.tokens == words(c.text)


def test_body_is_read_through_the_trees_join():
    t = BookTree(BOOK, [s107()], join=lambda lines: " ".join(line.text.upper() for line in lines))
    [c] = chunk_tree(t, budget=500, count_tokens=words)
    assert c.body.startswith("NOTWITHSTANDING THE PROVISIONS")


def test_header():
    a_1_A = node("usc17-s122-f-1-A", label="17 U.S.C. §122(f)(1)(A)", page=142,
                 lead=["(A) In general.—Whoever makes a secondary transmission outside the local market"],
                 children=[node("usc17-s122-f-1-A-i", label="17 U.S.C. §122(f)(1)(A)(i)", page=142,
                                lead=["(i) to one or more subscribers who are not eligible to receive it"])])
    s = node("usc17-s122", label="17 U.S.C. §122", heading=TITLE_122, page=142, children=[
        node("usc17-s122-f", label="17 U.S.C. §122(f)",
             heading="Violation of Territorial Restrictions on Statutory License", page=142,
             lead=["(f) Violation of Territorial Restrictions on Statutory License.—"],
             children=[node("usc17-s122-f-1", label="17 U.S.C. §122(f)(1)", heading="Individual violations",
                            page=142, lead=["(1) Individual violations.—A satellite carrier that makes"],
                            children=[a_1_A])]),
    ])

    chunks = chunk_tree(tree(s), budget=45, count_tokens=words)

    assert keys(chunks) == [P + "usc17-s122-f-1~lead", P + "usc17-s122-f-1-A~lead", P + "usc17-s122-f-1-A-i"]

    c = chunk(chunks, P + "usc17-s122-f-1-A-i")
    # Every node above the chunk is named in it, index for index (R13): a citation of one with no chunk
    # of its own, like §122 or §122(f), is checked and rendered from here.
    assert list(zip(c.ancestor_ref_ids, c.ancestor_labels)) == [
        (P + "usc17-s122-f-1-A-i", "17 U.S.C. §122(f)(1)(A)(i)"),
        (P + "usc17-s122-f-1-A", "17 U.S.C. §122(f)(1)(A)"),
        (P + "usc17-s122-f-1", "17 U.S.C. §122(f)(1)"),
        (P + "usc17-s122-f", "17 U.S.C. §122(f)"),
        (P + "usc17-s122", "17 U.S.C. §122"),
    ]
    assert len(c.ancestor_ref_ids) == len(c.ancestor_labels)
    # (A) has no heading, so it adds nothing to the path.
    path = f"{TITLE_122} › Violation of Territorial Restrictions on Statutory License › Individual violations"
    assert c.heading == path
    assert c.text == f"17 U.S.C. §122(f)(1)(A)(i) — {path}\n" + c.body
    # A unit's own heading is in its body, not its header; a lead chunk carries its parents' path.
    lead = chunk(chunks, P + "usc17-s122-f-1~lead")
    assert lead.heading == f"{TITLE_122} › Violation of Territorial Restrictions on Statutory License"
    # A section that fits is one chunk, headed by its title.
    [whole] = chunk_tree(tree(s), budget=500, count_tokens=words)
    assert whole.text.startswith(f"17 U.S.C. §122 — {TITLE_122}\n(f) Violation")
    assert whole.heading == TITLE_122


def test_header_without_a_heading_is_the_label():
    [c] = chunk_tree(tree(node("pl105-304-s9", label="Pub. L. No. 105-304, §9", lead=["Text of it."])),
                     budget=500, count_tokens=words)
    assert c.text == "Pub. L. No. 105-304, §9\nText of it."
    assert c.heading == ""


def test_history_note_header():
    s = node("usc17-s101", label="17 U.S.C. §101", heading="Definitions", lead=["As used in this title."])
    s.history_notes = ["ch1-n2"]
    n1 = node("ch1-n1", label="ch. 1 n.1", kind="history_note", lead=["In 1980, section 117 was amended."])
    n2 = node("ch1-n2", label="ch. 1 n.2", kind="history_note", annotates="usc17-s101",
              lead=["The Audio Home Recording Act of 1992 amended section 101."])

    chunks = chunk_tree(tree(s, n1, n2), budget=500, count_tokens=words)

    c1, c2 = chunk(chunks, P + "ch1-n1"), chunk(chunks, P + "ch1-n2")
    assert c1.text == f"ch. 1 n.1 — {HISTORY_NOTE_HEADING} — on Chapter 1\nIn 1980, section 117 was amended."
    assert c2.text.startswith(f"ch. 1 n.2 — {HISTORY_NOTE_HEADING} — on 17 U.S.C. §101\n")
    assert c1.heading == c2.heading == HISTORY_NOTE_HEADING == "amendment history, not statutory text"
    assert c1.kind == "history_note"


def test_treaty_header():
    art = node("trips-art38", label="TRIPs Agreement, art. 38", heading="Term of Protection", source_type="treaty",
               jurisdiction="international", scope="Appendix S", appendix="S",
               children=[node("trips-art38-1", label="TRIPs Agreement, art. 38(1)", source_type="treaty",
                              jurisdiction="international", scope="Appendix S", appendix="S",
                              lead=["1. In Members requiring registration, the term of protection"])])
    note = node("appS-n2", label="app. S n.2", kind="history_note", source_type="treaty",
                jurisdiction="international", scope="Appendix S", appendix="S", annotates="trips-art38",
                lead=["Article 38 includes a footnote."])

    chunks = chunk_tree(tree(art, note), budget=500, count_tokens=words)

    c = chunk(chunks, P + "trips-art38")
    assert c.text.startswith("TRIPs Agreement, art. 38 — Term of Protection (treaty, not U.S. law)\n")
    assert (c.source_type, c.jurisdiction, c.appendix) == ("treaty", "international", "S")
    assert chunk(chunks, P + "appS-n2").text.startswith(
        f"app. S n.2 — {HISTORY_NOTE_HEADING} — on TRIPs Agreement, art. 38 (treaty, not U.S. law)\n")


def test_over_budget_splits_by_structure():
    chunks = chunk_tree(tree(s122(NON_BARE_A_LEAD)), budget=45, count_tokens=words)

    # The section has no text of its own (no lead, no tail), so no chunk of its own.
    assert keys(chunks) == [P + "usc17-s122-a~lead", P + "usc17-s122-a-1", P + "usc17-s122-a-2"]
    lead = chunks[0]
    assert (lead.ref_id, lead.path, lead.label) == (P + "usc17-s122-a", "usc17-s122-a", "17 U.S.C. §122(a)")
    assert lead.body.startswith("(a) Secondary Transmissions into Local Markets.—A secondary transmission")
    assert lead.heading == TITLE_122
    assert lead.sub_refs == []
    # The children carry the parent's heading as context.
    for child in chunks[1:]:
        assert child.heading == f"{TITLE_122} › Secondary Transmissions into Local Markets"
    assert (chunks[2].page_start, chunks[2].page_end) == (140, 140)
    assert all(c.tokens <= 45 for c in chunks)


def test_bare_heading_lead_is_context_only():
    chunks = chunk_tree(tree(s122(["(a) Secondary Transmissions into Local Markets.—"])), budget=45,
                        count_tokens=words)
    assert keys(chunks) == [P + "usc17-s122-a-1", P + "usc17-s122-a-2"]
    assert chunks[0].heading == f"{TITLE_122} › Secondary Transmissions into Local Markets"

    # Small caps read back with a stray space, or a bracket, are still only the heading.
    assert keys(chunk_tree(tree(s122(["[(a) S econdary Transmissions into Local", "Markets.—"])), budget=45,
                           count_tokens=words))[0] == P + "usc17-s122-a-1"
    # A lead with words past its heading is text, however short.
    short = chunk_tree(tree(s122(["(a) Secondary Transmissions into Local Markets.—In this section—"])),
                       budget=45, count_tokens=words)
    assert keys(short)[0] == P + "usc17-s122-a~lead"



def test_a_dropped_bare_heading_keeps_its_page_in_the_chunks_below():
    """Final review C1: the heading line is on p. 138, the paragraphs on 139–140. The lead makes no chunk,
    so every chunk below starts on 139; the node's own span (138–140) travels in `ancestor_pages`, which is
    what a citation of §122(a) or §122 is paged from."""
    heading = TextLine(text="(a) Secondary Transmissions into Local Markets.—", page=138, para_start=True)
    chunks = chunk_tree(tree(s122([heading])), budget=45, count_tokens=words)

    assert keys(chunks) == [P + "usc17-s122-a-1", P + "usc17-s122-a-2"]
    assert min(c.page_start for c in chunks) == 139
    assert [list(zip(c.ancestor_ref_ids, c.ancestor_pages)) for c in chunks] == [
        [(P + "usc17-s122-a-1", (139, 139)), (P + "usc17-s122-a", (138, 140)), (P + "usc17-s122", (138, 140))],
        [(P + "usc17-s122-a-2", (140, 140)), (P + "usc17-s122-a", (138, 140)), (P + "usc17-s122", (138, 140))],
    ]

def test_tail_chunk():
    tail = ["the statutory license provided under this paragraph shall apply to secondary transmissions",
            "by a satellite carrier to subscribers in any such county."]
    chunks = chunk_tree(tree(s122(NON_BARE_A_LEAD, a_tail=[TextLine(t, 141) for t in tail])), budget=45,
                        count_tokens=words)

    assert keys(chunks) == [P + "usc17-s122-a~lead", P + "usc17-s122-a-1", P + "usc17-s122-a-2",
                            P + "usc17-s122-a~tail"]
    c = chunks[-1]
    assert c.ref_id == P + "usc17-s122-a"
    assert c.body == " ".join(tail)
    assert (c.page_start, c.page_end) == (141, 141)


def test_leaf_over_budget_splits_at_sentences():
    # A sentence ends inside a line (after "it.") and at a line's end (after "§101."), but not at an
    # abbreviation, at a line's end ("Pub.") or inside one ("Stat.", "U.S.C.").
    leaf = node("usc17-s120-b", label="17 U.S.C. §120(b)", heading="Alterations", page=130, lead=[
        TextLine("(b) Alterations.—The owners of a building may alter it. The Act, Pub.", 130, para_start=True),
        TextLine("L. No. 105-304, 112 Stat. 2860, is not cut at an abbreviation, nor at 17 U.S.C. §101.", 131),
        TextLine("And here the third sentence ends the paragraph.", 131),
    ])

    chunks = chunk_tree(tree(leaf), budget=25, count_tokens=words)

    assert keys(chunks) == [P + "usc17-s120-b", P + "usc17-s120-b~p2", P + "usc17-s120-b~p3"]
    assert [c.body for c in chunks] == [
        "(b) Alterations.—The owners of a building may alter it.",
        "The Act, Pub. L. No. 105-304, 112 Stat. 2860, is not cut at an abbreviation, nor at 17 U.S.C. §101.",
        "And here the third sentence ends the paragraph.",
    ]
    assert [(c.page_start, c.page_end) for c in chunks] == [(130, 130), (130, 131), (131, 131)]
    assert all(c.ref_id == P + "usc17-s120-b" for c in chunks)
    assert all(c.tokens == words(c.text) <= 25 for c in chunks)
    assert len({c.text.split("\n")[0] for c in chunks}) == 1  # every part has the node's header


def test_split_lead_keys():
    lead = ["(a) In general.—This lead is long. It has three sentences of text. Each one is short."]
    s = node("usc17-s1", label="17 U.S.C. §1", heading="Title", children=[
        node("usc17-s1-a", label="17 U.S.C. §1(a)", heading="In general", lead=lead,
             children=[node("usc17-s1-a-1", label="17 U.S.C. §1(a)(1)", lead=["(1) a child."])]),
    ])
    chunks = chunk_tree(tree(s), budget=14, count_tokens=words)
    assert keys(chunks) == [P + "usc17-s1-a~lead", P + "usc17-s1-a~lead~p2", P + "usc17-s1-a~lead~p3",
                            P + "usc17-s1-a-1"]


def test_note_splits_at_paragraphs():
    # Each paragraph fits alone, no two do. Packing by sentences would put "First paragraph, two." with
    # the next paragraph's first sentence; paragraph starts come first.
    paragraphs = [
        ["The first paragraph, one. First paragraph,", "two."],
        ["The second paragraph has a sentence. And another one."],
        ["The third paragraph is here. It has more words in it than others."],
    ]
    lead = [TextLine(text, 150 + i, para_start=j == 0) for i, para in enumerate(paragraphs)
            for j, text in enumerate(para)]
    note = node("ch1-n9", label="ch. 1 n.9", kind="history_note", lead=lead)

    chunks = chunk_tree(tree(note), budget=26, count_tokens=words)

    assert keys(chunks) == [P + "ch1-n9", P + "ch1-n9~p2", P + "ch1-n9~p3"]
    assert [c.body for c in chunks] == [
        "The first paragraph, one. First paragraph, two.",
        "The second paragraph has a sentence. And another one.",
        "The third paragraph is here. It has more words in it than others.",
    ]
    # Paragraphs that fit together go together, each on its own line.
    [two] = chunk_tree(tree(node("ch1-n9", label="ch. 1 n.9", kind="history_note", lead=lead[:3])),
                       budget=45, count_tokens=words)
    assert two.body == ("The first paragraph, one. First paragraph, two.\n"
                        "The second paragraph has a sentence. And another one.")


def test_over_budget_paragraph_splits_at_sentences_and_closes_its_parts():
    lead = [TextLine("Short opening paragraph.", 1, para_start=True),
            TextLine("The long paragraph starts. It goes on and on. It ends here.", 1, para_start=True),
            TextLine("Closing paragraph.", 1, para_start=True)]
    note = node("ch1-n9", label="ch. 1 n.9", kind="history_note", lead=lead)
    chunks = chunk_tree(tree(note), budget=22, count_tokens=words)
    assert [c.body for c in chunks] == [
        "Short opening paragraph.",
        "The long paragraph starts. It goes on and on.",
        "It ends here.",
        "Closing paragraph.",
    ]


def test_none_counts_as_over_budget():
    def window(text):  # an embedding window of 45 words, a budget far past it
        return None if words(text) > 45 else words(text)

    chunks = chunk_tree(tree(s122(NON_BARE_A_LEAD)), budget=1000, count_tokens=window)

    assert keys(chunks) == [P + "usc17-s122-a~lead", P + "usc17-s122-a-1", P + "usc17-s122-a-2"]
    assert all(isinstance(c.tokens, int) and 0 < c.tokens <= 45 for c in chunks)


def test_a_sentence_over_budget_stays_whole():
    # "1." is a paragraph's label, not a sentence of its own.
    sentence = "1. One long sentence with no end inside it runs past the budget without a break"
    leaf = node("usc17-s1-c", label="17 U.S.C. §1(c)", lead=[sentence])

    [c] = chunk_tree(tree(leaf), budget=5, count_tokens=words)
    assert c.body == sentence  # never dropped, never cut
    assert c.tokens == words(c.text) > 5

    [c] = chunk_tree(tree(leaf), budget=5, count_tokens=lambda text: None)
    assert c.body == sentence
    assert c.tokens == -1  # past the embedding window (R4): the dry run reports it


def test_definitions_one_chunk_each():
    def definition(slug, term, text, children=()):
        return node(f"usc17-s101-def-{slug}", kind="definition", term=term, label=f"17 U.S.C. §101 (“{term}”)",
                    page=4, lead=[text], children=children)

    visual = definition("work-of-visual-art", "work of visual art", "A “work of visual art” is—", children=[
        node("usc17-s101-def-work-of-visual-art-1", kind="definition",
             label="17 U.S.C. §101 (“work of visual art”)(1)", page=5, lead=["(1) a painting; or"]),
    ])
    s = node("usc17-s101", label="17 U.S.C. §101", heading="Definitions", page=4,
             lead=["Except as otherwise provided in this title, as used in this title, the following terms and",
                   "their variant forms mean the following:"],
             children=[definition("joint-work", "joint work", "A “joint work” is a work prepared by two authors."),
                       definition("literary-works", "literary works", "“Literary works” are works expressed."),
                       visual])

    chunks = chunk_tree(tree(s), budget=500, count_tokens=words)  # §101 would fit whole: it still splits

    assert keys(chunks) == [P + "usc17-s101~lead", P + "usc17-s101-def-joint-work",
                            P + "usc17-s101-def-literary-works", P + "usc17-s101-def-work-of-visual-art"]
    assert [c.kind for c in chunks] == ["rule", "definition", "definition", "definition"]
    joint = chunks[1]
    assert joint.text == ("17 U.S.C. §101 (“joint work”) — Definitions\n"
                          "A “joint work” is a work prepared by two authors.")
    assert chunks[3].sub_ref_ids == [P + "usc17-s101-def-work-of-visual-art-1"]
    assert (chunks[3].page_start, chunks[3].page_end) == (4, 5)


def test_repealed_node_has_a_body():
    s509 = node("usc17-s509", kind="repealed", label="17 U.S.C. §509", heading="[Repealed]",
                lead=[TextLine("[Repealed]", 206, marks=[8])], scope="Chapter 5")
    [c] = chunk_tree(tree(s509), budget=500, count_tokens=words)
    assert c.body == "[Repealed]"
    assert c.text == "17 U.S.C. §509 — [Repealed]\n[Repealed]"
    assert (c.kind, c.page_start, c.page_end) == ("repealed", 206, 206)


def test_a_node_with_no_text_is_refused():
    with pytest.raises(ValueError, match="usc17-s9"):
        chunk_tree(tree(node("usc17-s9", label="17 U.S.C. §9")), budget=500, count_tokens=words)


def s122_like(path):
    """A section with no text of its own: two subsections, nothing before or after them."""
    return node(path, label=path, heading="Another section", children=[
        node(f"{path}-a", label=f"{path}(a)", lead=["(a) First subsection of the other section, some words here."]),
        node(f"{path}-b", label=f"{path}(b)", lead=["(b) Second subsection of the other section, more words here."]),
    ])


def test_budget_moves_boundaries_not_ids():
    t = tree(s107(), s122(NON_BARE_A_LEAD), s122_like("usc17-s123"))
    paths = {n.path for n in t.walk()}

    small = chunk_tree(t, budget=30, count_tokens=words)
    large = chunk_tree(t, budget=500, count_tokens=words)

    assert len(small) != len(large)
    for chunks in (small, large):
        assert {c.ref_id.removeprefix(P) for c in chunks} <= paths
        assert all(c.chunk_key.startswith(c.ref_id) for c in chunks)
        # Every node is still found: as a chunk, inside one, or above one.
        found = {r for c in chunks for r in (*c.ancestor_ref_ids, *c.sub_ref_ids)}
        assert found == {P + p for p in paths}


def test_payload_links():
    s = s122(NON_BARE_A_LEAD)
    a = s.children[0]
    s.history_notes = ["ch1-n65"]
    a.history_notes = ["ch1-n66"]
    a.children[1].history_notes = ["ch1-n67"]
    a.xrefs = ["usc17-s119"]
    a.children[0].xrefs = ["usc17-s111", "usc17-s119"]
    notes = [node(f"ch1-n{n}", label=f"ch. 1 n.{n}", kind="history_note", lead=[f"Note {n}."],
                  annotates=on, effective_date=n == 66)
             for n, on in ((65, "usc17-s122"), (66, "usc17-s122-a"), (67, "usc17-s122-a-2"))]

    chunks = chunk_tree(tree(s, *notes), budget=45, count_tokens=words)

    lead, a1, a2 = chunks[:3]
    assert a2.ancestor_ref_ids == [P + "usc17-s122-a-2", P + "usc17-s122-a", P + "usc17-s122"]
    assert lead.ancestor_ref_ids == [P + "usc17-s122-a", P + "usc17-s122"]
    # A chunk's text is part of every node above it: their notes apply to it, its own, and those inside it.
    assert lead.history_notes == [P + "ch1-n65", P + "ch1-n66"]
    assert a1.history_notes == [P + "ch1-n65", P + "ch1-n66"]
    assert a2.history_notes == [P + "ch1-n65", P + "ch1-n66", P + "ch1-n67"]
    assert a2.effective_date_notes == [P + "ch1-n66"]
    for c in chunks:
        assert set(c.effective_date_notes) <= set(c.history_notes)
    # Cross-references are the law named in the chunk's own text: paths, unprefixed, once each.
    assert lead.xrefs == ["usc17-s119"]
    assert a1.xrefs == ["usc17-s111", "usc17-s119"]
    assert a2.xrefs == []
    # A whole chunk carries the links of every node inside it.
    [whole] = [c for c in chunk_tree(tree(s, *notes), budget=500, count_tokens=words)
               if c.path == "usc17-s122"]
    assert whole.history_notes == [P + "ch1-n65", P + "ch1-n66", P + "ch1-n67"]
    assert whole.xrefs == ["usc17-s119", "usc17-s111"]
    assert whole.ancestor_ref_ids == [P + "usc17-s122"]

    orders = [c.order for c in chunks]
    assert orders == sorted(orders) and len(set(orders)) == len(orders)
    assert keys(chunks)[3:] == [P + "ch1-n65", P + "ch1-n66", P + "ch1-n67"]  # document order, whole book
    assert len(set(keys(chunks))) == len(chunks)
    assert len({c.point_id for c in chunks}) == len(chunks)


def test_note_on_an_unknown_node_is_refused():
    note = node("ch1-n1", label="ch. 1 n.1", kind="history_note", annotates="usc17-s999", lead=["A note."])
    with pytest.raises(ValueError, match="usc17-s999"):
        chunk_tree(tree(note), budget=500, count_tokens=words)


def test_chunk_keys_follow_the_grammar():
    chunks = chunk_tree(tree(s122(NON_BARE_A_LEAD)), budget=45, count_tokens=words)
    assert chunks[0].chunk_key == chunk_key(P + "usc17-s122-a", "~lead")


def test_budget_must_be_positive():
    with pytest.raises(ValueError):
        chunk_tree(tree(s107()), budget=0, count_tokens=words)


# --- the token counter ----------------------------------------------------------------------------

def test_cached_counter_counts_each_text_once():
    seen = []

    def count(text):
        seen.append(text)
        return None if text == "long" else len(text)

    counter = CachedTokenCounter(count)
    assert [counter("ab"), counter("ab"), counter("long"), counter("long"), counter("c")] == [2, 2, None, None, 1]
    assert seen == ["ab", "long", "c"]  # "too long" is an answer too, cached like a number


def test_ollama_token_counter_asks_the_embedding_model(monkeypatch):
    asked = []
    monkeypatch.setattr(tokens, "prompt_token_count", lambda text: asked.append(text) or 7)

    counter = ollama_token_counter()

    assert isinstance(counter, CachedTokenCounter)
    assert (counter("some text"), counter("some text")) == (7, 7)
    assert asked == ["some text"]


def test_chunk_tree_counts_each_text_once():
    seen = []

    def count(text):
        seen.append(text)
        return words(text)

    chunk_tree(tree(s122(NON_BARE_A_LEAD)), budget=45, count_tokens=count)
    assert len(seen) == len(set(seen))


# --- the whole of chapters 1 and 5 ----------------------------------------------------------------

def test_ch1_ch5_every_node_is_reachable_and_under_budget(ch1_ch5_parse):
    t = ch1_ch5_parse.tree
    chunks = chunk_tree(t, budget=800, count_tokens=words)

    nodes = list(t.walk())
    direct = {P + c.path for c in chunks} | {r for c in chunks for r in c.sub_ref_ids}
    above = {r for c in chunks for r in c.ancestor_ref_ids}  # lookup scrolls these too
    assert [n.path for n in nodes if P + n.path not in direct | above] == []
    # A node reached only from above has no text of its own outside its children but its label and
    # heading: it was split, and its lead is empty or carried as context, and it has no tail.
    assert all(n.children and not n.tail for n in nodes if P + n.path not in direct)
    over = [(c.chunk_key, c.tokens) for c in chunks if c.tokens > 800 or c.tokens == -1]
    assert over == []
    assert all(c.body.strip() for c in chunks)
    assert len({c.chunk_key for c in chunks}) == len(chunks)
    # Each chunk names every node above it (R13), so a node with no chunk of its own is still renderable.
    labels = {n.path: n.label for n in nodes}
    spans = {n.path: n.pages() for n in nodes}
    for c in chunks:
        assert len(c.ancestor_labels) == len(c.ancestor_ref_ids)
        assert c.ancestor_labels[0] == c.label
        assert c.ancestor_labels == [labels[r.removeprefix(P)] for r in c.ancestor_ref_ids]
        assert c.ancestor_pages == [spans[r.removeprefix(P)] for r in c.ancestor_ref_ids]
    # Every note linked to a node is carried by some chunk, so no amendment history goes unflagged.
    carried = {r for c in chunks for r in c.history_notes}
    linked = {P + p for n in t.walk() for p in n.history_notes}
    assert linked <= carried


def _letters(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


@pytest.mark.parametrize("budget", [800, 60])
def test_ch1_ch5_chunks_keep_every_word_in_order(ch1_ch5_parse, budget):
    # Nothing dropped, nothing cut, nothing repeated (R4): the bodies, in chunk order, are the book's
    # text in document order, less only the leads that are a bare heading carried as context. At 60,
    # notes and long leaves are cut at paragraphs and sentences too.
    t = ch1_ch5_parse.tree
    chunks = chunk_tree(t, budget=budget, count_tokens=words)
    direct = {c.path for c in chunks} | {r.removeprefix(P) for c in chunks for r in c.sub_ref_ids}

    def text(n):
        lead = _letters(t.join(n.lead)) if n.path in direct else ""
        return lead + "".join(text(c) for c in n.children) + _letters(t.join(n.tail))

    assert "".join(_letters(c.body) for c in chunks) == "".join(text(n) for n in t.nodes)
    assert any("~p" in c.chunk_key for c in chunks)  # the text-run cut ran on the real book
