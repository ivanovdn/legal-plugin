#!/usr/bin/env python3
"""Batch ingest documents from a directory into Qdrant."""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.pipeline import ingest_document

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx"}


def load_metadata(filepath: Path) -> dict | None:
    """Load metadata from a sidecar JSON file (same name, .json extension)."""
    sidecar = filepath.with_suffix(".json")
    if sidecar.exists():
        with open(sidecar) as f:
            return json.load(f)
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest documents into Qdrant")
    parser.add_argument("directory", type=Path, help="Directory containing documents")
    parser.add_argument("--collection", default="legal_docs", help="Qdrant collection name")
    parser.add_argument("--client-id", default="internal", help="Default client ID")
    parser.add_argument("--jurisdiction", default="", help="Default jurisdiction")
    parser.add_argument("--doc-type", default="policy", help="Default doc type")
    parser.add_argument("--sensitivity", default="internal", help="Default sensitivity")
    args = parser.parse_args()

    if not args.directory.is_dir():
        logger.error("Not a directory: %s", args.directory)
        sys.exit(1)

    files = [
        f for f in sorted(args.directory.rglob("*"))
        if f.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    if not files:
        logger.warning("No supported files found in %s", args.directory)
        return

    logger.info("Found %d files to ingest", len(files))
    total_chunks = 0

    for filepath in files:
        logger.info("Ingesting %s ...", filepath.name)

        meta = load_metadata(filepath) or {}
        client_id = meta.get("client_id", args.client_id)
        jurisdiction = meta.get("jurisdiction", args.jurisdiction)
        doc_type = meta.get("doc_type", args.doc_type)
        sensitivity = meta.get("sensitivity", args.sensitivity)

        try:
            count = ingest_document(
                filepath=filepath,
                client_id=client_id,
                jurisdiction=jurisdiction,
                doc_type=doc_type,
                sensitivity=sensitivity,
                collection=args.collection,
            )
            total_chunks += count
            logger.info("  -> %d chunks", count)
        except Exception:
            logger.exception("  -> FAILED: %s", filepath.name)

    logger.info("Done. Total: %d chunks from %d files", total_chunks, len(files))


if __name__ == "__main__":
    main()
