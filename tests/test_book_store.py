# tests/test_book_store.py
"""The legal_books collection, against a real Qdrant (the compose-pinned image, via testcontainers).

A mock would pass whatever Qdrant really does with the sparse vector, which is the thing under test:
the server computes BM25 itself, from `models.Document`, through a client built with cloud_inference.
"""
import json

import httpx
import pytest
from qdrant_client import models

from config import get_settings
from ingest.books.models import BookChunk
from rag.book_store import (
    BOOK_PAYLOAD_INDEXES,
    count_book_points,
    delete_book_edition,
    ensure_book_collection,
    get_book_client,
    reset_book_client,
    upsert_book_chunks,
)

DIM = 768


def _chunk(n: int, *, book="circ92", edition="2025-12", heading="Heading", body=None) -> BookChunk:
    ref_id = f"{book}-{edition}:usc17-s{100 + n}"
    body = body or f"body of chunk {n}"
    return BookChunk(
        chunk_key=ref_id,
        ref_id=ref_id,
        book=book,
        edition=edition,
        path=f"usc17-s{100 + n}",
        kind="rule",
        source_type="usc_title",
        jurisdiction="US",
        label=f"17 U.S.C. §{100 + n}",
        heading=heading,
        page_start=n,
        page_end=n,
        text=f"17 U.S.C. §{100 + n} — {heading}\n{body}",
        body=body,
        tokens=10,
        order=n,
        ancestor_ref_ids=[ref_id],
        ancestor_labels=[f"17 U.S.C. §{100 + n}"],
        ancestor_pages=[(n, n)],
        sub_ref_ids=[f"{ref_id}-a"],
    )


def _vector(n: int) -> list[float]:
    """A dense vector with its 1.0 at position n: distinct per chunk, the right length."""
    vec = [0.0] * DIM
    vec[n] = 1.0
    return vec


def _upsert(chunks: list[BookChunk], **kw) -> None:
    upsert_book_chunks(chunks, [_vector(i) for i in range(len(chunks))], **kw)


def test_collection_shape(book_qdrant):
    assert ensure_book_collection() is True
    assert ensure_book_collection() is False

    info = get_book_client().get_collection(book_qdrant)
    dense = info.config.params.vectors["dense"]
    assert dense.size == DIM
    assert dense.distance == models.Distance.COSINE
    assert info.config.params.sparse_vectors["bm25"].modifier == models.Modifier.IDF
    assert BOOK_PAYLOAD_INDEXES == (
        "ref_id", "book", "edition", "kind", "source_type", "jurisdiction", "ancestor_ref_ids", "sub_ref_ids",
    )
    assert set(info.payload_schema) == set(BOOK_PAYLOAD_INDEXES)
    for field in BOOK_PAYLOAD_INDEXES:
        assert info.payload_schema[field].data_type == models.PayloadSchemaType.KEYWORD, field


def test_the_collection_is_the_one_the_setting_names(book_qdrant):
    assert get_settings().book_collection == book_qdrant
    ensure_book_collection()
    names = {c.name for c in get_book_client().get_collections().collections}
    assert book_qdrant in names


def test_reset_book_client_builds_a_new_client(book_qdrant):
    first = get_book_client()
    assert get_book_client() is first
    reset_book_client()
    assert get_book_client() is not first


def test_upsert_count_and_reupsert(book_qdrant):
    ensure_book_collection()
    chunks = [_chunk(1), _chunk(2), _chunk(3)]

    _upsert(chunks)
    assert count_book_points("circ92", "2025-12") == 3

    _upsert(chunks)
    assert count_book_points("circ92", "2025-12") == 3


def test_upsert_batches(book_qdrant):
    ensure_book_collection()
    _upsert([_chunk(n) for n in range(1, 6)], batch_size=2)
    assert count_book_points("circ92", "2025-12") == 5


def test_upsert_stores_the_payload_under_the_point_id(book_qdrant):
    ensure_book_collection()
    chunk = _chunk(1)
    _upsert([chunk])

    (point,) = get_book_client().retrieve(book_qdrant, [chunk.point_id], with_payload=True)
    # Qdrant holds the JSON form (a page span comes back as a list), and it reads back as the same chunk.
    assert point.payload == chunk.model_dump(mode="json")
    assert point.payload["ancestor_pages"] == [[1, 1]]
    assert BookChunk.model_validate(point.payload) == chunk
    assert "client_id" not in point.payload


def test_upsert_refuses_vectors_that_do_not_pair_with_chunks(book_qdrant):
    ensure_book_collection()
    with pytest.raises(ValueError, match="2 chunks.*1 vectors"):
        upsert_book_chunks([_chunk(1), _chunk(2)], [_vector(0)])
    assert count_book_points("circ92", "2025-12") == 0


def test_upsert_of_nothing_is_a_no_op(book_qdrant):
    ensure_book_collection()
    upsert_book_chunks([], [])
    assert count_book_points("circ92", "2025-12") == 0


def test_delete_edition(book_qdrant):
    ensure_book_collection()
    _upsert([_chunk(1, edition="2025-12"), _chunk(2, edition="2025-12"), _chunk(3, edition="2024-06")])
    assert count_book_points("circ92", "2025-12") == 2
    assert count_book_points("circ92", "2024-06") == 1

    delete_book_edition("circ92", "2025-12")

    assert count_book_points("circ92", "2025-12") == 0
    assert count_book_points("circ92", "2024-06") == 1


def test_delete_edition_leaves_another_book_alone(book_qdrant):
    ensure_book_collection()
    _upsert([_chunk(1, book="circ92"), _chunk(2, book="othr")])

    delete_book_edition("circ92", "2025-12")

    assert count_book_points("circ92", "2025-12") == 0
    assert count_book_points("othr", "2025-12") == 1


def test_server_side_bm25(book_qdrant):
    ensure_book_collection()
    _upsert(
        [
            _chunk(1, body="a phonorecord is a material object in which sounds are fixed"),
            _chunk(2, body="the owner of copyright has the exclusive right to reproduce the work"),
            _chunk(3, body="fair use is not an infringement of copyright"),
        ]
    )

    hits = get_book_client().query_points(
        book_qdrant,
        using="bm25",
        query=models.Document(text="phonorecord", model="qdrant/bm25"),
        limit=3,
        with_payload=True,
    ).points

    assert [h.payload["ref_id"] for h in hits] == ["circ92-2025-12:usc17-s101"]


def test_bm25_indexes_the_body_and_not_the_header(book_qdrant):
    """`text` (header + body) is what the dense vector embeds; BM25 is built from `body` alone."""
    ensure_book_collection()
    _upsert([_chunk(1, heading="Zymurgy", body="the owner of copyright has exclusive rights")])

    def bm25_hits(word: str) -> int:
        return len(
            get_book_client().query_points(
                book_qdrant, using="bm25", query=models.Document(text=word, model="qdrant/bm25")
            ).points
        )

    assert bm25_hits("copyright") == 1
    assert bm25_hits("zymurgy") == 0


def test_each_chunk_has_its_own_dense_vector(book_qdrant):
    ensure_book_collection()
    chunks = [_chunk(n) for n in range(1, 4)]
    _upsert(chunks, batch_size=2)  # vectors must pair up across a batch boundary too

    for i, chunk in enumerate(chunks):
        top = get_book_client().query_points(book_qdrant, using="dense", query=_vector(i), limit=1).points[0]
        assert top.id == chunk.point_id


def test_qdrant_computes_the_bm25_vector(book_qdrant, monkeypatch):
    """The point goes out carrying the body as text for Qdrant to index. The client can also compute
    BM25 itself (qdrant-client 1.18 has a built-in one, byte-identical here), so a result check cannot
    tell the two apart: this watches what is sent. Without cloud_inference the sparse vector would go
    out as ready-made indices and values."""
    ensure_book_collection()
    sent = []
    real_send = httpx.Client.send

    def spy(self, request, **kw):
        if request.method == "PUT" and request.url.path.endswith("/points"):
            sent.append(json.loads(request.content))
        return real_send(self, request, **kw)

    monkeypatch.setattr(httpx.Client, "send", spy)
    _upsert([_chunk(1, body="a phonorecord is a material object")])

    (request,) = sent
    (point,) = request["points"]
    assert point["vector"]["bm25"] == {"text": "a phonorecord is a material object", "model": "qdrant/bm25"}
