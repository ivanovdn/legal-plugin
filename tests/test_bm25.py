# tests/test_bm25.py
from rag.bm25_index import BM25Index


def test_bm25_add_and_search():
    """BM25 index can add chunks and return relevant results."""
    index = BM25Index()
    index.add("c1", {
        "chunk_id": "c1", "doc_id": "d1", "doc_title": "Contract A",
        "doc_type": "contract", "client_id": "client-x",
        "text": "The indemnification clause protects the buyer.",
    })
    index.add("c2", {
        "chunk_id": "c2", "doc_id": "d1", "doc_title": "Contract A",
        "doc_type": "contract", "client_id": "client-x",
        "text": "Payment terms are net 30 days from invoice date.",
    })

    results = index.search("indemnification clause", top_k=2)
    assert len(results) > 0
    assert results[0]["chunk_id"] == "c1"


def test_bm25_remove():
    """BM25 index can remove documents by doc_id."""
    index = BM25Index()
    index.add("c1", {
        "chunk_id": "c1", "doc_id": "d1", "doc_title": "t",
        "text": "important clause about liability",
    })
    index.add("c2", {
        "chunk_id": "c2", "doc_id": "d2", "doc_title": "t2",
        "text": "another clause about liability",
    })

    index.remove_by_doc_id("d1")

    results = index.search("liability", top_k=5)
    assert all(r["doc_id"] != "d1" for r in results)


def test_bm25_save_and_load(tmp_path):
    """BM25 index persists to JSON and reloads correctly."""
    index = BM25Index()
    index.add("c1", {
        "chunk_id": "c1", "doc_id": "d1", "doc_title": "t",
        "text": "arbitration clause for dispute resolution",
    })

    save_path = tmp_path / "bm25.json"
    index.save(str(save_path))

    loaded = BM25Index.load(str(save_path))
    results = loaded.search("arbitration", top_k=1)
    assert len(results) == 1
    assert results[0]["chunk_id"] == "c1"
