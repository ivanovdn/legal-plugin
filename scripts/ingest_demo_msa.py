#!/usr/bin/env python3
"""One-time demo prep: ingest the model MSA into Qdrant as the governing MSA.

Ingests `data/Trinetix Model MSA 2025 (3)-1.docx` with doc_type="msa" and
client_id="internal" so contract_review auto-attaches it when reviewing a SOW.
Clears any previously-ingested MSA chunks for the client first, because the
parsers assign random-UUID doc_ids (a naive re-ingest would otherwise leave a
second MSA on file).

    uv run python -m scripts.ingest_demo_msa
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.pipeline import ingest_document
from rag.vector_store import delete_document, scroll_by_filter

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

_MSA_PATH = Path(__file__).resolve().parent.parent / "data" / "Trinetix Model MSA 2025 (3)-1.docx"
_CLIENT_ID = "internal"
_COLLECTION = "legal_docs"


def main() -> int:
    if not _MSA_PATH.exists():
        logger.error("MSA file not found: %s", _MSA_PATH)
        return 1

    # Clear prior MSA chunks for this client so re-runs stay clean.
    existing = scroll_by_filter(
        filter_conditions={"doc_type": "msa", "client_id": _CLIENT_ID},
        collection=_COLLECTION,
        limit=1000,
    )
    for doc_id in sorted({c.get("doc_id", "") for c in existing if c.get("doc_id")}):
        delete_document(doc_id, _COLLECTION)
        logger.info("Cleared prior MSA doc_id=%s", doc_id)

    count = ingest_document(
        filepath=_MSA_PATH,
        client_id=_CLIENT_ID,
        jurisdiction="",
        doc_type="msa",
        sensitivity="internal",
        collection=_COLLECTION,
    )
    if count == 0:
        logger.error(
            "Ingested 0 chunks from %s — the MSA was NOT loaded (parse produced "
            "nothing). SOW reviews will find no governing MSA.",
            _MSA_PATH.name,
        )
        return 1
    logger.info("Ingested %d chunks from %s as doc_type=msa", count, _MSA_PATH.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
