# Legal Plugin — Project Wiki

> Last updated: 2026-05-21 | 121 tests passing | Python 3.12 | Redis checkpointer active

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

# Or start individually with auto-reload for development:
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
chainlit run clients/web/app.py --port 8080 --host 0.0.0.0 -w

# Access points:
#   Frontend:  http://localhost:8080  (Chainlit chat UI)
#   Backend:   http://localhost:8000  (FastAPI API)
#   API docs:  http://localhost:8000/docs
#   Langfuse:  http://localhost:3000  (admin@local.dev / admin1234)
```

### Prerequisites

- Python 3.12 (venv managed via `uv venv .venv --python 3.12`)
- Docker + Docker Compose
- Ollama running natively with models: `qwen3.6:latest`, `embeddinggemma:latest`
- llama-cpp reranker (optional, graceful fallback when unavailable)

### Demo Data

5 synthetic service agreements with clause-level variation are available:

```bash
python scripts/generate_demo_contracts.py   # saves to data/demo_contracts/ + ingests to case_history
```

CUAD dataset (510 real contracts) also available at `data/cuad/`.

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
├── clients/                    # Client surfaces (one per delivery channel)
│   ├── web/                    # Chainlit web client
│   │   ├── app.py              # Chat, file upload, side panel, human review, PDF export
│   │   └── api_client.py       # Async HTTP client to backend
│   └── word/                   # Microsoft Word add-in (task pane, Office.js)
│       └── ...                 # manifest.xml, Vite + React/TS, see clients/word/README.md
│
├── graph/                      # LangGraph supervisor graph
│   ├── state.py                # LegalAgentState TypedDict
│   ├── graph.py                # StateGraph wiring — all nodes + edges
│   └── nodes/                  # Shared processing nodes (all @observe traced)
│       ├── intake.py           # Resolve client_id, set filters, set Langfuse trace metadata
│       ├── intent_router.py    # LLM classifies task_type, logs prompt to Langfuse
│       ├── planner.py          # LLM decomposes multi-skill requests
│       ├── skill_dispatcher.py # Routes to correct skill node
│       ├── rag_retriever.py    # Calls hybrid_search (skips if agent skill already ran)
│       ├── llm_caller.py       # Calls Ollama with context, logs prompt+response to Langfuse
│       ├── risk_assessor.py    # Citation check + risk level
│       ├── human_review.py     # LangGraph interrupt() for blocking review
│       ├── output_formatter.py # Builds structured report dict
│       └── memory_writer.py    # Writes SQLite audit log
│
├── skills/                     # Legal capabilities — each in its own folder
│   ├── base.py                 # load_skill_prompt() with ceiling constraint
│   ├── schemas.py              # Output schemas (GeneratedContract, etc.)
│   ├── contract_generation/    # ReAct agent — searches + generates contracts
│   │   ├── contract_generation.py
│   │   └── SKILL.md            # Playbook — editable by legal team
│   ├── contract_review/        # Clause analysis with uploaded contract support
│   │   ├── contract_review.py
│   │   └── SKILL.md            # Playbook — editable by legal team
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
│   └── langfuse.py             # init_observability() — sets env vars for @observe
│
├── scripts/                    # Utility scripts
│   ├── create_collections.py   # Create 3 Qdrant collections
│   ├── generate_demo_contracts.py  # Generate + ingest 5 synthetic contracts
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

## Skills Architecture

### SKILL.md Pattern

Each skill lives in its own folder with two files:
- `skill_name.py` — the Python function
- `SKILL.md` — the playbook, editable by the legal team

The playbook is loaded via `load_skill_prompt()` from `skills/base.py`, which wraps it with a **ceiling constraint**: the LLM will ONLY follow the playbook instructions, never improvise from its pretraining knowledge. If the playbook doesn't cover something, the LLM says "Not covered by current playbook" and stops.

This means: **the legal team controls exactly what the LLM outputs by editing SKILL.md**. No code changes needed.

### Skill Types

**Agent Subgraphs** — have their own tools, handle retrieval and LLM calls internally:

| Skill | Tools | What it does |
|---|---|---|
| **contract_generation** | search_legal, get_document, extract_clauses, escalate | Searches case history for clause patterns, generates contracts with deviation report. Always → human_review. |
| **legal_research** | search_legal, get_document, escalate | Multi-hop retrieval to answer legal questions. Cites sources, flags conflicts. |

**Plain Skills** — set a system prompt, then the shared `rag_retriever` + `llm_caller` nodes handle retrieval and generation:

| Skill | What it does |
|---|---|
| **contract_review** | Clause-by-clause analysis with GREEN/YELLOW/RED severity. Supports uploaded contract files (PDF/DOCX/TXT). |
| **compliance_check** | Policy verification prompt → shared nodes. |
| **drafting** | Document generation prompt → shared nodes. Always → human_review. |

### Agent vs Plain Skill — Graph Behavior

For **agent skills** (contract_generation, legal_research): the skill function calls the LLM and tools itself, sets `llm_response`. The shared `rag_retriever` and `llm_caller` nodes detect this and skip.

For **plain skills** (contract_review, compliance_check, drafting): the skill function sets `messages` (system prompt from SKILL.md + user request). The shared nodes then do retrieval and call the LLM with those messages.

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
         │        Sets Langfuse trace metadata (user_id, session_id)
         ▼
  ┌──────────────┐
  │ INTENT_ROUTER│  Calls Ollama to classify task_type:
  │              │  contract_generation | contract_review | compliance
  └──────┬───────┘  research | drafting
         │          Falls back to "research" if LLM unavailable
         │          Logs prompt + classification to Langfuse
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
   │  SKILL NODE    │  One of 5 (see Skills Architecture above)
   └───────┬────────┘
           │
           ▼
    ┌──────────────┐
    │ RAG_RETRIEVER│  hybrid_search() with client_id filter
    │              │  Skips if agent skill already set llm_response
    └──────┬───────┘
           │
           ▼
     ┌───────────┐
     │ LLM_CALLER│  Ollama /api/chat, temperature=0.0
     │           │  Uses skill-provided messages or default prompt
     └─────┬─────┘  Skips if agent skill already set llm_response
           │        Logs full prompt + response to Langfuse
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
│ interrupt()│ │                 │
└─────┬──────┘ └────────┬────────┘
      │                 │
   route_review:        │
   loop_back ─→ skill_dispatcher (revise with previous_draft + notes)
   terminal  ─→ output_formatter
                        │
                        ▼
              ┌─────────────────┐
              │ HISTORY_APPENDER│  Appends turn to chat_history (capped)
              └────────┬────────┘
                       ▼
                ┌──────────────┐
                │MEMORY_WRITER │  Writes to SQLite audit_log table
                └──────┬───────┘  Every invocation, no exceptions
                       │
                       ▼
                      END → Response to attorney (or interrupt_payload if paused)
```

### Routing Rules

| Condition | Route |
|---|---|
| `task_type` = contract_generation | **always** → human_review |
| `task_type` = drafting | **always** → human_review |
| `risk_level` = high or medium | → human_review |
| `risk_level` = low | → output_formatter (skip review) |
| `skill_plan` has multiple skills | → planner first |

### human_review verdicts (after resume)

`route_review` reads state after `human_review` returns and decides whether to exit or loop:

| Resume payload | What `human_review` does | Where `route_review` routes |
|---|---|---|
| `approved=True` | Saves notes, clears `awaiting_review` | → output_formatter (terminal approve) |
| `revised_response` non-empty | Replaces `llm_response` with revised text | → output_formatter (terminal revise) |
| `notes` only + `iter < MAX_REVIEW_ITERATIONS` | Stashes `llm_response` into `previous_draft`, clears `llm_response`/chunks/messages, `iter += 1` | → skill_dispatcher (loop-back) |
| `notes` only + `iter ≥ MAX_REVIEW_ITERATIONS` | Sets `report_notes_unincorporated = notes`; clears `awaiting_review` | → output_formatter (terminal cap-hit) |
| `approved=False`, no notes (pure reject) | Clears `awaiting_review` | → output_formatter (terminal reject) |

On loop-back, the contract skill detects `previous_draft + attorney_notes` and runs a single direct LLM call (`reasoning=False`) instead of the full ReAct agent — much faster.

---

## Chainlit Frontend

### Chat Features
- Send legal queries, get responses with task type and risk level
- **File upload with message** → review/analyze the uploaded contract (PDF/DOCX/TXT)
- **File upload without message** → ingest into Qdrant knowledge base
- Contract generation/review results shown in **side panel** (click attachment to view)
- Each result gets a unique filename — all previous results remain clickable
- **PDF export** — approve a contract draft and download as PDF

### Human-in-the-Loop
- Contract generation and drafting always show review buttons
- Three actions: **Approve Draft** (generates PDF), **Request Changes**, **Reject**
- **Request Changes** loops back: skill revises previous draft using attorney notes (single-shot LLM call with `reasoning=False`, not the full ReAct agent — ~5x faster), then re-pauses for review. Capped at 3 iterations; unincorporated notes attached to final report on cap.
- Uses `cl.AskActionMessage` for per-iteration review prompts so buttons stay live across loop-backs (Chainlit disables same-named `cl.Action` after click — `AskActionMessage` blocks inline with fresh buttons each round).
- Session state persisted via RedisSaver checkpointer with 24h TTL refresh on every interaction.

### Development Mode
- Web client: `chainlit run clients/web/app.py -w` (auto-reload on file changes)
- Backend: `uvicorn api.main:app --reload` (auto-reload on file changes)

---

## Observability

### Langfuse Tracing (http://localhost:3000)

All graph nodes are instrumented with `@observe` from the Langfuse SDK. Every request creates a trace with:
- **Nested spans** for each graph node (intake → intent_router → skill → rag_retriever → llm_caller → ...)
- **Prompt + response logging** on LLM calls (intent_router, llm_caller)
- **User and session tracking** (user_id, session_id from the request)
- **Task type tags** for filtering in the Langfuse UI
- **Model metadata** (model name, temperature, chunk count)

No LangChain dependency — uses Langfuse native Python SDK (`@observe` decorator + `langfuse_context`).

### SQLite Audit Log (data/legal.db)

Every skill invocation writes to `audit_log` table: timestamp, session_id, user_id, skill_name, task_type, request_summary, risk_level, review_status, duration_ms.

### Phoenix (http://localhost:6006)

RAG evals — shared with compliance-bot. Already running, no setup needed.

**Rule: Langfuse = agent traces. Phoenix = RAG evals. Don't mix.**

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
| `case_history` | Past signed contracts (5 demo + CUAD) | Clause-level (one chunk per clause) |
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
| POST | `/api/query` | **Working** | Submit legal request → graph execution (supports `uploaded_text` for review). Returns `interrupt_payload` if graph pauses at `human_review`. |
| POST | `/api/query/{session_id}/resume` | **Working** | Resume after human review with `{approved, notes, revised_response}`. Iterates loop-back (request changes) until approve/reject/cap. |
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
attorney_notes, report, session_id, checkpoint_ref, trace_id,
chat_history,                  # within-session memory (capped at 2N entries via idempotent reducer)
review_iterations,             # number of loop-backs from human_review (capped by MAX_REVIEW_ITERATIONS)
report_notes_unincorporated,   # attorney notes that couldn't be applied (set on iteration cap)
previous_draft                 # llm_response from prior iteration, preserved across loop-back for revise-not-regenerate
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
- `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` — pre-seeded: `pk-lf-local` / `sk-lf-local`

---

## Constraints (Never Violate)

1. **temperature=0.0** on all LLM calls
2. **SKILL.md is the ceiling** — LLM strictly follows the playbook, no improvising from pretraining
3. **Every claim must cite a source** — no citation = high risk → escalate
4. **contract_generation and drafting always → human_review** — no exceptions
5. **client_id filter always applied** on retrieval — no cross-client data leakage
6. **Every skill invocation → SQLite audit log** before returning
7. **No external network calls** — fully air-gapped

---

## Tests

121 tests across 17 files, run in ~2 seconds:

```bash
source .venv/bin/activate && python -m pytest tests/ -v
```

Coverage includes: graph compilation + routing + audit, end-to-end interrupt/resume/loop/cap with `MemorySaver`, Redis checkpointer factory, FastAPI endpoints (submit + resume + interrupt detection via `__interrupt__` key), all five skills with mocked agents, RAG layer (embeddings, vector store, BM25, hybrid search, reranker), DOCX/PDF parsers, ingest pipeline, and config loading.

---

## Shipped Since Last Update (2026-05-15)

| Feature | Commit / Branch | Notes |
|---|---|---|
| Within-session conversation memory | `feat/within-session-memory` merged 2026-05-20 | `chat_history` field with idempotent reducer; N=5 turns / trim=300 chars; RedisSaver-backed |
| Redis checkpointer wired to graph | shipped with memory feature | `redis/redis-stack-server` (RediSearch + ReJSON) with auth |
| Resume after interrupt | `feat/resume-after-interrupt` merged 2026-05-21 | 4-way verdict (approve/revise/loop/cap), `Command(resume=...)`, 24h TTL refresh |
| Iterative review loop | shipped with resume | Up to 3 attorney-notes iterations; revise-not-regenerate via `previous_draft`; cap surfaces unincorporated notes |
| Revision speedup | shipped with resume | Loop-back uses direct ChatOllama call with `reasoning=False` instead of full ReAct agent |
| PDF download on approve | shipped with resume | `cl.File` element attached to final report |
| Chainlit review buttons | shipped with resume | Migrated `cl.action_callback` → `cl.AskActionMessage` to dodge Chainlit's same-name disable on loop-back |

---

## Follow-ups / Roadmap

| Feature | Priority | Notes |
|---|---|---|
| **Microsoft Word add-in** | **High** | Embed the resume/interrupt review flow as a Word task pane. Attorneys see drafts inline in Word with Approve / Request Changes / Reject inside the document. This is the strategic next surface — Chainlit is for demos. Needs: Office.js add-in scaffold, OAuth handshake, render draft into Word body via OOXML, surface `/api/query/{id}/resume` from the task pane. Resume-after-interrupt was prioritized precisely because it is the load-bearing piece for this. |
| WebSocket / SSE streaming | Medium | LLM responses arrive all at once; would improve perceived latency on long generations |
| Admin API endpoints (sessions, skills, reviews, audit) | Medium | Deferred until a UI consumes them |
| Long-term memory (Qdrant `memory` collection) | Medium | Cross-session attorney preferences; implement when usage patterns emerge |
| DOCX output generation | Medium | PDF works; DOCX needed for Word add-in round-tripping |
| Real authentication | Medium | Simple `X-User-ID` header today; needed before multi-user Linux VM |
| Remaining skills as folders | Low | `compliance_check`, `legal_research`, `drafting` still flat files |
| MCP tools integration | Low | Architecture supports it — tools plug into agent registries |
| Langfuse prompt versioning | Low | Prompts logged per-call; formal versioning not set up |
| Policy chunking strategy verification | Low | TBD — test clause-based vs section-based |
