# rag/vector_store.py
"""Qdrant vector store — all functions require a collection parameter."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from config import get_settings

if TYPE_CHECKING:
    from ingest.chunk_models import LegalChunk

logger = logging.getLogger(__name__)

_client: QdrantClient | None = None


def get_qdrant_client() -> QdrantClient:
    """Singleton Qdrant client."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = QdrantClient(url=settings.qdrant_url)
    return _client


def init_collection(collection: str) -> None:
    """Create collection with payload indexes. Idempotent."""
    settings = get_settings()
    client = get_qdrant_client()

    existing = {c.name for c in client.get_collections().collections}
    if collection in existing:
        logger.info("Collection %s already exists, skipping", collection)
        return

    client.create_collection(
        collection_name=collection,
        vectors_config=VectorParams(
            size=settings.qdrant_vector_dim,
            distance=Distance.COSINE,
        ),
    )

    for field in [
        "doc_id", "doc_type", "client_id", "jurisdiction",
        "sensitivity", "clause_type", "section", "section_number",
        "clause_number", "section_display",
    ]:
        client.create_payload_index(
            collection_name=collection,
            field_name=field,
            field_schema="keyword",
        )

    logger.info("Created collection %s with payload indexes", collection)


def upsert_chunks(
    chunks: list[LegalChunk],
    embeddings: list[list[float]],
    collection: str,
) -> None:
    """Batch upsert chunks with embeddings. 100 at a time."""
    client = get_qdrant_client()
    batch_size = 100

    for i in range(0, len(chunks), batch_size):
        batch_chunks = chunks[i : i + batch_size]
        batch_embeddings = embeddings[i : i + batch_size]

        points = [
            PointStruct(
                id=chunk.chunk_id,
                vector=emb,
                payload=chunk.model_dump(),
            )
            for chunk, emb in zip(batch_chunks, batch_embeddings)
        ]

        client.upsert(collection_name=collection, points=points)
        logger.info(
            "Upserted batch %d-%d to %s",
            i, i + len(batch_chunks), collection,
        )


def delete_document(doc_id: str, collection: str) -> None:
    """Delete all chunks for a document."""
    client = get_qdrant_client()
    client.delete(
        collection_name=collection,
        points_selector=Filter(
            must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
        ),
    )
    logger.info("Deleted doc %s from %s", doc_id, collection)


def search_vectors(
    query_vector: list[float],
    top_k: int,
    collection: str,
    filters: dict | None = None,
) -> list[dict]:
    """Search by vector similarity with optional payload filters."""
    client = get_qdrant_client()

    qdrant_filter = None
    if filters:
        must = [
            FieldCondition(key=k, match=MatchValue(value=v))
            for k, v in filters.items()
            if v is not None
        ]
        if must:
            qdrant_filter = Filter(must=must)

    results = client.query_points(
        collection_name=collection,
        query=query_vector,
        limit=top_k,
        query_filter=qdrant_filter,
        with_payload=True,
    )

    return [
        {"score": point.score, **point.payload}
        for point in results.points
    ]


def scroll_by_filter(
    filter_conditions: dict,
    collection: str,
    limit: int = 100,
) -> list[dict]:
    """Scroll through points matching filter conditions."""
    client = get_qdrant_client()

    must = [
        FieldCondition(key=k, match=MatchValue(value=v))
        for k, v in filter_conditions.items()
        if v is not None
    ]

    results, _ = client.scroll(
        collection_name=collection,
        scroll_filter=Filter(must=must),
        limit=limit,
        with_payload=True,
    )

    return [point.payload for point in results]
