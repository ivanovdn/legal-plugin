# Legal Plugin — RAG

> Part of the [project wiki](wiki.md).

## RAG Pipeline

```
Query → embed_query() → Qdrant vector search
                       ↘
                        RRF fusion (k=60) → reranker → top N results
                       ↗
        BM25 keyword search (if enabled)
```

### Qdrant Collections

| Collection | Content | Chunking |
|---|---|---|
| `legal_docs` | Contracts, legislation, templates, policies | Varies by doc_type |
| `case_history` | Past signed contracts (5 demo + CUAD) | Clause-level (one chunk per clause) |
| `memory` | Unused stub (attorney preferences shipped as per-attorney `USER.md` files, not here) | Small entries |

### Ingest Pipeline

```
Document (PDF/DOCX) → Parser → Chunks (LegalChunk) → Embed (Ollama) → Upsert (Qdrant)
```

- **DOCX parser** — heading detection, clause numbering, Word auto-numbering, table-to-text, chunk splitting/merging, noise filtering
- **PDF parser** — section-based chunking by heading regex
- **Batch ingest**: `python scripts/ingest_all.py <dir> --collection legal_docs --client-id <id>`
