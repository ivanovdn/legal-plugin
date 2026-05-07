#!/usr/bin/env python3
"""Create Qdrant collections for the legal plugin. Idempotent — skips existing."""

import sys
from pathlib import Path

# Allow running as script from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from config import get_settings

COLLECTIONS = [
    {
        "name": "legal_docs",
        "description": "Contracts, legislation, templates, policies",
    },
    {
        "name": "case_history",
        "description": "Past signed contracts — clause-level chunks",
    },
    {
        "name": "memory",
        "description": "Attorney preferences and past decisions",
    },
]


def create_collections() -> None:
    settings = get_settings()

    if settings.qdrant_vector_dim == 0:
        print("ERROR: QDRANT_VECTOR_DIM is not set in .env")
        sys.exit(1)

    client = QdrantClient(url=settings.qdrant_url)
    existing = {c.name for c in client.get_collections().collections}

    for col in COLLECTIONS:
        name = col["name"]
        if name in existing:
            print(f"  SKIP  {name} (already exists)")
            continue

        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(
                size=settings.qdrant_vector_dim,
                distance=Distance.COSINE,
            ),
        )
        print(f"  CREATED  {name}")

    print("\nDone. Collections:")
    for c in client.get_collections().collections:
        print(f"  - {c.name}")


if __name__ == "__main__":
    create_collections()
