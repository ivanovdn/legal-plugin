# tests/test_book_resolve.py
"""How a cited node is labelled and paged from the chunks that hold it (`resolve_nodes`, the pure step of
`rag.book_search.fetch_nodes`; spec D1, D8; final review C1).

No Qdrant: the function reads a list of chunks, so the whole book can be checked against its own tree.
The whole-book test is the anti-goal-1 guard: a citation the check accepts is rendered from what this
returns, so a node paged one page late here reaches the attorney as a verified citation with the wrong page.
"""
import pytest

from ingest.books.chunker import chunk_tree
from ingest.books.models import BookChunk, SubRef
from ingest.books.ref_ids import make_ref_id
from ingest.books.resolve import NodeInfo, resolve_nodes

P = "circ92-2025-12"


def _id(path: str) -> str:
    return f"{P}:{path}"


def stand_in_tokens(text: str) -> int:
    """Deterministic: the measured 4.46 characters per embeddinggemma token (spec §3)."""
    return int(len(text) / 4.46) + 1


def _chunk(path, *, label, order, pages, above=(), suffix="", sub_refs=(), kind="rule", own_pages=None):
    """`above`: (path, label, (first, last)) of each parent, nearest first. `own_pages`: the node's whole
    subtree, which a split node's part does not cover alone."""
    ref_id = _id(path)
    return BookChunk(
        chunk_key=f"{ref_id}{suffix}", ref_id=ref_id, book="circ92", edition="2025-12", path=path, kind=kind,
        source_type="usc_title", jurisdiction="US", label=label, heading="H", page_start=pages[0],
        page_end=pages[1], text=f"{label}\nbody", body="body", tokens=10, order=order,
        ancestor_ref_ids=[ref_id, *(_id(p) for p, _, _ in above)],
        ancestor_labels=[label, *(lbl for _, lbl, _ in above)],
        ancestor_pages=[own_pages or pages, *(span for _, _, span in above)],
        sub_refs=list(sub_refs), sub_ref_ids=[s.ref_id for s in sub_refs],
    )


# §111(d) as the book prints it: its heading line "(d) Statutory License for Secondary Transmissions by
# Cable Systems.—" is on p. 33, and the chunker drops that bare-heading lead (its words are in every child's
# header). Every chunk below starts on p. 34. The node's own span is 33–38.
S111 = ("usc17-s111", "17 U.S.C. §111", (28, 40))
S111_D = ("usc17-s111-d", "17 U.S.C. §111(d)", (33, 38))
D1 = _chunk("usc17-s111-d-1", label="17 U.S.C. §111(d)(1)", order=1, pages=(34, 36), above=(S111_D, S111))
D2 = _chunk("usc17-s111-d-2", label="17 U.S.C. §111(d)(2)", order=2, pages=(36, 38), above=(S111_D, S111))


def test_an_ancestor_only_node_is_paged_by_its_own_span_not_by_the_chunks_below():
    node = resolve_nodes([_id("usc17-s111-d")], [D1, D2])[_id("usc17-s111-d")]

    assert (node.label, node.page_start, node.page_end) == ("17 U.S.C. §111(d)", 33, 38)
    assert node.containers == {_id("usc17-s111-d")}


def test_a_node_with_its_own_chunk_is_paged_by_its_whole_subtree():
    """§108's lead in three parts: the node's span is its own entry, not one part's."""
    parts = [
        _chunk("usc17-s108", suffix=sfx, label="17 U.S.C. §108", order=o, pages=pp, own_pages=(5, 9))
        for sfx, o, pp in (("~lead", 10, (5, 5)), ("~lead~p2", 11, (6, 6)), ("~lead~p3", 12, (7, 8)))
    ]
    node = resolve_nodes([_id("usc17-s108")], parts)[_id("usc17-s108")]

    assert (node.page_start, node.page_end) == (5, 9)
    assert node.containers == {_id("usc17-s108")}


def test_a_node_inside_a_chunk_keeps_its_own_pages():
    sub = SubRef(ref_id=_id("usc17-s106-2"), label="17 U.S.C. §106(2)", page_start=9, page_end=10)
    s106 = _chunk("usc17-s106", label="17 U.S.C. §106", order=3, pages=(8, 10), sub_refs=(sub,))
    node = resolve_nodes([sub.ref_id], [s106])[sub.ref_id]

    assert (node.label, node.page_start, node.page_end) == ("17 U.S.C. §106(2)", 9, 10)
    assert node.containers == {_id("usc17-s106")}


def test_an_id_no_chunk_holds_is_absent_and_nothing_asked_is_nothing():
    assert resolve_nodes([_id("usc17-s999")], [D1, D2]) == {}
    assert resolve_nodes([], [D1, D2]) == {}


def test_each_id_is_resolved_once_whatever_the_order_of_the_chunks():
    ids = [_id("usc17-s111-d"), _id("usc17-s111"), _id("usc17-s111-d")]
    forward, backward = resolve_nodes(ids, [D1, D2]), resolve_nodes(ids, [D2, D1])

    assert list(forward) == [_id("usc17-s111-d"), _id("usc17-s111")]
    assert forward == backward
    assert isinstance(forward[_id("usc17-s111")], NodeInfo)
    assert (forward[_id("usc17-s111")].page_start, forward[_id("usc17-s111")].page_end) == (28, 40)


# --- the whole book -----------------------------------------------------------------------------------

@pytest.mark.parametrize("budget", [800, 250])
def test_every_node_of_the_book_resolves_to_its_own_label_kind_source_type_and_pages(circ92_whole_parse, budget):
    """For EVERY node of the whole-body parse, whatever holds it (its own chunk, a chunk it sits inside, or
    only the chunks below it): resolving it over the chunk list gives the tree's label, kind, source type,
    appendix and pages. 800 is the default budget; 250 splits far more, so far more nodes are held only
    from below. Before ancestor_pages, 6 nodes at 800 (§111(d), §115(b), §115(d)(4)(D), §802(f),
    §802(f)(1), §803(b)(6)) started one page late."""
    tree = circ92_whole_parse.tree
    chunks = chunk_tree(tree, budget=budget, count_tokens=stand_in_tokens)
    by_id = {make_ref_id(tree.book, node.path): node for node in tree.walk()}

    resolved = resolve_nodes(list(by_id), chunks)

    assert sorted(set(by_id) - set(resolved)) == []
    wrong = []
    for ref_id, node in by_id.items():
        info = resolved[ref_id]
        want = (node.label, node.kind, node.source_type, node.appendix, node.pages())
        got = (info.label, info.kind, info.source_type, info.appendix, (info.page_start, info.page_end))
        if got != want:
            wrong.append((ref_id, want, got))
    assert wrong == []
    assert len(by_id) > 4000  # the whole book, not a corner of it
