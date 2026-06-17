# Legal Plugin — Project Wiki

> Last updated: 2026-06-17 | 211 tests + 43 frontend parser asserts passing | Python 3.12 | Redis checkpointer active | Word add-in shipped (chat-driven edits) | Trinetix legal-team playbook integrated | Smoke-tested on real MSA / SOW / BAA | Main-flow audit + bug fixes #1/#2/#3/#4/#5 + placeholder-recall Layer 1 + Langfuse GENERATION spans & token usage (see docs/output_format_conflict.md)

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

---

## Follow-ups / Roadmap

| Feature | Priority | Notes |
|---|---|---|
| LLM evaluation across models + clause-content tuning | High | Smoke on real Trinetix NDA / MSA / SOW / BAA passed — the team's table format, No-Signature Gate, and per-type bundles all fire correctly. Next: evaluate on multiple LLMs (qwen vs llama vs others), and tune cases where the model misidentifies clause-by-number (e.g. asked to remove "3.4" but removed "Permitted Disclosures" by name match). Work on a separate `tuning/*` branch. |
| Playbook-adherence ("ceiling") violations — improvised findings | Medium | **Observed, not a code bug.** On the post-#5 NDA review (Langfuse `cad3631b-acc9-4c7c-b76e-70907aa18629`) the model raised a Yellow on the indemnity clause — *"broad indemnity… add standard carve-outs (gross negligence / willful misconduct)."* The NDA playbook has **no** carve-out rule; §11 + NDA-006 take the **opposite** stance — a broad receiving-party indemnity is the *preferred* position (it protects Trinetix, the Disclosing Party here), and the red flag is *weak/nominal* liability. The model improvised a generic commercial-contracting concept from pretraining, against the playbook — a "SKILL.md is the ceiling" violation. (An earlier Word run of the same file correctly did **not** flag it — so it's run-to-run, not deterministic.) Levers: (a) sharpen ceiling enforcement so the model doesn't volunteer un-playbooked clause opinions; (b) score "improvisation rate" (findings with no playbook rule ID) as an eval metric. Pairs with the LLM-evaluation item above. |
| Reconcile playbook `.docx` §10 (legal team) | High | Proper source fix for the output-format conflict — Fix #2 excludes §10.2/10.3/10.4 at build time as a stopgap. Legal should either mark those schemas internal-reference-only or have §10 defer to the operating-rules output format; then the build-script exclusion can be removed. See `docs/output_format_conflict.md`. |
| Source `SKILL.md` assumes file access (legal team) | Low | The per-type source `SKILL.md` reference `references/*.md` as if readable at runtime; our system inlines everything and the LLM has no file access. Fix #3 rewrites those pointers at build time. If legal updates the source, prefer inline phrasing ("in this playbook") over file paths. |
| System-wide self-improving harness | Medium | Every brittle heuristic (contract-type detection, parser `deriveBlockers` / `pick` column aliases / anchor matching, edit-promise `format=json` retry, tolerant JSON) emits a confidence/disagreement signal into one sink for tuning. Free seed: flag when `contract_type_detected` ≠ the model's own stated "Contract type:" line. |
| Deterministic placeholder / signature-block safety-net — Layer 2 | Medium (deferred) | **Layer 1 shipped + live-validated across all four contract types — this is the remaining *guarantee*.** Cause settled by A/B: the recall gap was Fix #2 collateral signal loss, **not** the two-column masking originally guessed; restoring the cue deterministically recovers structured detection on the NDA (`PROSE→FULL`, 6/6) and finds blank signature blocks on NDA/MSA/SOW/BAA in live smoke. But Layer 1 stays LLM-dependent — it repairs the regression and lifts recall, yet **cannot guarantee** it: the 129KB MSA (`f4b383f0`) missed all 6 placeholders (length-driven), and prose-only runs (`01d4e2ae`) never reach Word's `deriveBlockers` blockers card. Layer 2 = a deterministic post-LLM scan of the faithful `uploaded_text` for unfilled markers (`[__]`, `[Legal Name]`, `[Month] [Date], [Year]`, `[Address]`; **exclude** generated-draft `[Source: doc_id]` tags) that **forces** each into a structured Missing-Context row + "Do not send for signature" (Fix #4 then escalates `risk_level`/`requires_attorney`). Must emit **structured rows**, not just gate prose. Deferred per decision — build when Layer 1's measured recall (esp. on long docs) proves insufficient. A first instance of the self-improving harness. |
| Contract-type override in Word add-in | Low | Detection is now title-weighted + whole-doc (Fix #1), so mis-detection is rare; an override dropdown ("Reviewing as: MSA — change") in the FindingsTab chip is now defense-in-depth rather than a fix for a frequent failure. |
| Word "attorney sign-off required" banner | Low | Fix #4 added `report.requires_attorney` (the authoritative server verdict). Word already renders blockers client-side via `deriveBlockers`, so behavior is covered; a small banner in FindingsTab consuming the new flag would make the verdict explicit. Sideload smoke-test required. |
| Gate generation/drafting interrupt on `interactive_review` | Low | Fix #4 gates only the **contract_review** interrupt on the new capability flag. `contract_generation` / `drafting` still route to `human_review` unconditionally — safe today because those are Chainlit-only flows (Chainlit sets `interactive_review=True`), but a non-interactive caller doing generation would strand the session. Extend the flag-gate to those branches when a second non-interactive client surfaces. |
| RAG chunker: depth-1-numbered clause loss (`parse_docx`) | Medium | The SAME root cause Fix #5 routed around for the review path still affects **RAG ingestion**: `docx_parser._parse`'s `num_depth==1` branch treats any decimal-numbered paragraph as a section heading and discards its body. Mild on the policy corpus (3 long clauses lost across 52 files; numbered items there are short headings) but **mutilates any contract `.docx` ingested via `/api/ingest`**. Fix: only treat a numbered paragraph as a section title when heading-like (bold first run, or ≤~8 words); otherwise preserve the body (as depth-2+ already does). Refactor the decision into a unit-testable `_is_section_heading(para, num_info)` helper. **Must ship behind a regression harness** asserting per-file coverage across the 52 policy docs stays ≥ current, then **re-ingest** the corpus. PDF (`parse_pdf`) has separate section logic — audit separately. |
| Word add-in: playbook-grounded findings (Phase 2) | High | Make `contract_review` emit structured per-finding JSON including the retrieved RAG chunks; pane shows "your firm's standard says X" next to "this contract says Y" with source citations. The real Spellbook differentiator. Now that the team's matrix is in-prompt the LLM can already cite per-clause IDs (NDA-001, MSA-014…); JSON output would let the pane render them as live links. |
| Word add-in: chat history persistence across pane reopen | Medium | Chat is per-pane-lifetime today; persist messages + `session_id` to `localStorage` keyed by doc so reopening the pane restores the conversation. |
| Word add-in: bulk actions ("Apply all RED") | Medium | Findings tab — one click to track-change every RED finding, plus filter/sort pills and a stale-findings banner after the doc is edited. |
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
| Policy chunking strategy verification | Low | TBD — test clause-based vs section-based |
