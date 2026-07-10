# Legal Plugin — Project Wiki

> Last updated: 2026-07-10 | 271 tests + 154 frontend asserts passing | Python 3.12 | Redis checkpointer active | Word add-in shipped (chat-driven edits) | Trinetix legal-team playbook integrated | Smoke-tested on real MSA / SOW / BAA | Main-flow audit + bug fixes #1/#2/#3/#4/#5 + placeholder-recall Layer 1 + Langfuse GENERATION spans & token usage + SOW-vs-MSA review (see docs/output_format_conflict.md) + chat memory & grounding + Word add-in click-to-jump & findings filter/sort + clause-locator hardening

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
- **GENERATION observations with token usage + model** on every LLM call. The httpx nodes (`llm_caller`, `intent_router`, `planner`) mark their span `as_type="generation"` and attach Ollama's `prompt_eval_count`/`eval_count` (via `observability/tracing.py::ollama_usage`). The skill LLM calls — the Word chat-tab direct `ChatOllama` (`legal_research._run_doc_chat`), its `format='json'` retry, and both ReAct agents — run inside `traced_invoke`/`traced_agent_invoke`, which record a nested GENERATION with usage read from the returned `AIMessage.usage_metadata`. These were previously **invisible**.
- **Skill spans** — all five skills (`legal_research`, `contract_generation`, `contract_review`, `drafting`, `compliance_check`) carry their own `@observe` span (`capture_input/output=False` so the large state isn't dumped).
- **User and session tracking** (user_id, session_id from the request)
- **Task type tags** for filtering in the Langfuse UI
- **Model metadata** (model name, temperature, chunk count)

No LangChain dependency — uses Langfuse native Python SDK (`@observe` decorator + `langfuse_context`). The SDK is **v2** (`langfuse>=2.0,<3.0`); it reports into the **v3 Langfuse server** (`langfuse/langfuse:3`) over the backward-compatible ingestion API. Token **cost** stays null for local Ollama models unless a model price is registered in the Langfuse UI (Settings → Models). NB: Langfuse's *LangChain* callback handler is **not** usable here — it imports the full `langchain` package, which isn't a dependency (only `langchain-core`/`langchain-ollama`) — hence the manual `traced_invoke` wrapping.

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
| `clients/` regrouping | shipped with Word add-in | `frontend/` → `clients/web/`; new client surfaces land as sibling dirs under `clients/` |
| **Word add-in MVP (Iteration 1)** | `feat/word-addin-triage` | React + TS + Vite, sideloaded into Word for Mac via the `wef` folder. Reads doc body via Office.js → `POST /api/query` (`task_type=contract_review`, `uploaded_text=body`) → renders per-clause cards with severity badges, current text, suggested redline, rationale. Zero backend changes — parses the existing `contract_review` markdown via regex. |
| **Word add-in interactive assistant (Iteration 2)** | shipped with add-in | Each card gets "Show in document" (scrolls + Word Comment) and "Accept redline" (Track Changes replace). Multi-paragraph clauses handled via head + tail snippet search and `range.expandTo()`. New Chat tab uses the same `session_id` so `chat_history` carries the prior `contract_review` forward into Q&A turns. `legal_research` patched to read `uploaded_docs` and respond conversationally (no structured headers) when a doc is attached. Tabs stay mounted with `display:none` toggling so state persists across switches. |
| **Word add-in: chat-driven document edits (Iteration 3)** | `feat/word-addin-chat-edits` | Chat is now a drafting partner. When the lawyer asks for a change, `legal_research` emits fenced ```json``` edit proposals (`replace`/`insert`/`delete`) alongside its prose; the pane renders a preview card per proposal with an editable "after" textarea and **Apply with Track Changes**. `risk_assessor` no longer flags "no chunks" as high-risk when a doc is attached (that spurious interrupt was dropping chat output). Matching hardened: shorter word-prefix + clean-prefix search candidates, match-boundary ranges (fragment rewrites replace exactly the fragment), `insertParagraph`-based inserts. Light styling polish (Office `#0078D4`, Segoe UI, focus rings). |
| **Trinetix legal-team playbook integration (Phase 4)** | `feat/playbook-integration` | `contract_review` is now powered by the legal team's real materials: `Trinetix_Contract_Playbook_2026.docx` (11 tables) + per-type `SKILL.md` (NDA / MSA / SOW / BAA) + shared `references/`. `scripts/build_playbook.py` parses the .docx → `skills/contract_review/playbook/{global,nda,msa,sow,baa}/` markdown (committed for audit; idempotent on re-run). `skills/base.py::load_bundle()` concatenates the bundle in deterministic `BUNDLE_ORDER` (role → principles → risk rating → approval matrix → output format → AI procedure → external comments → catalogue → per-type rules → No-Signature Gate last). The skill detects contract type from heading keywords and stores it on `state["contract_type_detected"]`. Output format switches to the team's required tables (Review Summary + Key Findings + Red and Missing Context + Suggested Redlines + Business Questions + **No Signature Checklist Result**). Word add-in parser rewritten for GFM tables; FindingsTab renders a No-Signature Gate banner (green if "Signature may proceed", red if "DO NOT SEND FOR SIGNATURE") + per-finding owner / required-action / issueId; Blockers card derived as a strict subset of Key Findings (rating ∈ {Red, Missing Context}) + collapsible Business Questions section. See `docs/playbook_cross_reference.md`. |
| **Word add-in chat reliability + `replace_all` action** | shipped with playbook integration | Hardened the chat-driven edit pipeline against local-LLM quirks: tolerant JSON parser (escapes raw `\n` / `\t` inside JSON string values), accepts arrays inside a single fenced block, Ollama `format='json'` mode for the retry path, past-tense edit-promise detector, yellow safety-net warning when prose promises an edit without a block, raw-response viewer in the chat tab for diagnosing LLM output. New `replace_all` edit action: one block, snapshot all matches via `body.search` BEFORE any modification, iterate the snapshot — bypasses the Track Changes "deletion-marked original still visible to body.search" trap that broke earlier loops. `simplifyMultilineReplace` collapses `target/new` pairs that differ on one line down to a clean single-line replace. Match-completeness threshold (85%) refuses partial-prefix replacements that would inject `new_text` into a short prefix. `legal_research` for doc-attached chats switched from ReAct agent to direct ChatOllama call — multi-minute → ~15-30 s per turn on local Ollama. |
| **Main-flow audit + Fix #1: contract-type detection** | `fix/contract-type-detection` merged `ad177c0` | Full audit of graph flow / skill classification / system-prompt duplication / data-caching / output quality / Langfuse — real-trace case studies in the plan file; conflict report in `docs/output_format_conflict.md`. **Fix #1:** `_detect_contract_type` scored keyword hits over only the first 4000 chars, flat-weighted — a real MSA scored SOW (14 vs 12 in-window) and loaded the SOW playbook silently. Now weights the title region (first ~200 chars) ×100 and scans the whole doc as a tiebreak → MSA 469 vs SOW 154. Also tags `contract_type_detected` on the Langfuse trace (detection was previously invisible — the skill has no `@observe` span). |
| **Fix #2: output-format conflict (Option A)** | `fix/output-format-conflict` | The bundle handed the model three competing output specs: `output_format.md` (the endorsed 7-section format) plus the `.docx` §10.2/10.3/10.4 "AI output schema" blocks (issue-list / executive-summary / SOW-readiness). Source audit confirmed parsing is faithful and the conflict is a **source-document ambiguity** (operating rules + every `SKILL.md` + every `test_prompt.md` endorse the 7-section format; the §10 schemas are unendorsed). `build_playbook.py::_render_ai_review_procedure` now stops at the first "AI output schema" subsection (keeps §10.1 behavior rules); the regenerated bundle carries a single output spec. Rollback steps + the open legal-team source fix are recorded in `docs/output_format_conflict.md`. |
| **Fix #3: system-prompt noise cleanup** | `fix/prompt-noise-cleanup` | Two prompt-noise removals, source untouched. (1) **Build-notice artifacts:** every bundle file's `<!-- GENERATED by … -->` header was leaking into the assembled prompt (~24 comment lines/review); `skills/base.py::_clean_for_prompt` now strips leading HTML comments at assembly time — the files on disk keep the notice for humans. (2) **Dangling `references/*.md` pointers:** each per-type `SKILL.md` told the LLM (which has no file access) to "Use `references/shared_operating_rules.md` / `references/no_signature_checklist.md`" — but that content is already inline. `build_playbook.py::_rewrite_reference_pointers` rewrites the 3 pointers to "…in this playbook" while preserving intent. Assembled prompt now has zero `<!--` / `references/`. Rollback inline in both files. |
| **Fix #4: verdict-aware safety gate (Option A)** | `fix/review-verdict-gate` | The human-review gate keyed on the **wrong signal**: `risk_assessor` set `risk_level` purely from citation grounding, so a contract review (doc uploaded → no citation flags) always scored `low` and `route_risk` skipped `human_review` — even when the verdict was **"Do not send for signature."** Now `risk_assessor` branches on `task_type=="contract_review"` and reads the **verdict** via `_assess_review_verdict` (high if "do not send for signature" OR any Red / Missing Context finding; medium on Yellow; low when clean; high if empty) — detection mirrors `parser.ts` so server + client reconcile. Sets `report.requires_attorney` for **both** clients. The `interrupt()` only fires for resume-capable callers: new `interactive_review` request flag (Chainlit sends `True`; Word leaves `False`) gates the contract-review interrupt — Word has no resume UI and shares one `session_id` across review + chat, so an interrupt there would strand the session. Research/generation/drafting routing unchanged. Adds a Langfuse trace tag (`review_risk_level`, `requires_attorney`). +17 tests. |
| **Placeholder / signature-block recall — Layer 1 (prompt cue)** | `fix/placeholder-recall-cue` | The review intermittently dropped unfilled signature-block placeholders (`[__]`). Root-caused with a **faithful A/B**: reused trace `01d4e2ae`'s exact captured prompt + contract, varied a single thing, called ChatOllama at temp 0. Restoring the §10.2–10.4 block that **Fix #2 deleted** deterministically flipped the signature block from a passing **prose** mention to a structured **Missing-Context blocker** — `DELETED→PROSE ×6`, `RESTORED→FULL ×6`, zero crossover, byte-identical per arm. The originally-guessed cause (two-column block masking the empty client column) was **disproved** — the identical block is caught in a shorter NDA and missed in the 129KB MSA (`f4b383f0`), so it's document length + LLM recall variance, not structure. **Real cause:** Fix #2 correctly removed competing output *schemas* but discarded the only completeness *cues* riding in them — §10.2 `Current wording / issue:` and §10.4 `Open placeholders:`. Layer 1 re-injects the **cue, not the schema**, in the tracked `build_playbook.py` (the canonical source under `data/` is gitignored, so the build script is the only reproducible seam — same place Fix #2 made its change): a placeholder / blank-field / unfilled-signature-block bullet in the No-Signature gate's **Automatic blockers** list (highest-attention end of prompt, where it was absent) + a "Completeness and current wording" instruction in `output_format.md` (quote current wording per finding; raise every placeholder as its own Missing-Context row). Validated against the **real shipped bundle** (`load_bundle("nda")` minus the cue is byte-identical to the deleted arm → single variable) → `LAYER1→FULL ×3` deterministic, and **live-smoke-tested across all four contract types (NDA / MSA / SOW / BAA) — now finds blank signature blocks**. +2 build tests (200 total). Toggle for validation/rollback: `PLAYBOOK_PLACEHOLDER_CUE=0 python scripts/build_playbook.py` regenerates the pre-Layer-1 "first variant" prompt; default rebuild restores it. **Still LLM-dependent (a stronger instruction, not a hard guarantee)** — Layer 2 (deterministic scan) remains the floor for the long-doc / recall-variance tail; see follow-up. |
| **Fix #5: faithful document extraction (review/upload path)** | `fix/faithful-docx-extract` | Chainlit's contract-review upload path called the **RAG chunker** (`parse_docx`/`parse_pdf`) then rejoined the chunk text — inheriting the chunker's lossy heuristics. Proven on a real NDA: `parse_docx` mis-classified numbered clause bodies as section headings and **silently dropped 4 substantive clauses** (the Confidential-Information exclusions list, an indemnification sentence, the IN WITNESS execution block) — 89% coverage. That's why the same file reviewed in Chainlit (lossy) vs the Word add-in (Office.js `body.getText()`, faithful) produced contradictory findings. New `ingest/parsers/plain_text.py` provides lossless, order-preserving extractors (`extract_docx_text` = every paragraph + table; `extract_pdf_text` via pdfplumber; `extract_document_text` dispatches by suffix). `clients/web/app.py::_extract_file_text` now uses it → 100% coverage, matching Word. **The RAG ingestion chunker (`ingest/pipeline.py` → `/api/ingest`) is deliberately untouched** — it is correctly tuned for the policy corpus (52-file compliance set: ~96% mean coverage, numbered items are short headings). +7 tests. (RAG-side chunker fix tracked as a follow-up.) |
| **Langfuse GENERATION spans + per-call token usage + skill spans** | `feat/langfuse-generation-spans` | Prereq for the multi-LLM eval: traces had **no token / cost / model** (every node used a bare `@observe`, i.e. SPAN-type), skills had **no span**, and the Word chat-tab LLM call + both ReAct agents were **invisible**. Two mechanisms matching the two call styles, both via the native v2 SDK (no LangChain handler — that needs the full `langchain` package, absent here). (1) The httpx nodes (`llm_caller`/`intent_router`/`planner`) now mark `@observe(as_type="generation")` and attach Ollama's `prompt_eval_count`/`eval_count` as usage via `observability/tracing.py::ollama_usage` (planner previously recorded no observation at all). (2) The five skills get their own `@observe` span (`capture_input/output=False`), and the skill LLM calls run inside `traced_invoke`/`traced_agent_invoke` — an `@observe(as_type="generation")` wrapper that reads token usage from the returned `AIMessage.usage_metadata` (populated by `langchain-ollama` from Ollama's eval counts). The previously-invisible doc-chat call now shows as a nested GENERATION with tokens + model. v2 SDK reports cleanly into the v3 Langfuse server (`langfuse/langfuse:3`). +11 tests (211 total). Cost stays null until a per-model price is registered in the Langfuse UI (follow-up). |
| **Word add-in: tracked-change lifecycle (see-through / communicate / finalize)** | `feat/tracked-change-lifecycle` | Three fixes to the redline lifecycle, found while auditing chat-over-document. (1) **Extraction fix (`13f35da`):** `readBody()` returned `body.text`, which includes tracked-change *deletions* — a placeholder filled via redline (`[__]`→`Suzy Quatro`) still carried the struck `[__]`, so re-review/chat flagged already-filled fields as unfilled (false "DO NOT SEND" blockers; proven by traces `5f188799`→`3b7624b3`, where date/title/signed-by flipped from "missing" to correctly-filled and only genuinely-blank fields remained). Switched to `body.getReviewedText("current")` (text as if all changes accepted; WordApi 1.4, cross-OS; with no changes equals `body.text`, safe drop-in). Covers **both** review (FindingsTab) and chat (ChatTab) — both call `readBody`. `body.search` still matches the raw doc, so redline matching is unaffected. (2) **Lifecycle clarity (`65b1619`):** the applied edit card now states the change is a tracked change that is NOT final and persists through Save until accepted/rejected in Word's Review tab (was a terse "Applied — see Track Changes"). (3) **Finalize → clean copy:** new document-level footer (`FinalizeBar`, shows on both tabs) → `finalizeDocument()` = `body.getTrackedChanges().acceptAll()` + `changeTrackingMode=off`, **in place**, behind an in-pane confirm; the user then does Word's File → Save As. In-place (not separate-file export) because Office.js can't reliably save-as a new named file cross-OS; accepts ALL tracked changes, not only the assistant's (add-in edits are attributed to the current Word user, indistinguishable from manual edits by author). Also recorded (Step 0): Office.js has **no API to recolor tracked changes** — green-insertions / red-deletions is a per-reviewer Word display preference, not add-in code. Frontend-only + one Office.js helper; tsc clean, frontend checks green. Headed to legal-team test for feedback (UX/logic may change). |
| **Word add-in: multi-line signature-block fills (MSA/SOW)** | `fix/multiline-signature-fill` | Chat "fill all blank signature blocks" worked on NDA but errored on MSA/SOW with *"Couldn't find the exact target text."* **Root cause:** MSA/SOW have multiple signature blocks, so the LLM collapses each into ONE `replace` whose target is three `label: [__]` lines, all differing (`Signed by: [__]\nTitle: [__]\nfor and on behalf of [__]`). That target is unapplyable — `body.search` can't cross paragraph breaks, so only the first line matches (~15 chars vs ~52 intended) and the 85% completeness guard correctly refuses; it would also only ever fill the FIRST block. NDA worked only because it has one block (matchable per-field edits). Proven by traces `02e41ead` (MSA) / `ce45b899` (SOW). **Fix (parser, model-neutral):** `normalizeProposals` in [parseEditBlocks.ts](clients/word/src/parseEditBlocks.ts) (a) **splits** a multi-line `replace` whose changed lines are ALL labeled blanks into one `replace_all` per field, and (b) **collapses** the LLM's duplicate per-block cards (main + appendix) into one fill-every `replace_all` — `replace_all` because the blanks recur across blocks and `replaceAll` snapshots all matches in one pass (no struck-text re-find). The result is 3 clean `REPLACE ALL` cards, each filling both blocks' blank column and leaving filled columns untouched. **Key gotcha:** the first attempt lived only inside `extractEditBlocks` and had **zero effect** — [ChatTab.tsx](clients/word/src/components/ChatTab.tsx) prefers the BACKEND's `proposed_edits` over the frontend's extraction when non-empty (the common case), bypassing the transform. `normalizeProposals` now runs on the FINAL chosen list in ChatTab (idempotent). +8 frontend assertions (51 total in `parseEditBlocks.test.ts`); tsc clean; live-smoke-confirmed on MSA & SOW. |
| **SOW reviewed against its governing MSA** | `feat/sow-vs-msa-review` | When `contract_review` detects a **SOW**, it auto-pulls the governing **MSA** from Qdrant and injects it so the SOW is reviewed against its parent — closing a gap where the SOW playbook *required* a "conflicts with MSA" check (`sow/SKILL.md:6/59`, MSA-001) and named the MSA as a required input, but the system never put the MSA in context. **Backend-only, strictly additive** — no Word/API change; non-SOW, no-MSA-on-file, and lookup-error all degrade to the prior standalone review. New `rag/related_docs.py::get_parent_msa(client_id)` scrolls Qdrant (`doc_type="msa"` + `client_id`), sorts chunks by `chunk_index`, returns `(title, full_text)` (picks first by `doc_id` when >1 — one-MSA-per-client demo assumption). `contract_review` (SOW path only) appends a `--- GOVERNING MSA (title) ---` block to the user message (capped at `_MSA_MAX_CHARS=24000`, truncation-marked) + a **structural, model-neutral** `_MSA_COMPARISON_DIRECTIVE` as the last system message — it orchestrates the comparison and defers ALL legal judgment to the playbook (mirrors MSA-001 / SOW:59 precedence rules verbatim; forbids inventing MSA terms; **SKILL.md stays the ceiling**, same category as the existing `_OUTPUT_CONSTRAINTS`). Surfaces `msa_attached`/`msa_doc_title` on the trace. Demo prep: `scripts/ingest_demo_msa.py` ingests the model MSA as `doc_type="msa"` (clears prior MSA chunks first — parsers assign random-UUID doc_ids; fails loudly on a zero-chunk parse). +13 tests (227 total). ⚠️ backend change — `start.sh` restart required; run the ingest script once before smoke-testing. |
| **Word add-in: chat edit scope + signature-rewrite/tab matching** | `fix/chat-signature-fill-scope-and-tabs` | Three follow-on fixes from live signature-block testing. (1) **Scope rule (prompt, model-neutral):** primed by `chat_history` (a prior "fill signatures with John Doe" turn), the LLM volunteered an *unrequested* edit overwriting the already-filled counterparty block (Boris Bukengolts → John Doe) "to ensure consistency" — and admitted going beyond the request (trace `4b24ca1d`). No clean code guard (code can't distinguish a wanted "change Boris to X" from this; "new_text already in doc → drop" would wrongly block the legit multi-block fill). Added a SCOPE rule to `CHAT_SYSTEM_PROMPT` + `_JSON_RETRY_SYSTEM` — do only what's asked, don't mirror a prior value "for consistency", never overwrite a field already holding a real value; "fill" = an EMPTY placeholder. (`chat_history` is *kept* — it's how "Legal name the same we filled recently" resolves.) (2) **Generalized the multi-line split** (`splitMultilineBlankFills` → `splitMultilineFieldEdits`): now splits any multi-line block whose changed lines are structured fields (colon-label OR blank), `replace_all` for blanks / `replace` for specific filled values — so an explicit signatory **rewrite** (Boris→Suzy Quatro, company unchanged) applies as two per-line replaces (trace `32deb028`). Multi-paragraph **prose** (no per-line colon/blank) is left to the head+tail span matcher. (3) **Tab-segment reduction** (`reduceTabSegment`): a `…dotted…\tSigned by: [__]` target that `body.search` can't cross is reduced to the changed column `Signed by: [__]` (trace `9e5b804c`). +13 frontend assertions (64 total) + 1 backend prompt test (214 total); tsc clean; live-smoke-confirmed across fill / effective-date / preamble-edit / signatory-rewrite. ⚠️ the scope rule is a backend change — `start.sh` restart required. |
| **Chat memory & grounding** | `feat/chat-memory-grounding` | The Word Chat tab now persists each completed review to **SQLite** (`memory/review_store.py`) and recalls it in chat — replacing the old 300-char history stub with the real prior review findings. Reviews are keyed to a stable **`document_id`** (`memory/document_id.py`, preamble hash of the uploaded doc — interim; Office.js custom-document-property `document_id` is the durable upgrade). The playbook bundle and the governing MSA are now attached on the **chat path** via the shared `skills/grounding.py` helper (also adopted by `contract_review`, consolidating the two previously-divergent assembly paths); the prompt is assembled **stable-grounding-first / question-last**. **Latency hardening (live-smoke-driven):** heavy grounding is **conditional** (`config.chat_conditional_grounding`) — the playbook+MSA attach only when `_needs_grounding(question)` matches an edit / firm-position / MSA-conflict / clause keyword, so plain factual Q&A stays lean (~10s vs ~30s grounded); and every grounded LLM call now pins `config.ollama_num_ctx` (32768) — without it Ollama's ~4k default silently truncated the ~13.6k-token grounding out of context (middle-drop). (Ollama cross-turn prefix-cache reuse does **not** engage on this local setup even with `OLLAMA_NUM_PARALLEL=1`/`KEEP_ALIVE=-1`, so conditional grounding — not caching — is the real latency lever.) Degraded storage is surfaced loudly: a **startup-absent OR mid-session** Redis failure degrades `/api/query` to a stateless run + `memory_degraded` → an amber Word banner (the turn still answers; grounding/review still load from SQLite/Qdrant); a failed review write surfaces `report["review_persist_error"]` on the Findings tab (never silent). Chat context is capped (`config.chat_context_max_chars`, default 100 000 chars, kept below `ollama_num_ctx`) by truncating the **document**, never the grounding (playbook / MSA / findings). Also strips the prior review's `Suggested Redlines` section from the chat injection so chat doesn't re-propose fills. Verified via live smoke on SOW + NDA (both grounding paths). +44 tests (271 total). ⚠️ backend change — `start.sh` restart required; run `scripts/ingest_demo_msa.py` once if Qdrant was reset. |
| **Word add-in: click-to-jump navigation + findings filter/sort** | `feat/word-addin-quick-ux` | Frontend-only "Bucket A" UX wins (spec + plan in `docs/superpowers/`, sourced from `docs/legal-agent-upgrade-research.md` competitor items #1/#8). New `goToClause()` (`word.ts`) selects a clause **without mutating** the doc; clicking a finding **title** jumps to it, and the old "Show in document" button is renamed **"Comment in doc"** — the audit-trail comment is now opt-in, not fired on every look. Chat proposed-edit cards get a **"Go to"**. New pure `applyFindingFilters()` (`findingFilters.ts`, unit-tested) powers a **filter/sort bar** in `FindingsTab` — severity chips, Blockers-only, owner dropdown, sort by severity/clause, "showing X of Y", reset-on-re-review. Summary chips / gate banner / blockers card / business questions stay **whole-review** (only the finding list filters). No backend/graph/prompt/LLM change → review outputs byte-identical. tsc clean; +9 frontend asserts (133 total pass). **Sideload-smoke-confirmed in Word for Mac (2026-07-09):** filter bar (chips / Blockers-only / owner / sort / "showing X of Y"), click-to-jump, and Comment-in-doc all work. Smoke also surfaced a **pre-existing** clause-locator weakness on placeholder / short anchors — not caused by this change (it reuses the existing locator), tracked as a follow-up. |
| **Word add-in: clause-locator hardening** | `feat/clause-locator-hardening` | `searchFirst` now searches **whole-word-only** for **single-word** anchors via new pure `shouldMatchWholeWord()` (`word.ts`, unit-tested) — `"Title"` no longer matches inside `"entitled"`. (+21 frontend asserts this branch: 7 `word.ts` + 14 `parser.ts`; 154 total project-wide.) **Narrowed to single-word during final review** (approved design was ≤2-word): a single-word query has no space, so Office.js `matchWholeWord` is well-defined; on a space-containing 2-word query it's unverified and can only *hurt* recall (e.g. would stop `"Data Room"` matching `"Data Rooms"`) while buying nothing, so 2+-word anchors keep the tolerant substring match. `searchFirst` is shared by **all** its callers — the locator path (`goToClause`/`showInDocument`/`acceptRedline` via `findClauseRangeFromAnchors`, plus `deleteClause`) **and** `insertNear` — so the narrowing applies to navigation, comment, redline, delete, and insert-anchor lookups alike; behavior is strictly *safer* everywhere (a single-word target/anchor can no longer land mid-word), and redline keeps its 85% completeness guard. Null-range failures now show a calm `NO_MATCH_MESSAGE` ("this finding describes a section rather than quoting it") instead of "Couldn't locate this clause." **Backtick-anchor fix (smoke-driven, 2026-07-10):** signature-block Missing-Context findings wrap their real doc text in backticks (`` `Signed by: [__]` ``, `` `for and on behalf of [__]` ``), which `extractQuoted` (straight/curly only) missed — so 2 of 3 fell through to a clause-name label not in the doc and showed "nothing to locate," while `"Title"` worked only by coincidence (its clause segment equals the doc label). New `extractBacktickQuoted` (`parser.ts`) feeds **only** `buildAnchors` (kept separate so the `` `old` `` → new redline path is unaffected); the backtick literal becomes the primary anchor and `searchFirst`'s wildcard-escape retry locates the `[__]` blank — all three signature fields now locate. **MSA/SOW bundled-block split (smoke-driven, 2026-07-10):** MSA/SOW emit ONE finding whose backtick current wording bundles the whole block on one line joined by `" / "` (`` `Signed by: [__] / Title: [__] / for and on behalf of [__]` ``); that joined string isn't in the doc verbatim (fields on separate paragraphs) so `body.search` can't match it → "nothing to locate" (NDA worked only because it emits one finding per field). New `splitFieldSegments` (`parser.ts`) pushes each `" / "`-separated field segment as its own anchor — guarded to split only on a whitespace-padded slash and only when ≥2 segments look like a field (colon label or blank placeholder), so `"and/or"` and bundled headings aren't split — and the locator lands on the first real field. (Known limitation: MSA's two signature blocks share identical segments, so both findings resolve to the first block — same class as the deferred `[__]` first-occurrence disambiguation.) The wildcard-retry the original follow-up mentioned was **already live** in `searchFirst` — not part of this change. NOTE: the chat `replace_all` locate path (`replaceAll`) does **not** go through `searchFirst` and still matches by substring — separate follow-up if a short `replace_all` needle ever mis-hits. Frontend-only; review outputs byte-identical. Spec: `docs/superpowers/specs/2026-07-09-clause-locator-hardening-design.md`; plan: `docs/superpowers/plans/2026-07-09-clause-locator-hardening.md`. **Sideload-smoke-confirmed in Word for Mac (2026-07-10, NDA + MSA):** `"Title"` no longer lands in `"entitled"`; single-word heading anchors select correctly; 2-word anchors still locate (substring); the calm message shows for truly label-only findings; NDA's three per-field signature findings and MSA's bundled Signature-Block (Main)/(Appendix) findings all locate/comment on the actual `[__]` blank. |

---

## Follow-ups / Roadmap

| Feature | Priority | Notes |
|---|---|---|
| LLM evaluation across models + clause-content tuning | High | Smoke on real Trinetix NDA / MSA / SOW / BAA passed — the team's table format, No-Signature Gate, and per-type bundles all fire correctly. Next: evaluate on multiple LLMs (qwen vs llama vs others), and tune cases where the model misidentifies clause-by-number (e.g. asked to remove "3.4" but removed "Permitted Disclosures" by name match). Work on a separate `tuning/*` branch. **Idea — Langfuse Prompt Management:** register each per-type assembled bundle as a versioned Langfuse prompt and link it on the `llm_caller` GENERATION (`update_current_observation(prompt=...)`, supported in our v2 SDK via `create_prompt`/`get_prompt`). Gives prompt-version ↔ trace ↔ tokens/cost/score comparison in the UI — turns the ad-hoc `/tmp` A/B harnesses (placeholder-cue, `PLAYBOOK_PLACEHOLDER_CUE` toggle) into first-class persisted experiments, and seeds the improvisation-rate / self-improving-harness scoring. **Use as a one-way mirror only:** push from `build_playbook.py` (hash-labeled, idempotent, flag-gated); the legal-team `.docx` + `build_playbook.py` stay canonical — do NOT enable UI editing as a production source (would fork truth vs "SKILL.md is the ceiling"). Wire up when this eval workstream starts (no value until there are prompt variants to compare). |
| Playbook-adherence ("ceiling") violations — improvised findings | Medium | **Observed, not a code bug.** On the post-#5 NDA review (Langfuse `cad3631b-acc9-4c7c-b76e-70907aa18629`) the model raised a Yellow on the indemnity clause — *"broad indemnity… add standard carve-outs (gross negligence / willful misconduct)."* The NDA playbook has **no** carve-out rule; §11 + NDA-006 take the **opposite** stance — a broad receiving-party indemnity is the *preferred* position (it protects Trinetix, the Disclosing Party here), and the red flag is *weak/nominal* liability. The model improvised a generic commercial-contracting concept from pretraining, against the playbook — a "SKILL.md is the ceiling" violation. (An earlier Word run of the same file correctly did **not** flag it — so it's run-to-run, not deterministic.) Levers: (a) sharpen ceiling enforcement so the model doesn't volunteer un-playbooked clause opinions; (b) score "improvisation rate" (findings with no playbook rule ID) as an eval metric. Pairs with the LLM-evaluation item above. |
| Tracked-change lifecycle — enhancements after legal-team test | Medium | The shipped Finalize is **in-place** (accept all + tracking off, then user Save As). If portability becomes a hard requirement (sending to outside counsel who must see green/red regardless of their Word settings), add a **separate-file clean-copy export** that preserves the redline master (`getFileAsync` → download; reliable on Windows/WebView2, fragile in Word for Mac's WKWebView — needs platform handling). Other deferred UX: **in-pane per-edit Accept/Reject** + "Accept all from assistant" (`getTrackedChanges()` exposes `.accept()/.reject()`), a **redline⇄clean preview toggle**, and a **remaining-placeholders** indicator (the yellow `[Legal Name]`/`[Address]` blanks). Revisit wording/placement after legal-team feedback. |
| `chat_history` flows into `contract_review` re-reviews | Low | [graph/nodes/llm_caller.py](graph/nodes/llm_caller.py) injects `chat_history` between the system and user messages, and the Word pane reuses one `session_id` across review + chat. So a **re-review** in the same session carries prior chat/review turns into the prompt. No harm observed (the lingering `[__]` finding traced to a genuinely-unfilled field, not contamination — trace `3b7624b3`), but it could theoretically bias a re-review. If it ever does: scope history out of the `contract_review` path, or reset it on re-review. |
| Reconcile playbook `.docx` §10 (legal team) | High | Proper source fix for the output-format conflict — Fix #2 excludes §10.2/10.3/10.4 at build time as a stopgap. Legal should either mark those schemas internal-reference-only or have §10 defer to the operating-rules output format; then the build-script exclusion can be removed. See `docs/output_format_conflict.md`. |
| Source `SKILL.md` assumes file access (legal team) | Low | The per-type source `SKILL.md` reference `references/*.md` as if readable at runtime; our system inlines everything and the LLM has no file access. Fix #3 rewrites those pointers at build time. If legal updates the source, prefer inline phrasing ("in this playbook") over file paths. |
| System-wide self-improving harness | Medium | Every brittle heuristic (contract-type detection, parser `deriveBlockers` / `pick` column aliases / anchor matching, edit-promise `format=json` retry, tolerant JSON) emits a confidence/disagreement signal into one sink for tuning. Free seed: flag when `contract_type_detected` ≠ the model's own stated "Contract type:" line. |
| Deterministic placeholder / signature-block safety-net — Layer 2 | Medium (deferred) | **Layer 1 shipped + live-validated across all four contract types — this is the remaining *guarantee*.** Cause settled by A/B: the recall gap was Fix #2 collateral signal loss, **not** the two-column masking originally guessed; restoring the cue deterministically recovers structured detection on the NDA (`PROSE→FULL`, 6/6) and finds blank signature blocks on NDA/MSA/SOW/BAA in live smoke. But Layer 1 stays LLM-dependent — it repairs the regression and lifts recall, yet **cannot guarantee** it: the 129KB MSA (`f4b383f0`) missed all 6 placeholders (length-driven), and prose-only runs (`01d4e2ae`) never reach Word's `deriveBlockers` blockers card. Layer 2 = a deterministic post-LLM scan of the faithful `uploaded_text` for unfilled markers (`[__]`, `[Legal Name]`, `[Month] [Date], [Year]`, `[Address]`; **exclude** generated-draft `[Source: doc_id]` tags) that **forces** each into a structured Missing-Context row + "Do not send for signature" (Fix #4 then escalates `risk_level`/`requires_attorney`). Must emit **structured rows**, not just gate prose. Deferred per decision — build when Layer 1's measured recall (esp. on long docs) proves insufficient. A first instance of the self-improving harness. |
| Contract-type override in Word add-in | Low | Detection is now title-weighted + whole-doc (Fix #1), so mis-detection is rare; an override dropdown ("Reviewing as: MSA — change") in the FindingsTab chip is now defense-in-depth rather than a fix for a frequent failure. |
| Word "attorney sign-off required" banner | Low | Fix #4 added `report.requires_attorney` (the authoritative server verdict). Word already renders blockers client-side via `deriveBlockers`, so behavior is covered; a small banner in FindingsTab consuming the new flag would make the verdict explicit. Sideload smoke-test required. |
| Gate generation/drafting interrupt on `interactive_review` | Low | Fix #4 gates only the **contract_review** interrupt on the new capability flag. `contract_generation` / `drafting` still route to `human_review` unconditionally — safe today because those are Chainlit-only flows (Chainlit sets `interactive_review=True`), but a non-interactive caller doing generation would strand the session. Extend the flag-gate to those branches when a second non-interactive client surfaces. |
| RAG chunker: depth-1-numbered clause loss (`parse_docx`) | Medium | The SAME root cause Fix #5 routed around for the review path still affects **RAG ingestion**: `docx_parser._parse`'s `num_depth==1` branch treats any decimal-numbered paragraph as a section heading and discards its body. Mild on the policy corpus (3 long clauses lost across 52 files; numbered items there are short headings) but **mutilates any contract `.docx` ingested via `/api/ingest`**. Fix: only treat a numbered paragraph as a section title when heading-like (bold first run, or ≤~8 words); otherwise preserve the body (as depth-2+ already does). Refactor the decision into a unit-testable `_is_section_heading(para, num_info)` helper. **Must ship behind a regression harness** asserting per-file coverage across the 52 policy docs stays ≥ current, then **re-ingest** the corpus. PDF (`parse_pdf`) has separate section logic — audit separately. |
| Word add-in: playbook-grounded findings (Phase 2) | High | Make `contract_review` emit structured per-finding JSON including the retrieved RAG chunks; pane shows "your firm's standard says X" next to "this contract says Y" with source citations. The real Spellbook differentiator. Now that the team's matrix is in-prompt the LLM can already cite per-clause IDs (NDA-001, MSA-014…); JSON output would let the pane render them as live links. |
| ~~MSA + playbook on chat path~~ | ~~Medium~~ | **DONE** — shipped in `feat/chat-memory-grounding`. `skills/grounding.py` (shared with `contract_review`) attaches the full playbook bundle + governing MSA on every chat turn; prompt is assembled stable-grounding-first / question-last for Ollama prefix-cache reuse. |
| ~~Persist + recall review findings in chat~~ | ~~Medium~~ | **DONE** — shipped in `feat/chat-memory-grounding`. `memory/review_store.py` (SQLite) persists the markdown review keyed to `memory/document_id.py::resolve_document_id` (preamble hash); `legal_research._run_doc_chat` injects the latest review so chat recalls prior findings rather than re-deriving them. |
| Chat: spurious edit proposals on factual questions | High (next branch) | Live smoke (SOW) showed qwen3.6 emitting a `PROPOSED EDIT` card on pure questions — e.g. "what is the billing model?" answered correctly then proposed replacing it with "…(Confirmed)"; "who signs?" proposed a malformed fill. The `CHAT_SYSTEM_PROMPT` says "don't emit a block when only asking a question" but the local model ignores it. Contributing factors (hypothesis): the prompt's heavy "PROPOSING EDITS (REQUIRED…)" emphasis, the **conditional-grounding lean path** removing the analytical playbook anchor, and **chat_history priming** (turn 1's edit primes turns 2+). Intermittent — a fresh-session NDA "who signs?" answered clean, no edit. The naive guard ("lean path → strip edits") has a real failure mode: a keyword-less edit request ("make X say Y") is classified lean and would lose its legit edit. Needs its own focused branch — likely a smarter code guard + reducing chat_history edit-priming. Deferred out of `feat/chat-memory-grounding` by decision. |
| Cloud / hybrid inference + tool-calling grounding agent | Medium (needs privacy sign-off) | The current local qwen3.6 makes a full-grounding chat turn ~30s and never reuses the prefix cache, forcing the conditional-grounding workaround. A fast cloud model (Ollama Cloud / API) would dissolve both: cheap prefill makes always-grounding fine, AND a **tool-calling agent that fetches "skills where needed"** becomes viable (the ReAct path was removed locally only because it was multi-minute). BUT this is a legal tool — sending client contracts + the MSA to a cloud endpoint is an **attorney-client-privilege / data-residency decision**, not just perf; the stack is deliberately local-first. Treat as its own initiative with a firm sign-off before any code. |
| Ollama cross-turn prefix-cache reuse doesn't engage | Low | The grounding is ordered stable-first specifically so Ollama can reuse the ~13.6k-token prefix across chat turns, but traces show it re-prefills the full prompt every turn even with `OLLAMA_NUM_PARALLEL=1` + `KEEP_ALIVE=-1`. Conditional grounding is the current workaround. If reuse can be made to engage (model chat-template / llama.cpp cache behavior), always-grounding would become cheap and the gate could be relaxed. Low priority — the gate works. |
| Chat smoke: MSA-primary + BAA documents | Low | `feat/chat-memory-grounding` was live-smoked on **SOW + NDA** (both grounding code paths: playbook+MSA and playbook-only). MSA-as-primary-doc and BAA follow the same playbook-only path as NDA (detect type → load bundle → no MSA-attach), so they exercise no new code — but haven't been run. Smoke for completeness when convenient. |
| Chat path: surface detected contract-type on the trace | Low | `_build_chat_grounding` detects contract-type internally (to pick the bundle + decide SOW→MSA) but, unlike the review path, doesn't write it to Langfuse trace metadata — so auditing which bundle a chat turn used means inferring from the doc text. A one-line `langfuse_context.update_current_trace(metadata=…)` in the chat grounding builder would close the gap. |
| Structured-JSON review output / selective finding injection | Medium (measured-need) | `contract_review` currently persists and injects the full markdown review into chat context. Structured JSON per-finding would let the chat path inject only the findings most relevant to the question (retrieval-narrowing) and enable Phase 2 playbook citations in the Word pane ("your firm's standard says X"). Defer until recall gaps are measured on real docs. |
| Clause segmentation / retrieval-narrowing on chat | Medium (measured-need) | Chat context currently injects the full uploaded document (capped at `config.chat_context_max_chars` by truncating the doc). For long contracts a clause-segmentation + retrieval step (fetch the clauses most relevant to the chat question) would shrink the context and improve focus without losing grounding. Measure first: compare answer quality full-doc vs retrieved-clauses on the demo SOW/MSA. |
| FTS cross-matter precedent recall on the SQLite review store | Low (measured-need) | `review_store.py` appends one row per review session; today only the latest review per `document_id` is recalled in chat. Full-text search across all stored reviews (same `client_id`) could surface "we flagged this same IP clause Red in three prior SOWs" — cross-matter precedent. Build when the store has enough history to make it useful. |
| Office.js custom-document-property `document_id` | Low (measured-need) | `resolve_document_id` currently hashes the preamble of the uploaded text — it's reproducible but not perfectly stable (template versioning, preamble edits). The durable upgrade: write the UUID to Word's custom document properties (`document.properties.customProperties`) on first review and read it back on subsequent opens. Requires WordApi 1.3; Mac + Windows confirmed. Implement when preamble-hash collisions are observed in practice. |
| Word add-in: chat history persistence across pane reopen | Medium | Chat is per-pane-lifetime today; persist messages + `session_id` to `localStorage` keyed by doc so reopening the pane restores the conversation. |
| Word add-in: bulk actions ("Apply all RED") | Medium | Findings tab — one click to track-change every RED finding, plus a stale-findings banner after the doc is edited. (Filter/sort pills **shipped** in `feat/word-addin-quick-ux`; click-to-jump navigation shipped there too.) |
| ~~Clause-locator hardening for placeholder / short anchors~~ | ~~Medium~~ | **DONE** — shipped in `feat/clause-locator-hardening` (2026-07-09; smoke-confirm pending). Fixed the (b) mislocation half: new pure `shouldMatchWholeWord()` makes `searchFirst` whole-word-only for short (≤ 2-word) anchors, so "Title" no longer matches inside "entitled" — shared by `goToClause`/`showInDocument`/`acceptRedline`. Null-range failures now show a calm `NO_MATCH_MESSAGE` instead of "Couldn't locate this clause." The wildcard-retry this follow-up called for was already live in `searchFirst`. **Deferred (not in this fix):** `[__]` first-occurrence disambiguation (which of several identical blanks a label-only finding should anchor on), and predictive suppression of label-only Missing-Context findings with no literal doc text (they still attempt a locate and fall through to `NO_MATCH_MESSAGE` today). |
| Word add-in: generate-clause tab (Phase 6) | Low | Mostly subsumed by Iteration 3's chat-driven inserts; a dedicated tab is now optional polish rather than the primary path. |
| Word add-in: AppSource publishing | Low | Sideload-only for now; publish to AppSource when ready for outside attorneys. Needs proper icons + manifest signing. |
| WebSocket / SSE streaming | Medium | LLM responses arrive all at once; would improve perceived latency on long generations |
| Admin API endpoints (sessions, skills, reviews, audit) | Medium | Deferred until a UI consumes them |
| Long-term memory (Qdrant `memory` collection) | Medium | Cross-session attorney preferences; implement when usage patterns emerge |
| DOCX output generation | Medium | PDF works; DOCX needed for Word add-in round-tripping (attorney edits doc in Word → exports back as DOCX) |
| Real authentication | Medium | Simple `X-User-ID` header today; needed before multi-user Linux VM |
| Remaining skills as folders | Low | `compliance_check`, `legal_research`, `drafting` still flat files |
| MCP tools integration | Low | Architecture supports it — tools plug into agent registries |
| Langfuse prompt versioning | Low | Prompts logged per-call; formal versioning not set up |
| Langfuse per-model cost + ReAct span fidelity | Low | GENERATION spans now carry token usage, but **cost** is null until a model price for `qwen3.6:latest` (and any eval models) is registered in the Langfuse UI (Settings → Models). Also: `traced_agent_invoke` records each ReAct run as one outer GENERATION (input + final output + summed usage); per-internal-turn/tool spans would need the LangChain callback handler, which needs the full `langchain` package (deliberately not a dep). |
| Skill-span enrichment (timing-only today) | Low | By design the five skill spans set `capture_input/output=False`, and `contract_review`/`drafting`/`compliance_check` make no LLM call (they assemble the prompt for `llm_caller`), so their spans are **intentionally bare timing markers** — not a bug. The verbatim generation + token usage live on the downstream `llm_caller` GENERATION. Optional enrichment: attach small metadata per skill (contract_review → `contract_type_detected`/ambiguous/doc-size; legal_research → path + #edits) without re-enabling state capture. Verified empty on trace `c8cf747b`. |
| Trace/Redis bloat: full-state dumps on node spans | Medium | The non-skill nodes (`intake`, `rag_retriever`, `risk_assessor`, `output_formatter`, `history_appender`, `memory_writer`) auto-capture the entire `LegalAgentState` as both input and output — it snowballs ~14k→66k chars across the pipeline, captured ~2× per node (trace `c8cf747b`). Fix: set `capture_input/output=False` on those nodes (the explicit prompt/response I/O on `llm_caller`/`intent_router`/`planner` survives because it's set via `update_current_observation`). Bundles with the existing latency/bloat follow-up. |
| SOW-vs-MSA review: scale beyond the one-MSA demo | Medium | `get_parent_msa` currently returns "the one MSA on file for this `client_id`" (picks first by `doc_id` when >1). To scale: match the *specific* parent MSA by party name or an explicit MSA reference inside the SOW; RAG-select only the MSA clauses relevant to the SOW (instead of full text) when MSAs exceed the `_MSA_MAX_CHARS=24000` cap; promote `_MSA_MAX_CHARS` to `config.Settings`. The structural comparison directive could also be promoted into the SOW `SKILL.md` source (legal-team-owned) once the team wants to author formal MSA-vs-SOW rules. |
| Policy chunking strategy verification | Low | TBD — test clause-based vs section-based |
