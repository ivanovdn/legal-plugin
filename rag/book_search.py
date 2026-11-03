# rag/book_search.py
"""Reading the reference books' collection: hybrid search, lookup by citation, lookup of nodes.

Every function here names only `settings.book_collection`, through the book client: no `client_id`,
no collection argument, so nothing here can reach a client's documents (spec D7; decision
2026-10-30-reference-book-chunk-model).

The three reads:
- `search_book_chunks`: dense and BM25 prefetches under one `kind` filter, fused by Qdrant (RRF), then
  reranked, then cut to the top K and the score floor.
- `lookup_book_chunks`: "§ 107" / a ref_id -> the chunks under that node, in document order, up to a
  token cap.
- `fetch_nodes`: ref_ids -> what a citation of each needs to be checked and rendered (label, pages, the
  chunks it can be found through), resolved by `ingest.books.resolve.resolve_nodes`.
"""
from collections.abc import Collection
from dataclasses import dataclass

from qdrant_client import models

from config import get_settings
from ingest.books.models import BookChunk
from ingest.books.ref_ids import parse_reference
from ingest.books.registry import BOOKS
from ingest.books.resolve import NodeInfo, resolve_nodes
from rag.book_store import BM25_MODEL, DENSE_VECTOR, SPARSE_VECTOR, get_book_client
from rag.embeddings import embed_query
from rag.reranker import rerank

# Points per page of a scroll. A node can have more chunks than this (§115 has about thirty).
_SCROLL_PAGE = 256


@dataclass
class BookHit:
    chunk: BookChunk
    # The reranker's score when it scored this hit, else Qdrant's fused (RRF) score. The two are not on
    # one scale: `book_min_score` is compared against whichever this is.
    score: float


@dataclass
class LookupResult:
    target: str  # the node looked up, as a full ref_id
    shown: list[BookChunk]  # its chunks in document order, up to the token cap (at least one when any exist)
    more: list[str]  # ref_ids of the chunks that did not fit, each once, in order


def kind_filter(include_history_notes: bool) -> models.Filter:
    """The default search filter (D6): rules and definitions, plus history notes when asked.

    `repealed` stubs are never searched, only looked up. Treaties are `rule`s by kind, so they stay in."""
    kinds = ["rule", "definition"]
    if include_history_notes:
        kinds.append("history_note")
    return models.Filter(must=[models.FieldCondition(key="kind", match=models.MatchAny(any=kinds))])


def _score(result: dict) -> float:
    return result["rerank_score"] if "rerank_score" in result else result["score"]


def search_book_chunks(query: str, *, include_history_notes: bool = False) -> list[BookHit]:
    """Dense and BM25 candidates under the kind filter, RRF-fused by Qdrant, reranked, cut to the top K.

    The filter is Qdrant's, inside each prefetch, so the candidate cut counts only searchable chunks.
    Filtering the fused list afterwards would cut first, and a query that a history note matches best
    would be left with nothing."""
    settings = get_settings()
    flt = kind_filter(include_history_notes)

    fused = get_book_client().query_points(
        collection_name=settings.book_collection,
        prefetch=[
            models.Prefetch(
                query=embed_query(query), using=DENSE_VECTOR, filter=flt, limit=settings.book_dense_candidates
            ),
            models.Prefetch(
                query=models.Document(text=query, model=BM25_MODEL),
                using=SPARSE_VECTOR,
                filter=flt,
                limit=settings.book_sparse_candidates,
            ),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        limit=settings.book_dense_candidates + settings.book_sparse_candidates,
        with_payload=True,
    ).points

    # The reranker reads `text`, the header and the body (what was embedded), not the body alone: a body
    # such as "(1) the purpose and character of the use" has lost the section it belongs to.
    candidates = []
    for point in fused:
        chunk = BookChunk.model_validate(point.payload)
        candidates.append({"text": chunk.text, "chunk": chunk, "score": point.score})
    # `rerank` hands everything back, untrimmed, when it is off: cut after it.
    ranked = rerank(query, candidates, top_n=settings.book_search_top_k)[: settings.book_search_top_k]

    hits = [BookHit(chunk=r["chunk"], score=_score(r)) for r in ranked]
    if settings.book_min_score > 0:
        hits = [h for h in hits if h.score >= settings.book_min_score]
    return hits


def _scroll_all(flt: models.Filter) -> list[BookChunk]:
    """Every chunk matching the filter, in document order."""
    client = get_book_client()
    name = get_settings().book_collection
    chunks: list[BookChunk] = []
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=name,
            scroll_filter=flt,
            limit=_SCROLL_PAGE,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        chunks.extend(BookChunk.model_validate(p.payload) for p in points)
        if offset is None:
            break
    return sorted(chunks, key=lambda c: (c.order, c.chunk_key))


def _any_of(key: str, values: Collection[str]) -> models.FieldCondition:
    return models.FieldCondition(key=key, match=models.MatchAny(any=sorted(values)))


def _lookup_cost(chunk: BookChunk, cap: int) -> int:
    """`tokens` is -1 for a chunk the counter put past the embedding window: that is not a small
    chunk, so it costs the whole cap (shown only when nothing has been shown before it)."""
    return chunk.tokens if chunk.tokens >= 0 else cap


def lookup_book_chunks(reference: str) -> LookupResult | None:
    """The chunks under the node `reference` names, in document order, up to `book_lookup_max_tokens`.

    None when the reference does not parse; an empty `shown` when it parses and nothing matches. A path
    ("usc17-s107") is tried under every registered book's prefix. Whatever the kind: a repealed stub or a
    history note is reached here, by its id."""
    parsed = parse_reference(reference)
    if parsed is None:
        return None
    candidates = [parsed] if ":" in parsed else [f"{book.ref_prefix}:{parsed}" for book in BOOKS.values()]

    # A node is found by its own chunks and its descendants' (both list it in `ancestor_ref_ids`), or, when
    # it sits inside a chunk, by the chunk's `sub_ref_ids`.
    chunks = _scroll_all(
        models.Filter(should=[_any_of("ancestor_ref_ids", candidates), _any_of("sub_ref_ids", candidates)])
    )

    def under(chunk: BookChunk, ref_id: str) -> bool:
        return ref_id in chunk.ancestor_ref_ids or ref_id in chunk.sub_ref_ids

    # One node: the first candidate that matched anything (the one candidate, with the one book registered).
    target = next((c for c in candidates if any(under(chunk, c) for chunk in chunks)), candidates[0])
    chunks = [chunk for chunk in chunks if under(chunk, target)]

    cap = get_settings().book_lookup_max_tokens
    shown: list[BookChunk] = []
    used = 0
    for chunk in chunks:
        cost = _lookup_cost(chunk, cap)
        if shown and used + cost > cap:
            break
        shown.append(chunk)
        used += cost
    return LookupResult(
        target=target,
        shown=shown,
        more=list(dict.fromkeys(chunk.ref_id for chunk in chunks[len(shown) :])),
    )


def fetch_nodes(ref_ids: Collection[str]) -> dict[str, NodeInfo]:
    """What a citation of each id needs, for the ids the collection knows; an unknown id is absent.

    Reads every chunk that holds one of the ids (its own, one it sits inside, or one below it) and hands
    them to `resolve_nodes`, which says how each id is labelled and paged."""
    wanted = list(dict.fromkeys(ref_ids))
    if not wanted:
        return {}
    chunks = _scroll_all(
        models.Filter(
            should=[_any_of("ref_id", wanted), _any_of("sub_ref_ids", wanted), _any_of("ancestor_ref_ids", wanted)]
        )
    )
    return resolve_nodes(wanted, chunks)
