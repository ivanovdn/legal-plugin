# tests/test_book_models.py
import pytest
from pydantic import ValidationError

from ingest.books.models import (
    KINDS,
    SOURCE_TYPES,
    BookChunk,
    BookTree,
    Node,
    SubRef,
    TextLine,
)
from ingest.books.ref_ids import chunk_key, point_id
from ingest.books.registry import get_book

BOOK = get_book("circ92")


def _node(path, *, lead=(), children=(), tail=(), **kw):
    fields = dict(
        path=path, kind="rule", source_type="usc_title", jurisdiction="US", label=path, heading="H", scope="Chapter 1"
    )
    fields.update(kw)
    return Node(**fields, lead=list(lead), children=list(children), tail=list(tail))


def _line(text, page, **kw):
    return TextLine(text=text, page=page, **kw)


def _chunk(**over):
    fields = dict(
        chunk_key="circ92-2025-12:usc17-s107",
        ref_id="circ92-2025-12:usc17-s107",
        book="circ92",
        edition="2025-12",
        path="usc17-s107",
        kind="rule",
        source_type="usc_title",
        jurisdiction="US",
        label="17 U.S.C. §107",
        heading="Limitations on exclusive rights: Fair use",
        page_start=20,
        page_end=20,
        text="17 U.S.C. §107 — Fair use\nthe fair use of a copyrighted work",
        body="the fair use of a copyrighted work",
        tokens=12,
        order=3,
    )
    fields.update(over)
    return BookChunk(**fields)


def test_constants():
    assert KINDS == ("rule", "definition", "history_note", "repealed")
    assert SOURCE_TYPES == ("usc_title", "uncodified_act", "treaty")


def test_text_line_defaults_are_not_shared():
    a, b = _line("x", 1), _line("y", 2)
    assert (a.marks, a.para_start) == ([], False)
    a.marks.append(3)
    assert b.marks == []


def test_node_defaults_are_not_shared():
    a = _node("usc17-s1")
    b = _node("usc17-s2")
    a.xrefs.append("usc17-s2")
    a.xrefs_unparsed.append("the Act of 1999")
    a.history_notes.append("ch1-n1")
    assert (b.xrefs, b.xrefs_unparsed, b.history_notes) == ([], [], [])
    assert (a.appendix, a.term, a.note_number, a.annotates, a.effective_date) == (None, None, None, None, False)


@pytest.mark.parametrize("over", [dict(kind="section"), dict(kind="history note"), dict(source_type="statute")])
def test_node_refuses_an_unknown_kind_or_source_type(over):
    with pytest.raises(ValueError):
        _node("usc17-s1", **over)


def test_node_walk_is_depth_first_in_document_order():
    leaf_a, leaf_b = _node("p-a"), _node("p-b")
    mid = _node("p-b-1")
    leaf_b.children.append(mid)
    root = _node("p", children=[leaf_a, leaf_b])
    assert [n.path for n in root.walk()] == ["p", "p-a", "p-b", "p-b-1"]


def test_own_lines_are_lead_then_tail_without_children():
    child = _node("p-a", lead=[_line("child", 5)])
    root = _node("p", lead=[_line("lead", 4)], children=[child], tail=[_line("tail", 6)])
    assert [l.text for l in root.own_lines()] == ["lead", "tail"]


def test_all_lines_are_in_document_order():
    c1 = _node("p-a", lead=[_line("c1", 5)])
    c2 = _node("p-b", lead=[_line("c2a", 5), _line("c2b", 6)])
    root = _node("p", lead=[_line("lead", 4)], children=[c1, c2], tail=[_line("tail", 6)])
    assert [l.text for l in root.all_lines()] == ["lead", "c1", "c2a", "c2b", "tail"]


def test_pages_span_the_whole_subtree():
    c1 = _node("p-a", lead=[_line("c1", 9)])
    root = _node("p", lead=[_line("lead", 7)], children=[c1], tail=[_line("tail", 8)])
    assert root.pages() == (7, 9)
    assert c1.pages() == (9, 9)


def test_pages_of_a_node_with_no_lines_is_an_error():
    with pytest.raises(ValueError, match="usc17-s1"):
        _node("usc17-s1").pages()


def test_a_node_with_only_children_takes_their_pages():
    root = _node("p", children=[_node("p-a", lead=[_line("c", 12)])])
    assert root.pages() == (12, 12)


def test_book_tree_walk_and_index():
    a = _node("usc17-s1", children=[_node("usc17-s1-a")])
    b = _node("usc17-s2")
    tree = BookTree(book=BOOK, nodes=[a, b])
    assert [n.path for n in tree.walk()] == ["usc17-s1", "usc17-s1-a", "usc17-s2"]
    index = tree.index()
    assert set(index) == {"usc17-s1", "usc17-s1-a", "usc17-s2"}
    assert index["usc17-s1-a"] is a.children[0]


def test_book_tree_index_refuses_a_duplicate_path():
    tree = BookTree(book=BOOK, nodes=[_node("usc17-s1"), _node("usc17-s1")])
    with pytest.raises(ValueError, match="usc17-s1"):
        tree.index()


def test_book_chunk_defaults_and_round_trip():
    c = _chunk(sub_refs=[SubRef(ref_id="circ92-2025-12:usc17-s107-a", label="(a)", page_start=20, page_end=20)])
    assert c.appendix is None
    assert (c.ancestor_ref_ids, c.sub_ref_ids, c.xrefs, c.history_notes, c.effective_date_notes) == ([], [], [], [], [])
    assert BookChunk.model_validate(c.model_dump()) == c
    assert c.model_dump()["sub_refs"][0]["ref_id"] == "circ92-2025-12:usc17-s107-a"


def test_book_chunk_list_defaults_are_not_shared():
    a, b = _chunk(), _chunk()
    a.xrefs.append("usc17-s108")
    assert b.xrefs == []


def test_point_id_is_derived_not_stored():
    c = _chunk(chunk_key=chunk_key("circ92-2025-12:usc17-s107", "~lead"))
    assert c.point_id == point_id("circ92-2025-12:usc17-s107~lead")
    assert "point_id" not in c.model_dump()


def test_tokens_may_be_minus_one_for_text_over_the_window():
    assert _chunk(tokens=-1).tokens == -1


@pytest.mark.parametrize("over", [dict(kind="section"), dict(source_type="statute")])
def test_book_chunk_refuses_an_unknown_kind_or_source_type(over):
    with pytest.raises(ValidationError):
        _chunk(**over)


def test_ancestor_labels_pair_with_ancestor_ref_ids():
    # Ruling R13: a node with no chunk of its own is cited from a chunk below it, so the chunk names it.
    c = _chunk(ancestor_ref_ids=["circ92-2025-12:usc17-s107-1", "circ92-2025-12:usc17-s107"],
               ancestor_labels=["17 U.S.C. §107(1)", "17 U.S.C. §107"])
    assert c.ancestor_labels == ["17 U.S.C. §107(1)", "17 U.S.C. §107"]
    assert BookChunk.model_validate(c.model_dump()) == c
    assert _chunk().ancestor_labels == []


@pytest.mark.parametrize("labels", [[], ["17 U.S.C. §107(1)"], ["a", "b", "c"]])
def test_book_chunk_refuses_ancestor_lists_of_unequal_length(labels):
    with pytest.raises(ValidationError, match="ancestor_labels"):
        _chunk(ancestor_ref_ids=["circ92-2025-12:usc17-s107-1", "circ92-2025-12:usc17-s107"],
               ancestor_labels=labels)
