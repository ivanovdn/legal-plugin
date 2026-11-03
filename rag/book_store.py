# rag/book_store.py
"""The reference books' Qdrant collection: dense vector plus a BM25 vector Qdrant computes itself.

Own client, own collection, no `client_id`: the collection holds no client data (decision
2026-10-30-reference-book-chunk-model). The name is the `book_collection` setting, read at each call,
so a caller never passes one.

The BM25 vector is built on the server: the point carries `models.Document(text=..., model="qdrant/bm25")`
and Qdrant tokenises and weights it. The client is created with `cloud_inference=True`. The flag
means "send the text to the server that is there to embed it", which is our own Qdrant, not a cloud;
without it the locked client (qdrant-client 1.18.0) computes the same vector itself with its built-in
BM25 (byte-identical, no fastembed), so the flag only makes Qdrant do the work. Either way nothing is
downloaded (spec constraint 7).
`rag/vector_store.py`'s client is built without it and is left alone.
"""
import logging

from qdrant_client import QdrantClient, models

from config import get_settings
from ingest.books.models import BookChunk

logger = logging.getLogger(__name__)

DENSE_VECTOR = "dense"
SPARSE_VECTOR = "bm25"
BM25_MODEL = "qdrant/bm25"

# `ancestor_ref_ids` and `sub_ref_ids` are arrays: a keyword index matches any element, which is how
# "everything under this node" is asked.
BOOK_PAYLOAD_INDEXES = (
    "ref_id", "book", "edition", "kind", "source_type", "jurisdiction", "ancestor_ref_ids", "sub_ref_ids",
)

_client: QdrantClient | None = None


def get_book_client() -> QdrantClient:
    """Singleton client for the book collection, able to have Qdrant compute BM25."""
    global _client
    if _client is None:
        _client = QdrantClient(url=get_settings().qdrant_url, cloud_inference=True)
    return _client


def reset_book_client() -> None:
    """Forget the client, so the next call builds one from the settings as they now are."""
    global _client
    if _client is not None:
        _client.close()
    _client = None


def ensure_book_collection() -> bool:
    """Create the collection and its payload indexes if absent. True when this call created it."""
    settings = get_settings()
    client = get_book_client()
    name = settings.book_collection

    if client.collection_exists(name):
        return False

    client.create_collection(
        collection_name=name,
        vectors_config={
            DENSE_VECTOR: models.VectorParams(size=settings.qdrant_vector_dim, distance=models.Distance.COSINE),
        },
        sparse_vectors_config={SPARSE_VECTOR: models.SparseVectorParams(modifier=models.Modifier.IDF)},
    )
    for field in BOOK_PAYLOAD_INDEXES:
        client.create_payload_index(
            collection_name=name,
            field_name=field,
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
    logger.info("Created collection %s with payload indexes", name)
    return True


def _edition_filter(book_id: str, edition_id: str) -> models.Filter:
    """Both conditions: an edition id alone would also name the same edition of another book."""
    return models.Filter(
        must=[
            models.FieldCondition(key="book", match=models.MatchValue(value=book_id)),
            models.FieldCondition(key="edition", match=models.MatchValue(value=edition_id)),
        ]
    )


def delete_book_edition(book_id: str, edition_id: str) -> None:
    """Remove every point of one edition of one book."""
    get_book_client().delete(
        collection_name=get_settings().book_collection,
        points_selector=models.FilterSelector(filter=_edition_filter(book_id, edition_id)),
        wait=True,
    )


def upsert_book_chunks(chunks: list[BookChunk], vectors: list[list[float]], batch_size: int = 64) -> None:
    """Write the chunks, `vectors[i]` being the dense vector of `chunks[i]`.

    A point's id is the chunk key's uuid5, so writing the same chunk again replaces its point."""
    if len(chunks) != len(vectors):
        raise ValueError(f"{len(chunks)} chunks but {len(vectors)} vectors: each chunk needs exactly one")

    client = get_book_client()
    name = get_settings().book_collection
    for start in range(0, len(chunks), batch_size):
        points = [
            models.PointStruct(
                id=chunk.point_id,
                vector={
                    DENSE_VECTOR: vector,
                    SPARSE_VECTOR: models.Document(text=chunk.body, model=BM25_MODEL),
                },
                payload=chunk.model_dump(),
            )
            for chunk, vector in zip(
                chunks[start : start + batch_size], vectors[start : start + batch_size], strict=True
            )
        ]
        client.upsert(collection_name=name, points=points, wait=True)


def count_book_points(book_id: str, edition_id: str) -> int:
    """How many points one edition of one book has in the collection."""
    return get_book_client().count(
        collection_name=get_settings().book_collection,
        count_filter=_edition_filter(book_id, edition_id),
        exact=True,
    ).count
