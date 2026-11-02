# tests/test_book_search.py
"""Reading the legal_books collection: hybrid search, lookup by citation, node lookup.

Against a real Qdrant (the compose-pinned image, via testcontainers): the RRF fusion, the BM25 vector
and the `kind` / array-field filters are Qdrant's, and a mock would pass whatever Qdrant really does.
Only the embedding (Ollama) is replaced, by a deterministic hashed bag of words, and the reranker.
"""
import hashlib
import math
import re

import pytest
from qdrant_client import models

from config import get_settings
from ingest.books.models import BookChunk, SubRef
from rag.book_search import (
    BookHit,
    LookupResult,
    NodeInfo,
    fetch_nodes,
    kind_filter,
    lookup_book_chunks,
    search_book_chunks,
)
from rag.book_store import ensure_book_collection, get_book_client, upsert_book_chunks

DIM = 768
PREFIX = "circ92-2025-12"


def _id(path: str) -> str:
    return f"{PREFIX}:{path}"


def _embed(text: str) -> list[float]:
    """A hashed bag of words: texts sharing words are close, and the same text is the same vector."""
    vec = [0.0] * DIM
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        vec[int(hashlib.sha256(word.encode()).hexdigest(), 16) % DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _chunk(
    path: str,
    *,
    label: str,
    heading: str,
    body: str,
    order: int,
    suffix: str = "",
    kind: str = "rule",
    source_type: str = "usc_title",
    jurisdiction: str = "US",
    pages: tuple[int, int] = (1, 1),
    tokens: int = 100,
    above: tuple[tuple[str, str], ...] = (),  # (path, label) of the parents, nearest first
    sub_refs: tuple[SubRef, ...] = (),
    appendix: str | None = None,
) -> BookChunk:
    ref_id = _id(path)
    return BookChunk(
        chunk_key=f"{ref_id}{suffix}",
        ref_id=ref_id,
        book="circ92",
        edition="2025-12",
        path=path,
        kind=kind,
        source_type=source_type,
        jurisdiction=jurisdiction,
        label=label,
        heading=heading,
        appendix=appendix,
        page_start=pages[0],
        page_end=pages[1],
        text=f"{label} — {heading}\n{body}",
        body=body,
        tokens=tokens,
        order=order,
        ancestor_ref_ids=[ref_id, *(_id(p) for p, _ in above)],
        ancestor_labels=[label, *(lbl for _, lbl in above)],
        sub_refs=list(sub_refs),
        sub_ref_ids=[s.ref_id for s in sub_refs],
    )


S107_LEAD = _chunk(
    "usc17-s107", suffix="~lead", label="17 U.S.C. §107", heading="Limitations on exclusive rights: Fair use",
    body="the fair use of a copyrighted work for criticism comment news reporting teaching is not an infringement",
    order=1, pages=(10, 10),
)
S107_FACTORS = _chunk(
    "usc17-s107-1", label="17 U.S.C. §107(1)", heading="Limitations on exclusive rights: Fair use",
    body="the purpose and character of the use of the work including whether such use is of a commercial nature",
    order=2, pages=(10, 11), above=(("usc17-s107", "17 U.S.C. §107"),),
)
S106 = _chunk(
    "usc17-s106", label="17 U.S.C. §106", heading="Exclusive rights in copyrighted works",
    body="the owner of copyright has the exclusive rights to reproduce the work in copies or phonorecords",
    order=3, pages=(9, 9),
    sub_refs=(
        SubRef(ref_id=_id("usc17-s106-1"), label="17 U.S.C. §106(1)", page_start=9, page_end=9),
        SubRef(ref_id=_id("usc17-s106-2"), label="17 U.S.C. §106(2)", page_start=9, page_end=10),
    ),
)
JOINT_WORK = _chunk(
    "usc17-s101-def-joint-work", kind="definition", label='17 U.S.C. §101 "joint work"', heading="Definitions",
    body="a joint work is a work prepared by two or more authors with the intention that their contributions merge",
    order=5, pages=(6, 6), above=(("usc17-s101", "17 U.S.C. §101"),),
)
NOTE = _chunk(
    "ch1-n3", kind="history_note", label="Chapter 1 note 3", heading="Notes",
    body="Pub. L. 94-553 amended the provisions on the work and took effect on January 1 1978",
    order=6, pages=(30, 30),
)
TREATY = _chunk(
    "trips-art38-1", source_type="treaty", jurisdiction="international", label="TRIPs art. 38(1)",
    heading="Term of protection", appendix="S",
    body="the term of protection of a work other than a photographic work shall be not less than fifty years",
    order=7, pages=(457, 457),
)
REPEALED = _chunk(
    "usc17-s101-def-berne-convention-work", kind="repealed", label='17 U.S.C. §101 "Berne Convention work"',
    heading="Definitions", body="the definition of a Berne Convention work was deleted from the work provisions",
    order=8, pages=(480, 480), appendix="T",
)
S108 = [
    _chunk("usc17-s108", suffix=suffix, label="17 U.S.C. §108", heading="Reproduction by libraries and archives",
           body=f"libraries and archives may reproduce a work for preservation {part}",
           order=order, pages=pages, tokens=400)
    for suffix, part, order, pages in (
        ("~lead", "part one", 10, (5, 5)),
        ("~lead~p2", "part two", 11, (6, 6)),
        ("~lead~p3", "part three", 12, (7, 8)),
    )
]
# A section whose whole text sits in its subsections: it has no chunk of its own, and appears only in
# its descendants' ancestor lists.
S114_A = _chunk(
    "usc17-s114-a", label="17 U.S.C. §114(a)", heading="Scope of exclusive rights in sound recordings",
    body="the exclusive rights of the owner of copyright in a sound recording are limited work", order=20,
    pages=(30, 31), above=(("usc17-s114", "17 U.S.C. §114"),),
)
S114_B = _chunk(
    "usc17-s114-b", label="17 U.S.C. §114(b)", heading="Scope of exclusive rights in sound recordings",
    body="the exclusive right to reproduce a sound recording does not extend to independent fixations work",
    order=21, pages=(31, 33), above=(("usc17-s114", "17 U.S.C. §114"),),
)

CORPUS = [S107_LEAD, S107_FACTORS, S106, JOINT_WORK, NOTE, TREATY, REPEALED, *S108, S114_A, S114_B]
ALLOWED = {c.ref_id for c in CORPUS if c.kind in ("rule", "definition")}


def _env(monkeypatch, **env: str) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key.upper(), value)
    get_settings.cache_clear()


@pytest.fixture
def books(book_qdrant, monkeypatch):
    """The corpus in a fresh collection, the embedding replaced and the reranker off."""
    _env(monkeypatch, reranker_enabled="false", book_search_top_k="50")
    monkeypatch.setattr("rag.book_search.embed_query", _embed)
    ensure_book_collection()
    # Upserted out of document order: the order a lookup shows is the `order` field's, not insertion.
    chunks = list(reversed(CORPUS))
    upsert_book_chunks(chunks, [_embed(c.text) for c in chunks])
    return book_qdrant


def _ids(hits: list[BookHit]) -> list[str]:
    return [h.chunk.ref_id for h in hits]


# --- search ---------------------------------------------------------------------------------------

def test_default_search_excludes_notes_and_repealed(books):
    hits = search_book_chunks("work")

    assert isinstance(hits[0], BookHit)
    assert set(_ids(hits)) == ALLOWED
    kinds = {h.chunk.kind for h in hits}
    assert kinds == {"rule", "definition"}


def test_history_notes_on_request(books):
    hits = search_book_chunks("work", include_history_notes=True)

    assert set(_ids(hits)) == ALLOWED | {NOTE.ref_id}
    assert REPEALED.ref_id not in _ids(hits)


def test_kind_filter_is_the_d6_filter(books):
    def kinds(flt: models.Filter) -> set[str]:
        points, _ = get_book_client().scroll(books, scroll_filter=flt, limit=100, with_payload=True)
        return {p.payload["kind"] for p in points}

    assert kinds(kind_filter(False)) == {"rule", "definition"}
    assert kinds(kind_filter(True)) == {"rule", "definition", "history_note"}


def test_a_note_found_by_its_own_words_still_does_not_surface_by_default(books):
    """The note is the best match for its own text, in both rankings."""
    hits = search_book_chunks(NOTE.body)

    assert NOTE.ref_id not in _ids(hits)
    assert hits


def test_filter_applies_before_the_candidate_cut(books, monkeypatch):
    """One candidate per ranking. The note is the first candidate of both rankings for its own text, so a
    filter applied after the cut would leave nothing; applied inside each prefetch it leaves the best
    allowed chunk."""
    _env(monkeypatch, book_dense_candidates="1", book_sparse_candidates="1")

    assert search_book_chunks(NOTE.body)


def test_treaty_in_default_results(books):
    hits = search_book_chunks(TREATY.body)

    treaty = next(h for h in hits if h.chunk.ref_id == TREATY.ref_id)
    assert treaty.chunk.source_type == "treaty"
    assert hits[0].chunk.ref_id == TREATY.ref_id


def test_the_exact_words_rank_first(books):
    assert search_book_chunks(JOINT_WORK.body)[0].chunk.ref_id == JOINT_WORK.ref_id
    assert search_book_chunks(S107_FACTORS.body)[0].chunk.ref_id == S107_FACTORS.ref_id


def test_hits_carry_the_whole_chunk_and_the_fused_score(books):
    top = search_book_chunks(TREATY.body)[0]

    assert top.chunk == TREATY
    assert top.score > 0


def test_top_k_and_min_score(books, monkeypatch):
    _env(monkeypatch, book_search_top_k="2")
    assert len(search_book_chunks("work")) == 2

    _env(monkeypatch, book_search_top_k="50", book_min_score="1000")
    assert search_book_chunks("work") == []


def test_min_score_keeps_what_reaches_it(books, monkeypatch):
    scores = sorted((h.score for h in search_book_chunks("work")), reverse=True)
    assert len(scores) >= 3
    _env(monkeypatch, book_min_score=str(scores[1]))

    assert [h.score for h in search_book_chunks("work")] == scores[:2]


def _fake_rerank(scores_by_ref: dict[str, float], seen: dict | None = None):
    """A reranker that orders by the given scores, trims to top_n as the real one does when it runs."""

    def rerank(query, results, top_n=None):
        if seen is not None:
            seen.update(query=query, top_n=top_n, results=[dict(r) for r in results])
        scored = [{**r, "rerank_score": scores_by_ref[r["chunk"].ref_id]} for r in results]
        scored.sort(key=lambda r: r["rerank_score"], reverse=True)
        return scored[:top_n]

    return rerank


def test_the_reranker_orders_and_its_score_replaces_the_fused_one(books, monkeypatch):
    seen: dict = {}
    scores = {c.ref_id: 0.0 for c in CORPUS}
    scores[S106.ref_id] = 0.9
    scores[TREATY.ref_id] = 0.8
    monkeypatch.setattr("rag.book_search.rerank", _fake_rerank(scores, seen))
    _env(monkeypatch, book_search_top_k="2")

    hits = search_book_chunks(TREATY.body)

    assert [(h.chunk.ref_id, h.score) for h in hits] == [(S106.ref_id, 0.9), (TREATY.ref_id, 0.8)]
    assert seen["query"] == TREATY.body
    assert seen["top_n"] == 2
    # The reranker reads the embedded text, header included: a body alone ("(1) the purpose ...") has
    # lost the section it belongs to.
    by_ref = {r["chunk"].ref_id: r for r in seen["results"]}
    assert by_ref[TREATY.ref_id]["text"] == TREATY.text
    assert by_ref[TREATY.ref_id]["score"] > 0


def test_top_k_is_cut_after_the_reranker(books, monkeypatch):
    """The reranker returns everything it was given when it is off or has failed."""
    _env(monkeypatch, book_search_top_k="3")
    monkeypatch.setattr("rag.book_search.rerank", lambda query, results, top_n=None: results)

    assert len(search_book_chunks("work")) == 3


def test_min_score_compares_the_rerank_score_when_there_is_one(books, monkeypatch):
    """The fused score is a rank-based sum (1.0 for first in both rankings, then falling away); the
    reranker's is a relevance probability. A floor set for one means something else for the other, so a
    hit the reranker scored is judged by its own score."""
    fused = {h.chunk.ref_id: h.score for h in search_book_chunks(TREATY.body)}
    assert sum(score >= 0.5 for score in fused.values()) > 1  # judged by these, two would pass the floor
    scores = {c.ref_id: 0.05 for c in CORPUS}
    scores[TREATY.ref_id] = 0.95
    monkeypatch.setattr("rag.book_search.rerank", _fake_rerank(scores))
    _env(monkeypatch, book_min_score="0.5")

    assert _ids(search_book_chunks(TREATY.body)) == [TREATY.ref_id]


def test_a_hit_the_reranker_did_not_score_is_judged_by_its_fused_score(books, monkeypatch):
    """A fallback (reranker down) hands the fused order back with no rerank_score."""
    monkeypatch.setattr("rag.book_search.rerank", lambda query, results, top_n=None: results[:top_n])
    fused = search_book_chunks(TREATY.body)
    _env(monkeypatch, book_min_score=str(fused[0].score + 1e-6))

    assert search_book_chunks(TREATY.body) == []


# --- lookup ---------------------------------------------------------------------------------------

def test_lookup_section_in_order(books):
    result = lookup_book_chunks("§ 107")

    assert isinstance(result, LookupResult)
    assert result.target == _id("usc17-s107")
    # Inserted child-first; shown in document order.
    assert [c.chunk_key for c in result.shown] == [S107_LEAD.chunk_key, S107_FACTORS.chunk_key]
    assert result.more == []


def test_lookup_a_ref_id(books):
    result = lookup_book_chunks(_id("usc17-s107"))

    assert result.target == _id("usc17-s107")
    assert [c.chunk_key for c in result.shown] == [S107_LEAD.chunk_key, S107_FACTORS.chunk_key]


def test_lookup_reaches_a_section_with_no_chunk_of_its_own(books):
    result = lookup_book_chunks("17 U.S.C. §114")

    assert result.target == _id("usc17-s114")
    assert [c.chunk_key for c in result.shown] == [S114_A.chunk_key, S114_B.chunk_key]


def test_lookup_cap_lists_the_rest(books, monkeypatch):
    # Three parts of 400 tokens: 900 holds two.
    _env(monkeypatch, book_lookup_max_tokens="900")
    result = lookup_book_chunks("§108")
    assert [c.chunk_key for c in result.shown] == [s.chunk_key for s in S108[:2]]
    assert result.more == [_id("usc17-s108")]

    # And the whole node when the cap allows it.
    _env(monkeypatch, book_lookup_max_tokens="1200")
    result = lookup_book_chunks("§108")
    assert len(result.shown) == 3
    assert result.more == []


def test_lookup_cap_always_shows_one(books, monkeypatch):
    _env(monkeypatch, book_lookup_max_tokens="10")
    result = lookup_book_chunks("§108")

    assert [c.chunk_key for c in result.shown] == [S108[0].chunk_key]
    assert result.more == [_id("usc17-s108")]


def test_lookup_cap_stops_at_the_first_chunk_that_does_not_fit(books, monkeypatch):
    """Not the cheapest chunks that fit: the node in order, up to the cap."""
    _env(monkeypatch, book_lookup_max_tokens="250")
    result = lookup_book_chunks("§107")  # two chunks of 100: both fit
    assert len(result.shown) == 2

    _env(monkeypatch, book_lookup_max_tokens="150")
    result = lookup_book_chunks("§107")
    assert [c.chunk_key for c in result.shown] == [S107_LEAD.chunk_key]
    assert result.more == [_id("usc17-s107-1")]


def test_lookup_lists_each_remaining_node_once_in_order(books, monkeypatch):
    _env(monkeypatch, book_lookup_max_tokens="10")
    result = lookup_book_chunks("§114")  # no chunk of its own: two children of 100 tokens each

    assert [c.chunk_key for c in result.shown] == [S114_A.chunk_key]
    assert result.more == [_id("usc17-s114-b")]


def test_a_chunk_past_the_embedding_window_counts_as_the_whole_cap(books, monkeypatch):
    """`tokens` is -1 for a chunk the counter said was past the window; counting it as nothing would let
    it ride along under any cap."""
    over = S114_B.model_copy(update={"tokens": -1})
    upsert_book_chunks([over], [_embed(over.text)])

    assert [c.chunk_key for c in lookup_book_chunks("§114").shown] == [S114_A.chunk_key]


def test_lookup_reaches_repealed(books):
    result = lookup_book_chunks("usc17-s101-def-berne-convention-work")

    assert [c.kind for c in result.shown] == ["repealed"]


def test_lookup_reaches_a_note(books):
    result = lookup_book_chunks("ch. 1 n.3")

    assert result.target == _id("ch1-n3")
    assert [c.kind for c in result.shown] == ["history_note"]


def test_lookup_sub_path_returns_its_chunk(books):
    result = lookup_book_chunks("17 U.S.C. §106(2)")

    assert result.target == _id("usc17-s106-2")
    assert [c.chunk_key for c in result.shown] == [S106.chunk_key]


def test_lookup_nothing_matches(books):
    result = lookup_book_chunks("§999")

    assert result.shown == []
    assert result.more == []
    assert result.target == _id("usc17-s999")


def test_lookup_a_ref_id_of_another_edition_matches_nothing(books):
    result = lookup_book_chunks("circ92-2024-06:usc17-s107")

    assert result.target == "circ92-2024-06:usc17-s107"
    assert result.shown == []


def test_lookup_unparseable_is_none(books):
    assert lookup_book_chunks("tell me about fair use") is None


def test_lookup_does_not_match_a_longer_section_number(books):
    """§10 is not under §107: whole ids only."""
    assert lookup_book_chunks("§10").shown == []


# --- fetch_nodes ----------------------------------------------------------------------------------

def test_fetch_nodes(books):
    split = _id("usc17-s108")
    sub = _id("usc17-s106-2")
    nodes = fetch_nodes([split, sub, _id("usc17-s999")])

    assert set(nodes) == {split, sub}

    node = nodes[split]
    assert isinstance(node, NodeInfo)
    assert (node.ref_id, node.label, node.kind, node.source_type) == (
        split, "17 U.S.C. §108", "rule", "usc_title",
    )
    assert node.appendix is None
    assert (node.page_start, node.page_end) == (5, 8)  # across its three parts
    assert node.containers == {split}

    inner = nodes[sub]
    assert inner.label == "17 U.S.C. §106(2)"
    assert (inner.page_start, inner.page_end) == (9, 10)  # its own, not the chunk's
    assert inner.containers == {_id("usc17-s106")}
    assert (inner.kind, inner.source_type) == ("rule", "usc_title")


def test_fetch_nodes_for_a_section_with_no_chunk_of_its_own(books):
    section = _id("usc17-s114")
    nodes = fetch_nodes([section])

    node = nodes[section]
    assert node.label == "17 U.S.C. §114"  # from the children's ancestor_labels
    assert (node.page_start, node.page_end) == (30, 33)  # the whole subtree
    # Valid to cite exactly when it was itself retrieved: no child chunk stands in for it.
    assert node.containers == {section}
    assert (node.kind, node.source_type) == ("rule", "usc_title")


def test_fetch_nodes_pages_of_a_section_with_its_own_chunk_cover_its_subtree(books):
    node = fetch_nodes([_id("usc17-s107")])[_id("usc17-s107")]

    assert node.label == "17 U.S.C. §107"
    assert (node.page_start, node.page_end) == (10, 11)  # the lead's page and the child's
    assert node.containers == {_id("usc17-s107")}


def test_fetch_nodes_carries_the_appendix_and_the_treaty_type(books):
    nodes = fetch_nodes([TREATY.ref_id, REPEALED.ref_id, NOTE.ref_id])

    assert (nodes[TREATY.ref_id].source_type, nodes[TREATY.ref_id].appendix) == ("treaty", "S")
    assert (nodes[REPEALED.ref_id].kind, nodes[REPEALED.ref_id].appendix) == ("repealed", "T")
    assert nodes[NOTE.ref_id].kind == "history_note"


def test_fetch_nodes_with_nothing_asked_reads_nothing(books, monkeypatch):
    client = get_book_client()
    calls = []
    monkeypatch.setattr(client, "scroll", lambda *a, **kw: calls.append(kw) or ([], None))

    assert fetch_nodes([]) == {}
    assert calls == []


def test_the_scroll_is_followed_to_its_end(books, monkeypatch):
    """A section of many chunks must not lose the ones past the first page of a scroll."""
    _env(monkeypatch, book_lookup_max_tokens="1000000")
    many = [
        _chunk(
            f"usc17-s200-{n}", label=f"17 U.S.C. §200({n})", heading="Many", body=f"paragraph {n} of the work",
            order=100 + n, pages=(50 + n, 50 + n), above=(("usc17-s200", "17 U.S.C. §200"),),
        )
        for n in range(300)
    ]
    upsert_book_chunks(many, [_embed(c.text) for c in many])

    node = fetch_nodes([_id("usc17-s200")])[_id("usc17-s200")]

    assert (node.page_start, node.page_end) == (50, 349)
    assert [c.chunk_key for c in lookup_book_chunks("§200").shown] == [c.chunk_key for c in many]


# --- scope ----------------------------------------------------------------------------------------

def test_search_never_reaches_client_collections(books, monkeypatch):
    client = get_book_client()
    client.create_collection("legal_docs", vectors_config=models.VectorParams(size=DIM, distance=models.Distance.COSINE))
    try:
        client.upsert(
            "legal_docs",
            [models.PointStruct(id=1, vector=_embed("work client secret"), payload={"client_id": "acme", "text": "secret"})],
            wait=True,
        )
        named: list[str] = []
        for method in ("query_points", "scroll", "retrieve", "count"):
            real = getattr(client, method)

            def spy(*args, _real=real, **kw):
                named.append(kw.get("collection_name", args[0] if args else None))
                return _real(*args, **kw)

            monkeypatch.setattr(client, method, spy)

        hits = search_book_chunks("work client secret")
        lookup_book_chunks("§107")
        fetch_nodes([_id("usc17-s107")])

        assert hits
        assert all(isinstance(h.chunk, BookChunk) for h in hits)
        assert named
        assert set(named) == {books}
    finally:
        client.delete_collection("legal_docs")
