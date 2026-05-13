# Legal Plugin — Project Wiki

> Last updated: 2026-05-13 | 64 tests passing | ~3,300 lines production code | Python 3.12

## What Is This

AI-powered legal assistant for an internal legal team. Attorneys submit requests through a Chainlit chat UI or via REST API. A LangGraph supervisor graph routes requests to specialized skills, retrieves grounding context via hybrid RAG search, and returns structured legal output using a local LLM.

**Fully air-gapped** — no external API calls. Everything runs locally on Mac M4 Pro (48 GB).

---

## How to Run

```bash
# 1. Start Docker services (Qdrant, Redis, Langfuse v3, ClickHouse, PostgreSQL, MinIO)
docker compose up -d

# 2. Start both backend + frontend
bash scripts/start.sh

# Access points:
#   Frontend:  http://localhost:8080  (Chainlit chat UI)
#   Backend:   http://localhost:8000  (FastAPI API)
#   API docs:  http://localhost:8000/docs
#   Langfuse:  http://localhost:3000  (admin@local.dev / admin1234)
```

### Prerequisites

- Python 3.12 (venv managed via `uv`)
- Docker + Docker Compose
- Ollama running natively with models: `qwen3.6:latest`, `embeddinggemma:latest`
- llama-cpp reranker (optional, graceful fallback when unavailable)

---

## Architecture

```
Attorney → Chainlit (:8080) → FastAPI (:8000) → LangGraph graph
                                                    ├── Skills (5 capabilities)
                                                    ├── RAG layer → Qdrant
                                                    ├── LLM → Ollama
                                                    ├── Reranker → llama-cpp
                                                    ├── Checkpointer → Redis
                                                    ├── Audit → SQLite
                                                    └── Tracing → Langfuse
```

**Two-process model:**
- **Backend** (FastAPI, port 8000) — owns all business logic, graph execution, RAG, LLM calls, audit
- **Frontend** (Chainlit, port 8080) — thin client, talks to backend via HTTP

---

## Project Structure

```
legal-plugin/
├── api/                        # FastAPI backend
│   ├── main.py                 # App entry point, lifespan, CORS
│   ├── models.py               # Pydantic request/response models
│   └── routes/
│       ├── query.py            # POST /api/query, resume, status
│       ├── documents.py        # POST /api/ingest
│       └── health.py           # GET /health
│
├── frontend/                   # Chainlit frontend
│   ├── app.py                  # Chat handlers, file upload, human review
│   └── api_client.py           # Async HTTP client to backend
│
├── graph/                      # LangGraph supervisor graph
│   ├── state.py                # LegalAgentState TypedDict
│   ├── graph.py                # StateGraph wiring — all nodes + edges
│   └── nodes/                  # Shared processing nodes
│       ├── intake.py           # Resolve client_id, set filters
│       ├── intent_router.py    # LLM classifies task_type
│       ├── planner.py          # LLM decomposes multi-skill requests
│       ├── skill_dispatcher.py # Routes to correct skill node
│       ├── rag_retriever.py    # Calls hybrid_search
│       ├── llm_caller.py       # Calls Ollama with context
│       ├── risk_assessor.py    # Citation check + risk level
│       ├── human_review.py     # LangGraph interrupt() for blocking review
│       ├── output_formatter.py # Builds structured report dict
│       └── memory_writer.py    # Writes SQLite audit log
│
├── skills/                     # Legal capabilities
│   ├── schemas.py              # Output schemas (GeneratedContract, etc.)
│   ├── contract_generation.py  # ReAct agent — searches + generates contracts
│   ├── contract_review.py      # Clause analysis prompt → shared nodes
│   ├── compliance_check.py     # Policy verification prompt → shared nodes
│   ├── legal_research.py       # ReAct agent — multi-hop research
│   └── drafting.py             # Document generation prompt → shared nodes
│
├── rag/                        # RAG layer (ported from compliance-bot)
│   ├── embeddings.py           # Ollama /api/embed
│   ├── vector_store.py         # Qdrant client — all ops take collection param
│   ├── bm25_index.py           # Pure-Python BM25 with JSON persistence
│   ├── hybrid_search.py        # RRF fusion (vector + BM25) + reranker
│   ├── reranker.py             # Multi-backend reranker (llama-cpp, vLLM)
│   └── tools/                  # LangGraph @tool decorated functions
│       ├── search_legal.py     # Hybrid search as agent tool
│       ├── get_document.py     # Retrieve full doc by doc_id
│       ├── extract_clauses.py  # Query case_history by clause_type
│       └── escalate.py         # Flag for attorney review
│
├── ingest/                     # Document ingestion
│   ├── chunk_models.py         # LegalChunk Pydantic model
│   ├── pipeline.py             # parse → chunk → embed → upsert
│   ├── numbering.py            # Word auto-numbering resolver
│   └── parsers/
│       ├── docx_parser.py      # DOCX parser (ported from compliance-bot)
│       └── pdf_parser.py       # PDF parser (pdfplumber, section-based)
│
├── memory/                     # Persistence
│   └── audit.py                # SQLite audit log (create table + write)
│
├── observability/              # Tracing
│   └── langfuse.py             # init_observability()
│
├── scripts/                    # Utility scripts
│   ├── create_collections.py   # Create 3 Qdrant collections
│   ├── ingest_all.py           # Batch ingest from directory
│   ├── start.sh                # Start backend + frontend
│   ├── test_api.sh             # Curl-based API verification
│   ├── test_contract_gen.py    # Contract generation e2e test
│   └── test_graph_flow.py      # Graph routing verification
│
├── config.py                   # pydantic-settings — single config import
├── docker-compose.yml          # 7 services (Qdrant, Redis, Langfuse v3 stack)
├── requirements.txt            # All Python dependencies
└── tests/                      # 64 tests, 13 test files
```

---

## Graph Flow

```
Attorney sends request
         │
         ▼
    ┌──────────┐
    │  INTAKE   │  Resolves client_id from user_id
    │           │  Sets filters (client_id always present)
    └────┬─────┘  Sets retrieval_query = request
         │
         ▼
  ┌──────────────┐
  │ INTENT_ROUTER│  Calls Ollama to classify task_type:
  │              │  contract_generation | contract_review | compliance
  └──────┬───────┘  research | drafting
         │          Falls back to "research" if LLM unavailable
         │
    ┌────┴────┐
    │         │
 (multi)  (single)
    │         │
    ▼         ▼
┌────────┐ ┌──────────────────┐
│PLANNER │ │ SKILL_DISPATCHER │  Routes to skill node by task_type
└───┬────┘ └────────┬─────────┘
    │               │
    └───────┬───────┘
            ▼
   ┌────────────────┐
   │  SKILL NODE    │  One of 5:
   │                │  • contract_generation — ReAct agent (own tools)
   │                │  • contract_review — sets clause analysis prompt
   │                │  • compliance_check — sets policy verification prompt
   │                │  • legal_research — ReAct agent (own tools)
   │                │  • drafting — sets document generation prompt
   └───────┬────────┘
           │
           ▼
    ┌──────────────┐
    │ RAG_RETRIEVER│  hybrid_search() with client_id filter
    └──────┬───────┘  Vector + BM25 (if enabled) + RRF + reranker
           │
           ▼
     ┌───────────┐
     │ LLM_CALLER│  Ollama /api/chat, temperature=0.0
     │           │  Uses skill-provided system prompt if available
     └─────┬─────┘  Otherwise uses default legal assistant prompt
           │
           ▼
    ┌──────────────┐
    │RISK_ASSESSOR │  Checks if LLM response cites sources
    │              │  No citation = high risk → human_review
    └──────┬───────┘
           │
      ┌────┴────┐
      │         │
  (high risk   (low risk)
   OR contract_gen
   OR drafting)
      │         │
      ▼         ▼
┌────────────┐ ┌─────────────────┐
│HUMAN_REVIEW│ │OUTPUT_FORMATTER │
│            │ │                 │  Builds report dict:
└─────┬──────┘ └────────┬────────┘  {task_type, response, risk_level,
      │                 │            risk_flags, sources, awaiting_review}
      └────────┬────────┘
               ▼
        ┌──────────────┐
        │MEMORY_WRITER │  Writes to SQLite audit_log table
        └──────┬───────┘  Every invocation, no exceptions
               │
               ▼
              END → Response to attorney
```

### Routing Rules

| Condition | Route |
|---|---|
| `task_type` = contract_generation | **always** → human_review |
| `task_type` = drafting | **always** → human_review |
| `risk_level` = high or medium | → human_review |
| `risk_level` = low | → output_formatter (skip review) |
| `skill_plan` has multiple skills | → planner first |

---

## Skills

### Agent Subgraphs (have their own tools)

| Skill | Tools | What it does |
|---|---|---|
| **contract_generation** | search_legal, get_document, extract_clauses, escalate | Searches case history for clause patterns, generates new contracts. Always → human_review. |
| **legal_research** | search_legal, get_document, escalate | Multi-hop retrieval to answer legal questions. Cites sources, flags conflicts. |

### Plain Skills (use shared rag_retriever + llm_caller)

| Skill | What it does |
|---|---|
| **contract_review** | Sets clause analysis system prompt. LLM identifies clauses, risk levels, suggested edits. |
| **compliance_check** | Sets policy verification system prompt. LLM checks documents against regulations. |
| **drafting** | Sets document generation system prompt. Always → human_review. |

---

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
| `case_history` | Past signed contracts | Clause-level (one chunk per clause) |
| `memory` | Attorney preferences (stub — future) | Small entries |

### Ingest Pipeline

```
Document (PDF/DOCX) → Parser → Chunks (LegalChunk) → Embed (Ollama) → Upsert (Qdrant)
```

- **DOCX parser** — heading detection, clause numbering, Word auto-numbering, table-to-text, chunk splitting/merging, noise filtering
- **PDF parser** — section-based chunking by heading regex
- **Batch ingest**: `python scripts/ingest_all.py <dir> --collection legal_docs --client-id <id>`

---

## API Endpoints

| Method | Path | Status | Purpose |
|---|---|---|---|
| GET | `/health` | **Working** | Health check |
| POST | `/api/query` | **Working** | Submit legal request → graph execution |
| POST | `/api/query/{session_id}/resume` | Placeholder | Resume after human review |
| GET | `/api/query/{session_id}/status` | Placeholder | Check execution status |
| POST | `/api/ingest` | **Working** | Upload PDF/DOCX for ingestion |
| GET | `/api/documents` | Not built | List ingested documents |
| GET/DELETE | `/api/documents/{doc_id}` | Not built | Get/delete document |
| GET | `/api/sessions` | Not built | List sessions |
| GET/DELETE | `/api/sessions/{id}` | Not built | Get/delete session |
| GET/PUT | `/api/skills` | Not built | List/toggle skills |
| GET | `/api/reviews/pending` | Not built | Pending human reviews |
| GET | `/api/audit` | Not built | Query audit log |

**Auth:** Simple `X-User-ID` header. Real auth planned for multi-user/Linux VM.

**Response envelope:** `{"status": "ok|error", "data": {...}, "errors": [...]}`

---

## Data Models

### LegalChunk (ingest/chunk_models.py)

```python
chunk_id, doc_id, doc_title, doc_filename, doc_type, client_id,
jurisdiction, sensitivity, section, section_number, clause,
clause_number, clause_type, section_display, text, char_count,
chunk_index, chunk_strategy, last_updated
```

### LegalAgentState (graph/state.py)

```python
request, user_id, uploaded_docs, task_type, skill_plan,
retrieval_query, retrieved_chunks, filters, messages,
llm_response, risk_level, risk_flags, awaiting_review,
attorney_notes, report, session_id, checkpoint_ref, trace_id
```

---

## Docker Services (docker-compose.yml)

| Service | Port | Purpose |
|---|---|---|
| **qdrant** | 6333, 6334 | Vector database |
| **redis** | 6379 | Session checkpointer + Langfuse cache (shared, password: `myredissecret`) |
| **langfuse-web** | 3000 | Agent tracing UI |
| **langfuse-worker** | 3030 | Background trace processing |
| **postgres** | 5433 | Langfuse transactional DB |
| **clickhouse** | 8123, 9000 | Langfuse analytics |
| **minio** | 9090, 9091 | Langfuse blob storage |

**Running natively (not Docker):** Ollama (:11434), llama-cpp reranker, Phoenix (:6006), FastAPI (:8000), Chainlit (:8080)

---

## Configuration

All config via `.env` loaded by `pydantic-settings` in `config.py`. Import anywhere: `from config import get_settings`.

Key settings:
- `LLM_MODEL` — Ollama model name (e.g., `qwen3.6:latest`)
- `EMBEDDING_MODEL` — embedding model (e.g., `embeddinggemma:latest`)
- `QDRANT_VECTOR_DIM` — must match embedding model (768 for embeddinggemma)
- `RERANKER_ENABLED` — toggle reranker on/off
- `BM25_ENABLED` — toggle BM25 keyword search
- `SQLITE_PATH` — audit log location (default: `data/legal.db`)

---

## Constraints (Never Violate)

1. **temperature=0.0** on all LLM calls
2. **Every claim must cite a source** — no citation = high risk → escalate
3. **contract_generation and drafting always → human_review** — no exceptions
4. **client_id filter always applied** on retrieval — no cross-client data leakage
5. **Every skill invocation → SQLite audit log** before returning
6. **No external network calls** — fully air-gapped

---

## Test Data

**CUAD dataset** (510 commercial contracts, CC BY 4.0) cloned at `data/cuad/`. PDFs organized by contract type:
```
data/cuad/CUAD_v1/full_contract_pdf/Part_I/
├── Affiliate_Agreements/
├── License_Agreements/
├── Service/
├── ... (22 contract types)
```

Ingest with: `python scripts/ingest_all.py data/cuad/CUAD_v1/full_contract_pdf/Part_I/Service/ --client-id test-client --doc-type contract`

---

## Tests

64 tests across 13 files, run in ~1 second:

```bash
source .venv/bin/activate && python -m pytest tests/ -v
```

| File | Tests | What |
|---|---|---|
| test_config.py | 2 | Config loading from env |
| test_chunk_models.py | 3 | LegalChunk model |
| test_embeddings.py | 2 | Ollama embedding calls |
| test_vector_store.py | 3 | Qdrant operations |
| test_bm25.py | 3 | BM25 index add/search/persist |
| test_hybrid_search.py | 2 | RRF fusion |
| test_reranker.py | 3 | Reranker with fallback |
| test_parsers.py | 3 | DOCX + PDF parsers |
| test_pipeline.py | 1 | Ingest pipeline |
| test_nodes.py | 18 | All shared node implementations |
| test_graph.py | 6 | Graph compilation + flow + audit |
| test_skills.py | 8 | All 5 skills (mocked agents) |
| test_api.py | 7 | FastAPI endpoints |
| **Total** | **64** | |

---

## What's Not Built Yet

| Feature | Status | Notes |
|---|---|---|
| Redis checkpointer wired to graph | Not done | `build_graph(checkpointer=...)` ready, needs Redis integration |
| Resume after interrupt (`POST /api/query/{id}/resume`) | Placeholder | Needs checkpointer |
| WebSocket streaming | Not done | Deferred — polling via status endpoint for now |
| Admin API endpoints (sessions, skills, reviews, audit) | Not done | Deferred to when Chainlit needs them |
| Long-term memory (Qdrant `memory` collection) | Stub | Implement when usage patterns emerge |
| Langfuse trace wrapping per node | Not done | `init_observability()` ready, trace decorators not wired |
| Real authentication | Not done | Simple `X-User-ID` header for now |
| MCP tools integration | Not done | Architecture supports it — tools plug into agent registries |
| DOCX output generation (docx_path in schemas) | Not done | Skills return text, no file generation yet |
| Policy chunking strategy verification | Not done | TBD — test clause-based vs section-based |
