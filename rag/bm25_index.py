# rag/bm25_index.py
"""
Pure-Python BM25 keyword index for hybrid search.

No external dependencies — just math and re.
The index is stored in memory and persisted to a JSON file so it
survives restarts without re-ingestion.

BM25 parameters (Okapi BM25):
  k1 = 1.2  (term frequency saturation)
  b  = 0.75 (document length normalization)
"""

import json
import logging
import math
import re
from collections import Counter
from pathlib import Path

from config import get_settings

logger = logging.getLogger(__name__)

_INDEX_PATH = Path(".bm25_index.json")

# BM25 parameters
_K1 = 1.2
_B = 0.75

# Stop words to skip during tokenization
_STOP_WORDS = frozenset(
    "a an and are as at be by for from has have in is it of on or the "
    "to was were with that this these those not but they them their its "
    "will can may shall should would could also been being into such than "
    "each other which who whom what where when how all any both few more "
    "most no nor some do does did doing done".split()
)


def _tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumeric, remove stop words."""
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return [t for t in tokens if t not in _STOP_WORDS and len(t) > 1]


class BM25Index:
    """In-memory BM25 index with JSON persistence."""

    def __init__(self):
        self.documents: dict[str, dict] = {}
        self.inverted_index: dict[str, set[str]] = {}
        self.avg_dl: float = 0.0

    def _recompute_avg_dl(self):
        if not self.documents:
            self.avg_dl = 0.0
            return
        total = sum(len(d["tokens"]) for d in self.documents.values())
        self.avg_dl = total / len(self.documents)

    def add(self, chunk_id: str, metadata: dict) -> None:
        """Add a single chunk to the index."""
        tokens = _tokenize(metadata.get("text", ""))
        self.documents[chunk_id] = {
            "doc_id": metadata.get("doc_id", ""),
            "doc_title": metadata.get("doc_title", ""),
            "doc_type": metadata.get("doc_type", ""),
            "client_id": metadata.get("client_id", ""),
            "jurisdiction": metadata.get("jurisdiction", ""),
            "section": metadata.get("section", ""),
            "section_number": metadata.get("section_number", ""),
            "clause": metadata.get("clause", ""),
            "clause_number": metadata.get("clause_number", ""),
            "section_display": metadata.get("section_display", ""),
            "text": metadata.get("text", ""),
            "tokens": tokens,
        }
        for token in set(tokens):
            if token not in self.inverted_index:
                self.inverted_index[token] = set()
            self.inverted_index[token].add(chunk_id)

        self._recompute_avg_dl()

    def remove_by_doc_id(self, doc_id: str) -> int:
        """Remove all chunks for a given doc_id. Returns number removed."""
        to_remove = [
            cid for cid, d in self.documents.items() if d["doc_id"] == doc_id
        ]
        for cid in to_remove:
            tokens = set(self.documents[cid]["tokens"])
            for token in tokens:
                if token in self.inverted_index:
                    self.inverted_index[token].discard(cid)
                    if not self.inverted_index[token]:
                        del self.inverted_index[token]
            del self.documents[cid]

        if to_remove:
            self._recompute_avg_dl()
        return len(to_remove)

    def search(self, query: str, top_k: int = 20) -> list[dict]:
        """Search the index. Returns list of dicts with chunk metadata + bm25_score."""
        if not self.documents:
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        n = len(self.documents)
        scores: dict[str, float] = {}

        for token in query_tokens:
            if token not in self.inverted_index:
                continue

            doc_ids_with_term = self.inverted_index[token]
            df = len(doc_ids_with_term)
            idf = math.log((n - df + 0.5) / (df + 0.5) + 1.0)

            for cid in doc_ids_with_term:
                doc = self.documents[cid]
                tf = Counter(doc["tokens"])[token]
                dl = len(doc["tokens"])
                numerator = tf * (_K1 + 1)
                denominator = tf + _K1 * (1 - _B + _B * dl / self.avg_dl)
                score = idf * numerator / denominator
                scores[cid] = scores.get(cid, 0.0) + score

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        results = []
        for cid, score in ranked[:top_k]:
            doc = self.documents[cid]
            results.append({
                "chunk_id": cid,
                "bm25_score": score,
                **{k: v for k, v in doc.items() if k != "tokens"},
            })
        return results

    def save(self, path: str | Path | None = None) -> None:
        """Persist index to JSON."""
        path = Path(path) if path else _INDEX_PATH
        data = {
            "documents": {
                cid: {k: v for k, v in doc.items() if k != "tokens"}
                for cid, doc in self.documents.items()
            }
        }
        path.write_text(json.dumps(data), encoding="utf-8")
        logger.info("BM25 index saved: %d chunks -> %s", len(self.documents), path)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "BM25Index":
        """Load index from JSON. Returns new BM25Index."""
        path = Path(path) if path else _INDEX_PATH
        index = cls()

        if not path.exists():
            return index

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for cid, doc in data["documents"].items():
                tokens = _tokenize(doc["text"])
                doc["tokens"] = tokens
                index.documents[cid] = doc
                for token in set(tokens):
                    if token not in index.inverted_index:
                        index.inverted_index[token] = set()
                    index.inverted_index[token].add(cid)

            index._recompute_avg_dl()
            logger.info("BM25 index loaded: %d chunks from %s", len(index.documents), path)
        except Exception as e:
            logger.warning("Failed to load BM25 index: %s", e)

        return index


# --- Module-level singleton ---

_index: BM25Index | None = None


def get_bm25_index() -> BM25Index:
    """Get or create the singleton BM25 index, loading from disk if available."""
    global _index
    if _index is None:
        _index = BM25Index.load()
    return _index


def search_bm25(query: str, top_k: int | None = None) -> list[dict]:
    """Search BM25 index. Returns list of dicts with chunk metadata + bm25_score."""
    settings = get_settings()
    idx = get_bm25_index()
    k = top_k or settings.hybrid_bm25_candidates
    return idx.search(query, top_k=k)
