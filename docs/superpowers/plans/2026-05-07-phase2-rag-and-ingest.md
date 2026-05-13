# Phase 2 — RAG Layer & Ingest Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the RAG layer from compliance-bot and build the ingest pipeline so that documents can be parsed, chunked, embedded, and retrieved via hybrid search.

**Architecture:** Pure Python RAG layer (no LlamaIndex). Embeddings via Ollama, vector search via Qdrant, keyword search via BM25 with RRF fusion, reranking via llama-cpp `/v1/rerank`. Ingest pipeline parses PDF/DOCX, chunks by doc_type strategy, embeds, and upserts to Qdrant. Tools are LangGraph `@tool` decorated functions.

**Tech Stack:** qdrant-client, httpx (Ollama + reranker), rank-bm25 (reference only — custom pure-Python BM25), pdfplumber, python-docx, pydantic, pytest

**Source project:** `/Users/dmytroivanov/projects/compliance-bot/` — files are ported (not rewritten) with targeted modifications per the porting table in the design spec.

---

## File Structure

```
legal-plugin/
|-- config.py                          # MODIFY — add RAG/ingest settings
|-- ingest/
|   |-- __init__.py
|   |-- chunk_models.py                # CREATE — LegalChunk model (replaces PolicyChunk)
|   |-- pipeline.py                    # CREATE — orchestrates parse → chunk → embed → upsert
|   |-- numbering.py                   # PORT as-is from compliance-bot (docx_parser dependency)
|   +-- parsers/
|       |-- __init__.py
|       |-- pdf_parser.py              # CREATE — new, pdfplumber-based
|       +-- docx_parser.py             # PORT — add client_id, jurisdiction, doc_type, sensitivity
|-- rag/
|   |-- __init__.py
|   |-- embeddings.py                  # PORT — Ollama-only, model from env var
|   |-- vector_store.py                # PORT — add collection param, new payload indexes
|   |-- bm25_index.py                  # PORT — adapt field names to LegalChunk
|   |-- hybrid_search.py               # PORT — adapt field names to LegalChunk
|   |-- reranker.py                    # PORT as-is, update config imports
|   +-- tools/
|       |-- __init__.py
|       |-- search_legal.py            # CREATE — hybrid search + rerank as LangGraph tool
|       |-- get_document.py            # PORT from get_section.py — LangGraph tool, LegalChunk fields
|       |-- extract_clauses.py         # CREATE — queries case_history by clause_type
|       +-- escalate.py               # PORT — LangGraph tool, legal ticket prefix
|-- scripts/
|   +-- ingest_all.py                  # CREATE — batch ingest from directory
+-- tests/
    |-- test_chunk_models.py           # CREATE
    |-- test_embeddings.py             # CREATE
    |-- test_vector_store.py           # CREATE
    |-- test_bm25.py                   # CREATE
    |-- test_hybrid_search.py          # CREATE
    |-- test_reranker.py               # CREATE
    |-- test_parsers.py                # CREATE
    +-- test_pipeline.py               # CREATE
```

---

### Task 1: Add missing config fields

Config needs additional RAG/ingest settings consumed by the ported files. The compliance-bot config has many settings we need equivalents for.

**Files:**
- Modify: `config.py`
- Modify: `tests/test_config.py`
- Modify: `.env.example`

- [ ] **Step 1: Add new fields to config.py**

Add these fields to the `Settings` class in `config.py`, after the existing `reranker_candidates` field:

```python
    # Reranker (add after reranker_candidates)
    reranker_query_template: str = ""
    reranker_instruction: str = ""

    # Retrieval
    retrieval_top_k: int = 10
    min_confidence_score: float = 0.45

    # Hybrid search
    hybrid_vector_candidates: int = 20
    hybrid_bm25_candidates: int = 20

    # Embedding
    embedding_query_prefix: str = ""
    embedding_passage_prefix: str = ""

    # Chunking
    chunk_min_tokens: int = 50
    chunk_max_tokens: int = 400

    # Escalation
    escalation_ticket_prefix: str = "LEG"
```

- [ ] **Step 2: Add new fields to .env.example**

Add after the `RERANKER_CANDIDATES=25` line:

```bash
RERANKER_QUERY_TEMPLATE=
RERANKER_INSTRUCTION=

# Retrieval
RETRIEVAL_TOP_K=10
MIN_CONFIDENCE_SCORE=0.45

# Hybrid search
HYBRID_VECTOR_CANDIDATES=20
HYBRID_BM25_CANDIDATES=20

# Embedding
EMBEDDING_QUERY_PREFIX=
EMBEDDING_PASSAGE_PREFIX=

# Chunking
CHUNK_MIN_TOKENS=50
CHUNK_MAX_TOKENS=400

# Escalation
ESCALATION_TICKET_PREFIX=LEG
```

- [ ] **Step 3: Update test_config.py**

Add the new env vars to `test_config_loads_from_env`:

```python
    monkeypatch.setenv("RERANKER_QUERY_TEMPLATE", "")
    monkeypatch.setenv("RERANKER_INSTRUCTION", "")
    monkeypatch.setenv("RETRIEVAL_TOP_K", "10")
    monkeypatch.setenv("MIN_CONFIDENCE_SCORE", "0.45")
    monkeypatch.setenv("HYBRID_VECTOR_CANDIDATES", "20")
    monkeypatch.setenv("HYBRID_BM25_CANDIDATES", "20")
    monkeypatch.setenv("EMBEDDING_QUERY_PREFIX", "")
    monkeypatch.setenv("EMBEDDING_PASSAGE_PREFIX", "")
    monkeypatch.setenv("CHUNK_MIN_TOKENS", "50")
    monkeypatch.setenv("CHUNK_MAX_TOKENS", "400")
    monkeypatch.setenv("ESCALATION_TICKET_PREFIX", "LEG")
```

And add assertions:

```python
    assert settings.retrieval_top_k == 10
    assert settings.min_confidence_score == 0.45
    assert settings.hybrid_vector_candidates == 20
    assert settings.hybrid_bm25_candidates == 20
    assert settings.chunk_min_tokens == 50
    assert settings.chunk_max_tokens == 400
    assert settings.escalation_ticket_prefix == "LEG"
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_config.py -v
```

Expected: 2 passed

- [ ] **Step 5: Update .env from .env.example and commit**

```bash
cp .env.example .env
# Re-fill EMBEDDING_MODEL, QDRANT_VECTOR_DIM, and any other local values
git add config.py .env.example tests/test_config.py
git commit -m "feat: add RAG and ingest config fields"
```

---

### Task 2: Create LegalChunk model

The core data model for all chunks across all collections. Replaces `PolicyChunk` from compliance-bot.

**Files:**
- Create: `ingest/__init__.py`
- Create: `ingest/chunk_models.py`
- Create: `tests/test_chunk_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chunk_models.py
from ingest.chunk_models import LegalChunk


def test_legal_chunk_required_fields():
    """LegalChunk can be created with all required fields."""
    chunk = LegalChunk(
        chunk_id="chunk-001",
        doc_id="doc-001",
        doc_title="Service Agreement",
        doc_filename="service_agreement.docx",
        doc_type="contract",
        client_id="client-abc",
        jurisdiction="US-DE",
        sensitivity="confidential",
        text="The parties agree to the following terms.",
    )
    assert chunk.chunk_id == "chunk-001"
    assert chunk.doc_type == "contract"
    assert chunk.client_id == "client-abc"
    assert chunk.sensitivity == "confidential"
    assert chunk.char_count == 0  # default
    assert chunk.chunk_strategy == ""  # default


def test_legal_chunk_optional_fields():
    """LegalChunk optional fields have correct defaults."""
    chunk = LegalChunk(
        chunk_id="c1",
        doc_id="d1",
        doc_title="t",
        doc_filename="f.docx",
        doc_type="policy",
        client_id="internal",
        jurisdiction="EU",
        sensitivity="internal",
        text="test",
    )
    assert chunk.section == ""
    assert chunk.section_number == ""
    assert chunk.clause == ""
    assert chunk.clause_number == ""
    assert chunk.clause_type == ""
    assert chunk.section_display == ""
    assert chunk.chunk_index == 0
    assert chunk.chunk_strategy == ""
    assert chunk.last_updated == ""


def test_legal_chunk_model_dump():
    """model_dump() returns all fields as a dict (used by Qdrant upsert)."""
    chunk = LegalChunk(
        chunk_id="c1",
        doc_id="d1",
        doc_title="t",
        doc_filename="f.docx",
        doc_type="contract",
        client_id="client-x",
        jurisdiction="US-NY",
        sensitivity="public",
        text="hello",
        clause_type="indemnification",
        chunk_strategy="clause-level",
    )
    d = chunk.model_dump()
    assert d["doc_type"] == "contract"
    assert d["client_id"] == "client-x"
    assert d["clause_type"] == "indemnification"
    assert d["chunk_strategy"] == "clause-level"
    assert "text" in d
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_chunk_models.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'ingest'`

- [ ] **Step 3: Create ingest package and LegalChunk**

Create `ingest/__init__.py` (empty) and:

```python
# ingest/chunk_models.py
from pydantic import BaseModel


class LegalChunk(BaseModel):
    chunk_id: str
    doc_id: str
    doc_title: str
    doc_filename: str
    doc_type: str       # contract | legislation | template | policy | case_law
    client_id: str      # "internal" for shared docs
    jurisdiction: str
    sensitivity: str    # confidential | internal | public
    section: str = ""
    section_number: str = ""
    clause: str = ""
    clause_number: str = ""
    clause_type: str = ""
    section_display: str = ""
    text: str
    char_count: int = 0
    chunk_index: int = 0
    chunk_strategy: str = ""  # clause-level | section-based | template-aware
    last_updated: str = ""
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_chunk_models.py -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add ingest/__init__.py ingest/chunk_models.py tests/test_chunk_models.py
git commit -m "feat: add LegalChunk model"
```

---

### Task 3: Port embeddings.py

Port from compliance-bot. Simplify to Ollama-only (drop HuggingFace path). Model name from env var.

**Files:**
- Create: `rag/__init__.py`
- Create: `rag/embeddings.py`
- Create: `tests/test_embeddings.py`
- Source: `/Users/dmytroivanov/projects/compliance-bot/rag/embeddings.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_embeddings.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx


def test_embed_texts_calls_ollama(monkeypatch):
    """embed_texts() sends correct payload to Ollama /api/embed."""
    monkeypatch.setenv("EMBEDDING_MODEL", "embeddinggemma:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")

    # Must reimport to pick up env changes
    import importlib
    import rag.embeddings as mod
    importlib.reload(mod)

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "embeddings": [[0.1] * 768, [0.2] * 768]
    }

    with patch("rag.embeddings.httpx.post", return_value=fake_response) as mock_post:
        result = mod.embed_texts(["hello", "world"])

    assert len(result) == 2
    assert len(result[0]) == 768
    call_kwargs = mock_post.call_args
    body = call_kwargs[1]["json"] if "json" in call_kwargs[1] else call_kwargs[0][1]
    assert body["model"] == "embeddinggemma:latest"


def test_embed_query_calls_ollama(monkeypatch):
    """embed_query() returns a single vector."""
    monkeypatch.setenv("EMBEDDING_MODEL", "embeddinggemma:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")

    import importlib
    import rag.embeddings as mod
    importlib.reload(mod)

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "embeddings": [[0.5] * 768]
    }

    with patch("rag.embeddings.httpx.post", return_value=fake_response):
        result = mod.embed_query("test query")

    assert len(result) == 768
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_embeddings.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'rag'`

- [ ] **Step 3: Port embeddings.py from compliance-bot**

Create `rag/__init__.py` (empty) and port from `/Users/dmytroivanov/projects/compliance-bot/rag/embeddings.py`. Key changes:
- Remove HuggingFace path entirely — Ollama only
- Replace `settings.embedding_source` check — always Ollama
- Replace `settings.ollama_embedding_url` with `settings.ollama_base_url`
- Replace `settings.embedding_model` (same name, just different config import)
- Keep `\\n` → `\n` prefix conversion (pitfall from spec)
- Use `config.get_settings()` instead of `config.settings`

```python
# rag/embeddings.py
"""Embedding via Ollama /api/embed endpoint."""

import logging

import httpx

from config import get_settings

logger = logging.getLogger(__name__)


def _ollama_embed(texts: list[str], prefix: str = "") -> list[list[float]]:
    """Call Ollama /api/embed for a batch of texts."""
    settings = get_settings()
    # Convert literal \n to newline (common .env pitfall)
    resolved_prefix = prefix.replace("\\n", "\n") if prefix else ""
    prefixed = [f"{resolved_prefix}{t}" for t in texts] if resolved_prefix else texts

    url = f"{settings.ollama_base_url}/api/embed"
    response = httpx.post(
        url,
        json={"model": settings.embedding_model, "input": prefixed},
        timeout=120.0,
    )
    response.raise_for_status()
    return response.json()["embeddings"]


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts with passage prefix."""
    settings = get_settings()
    return _ollama_embed(texts, prefix=settings.embedding_passage_prefix)


def embed_query(query: str) -> list[float]:
    """Embed a single query with query prefix."""
    settings = get_settings()
    vectors = _ollama_embed([query], prefix=settings.embedding_query_prefix)
    return vectors[0]
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_embeddings.py -v
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add rag/__init__.py rag/embeddings.py tests/test_embeddings.py
git commit -m "feat: port embeddings.py — Ollama only"
```

---

### Task 4: Port vector_store.py

Port from compliance-bot. Add `collection` param to all functions (no hardcoded collection name). Update payload indexes for LegalChunk fields.

**Files:**
- Create: `rag/vector_store.py`
- Create: `tests/test_vector_store.py`
- Source: `/Users/dmytroivanov/projects/compliance-bot/rag/vector_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vector_store.py
import pytest
from unittest.mock import MagicMock, patch


def test_get_qdrant_client_singleton(monkeypatch):
    """get_qdrant_client() returns same instance on repeated calls."""
    monkeypatch.setenv("QDRANT_URL", "http://localhost:6333")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")

    import importlib
    import rag.vector_store as mod
    importlib.reload(mod)

    # Reset singleton
    mod._client = None

    with patch("rag.vector_store.QdrantClient") as MockClient:
        c1 = mod.get_qdrant_client()
        c2 = mod.get_qdrant_client()
        assert c1 is c2
        MockClient.assert_called_once()


def test_upsert_chunks_calls_qdrant(monkeypatch):
    """upsert_chunks() calls client.upsert with correct collection."""
    monkeypatch.setenv("QDRANT_URL", "http://localhost:6333")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")

    import importlib
    import rag.vector_store as mod
    importlib.reload(mod)

    mock_client = MagicMock()
    mod._client = mock_client

    from ingest.chunk_models import LegalChunk
    chunks = [
        LegalChunk(
            chunk_id="c1", doc_id="d1", doc_title="t", doc_filename="f.docx",
            doc_type="contract", client_id="client-a", jurisdiction="US",
            sensitivity="internal", text="hello",
        )
    ]
    embeddings = [[0.1] * 768]

    mod.upsert_chunks(chunks, embeddings, collection="legal_docs")

    mock_client.upsert.assert_called_once()
    call_kwargs = mock_client.upsert.call_args[1]
    assert call_kwargs["collection_name"] == "legal_docs"


def test_search_vectors_passes_collection(monkeypatch):
    """search_vectors() queries the specified collection."""
    monkeypatch.setenv("QDRANT_URL", "http://localhost:6333")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")

    import importlib
    import rag.vector_store as mod
    importlib.reload(mod)

    mock_client = MagicMock()
    mock_client.query_points.return_value.points = []
    mod._client = mock_client

    mod.search_vectors([0.1] * 768, top_k=5, collection="case_history")

    call_kwargs = mock_client.query_points.call_args[1]
    assert call_kwargs["collection_name"] == "case_history"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_vector_store.py -v
```

Expected: FAIL — `ModuleNotFoundError` or `AttributeError`

- [ ] **Step 3: Port vector_store.py from compliance-bot**

Port from `/Users/dmytroivanov/projects/compliance-bot/rag/vector_store.py`. Key changes:
- All functions take `collection: str` parameter — no default, no hardcoded name
- Replace `PolicyChunk` type hints with `LegalChunk`
- Update `init_collection()` payload indexes for new fields: `doc_type`, `client_id`, `jurisdiction`, `sensitivity`, `clause_type`, `date`
- Use `config.get_settings()` instead of `config.settings`
- Keep batch upsert (100 at a time)

```python
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

    # Create payload indexes for common filter fields
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
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_vector_store.py -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add rag/vector_store.py tests/test_vector_store.py
git commit -m "feat: port vector_store.py with collection param"
```

---

### Task 5: Port bm25_index.py

Port from compliance-bot. Adapt field names from PolicyChunk to LegalChunk. Remove `doc_link` references.

**Files:**
- Create: `rag/bm25_index.py`
- Create: `tests/test_bm25.py`
- Source: `/Users/dmytroivanov/projects/compliance-bot/rag/bm25_index.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_bm25.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Port bm25_index.py from compliance-bot**

Port from `/Users/dmytroivanov/projects/compliance-bot/rag/bm25_index.py`. Key changes:
- Replace `PolicyChunk` type references with `LegalChunk`
- Remove `doc_link` from metadata fields
- Add `doc_type`, `client_id`, `jurisdiction` to metadata fields stored per chunk
- Use `config.get_settings()` instead of `config.settings`
- Keep the pure-Python BM25 (Okapi BM25, k1=1.2, b=0.75)
- Keep JSON persistence

The implementation should be ported directly from the compliance-bot file. Read it, apply the changes listed above, and write the result to `rag/bm25_index.py`.

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_bm25.py -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add rag/bm25_index.py tests/test_bm25.py
git commit -m "feat: port bm25_index.py with LegalChunk fields"
```

---

### Task 6: Port reranker.py

Port as-is from compliance-bot. Only change config imports.

**Files:**
- Create: `rag/reranker.py`
- Create: `tests/test_reranker.py`
- Source: `/Users/dmytroivanov/projects/compliance-bot/rag/reranker.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_reranker.py
from unittest.mock import MagicMock, patch


def test_rerank_calls_endpoint(monkeypatch):
    """rerank() calls /v1/rerank and returns sorted results."""
    monkeypatch.setenv("RERANKER_ENABLED", "true")
    monkeypatch.setenv("RERANKER_BACKEND", "llama-cpp")
    monkeypatch.setenv("RERANKER_URL", "http://localhost:8081/v1/rerank")
    monkeypatch.setenv("RERANKER_MODEL", "bge-reranker")
    monkeypatch.setenv("RERANKER_TOP_N", "2")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")

    import importlib
    import rag.reranker as mod
    importlib.reload(mod)

    results = [
        {"chunk_id": "c1", "text": "low relevance text"},
        {"chunk_id": "c2", "text": "high relevance text about contracts"},
        {"chunk_id": "c3", "text": "medium relevance"},
    ]

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "results": [
            {"index": 1, "relevance_score": 0.9},
            {"index": 2, "relevance_score": 0.5},
            {"index": 0, "relevance_score": 0.1},
        ]
    }

    with patch("rag.reranker.httpx.post", return_value=fake_response):
        reranked = mod.rerank("contract terms", results, top_n=2)

    assert len(reranked) == 2
    assert reranked[0]["chunk_id"] == "c2"  # highest score
    assert "rerank_score" in reranked[0]


def test_rerank_disabled_returns_original(monkeypatch):
    """When reranker is disabled, returns original results unchanged."""
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")

    import importlib
    import rag.reranker as mod
    importlib.reload(mod)

    results = [{"chunk_id": "c1", "text": "a"}, {"chunk_id": "c2", "text": "b"}]
    reranked = mod.rerank("query", results, top_n=2)

    assert len(reranked) == 2
    assert reranked[0]["chunk_id"] == "c1"


def test_rerank_connection_error_returns_original(monkeypatch):
    """On connection error, gracefully returns original results."""
    monkeypatch.setenv("RERANKER_ENABLED", "true")
    monkeypatch.setenv("RERANKER_BACKEND", "llama-cpp")
    monkeypatch.setenv("RERANKER_URL", "http://localhost:8081/v1/rerank")
    monkeypatch.setenv("RERANKER_MODEL", "bge-reranker")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")

    import importlib
    import rag.reranker as mod
    importlib.reload(mod)

    results = [{"chunk_id": "c1", "text": "a"}]

    with patch("rag.reranker.httpx.post", side_effect=httpx.ConnectError("down")):
        reranked = mod.rerank("query", results, top_n=1)

    assert len(reranked) == 1
    assert reranked[0]["chunk_id"] == "c1"
```

Add at top of file: `import httpx`

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_reranker.py -v
```

Expected: FAIL

- [ ] **Step 3: Port reranker.py from compliance-bot**

Port from `/Users/dmytroivanov/projects/compliance-bot/rag/reranker.py`. Key changes:
- Use `config.get_settings()` instead of `config.settings`
- Keep multi-backend support (llama-server, vllm, vllm-score)
- Keep graceful fallback on ConnectError/Timeout
- When `reranker_enabled` is False, return original results immediately

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_reranker.py -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add rag/reranker.py tests/test_reranker.py
git commit -m "feat: port reranker.py with multi-backend support"
```

---

### Task 7: Port hybrid_search.py

Port from compliance-bot. Adapt field names for LegalChunk. Remove `doc_link`.

**Files:**
- Create: `rag/hybrid_search.py`
- Create: `tests/test_hybrid_search.py`
- Source: `/Users/dmytroivanov/projects/compliance-bot/rag/hybrid_search.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hybrid_search.py
from unittest.mock import patch, MagicMock


def test_hybrid_search_merges_results(monkeypatch):
    """hybrid_search() merges vector and BM25 results via RRF."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("BM25_ENABLED", "true")
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.setenv("HYBRID_VECTOR_CANDIDATES", "5")
    monkeypatch.setenv("HYBRID_BM25_CANDIDATES", "5")

    import importlib
    import rag.hybrid_search as mod
    importlib.reload(mod)

    vector_results = [
        {"chunk_id": "c1", "doc_id": "d1", "text": "clause about indemnity",
         "score": 0.9, "doc_title": "Contract A", "doc_type": "contract",
         "client_id": "x", "jurisdiction": "US"},
        {"chunk_id": "c2", "doc_id": "d1", "text": "payment terms",
         "score": 0.7, "doc_title": "Contract A", "doc_type": "contract",
         "client_id": "x", "jurisdiction": "US"},
    ]

    bm25_results = [
        {"chunk_id": "c1", "doc_id": "d1", "text": "clause about indemnity",
         "bm25_score": 5.2, "doc_title": "Contract A"},
        {"chunk_id": "c3", "doc_id": "d2", "text": "indemnity in case law",
         "bm25_score": 3.1, "doc_title": "Case B"},
    ]

    with patch("rag.hybrid_search.embed_query", return_value=[0.1] * 768), \
         patch("rag.hybrid_search.search_vectors", return_value=vector_results), \
         patch("rag.hybrid_search.search_bm25", return_value=bm25_results):

        results = mod.hybrid_search(
            query="indemnity clause",
            top_k=3,
            collection="legal_docs",
        )

    assert len(results) <= 3
    # c1 appears in both → should have highest RRF score
    assert results[0]["chunk_id"] == "c1"
    assert "rrf_score" in results[0]


def test_hybrid_search_bm25_disabled(monkeypatch):
    """When BM25 disabled, only vector results are returned."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("BM25_ENABLED", "false")
    monkeypatch.setenv("RERANKER_ENABLED", "false")

    import importlib
    import rag.hybrid_search as mod
    importlib.reload(mod)

    vector_results = [
        {"chunk_id": "c1", "doc_id": "d1", "text": "text",
         "score": 0.9, "doc_title": "t", "doc_type": "contract",
         "client_id": "x", "jurisdiction": "US"},
    ]

    with patch("rag.hybrid_search.embed_query", return_value=[0.1] * 768), \
         patch("rag.hybrid_search.search_vectors", return_value=vector_results):

        results = mod.hybrid_search(
            query="test",
            top_k=5,
            collection="legal_docs",
        )

    assert len(results) == 1
    assert results[0]["chunk_id"] == "c1"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_hybrid_search.py -v
```

Expected: FAIL

- [ ] **Step 3: Port hybrid_search.py from compliance-bot**

Port from `/Users/dmytroivanov/projects/compliance-bot/rag/hybrid_search.py`. Key changes:
- All functions take `collection: str` parameter, passed to `search_vectors()`
- Accept optional `filters: dict` parameter, passed to `search_vectors()`
- Remove `doc_link` from merged fields
- Add `doc_type`, `client_id`, `jurisdiction`, `sensitivity` to merged fields
- Use `config.get_settings()` instead of `config.settings`
- Keep RRF with k=60
- Integrate reranker: after RRF fusion, call `rerank()` if enabled, before returning top_k

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_hybrid_search.py -v
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add rag/hybrid_search.py tests/test_hybrid_search.py
git commit -m "feat: port hybrid_search.py with collection param and reranker"
```

---

### Task 8: Port numbering.py and docx_parser.py

Port both from compliance-bot. `numbering.py` is a dependency of `docx_parser.py` (resolves Word auto-numbering XML). Not in the original porting table but required.

**Files:**
- Create: `ingest/numbering.py`
- Create: `ingest/parsers/__init__.py`
- Create: `ingest/parsers/docx_parser.py`
- Create: `tests/test_parsers.py`
- Source: `/Users/dmytroivanov/projects/compliance-bot/ingest/numbering.py`
- Source: `/Users/dmytroivanov/projects/compliance-bot/ingest/docx_parser.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_parsers.py
import pytest
from pathlib import Path
from ingest.chunk_models import LegalChunk


def test_docx_parser_returns_legal_chunks(tmp_path):
    """DOCX parser returns list of LegalChunk objects."""
    from docx import Document

    # Create a minimal test DOCX
    doc = Document()
    doc.add_heading("Section 1: Definitions", level=1)
    doc.add_paragraph("This agreement defines the terms used herein.")
    doc.add_heading("Section 2: Obligations", level=1)
    doc.add_paragraph("The contractor shall deliver services as described.")
    filepath = tmp_path / "test_contract.docx"
    doc.save(str(filepath))

    from ingest.parsers.docx_parser import parse_docx

    chunks = parse_docx(
        filepath=filepath,
        client_id="client-abc",
        jurisdiction="US-DE",
        doc_type="contract",
        sensitivity="confidential",
    )

    assert len(chunks) > 0
    assert all(isinstance(c, LegalChunk) for c in chunks)
    assert all(c.client_id == "client-abc" for c in chunks)
    assert all(c.jurisdiction == "US-DE" for c in chunks)
    assert all(c.doc_type == "contract" for c in chunks)
    assert all(c.sensitivity == "confidential" for c in chunks)
    assert all(c.doc_filename == "test_contract.docx" for c in chunks)


def test_docx_parser_chunk_has_text(tmp_path):
    """Each chunk has non-empty text."""
    from docx import Document

    doc = Document()
    doc.add_heading("Important Clause", level=1)
    doc.add_paragraph("The parties hereby agree to binding arbitration.")
    filepath = tmp_path / "test.docx"
    doc.save(str(filepath))

    from ingest.parsers.docx_parser import parse_docx

    chunks = parse_docx(
        filepath=filepath,
        client_id="internal",
        jurisdiction="US",
        doc_type="policy",
        sensitivity="internal",
    )

    assert all(len(c.text.strip()) > 0 for c in chunks)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_parsers.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Port numbering.py as-is**

Copy `/Users/dmytroivanov/projects/compliance-bot/ingest/numbering.py` to `ingest/numbering.py`. No changes needed — it's a self-contained utility for resolving Word XML numbering.

- [ ] **Step 4: Port docx_parser.py**

Port from `/Users/dmytroivanov/projects/compliance-bot/ingest/docx_parser.py`. Key changes:
- Function signature: `parse_docx(filepath, client_id, jurisdiction, doc_type, sensitivity)` replaces `parse_docx(filepath, doc_link)`
- Create `LegalChunk` instead of `PolicyChunk`
- Set `client_id`, `jurisdiction`, `doc_type`, `sensitivity` on every chunk
- Remove `doc_link` field
- Add `chunk_strategy` field based on doc_type (e.g. "clause-level" for contracts)
- Use `config.get_settings()` for `chunk_min_tokens` and `chunk_max_tokens`
- Keep all existing logic: heading detection, clause numbering, table-to-text, chunk splitting/merging, noise filtering

- [ ] **Step 5: Create parsers __init__.py**

Create `ingest/parsers/__init__.py` (empty).

- [ ] **Step 6: Run tests**

```bash
pytest tests/test_parsers.py -v
```

Expected: 2 passed

- [ ] **Step 7: Commit**

```bash
git add ingest/numbering.py ingest/parsers/__init__.py ingest/parsers/docx_parser.py tests/test_parsers.py
git commit -m "feat: port docx_parser and numbering from compliance-bot"
```

---

### Task 9: Create pdf_parser.py

New file — no compliance-bot equivalent. Uses pdfplumber to extract text from PDFs.

**Files:**
- Create: `ingest/parsers/pdf_parser.py`
- Modify: `tests/test_parsers.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_parsers.py`:

```python
def test_pdf_parser_returns_legal_chunks(tmp_path):
    """PDF parser returns list of LegalChunk objects."""
    # Create a minimal test PDF using pdfplumber's test helper isn't easy,
    # so we'll create one with reportlab or just test with a real tiny PDF.
    # For now, test the chunking logic with a text string.
    from ingest.parsers.pdf_parser import parse_pdf_text

    text = """Section 1: Definitions
This agreement defines the terms used herein and throughout.

Section 2: Obligations
The contractor shall deliver services as described in Exhibit A.
All deliverables must meet the quality standards specified."""

    chunks = parse_pdf_text(
        text=text,
        filename="test.pdf",
        client_id="client-y",
        jurisdiction="UK",
        doc_type="legislation",
        sensitivity="public",
    )

    assert len(chunks) > 0
    assert all(isinstance(c, LegalChunk) for c in chunks)
    assert all(c.client_id == "client-y" for c in chunks)
    assert all(c.doc_type == "legislation" for c in chunks)
    assert all(c.doc_filename == "test.pdf" for c in chunks)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_parsers.py::test_pdf_parser_returns_legal_chunks -v
```

Expected: FAIL

- [ ] **Step 3: Create pdf_parser.py**

```python
# ingest/parsers/pdf_parser.py
"""PDF parser using pdfplumber. Extracts text and chunks by section headings."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pdfplumber

from config import get_settings
from ingest.chunk_models import LegalChunk

# Heuristic: lines that look like section headings
_HEADING_RE = re.compile(
    r"^(?:section|article|chapter|part|clause)\s+\d+",
    re.IGNORECASE,
)


def _split_into_sections(text: str) -> list[tuple[str, str]]:
    """Split text into (heading, body) tuples by section headings."""
    lines = text.split("\n")
    sections: list[tuple[str, str]] = []
    current_heading = ""
    current_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if _HEADING_RE.match(stripped):
            if current_lines:
                body = "\n".join(current_lines).strip()
                if body:
                    sections.append((current_heading, body))
            current_heading = stripped
            current_lines = []
        else:
            current_lines.append(line)

    # Last section
    if current_lines:
        body = "\n".join(current_lines).strip()
        if body:
            sections.append((current_heading, body))

    return sections


def _chunk_text(text: str, max_tokens: int) -> list[str]:
    """Split text into chunks at sentence boundaries, respecting max_tokens."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for sentence in sentences:
        words = len(sentence.split())
        if current_len + words > max_tokens and current:
            chunks.append(" ".join(current))
            current = []
            current_len = 0
        current.append(sentence)
        current_len += words

    if current:
        chunks.append(" ".join(current))

    return chunks


def parse_pdf_text(
    text: str,
    filename: str,
    client_id: str,
    jurisdiction: str,
    doc_type: str,
    sensitivity: str,
) -> list[LegalChunk]:
    """Parse extracted PDF text into LegalChunks."""
    settings = get_settings()
    doc_id = str(uuid.uuid4())
    doc_title = Path(filename).stem.replace("_", " ").title()
    now = datetime.now(timezone.utc).isoformat()

    sections = _split_into_sections(text)
    if not sections:
        # No headings found — treat entire text as one section
        sections = [("", text)]

    chunks: list[LegalChunk] = []
    chunk_index = 0

    for heading, body in sections:
        text_chunks = _chunk_text(body, settings.chunk_max_tokens)
        for chunk_text in text_chunks:
            word_count = len(chunk_text.split())
            if word_count < settings.chunk_min_tokens:
                continue

            chunks.append(LegalChunk(
                chunk_id=str(uuid.uuid4()),
                doc_id=doc_id,
                doc_title=doc_title,
                doc_filename=filename,
                doc_type=doc_type,
                client_id=client_id,
                jurisdiction=jurisdiction,
                sensitivity=sensitivity,
                section=heading,
                text=chunk_text,
                char_count=len(chunk_text),
                chunk_index=chunk_index,
                chunk_strategy="section-based",
                last_updated=now,
            ))
            chunk_index += 1

    return chunks


def parse_pdf(
    filepath: Path | str,
    client_id: str,
    jurisdiction: str,
    doc_type: str,
    sensitivity: str,
) -> list[LegalChunk]:
    """Parse a PDF file into LegalChunks."""
    filepath = Path(filepath)
    with pdfplumber.open(filepath) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
    full_text = "\n".join(pages)
    return parse_pdf_text(
        text=full_text,
        filename=filepath.name,
        client_id=client_id,
        jurisdiction=jurisdiction,
        doc_type=doc_type,
        sensitivity=sensitivity,
    )
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_parsers.py -v
```

Expected: 3 passed (2 docx + 1 pdf)

- [ ] **Step 5: Commit**

```bash
git add ingest/parsers/pdf_parser.py tests/test_parsers.py
git commit -m "feat: add PDF parser with section-based chunking"
```

---

### Task 10: Create ingest pipeline

Orchestrates the full flow: parse → chunk → embed → upsert to Qdrant (+ BM25 if enabled).

**Files:**
- Create: `ingest/pipeline.py`
- Create: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline.py
from unittest.mock import patch, MagicMock
from pathlib import Path
from ingest.chunk_models import LegalChunk


def _make_chunks(n: int) -> list[LegalChunk]:
    return [
        LegalChunk(
            chunk_id=f"c{i}", doc_id="d1", doc_title="t",
            doc_filename="f.docx", doc_type="contract",
            client_id="client-a", jurisdiction="US",
            sensitivity="internal", text=f"chunk text {i}",
        )
        for i in range(n)
    ]


def test_ingest_document_calls_embed_and_upsert(monkeypatch):
    """ingest_document() parses, embeds, and upserts to Qdrant."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("EMBEDDING_MODEL", "embeddinggemma:latest")
    monkeypatch.setenv("BM25_ENABLED", "false")

    import importlib
    import ingest.pipeline as mod
    importlib.reload(mod)

    chunks = _make_chunks(3)
    embeddings = [[0.1] * 768] * 3

    with patch("ingest.pipeline.parse_docx", return_value=chunks), \
         patch("ingest.pipeline.parse_pdf", return_value=chunks), \
         patch("ingest.pipeline.embed_texts", return_value=embeddings) as mock_embed, \
         patch("ingest.pipeline.upsert_chunks") as mock_upsert:

        from ingest.pipeline import ingest_document
        result = ingest_document(
            filepath=Path("test.docx"),
            client_id="client-a",
            jurisdiction="US",
            doc_type="contract",
            sensitivity="internal",
            collection="legal_docs",
        )

    assert result == 3
    mock_embed.assert_called_once()
    mock_upsert.assert_called_once()


def test_ingest_document_adds_to_bm25_when_enabled(monkeypatch):
    """When BM25 enabled, ingest also adds chunks to BM25 index."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("EMBEDDING_MODEL", "embeddinggemma:latest")
    monkeypatch.setenv("BM25_ENABLED", "true")

    import importlib
    import ingest.pipeline as mod
    importlib.reload(mod)

    chunks = _make_chunks(2)
    embeddings = [[0.1] * 768] * 2

    with patch("ingest.pipeline.parse_docx", return_value=chunks), \
         patch("ingest.pipeline.embed_texts", return_value=embeddings), \
         patch("ingest.pipeline.upsert_chunks"), \
         patch("ingest.pipeline.get_bm25_index") as mock_bm25:

        mock_index = MagicMock()
        mock_bm25.return_value = mock_index

        from ingest.pipeline import ingest_document
        ingest_document(
            filepath=Path("test.docx"),
            client_id="client-a",
            jurisdiction="US",
            doc_type="contract",
            sensitivity="internal",
            collection="legal_docs",
        )

    assert mock_index.add.call_count == 2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_pipeline.py -v
```

Expected: FAIL

- [ ] **Step 3: Create pipeline.py**

```python
# ingest/pipeline.py
"""Ingest pipeline: parse → chunk → embed → upsert to Qdrant (+ BM25)."""

from __future__ import annotations

import logging
from pathlib import Path

from config import get_settings
from ingest.parsers.docx_parser import parse_docx
from ingest.parsers.pdf_parser import parse_pdf
from rag.embeddings import embed_texts
from rag.vector_store import upsert_chunks

logger = logging.getLogger(__name__)

PARSERS = {
    ".docx": parse_docx,
    ".pdf": parse_pdf,
}


def ingest_document(
    filepath: Path | str,
    client_id: str,
    jurisdiction: str,
    doc_type: str,
    sensitivity: str,
    collection: str,
) -> int:
    """Parse, embed, and upsert a single document. Returns chunk count."""
    filepath = Path(filepath)
    settings = get_settings()

    # Select parser by extension
    ext = filepath.suffix.lower()
    parser = PARSERS.get(ext)
    if parser is None:
        raise ValueError(f"Unsupported file type: {ext}. Supported: {list(PARSERS)}")

    # Parse into chunks
    chunks = parser(
        filepath=filepath,
        client_id=client_id,
        jurisdiction=jurisdiction,
        doc_type=doc_type,
        sensitivity=sensitivity,
    )

    if not chunks:
        logger.warning("No chunks produced from %s", filepath)
        return 0

    logger.info("Parsed %d chunks from %s", len(chunks), filepath.name)

    # Embed
    texts = [c.text for c in chunks]
    embeddings = embed_texts(texts)

    # Upsert to Qdrant
    upsert_chunks(chunks, embeddings, collection=collection)
    logger.info("Upserted %d chunks to %s", len(chunks), collection)

    # Add to BM25 index if enabled
    if settings.bm25_enabled:
        from rag.bm25_index import get_bm25_index
        index = get_bm25_index()
        for chunk in chunks:
            index.add(chunk.chunk_id, chunk.model_dump())
        logger.info("Added %d chunks to BM25 index", len(chunks))

    return len(chunks)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_pipeline.py -v
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add ingest/pipeline.py tests/test_pipeline.py
git commit -m "feat: add ingest pipeline — parse, embed, upsert"
```

---

### Task 11: Create ingest_all.py script

Batch ingest from a directory. Resolves metadata from directory structure or sidecar JSON.

**Files:**
- Create: `scripts/ingest_all.py`

- [ ] **Step 1: Create ingest_all.py**

```python
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

        # Check for sidecar metadata
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

    logger.info("\nDone. Total: %d chunks from %d files", total_chunks, len(files))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add scripts/ingest_all.py
git commit -m "feat: add ingest_all.py batch ingest script"
```

---

### Task 12: Create RAG tools (search_legal, get_document, extract_clauses, escalate)

LangGraph `@tool` decorated functions. These will be used by agent subgraphs (contract_generation, legal_research).

**Files:**
- Create: `rag/tools/__init__.py`
- Create: `rag/tools/search_legal.py`
- Create: `rag/tools/get_document.py`
- Create: `rag/tools/extract_clauses.py`
- Create: `rag/tools/escalate.py`
- Source (get_document): `/Users/dmytroivanov/projects/compliance-bot/rag/tools/get_section.py`
- Source (escalate): `/Users/dmytroivanov/projects/compliance-bot/rag/tools/escalate.py`

- [ ] **Step 1: Create rag/tools/__init__.py**

```python
# rag/tools/__init__.py
```

- [ ] **Step 2: Create search_legal.py**

```python
# rag/tools/search_legal.py
"""Hybrid search tool for LangGraph agents."""

from langchain_core.tools import tool

from rag.hybrid_search import hybrid_search


@tool
def search_legal(
    query: str,
    collection: str = "legal_docs",
    client_id: str | None = None,
    jurisdiction: str | None = None,
    doc_type: str | None = None,
    top_k: int = 10,
) -> str:
    """Search the legal knowledge base using hybrid search (vector + BM25 + reranking).

    Args:
        query: The search query.
        collection: Qdrant collection to search. One of: legal_docs, case_history, memory.
        client_id: Filter by client ID. Always required for cross-client safety.
        jurisdiction: Filter by jurisdiction.
        doc_type: Filter by document type (contract, legislation, template, policy, case_law).
        top_k: Number of results to return.

    Returns:
        Formatted search results with chunk text and metadata.
    """
    filters = {}
    if client_id:
        filters["client_id"] = client_id
    if jurisdiction:
        filters["jurisdiction"] = jurisdiction
    if doc_type:
        filters["doc_type"] = doc_type

    results = hybrid_search(
        query=query,
        top_k=top_k,
        collection=collection,
        filters=filters if filters else None,
    )

    if not results:
        return "No results found."

    formatted = []
    for i, r in enumerate(results, 1):
        formatted.append(
            f"[{i}] {r.get('doc_title', 'Unknown')} "
            f"(doc_id: {r.get('doc_id', '?')}, "
            f"type: {r.get('doc_type', '?')}, "
            f"jurisdiction: {r.get('jurisdiction', '?')})\n"
            f"Section: {r.get('section', '')}\n"
            f"Text: {r.get('text', '')}\n"
            f"Score: {r.get('rrf_score', r.get('score', '?')):.4f}"
        )

    return "\n\n---\n\n".join(formatted)
```

- [ ] **Step 3: Create get_document.py**

Port from `/Users/dmytroivanov/projects/compliance-bot/rag/tools/get_section.py`. Replace `FunctionTool` with `@tool`. Adapt to LegalChunk fields. Add `client_id` filter.

```python
# rag/tools/get_document.py
"""Retrieve full document or section by doc_id."""

from langchain_core.tools import tool

from rag.vector_store import scroll_by_filter


@tool
def get_document(
    doc_id: str,
    collection: str = "legal_docs",
    section: str | None = None,
    client_id: str | None = None,
) -> str:
    """Retrieve the full text of a document or a specific section.

    Args:
        doc_id: The document ID to retrieve.
        collection: Qdrant collection to search.
        section: Optional section name to filter by.
        client_id: Client ID filter for access control.

    Returns:
        Full document text or specific section text.
    """
    filters = {"doc_id": doc_id}
    if client_id:
        filters["client_id"] = client_id
    if section:
        filters["section_display"] = section

    results = scroll_by_filter(
        filter_conditions=filters,
        collection=collection,
        limit=500,
    )

    if not results:
        return f"No content found for doc_id={doc_id}"

    # Sort by chunk_index for correct ordering
    results.sort(key=lambda r: r.get("chunk_index", 0))

    title = results[0].get("doc_title", "Unknown")
    texts = [r.get("text", "") for r in results]

    return f"# {title}\n\n" + "\n\n".join(texts)
```

- [ ] **Step 4: Create extract_clauses.py**

New tool — queries case_history by clause_type for contract generation.

```python
# rag/tools/extract_clauses.py
"""Extract clauses by type from case_history for contract generation."""

from langchain_core.tools import tool

from rag.vector_store import scroll_by_filter


@tool
def extract_clauses(
    clause_type: str,
    client_id: str,
    jurisdiction: str | None = None,
    limit: int = 20,
) -> str:
    """Extract historical clauses of a specific type from signed contracts.

    Searches the case_history collection for past clauses matching the type.
    Used by contract generation to find patterns.

    Args:
        clause_type: Type of clause (e.g., indemnification, termination, payment).
        client_id: Client ID — required, never cross-client.
        jurisdiction: Optional jurisdiction filter.
        limit: Maximum clauses to return.

    Returns:
        Formatted list of historical clauses with metadata.
    """
    filters: dict = {
        "clause_type": clause_type,
        "client_id": client_id,
    }
    if jurisdiction:
        filters["jurisdiction"] = jurisdiction

    results = scroll_by_filter(
        filter_conditions=filters,
        collection="case_history",
        limit=limit,
    )

    if not results:
        return f"No historical clauses found for type={clause_type}, client={client_id}"

    formatted = []
    for i, r in enumerate(results, 1):
        formatted.append(
            f"[{i}] {r.get('doc_title', 'Unknown')} "
            f"(jurisdiction: {r.get('jurisdiction', '?')})\n"
            f"Clause: {r.get('text', '')}"
        )

    return f"Found {len(results)} '{clause_type}' clauses:\n\n" + "\n\n---\n\n".join(formatted)
```

- [ ] **Step 5: Create escalate.py**

Port from `/Users/dmytroivanov/projects/compliance-bot/rag/tools/escalate.py`. Replace `FunctionTool` with `@tool`. Use "LEG" prefix.

```python
# rag/tools/escalate.py
"""Escalation tool — flags items for attorney review."""

import uuid
from datetime import datetime, timezone

from langchain_core.tools import tool

from config import get_settings

_escalation_store: list[dict] = []


@tool
def escalate(
    reason: str,
    context: str = "",
    severity: str = "medium",
) -> str:
    """Escalate an issue for attorney review.

    Use when: retrieval confidence is too low, no citations found,
    conflicting information, or high-risk determination needed.

    Args:
        reason: Why this is being escalated.
        context: Relevant context (query, partial results, etc.).
        severity: low, medium, or high.

    Returns:
        Escalation ticket ID and confirmation.
    """
    settings = get_settings()
    prefix = settings.escalation_ticket_prefix
    year = datetime.now(timezone.utc).strftime("%Y")
    seq = len(_escalation_store) + 1
    ticket_id = f"{prefix}-{year}-{seq:04d}"

    ticket = {
        "ticket_id": ticket_id,
        "reason": reason,
        "context": context,
        "severity": severity,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _escalation_store.append(ticket)

    return (
        f"Escalation ticket created: {ticket_id}\n"
        f"Severity: {severity}\n"
        f"Reason: {reason}\n"
        f"This will be routed to attorney review."
    )
```

- [ ] **Step 6: Commit**

```bash
git add rag/tools/__init__.py rag/tools/search_legal.py rag/tools/get_document.py rag/tools/extract_clauses.py rag/tools/escalate.py
git commit -m "feat: add LangGraph RAG tools — search, get_document, extract_clauses, escalate"
```

---

### Task 13: End-to-end verification script

Create a test script that ingests a sample document and retrieves chunks — proving the full pipeline works.

**Files:**
- Create: `scripts/test_query.py`

- [ ] **Step 1: Create test_query.py**

```python
#!/usr/bin/env python3
"""End-to-end test: ingest a sample doc, then retrieve via hybrid search."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from docx import Document


def create_sample_docx(path: Path) -> None:
    """Create a minimal test contract."""
    doc = Document()
    doc.add_heading("Sample Service Agreement", level=0)
    doc.add_heading("Section 1: Definitions", level=1)
    doc.add_paragraph(
        "In this Agreement, 'Service Provider' means the party providing services "
        "as described in Schedule A. 'Client' means the party receiving services."
    )
    doc.add_heading("Section 2: Payment Terms", level=1)
    doc.add_paragraph(
        "The Client shall pay the Service Provider within 30 days of invoice date. "
        "Late payments shall accrue interest at a rate of 1.5% per month."
    )
    doc.add_heading("Section 3: Indemnification", level=1)
    doc.add_paragraph(
        "The Service Provider shall indemnify and hold harmless the Client against "
        "all claims, damages, and expenses arising from the Provider's negligence."
    )
    doc.save(str(path))


def main() -> None:
    print("=== Phase 2 End-to-End Test ===\n")

    # 1. Create sample document
    with tempfile.TemporaryDirectory() as tmpdir:
        docx_path = Path(tmpdir) / "sample_contract.docx"
        create_sample_docx(docx_path)
        print(f"1. Created sample DOCX: {docx_path.name}")

        # 2. Ingest
        from ingest.pipeline import ingest_document
        count = ingest_document(
            filepath=docx_path,
            client_id="test-client",
            jurisdiction="US-NY",
            doc_type="contract",
            sensitivity="internal",
            collection="legal_docs",
        )
        print(f"2. Ingested {count} chunks into legal_docs\n")

        # 3. Search
        from rag.hybrid_search import hybrid_search
        print("3. Searching for 'indemnification clause'...")
        results = hybrid_search(
            query="indemnification clause",
            top_k=3,
            collection="legal_docs",
            filters={"client_id": "test-client"},
        )

        if not results:
            print("   ERROR: No results found!")
            sys.exit(1)

        print(f"   Found {len(results)} results:\n")
        for i, r in enumerate(results, 1):
            print(f"   [{i}] Score: {r.get('rrf_score', r.get('score', '?')):.4f}")
            print(f"       Section: {r.get('section', '(none)')}")
            print(f"       Text: {r.get('text', '')[:120]}...")
            print()

        # 4. Cleanup: delete test document from Qdrant
        from rag.vector_store import delete_document
        doc_id = results[0].get("doc_id")
        if doc_id:
            delete_document(doc_id, collection="legal_docs")
            print(f"4. Cleaned up: deleted doc {doc_id} from legal_docs")

    print("\n=== Phase 2 verification PASSED ===")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the script**

Requires Docker services running and Ollama with embeddinggemma:latest available.

```bash
python scripts/test_query.py
```

Expected: Ingests sample doc, retrieves relevant chunks for "indemnification clause", cleans up.

- [ ] **Step 3: Commit**

```bash
git add scripts/test_query.py
git commit -m "feat: add end-to-end test script for Phase 2 verification"
```

---

## Phase 2 Exit Criteria

- [ ] `pytest tests/ -v` — all tests pass (config, chunk_models, embeddings, vector_store, bm25, hybrid_search, reranker, parsers, pipeline)
- [ ] `python scripts/test_query.py` — ingests a doc, retrieves relevant chunks, cleans up
- [ ] `python scripts/ingest_all.py <directory> --collection legal_docs` — batch ingest works
