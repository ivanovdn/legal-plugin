# Legal Plugin — Design Spec

## Overview

AI-powered legal assistant for an internal legal team. Attorneys interact through a Chainlit frontend that communicates with a FastAPI backend. A LangGraph supervisor graph routes requests to skills or agent subgraphs, retrieves grounding context via a shared RAG layer, and returns structured legal output via a local LLM.

**Fully air-gapped** — no external API calls. Everything runs locally on Mac M4 Pro (48 GB). Later migration to Linux VM with model inference on Apache Spark.

---

## Architecture

### Two-process model

- **Backend** — FastAPI (port 8000). Owns LangGraph graph, RAG layer, LLM calls, audit, all business logic.
- **Frontend** — Chainlit (port 8080). Chat UI + admin dashboard. Connects to backend via HTTP REST + WebSocket for streaming/notifications.

### Supporting services (Docker)

- Qdrant (vector store, ports 6333/6334)
- Redis (session checkpointer + permanent sessions, port 6379)
- Langfuse (agent tracing, port 3000)

### Running natively on macOS

- Ollama (LLM + embeddings, port 11434)
- llama-cpp (reranker, `/v1/rerank` endpoint)
- Phoenix (RAG evals, port 6006) — already running, shared with compliance-bot

### Data flow

```
Attorney -> Chainlit -> FastAPI -> LangGraph graph
                                    |-- RAG layer -> Qdrant
                                    |-- LLM -> Ollama
                                    |-- Reranker -> llama-cpp
                                    |-- Checkpointer -> Redis
                                    |-- Audit -> SQLite
                                    +-- Tracing -> Langfuse
```

### LLM abstraction

The LLM layer is abstracted behind a clean interface so that swapping Ollama for a remote Spark endpoint later does not require rewiring the graph. All LLM calls go through a single client that reads its configuration from env vars.

---

## Stack

| Layer | Technology |
|---|---|
| LLM runtime | Ollama — model configured via `LLM_MODEL` env var |
| Embedding | Ollama — model configured via `EMBEDDING_MODEL` env var |
| Vector store | Qdrant (Docker) |
| Keyword search | BM25 (pure Python, JSON persistence) — toggle via `BM25_ENABLED` |
| Search fusion | Reciprocal Rank Fusion (RRF, k=60) |
| Reranker | `/v1/rerank` endpoint via llama-cpp — model and backend configured via env vars |
| Orchestration | LangGraph `StateGraph` |
| RAG utilities | Pure Python — ported from compliance-bot (LlamaIndex agent NOT ported, replaced by LangGraph) |
| Document parsing | `pdfplumber` + `python-docx` |
| Backend API | FastAPI (port 8000) |
| Frontend | Chainlit (port 8080) |
| Session memory | Redis — LangGraph `RedisSaver` checkpointer, sessions permanent until deleted |
| Audit / long-term | SQLite (`data/legal.db`) |
| Agent tracing | Langfuse (self-hosted Docker, port 3000) |
| RAG evals | Phoenix (self-hosted Docker, port 6006) — shared with compliance-bot |
| Config | `pydantic-settings` + `.env` |

---

## Project Structure

```
legal-plugin/
|-- CLAUDE.md
|-- .env
|-- .env.example
|-- config.py
|-- requirements.txt
|-- docker-compose.yml
|
|-- rag/
|   |-- embeddings.py
|   |-- vector_store.py
|   |-- bm25_index.py
|   |-- hybrid_search.py
|   |-- reranker.py
|   +-- tools/
|       |-- search_legal.py
|       |-- get_document.py
|       |-- extract_clauses.py
|       +-- escalate.py
|
|-- ingest/
|   |-- chunk_models.py
|   |-- pipeline.py
|   +-- parsers/
|       |-- pdf_parser.py
|       +-- docx_parser.py
|
|-- graph/
|   |-- state.py
|   |-- graph.py
|   +-- nodes/
|       |-- intake.py
|       |-- intent_router.py
|       |-- planner.py
|       |-- skill_dispatcher.py
|       |-- rag_retriever.py
|       |-- llm_caller.py
|       |-- risk_assessor.py
|       |-- human_review.py
|       |-- output_formatter.py
|       +-- memory_writer.py
|
|-- skills/
|   |-- base.py
|   |-- contract_generation.py
|   |-- contract_review.py
|   |-- compliance_check.py
|   |-- legal_research.py
|   +-- drafting.py
|
|-- api/
|   |-- main.py
|   |-- models.py
|   +-- routes/
|       |-- query.py
|       |-- documents.py
|       |-- sessions.py
|       |-- skills.py
|       |-- reviews.py
|       +-- audit.py
|
|-- frontend/
|   |-- app.py              # Chainlit entry point
|   |-- api_client.py       # HTTP/WS client to backend
|   +-- components/
|       |-- admin_panel.py
|       |-- review_panel.py
|       +-- document_browser.py
|
|-- memory/
|   |-- session.py
|   +-- audit.py
|
|-- scripts/
|   |-- create_collections.py
|   |-- ingest_all.py
|   +-- test_query.py
|
+-- tests/
    |-- test_graph.py
    |-- test_skills.py
    +-- test_rag.py
```

---

## Porting from compliance-bot

Port these files — do not rewrite from scratch. LlamaIndex agent (`rag/agent.py`) is NOT ported — LangGraph replaces it entirely.

| Source | Destination | Change |
|---|---|---|
| `rag/embeddings.py` | `rag/embeddings.py` | Model name from env var only |
| `rag/vector_store.py` | `rag/vector_store.py` | Add `collection` param — no hardcoded collection name |
| `rag/bm25_index.py` | `rag/bm25_index.py` | Port as-is |
| `rag/hybrid_search.py` | `rag/hybrid_search.py` | Port as-is |
| `rag/reranker.py` | `rag/reranker.py` | Port as-is |
| `rag/tools/get_section.py` | `rag/tools/get_document.py` | Rename + adapt field names to `LegalChunk` |
| `rag/tools/escalate.py` | `rag/tools/escalate.py` | Port as-is |
| `ingest/docx_parser.py` | `ingest/parsers/docx_parser.py` | Add `client_id`, `jurisdiction`, `doc_type` metadata |
| `ingest/chunk_models.py` | `ingest/chunk_models.py` | Replace `PolicyChunk` with `LegalChunk` |

---

## Core Models

### LegalChunk

```python
class LegalChunk(BaseModel):
    chunk_id:        str
    doc_id:          str
    doc_title:       str
    doc_filename:    str
    doc_type:        str   # contract | legislation | template | policy | case_law
    client_id:       str   # "internal" for shared docs
    jurisdiction:    str
    sensitivity:     str   # confidential | internal | public
    section:         str = ""
    section_number:  str = ""
    clause:          str = ""
    clause_number:   str = ""
    clause_type:     str = ""
    section_display: str = ""
    text:            str
    char_count:      int = 0
    chunk_index:     int = 0
    chunk_strategy:  str = ""   # clause-level | section-based | template-aware
    last_updated:    str = ""
```

### LegalAgentState

```python
class LegalAgentState(TypedDict):
    request:          str
    user_id:          str
    uploaded_docs:    list[LegalChunk]
    task_type:        str        # contract_generation | contract_review | compliance | research | drafting | multi
    skill_plan:       list[str]
    retrieval_query:  str
    retrieved_chunks: list[LegalChunk]
    filters:          dict       # client_id, jurisdiction, doc_type
    messages:         list[dict]
    llm_response:     str
    risk_level:       str        # low | medium | high
    risk_flags:       list[dict]
    awaiting_review:  bool
    attorney_notes:   str
    report:           dict
    session_id:       str
    checkpoint_id:    str
    trace_id:         str
```

---

## Skill Output Schemas

### contract_generation

```python
class GeneratedContract(BaseModel):
    doc_type:           str
    jurisdiction:       str
    full_text:          str
    source_contracts:   list[str]   # doc_ids used as source
    extracted_patterns: dict        # clause_type -> pattern used
    deviations:         list[str]   # patterns absent from history — flagged
    docx_path:          str | None
```

### contract_review

```python
class ClauseAnalysis(BaseModel):
    clause_type:    str
    original_text:  str
    risk_level:     Literal["low", "medium", "high"]
    risk_reason:    str
    standard_ref:   str | None
    suggested_edit: str | None

class ContractReviewReport(BaseModel):
    contract_name:   str
    jurisdiction:    str
    clauses:         list[ClauseAnalysis]
    summary:         str
    missing_clauses: list[str]
```

### compliance_check

```python
class ComplianceCheck(BaseModel):
    rule_id:      str
    rule_text:    str
    source_chunk: str
    status:       Literal["pass", "fail", "partial", "n/a"]
    evidence:     str
    remediation:  str | None

class ComplianceReport(BaseModel):
    jurisdiction: str
    policy_scope: str
    checks:       list[ComplianceCheck]
    overall:      Literal["pass", "fail", "partial"]
    escalate:     bool
```

### legal_research

```python
class Citation(BaseModel):
    chunk_id:  str
    doc_title: str
    excerpt:   str

class ResearchReport(BaseModel):
    question:   str
    answer:     str
    citations:  list[Citation]
    confidence: float
    conflicts:  list[str]
    open_gaps:  list[str]
    escalate:   bool
```

### drafting

```python
class DraftResult(BaseModel):
    doc_type:         str
    jurisdiction:     str
    full_text:        str
    template_ref:     str | None
    deviations:       list[str]
    variables_filled: dict
    docx_path:        str | None
```

---

## Qdrant Collections

Create with `scripts/create_collections.py` before first ingest. Vector dim from `QDRANT_VECTOR_DIM` env var.

| Collection | Content | Chunking | Key payload filters |
|---|---|---|---|
| `legal_docs` | contracts, legislation, templates, policies | varies by `doc_type` — clause-level, section-based, or template-aware. `chunk_strategy` stored in metadata | `doc_type`, `client_id`, `jurisdiction`, `sensitivity`, `date` |
| `case_history` | past signed contracts (clause-level, one chunk = one clause tagged with `clause_type`) | clause-level | `client_id`, `contract_type`, `jurisdiction`, `clause_type`, `date`, `outcome` |
| `memory` | attorney preferences, past decisions (stub in Phase 1) | small entries, no windowing | `user_id`, `preference_type`, `date` |

**Chunking strategy by doc_type:**

| doc_type | Strategy | Notes |
|---|---|---|
| contract, case_law | clause-level | one chunk per clause, tagged with `clause_type` |
| legislation | section-based | split by section/article headings |
| policy | TBD — verify clause-based vs section-based against real documents |
| template | template-aware | preserve structure and placeholders intact |

---

## LangGraph Graph Structure

### Node flow

```
intake -> intent_router -> planner (if multi-skill) -> skill_dispatcher
                                |
                   +------------+------------+--------------+------------+
                   |            |            |              |            |
           contract_gen  contract_rev  compliance  legal_research  drafting
                   |            |            |              |            |
                   +------------+------------+--------------+------------+
                                |
                          rag_retriever (shared, called by skills that need it)
                                |
                          llm_caller
                                |
                        risk_assessor
                                |
                     +---- route_risk ----+
                     |                    |
              human_review         output_formatter
              (blocking/async)           |
                     |                   |
                     +---------+---------+
                               |
                         memory_writer -> END
```

### Routing logic

```python
def route_intent(state) -> str:
    return "planner" if len(state["skill_plan"]) > 1 else "skill_dispatcher"

def route_risk(state) -> str:
    if state["task_type"] in ("contract_generation", "drafting"):
        return "human_review"   # always — never auto-deliver generated documents
    if state["risk_level"] in ("high", "medium"):
        return "human_review"
    return "output_formatter"
```

### Human-in-the-loop

- **Blocking** (contract_generation, drafting, high-risk): LangGraph `interrupt()` pauses graph. State saved to Redis. Backend sends WebSocket notification. Attorney approves/rejects/edits in UI. Resume via `POST /api/query/{session_id}/resume`.
- **Async** (medium-risk): Output delivered but flagged. Attorney reviews at their own pace. No graph pause.

### Skill types

- **Agent subgraphs** (contract_generation, legal_research): `create_react_agent` with tool loops, max 8 iterations. Decide their own retrieval strategy.
- **Async functions** (contract_review, compliance_check, drafting): Call `rag_retriever` and `llm_caller` through shared graph nodes.

### Agent tools

- contract_generation: `search_legal`, `get_full_document`, `extract_clauses`, `escalate`
- legal_research: `search_legal`, `get_full_document`, `escalate`

Note: `extract_clauses` is a new tool (not ported from compliance-bot). It queries `case_history` collection by `clause_type` to extract patterns for contract generation.

### MCP integration (future)

MCP tools plug into agent/skill tool registries. No graph structure changes needed. Plain skills that need MCP tools get promoted to agent subgraphs. Non-skill MCP actions (email, calendar) handled by intake or a dedicated action_dispatcher node.

---

## Frontend (Chainlit)

### Chat features

- Streaming responses via WebSocket
- File upload (PDF/DOCX) — sent to FastAPI ingest endpoint
- Session history — persistent, loaded from Redis via API
- Citations displayed inline with links to source chunks

### Admin/dashboard features

- Document browser — view ingested docs, metadata, chunk counts
- Plugin manager — toggle skills on/off per user/session
- Human-in-the-loop panel — pending reviews, approve/reject/edit, add attorney notes
- Session viewer — browse past conversations

### Human-in-the-loop UI flow

```
Graph hits human_review node
    -> LangGraph interrupt() — graph pauses, state saved to Redis
    -> Backend sends WebSocket notification to Chainlit
    -> Attorney sees review panel with output + risk flags
    -> Attorney approves/rejects/edits
    -> Chainlit POSTs decision to FastAPI resume endpoint
    -> Graph resumes from checkpoint
```

---

## API Layer

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/query` | Submit a legal request, starts graph execution |
| POST | `/api/query/{session_id}/resume` | Resume after human_review interrupt |
| GET | `/api/query/{session_id}/status` | Check graph execution status |
| WS | `/api/ws/{session_id}` | Stream responses + notifications |
| POST | `/api/ingest` | Upload and ingest documents |
| GET | `/api/documents` | List ingested documents with metadata |
| GET | `/api/documents/{doc_id}` | Get document details + chunks |
| DELETE | `/api/documents/{doc_id}` | Remove document from collections |
| GET | `/api/sessions` | List sessions for a user |
| GET | `/api/sessions/{session_id}` | Get session history |
| DELETE | `/api/sessions/{session_id}` | Delete a session |
| GET | `/api/skills` | List available skills + enabled status |
| PUT | `/api/skills/{skill_name}` | Toggle skill on/off |
| GET | `/api/reviews/pending` | List items awaiting human review |
| GET | `/api/audit` | Query audit log |
| GET | `/health` | Health check |

**Auth:** Simple `user_id` header for now. Real auth added when moving to multi-user/Linux VM.

**Response envelope:** `{ status, data, errors }` — consistent across all endpoints.

---

## Observability & Audit

| Layer | Tool | What it captures |
|---|---|---|
| Agent tracing | Langfuse (port 3000) | Every graph node execution, prompt versions per skill, session + user grouping via `trace_id`, attorney feedback |
| RAG evals | Phoenix (port 6006) | Retrieval quality, chunk relevance scoring, eval datasets and experiments. Shared with compliance-bot |
| Audit log | SQLite (`data/legal.db`) | Every skill invocation, inputs, outputs, risk level, review decisions. Permanent, queryable via API |

**Rule: Langfuse = agent traces. Phoenix = RAG evals. Do not mix.**

### Langfuse integration

- `init_observability()` called in FastAPI lifespan, before graph compiles
- Every node wrapped with Langfuse trace context
- `trace_id` = `session_id` for grouping

### SQLite audit schema

- `id`, `timestamp`, `session_id`, `user_id`, `skill_name`, `task_type`, `request_summary`, `risk_level`, `review_status`, `review_notes`, `duration_ms`
- Written before skill returns — not fire-and-forget

### Phoenix

Already running, no setup needed. RAG layer instruments itself with Phoenix callbacks during search/rerank.

---

## Memory Strategy

### Short-term (Redis) — build now

- Conversation history, current task state, intermediate results
- LangGraph `RedisSaver` checkpointer
- Sessions permanent until attorney deletes them

### Long-term (Qdrant `memory` + SQLite) — stub now, implement later

- Attorney preferences, past decisions, learned patterns
- `memory` Qdrant collection and `memory_writer` node exist as stubs
- SQLite audit log captures raw data — used later to decide what goes into long-term memory
- Implement when enough usage data exists to know what's worth remembering

---

## Configuration

### `.env`

```bash
# LLM
OLLAMA_BASE_URL=http://localhost:11434
LLM_MODEL=
EMBEDDING_MODEL=
QDRANT_VECTOR_DIM=

# Qdrant
QDRANT_URL=http://localhost:6333

# Redis
REDIS_URL=redis://localhost:6379

# Reranker
RERANKER_ENABLED=true
RERANKER_BACKEND=
RERANKER_URL=
RERANKER_MODEL=
RERANKER_TOP_N=6
RERANKER_CANDIDATES=25

# BM25
BM25_ENABLED=false

# Langfuse
LANGFUSE_HOST=http://localhost:3000
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=

# Phoenix
PHOENIX_HOST=http://localhost:6006

# App
API_PORT=8000
CHAINLIT_PORT=8080
SQLITE_PATH=data/legal.db
```

### `config.py`

`pydantic-settings` `BaseSettings` class. Loads from `.env`. Single import across the project.

---

## Docker

```yaml
services:
  qdrant:
    image: qdrant/qdrant
    ports: ["6333:6333", "6334:6334"]
    volumes: ["./data/qdrant:/qdrant/storage"]

  redis:
    image: redis:alpine
    ports: ["6379:6379"]

  langfuse:
    image: langfuse/langfuse:latest
    ports: ["3000:3000"]
    environment:
      DATABASE_URL: file:/data/langfuse.db
    volumes: ["./data/langfuse:/data"]
```

Ollama, llama-cpp reranker, Phoenix, FastAPI, and Chainlit run natively on macOS — not in Docker. Use `host.docker.internal` to reach Ollama from containers.

---

## Build Order

Each phase verified before starting the next.

| Phase | Steps | Exit criterion |
|---|---|---|
| **1 — Infrastructure** | docker-compose, config.py, .env.example, create Qdrant collections | All services start, collections exist |
| **2 — RAG layer** | Port from compliance-bot (embeddings, vector_store, bm25, hybrid_search, reranker), ingest pipeline, parsers, LegalChunk model | Can ingest a doc and retrieve relevant chunks via script |
| **3 — Graph skeleton** | LegalAgentState, all nodes as stubs, all edges wired, routing logic | Graph compiles, request flows end-to-end through stubs |
| **4 — Shared nodes** | intake, intent_router, rag_retriever, risk_assessor, output_formatter, memory_writer, human_review | Real routing, retrieval, risk assessment working. Audit log writes. Langfuse receives traces |
| **5 — API** | FastAPI endpoints, WebSocket streaming, ingest endpoint | Can submit query via curl, get streamed response, upload doc |
| **6 — Contract generation** | Agent subgraph with tools, replace stub, test with synthetic contracts | End-to-end contract generation with human review |
| **7 — Chainlit frontend** | Chat UI, file upload, admin panel, human-in-the-loop UI | Attorneys can interact through browser |
| **8 — Remaining skills** | contract_review, compliance_check, legal_research, drafting, planner | Each skill produces structured output, passes through graph |

---

## Constraints — Never Violate

- `temperature=0.0` on all LLM calls
- Every LLM output claim must reference a retrieved chunk — no citation means escalate
- `contract_generation` and `drafting` always trigger `human_review` — no exceptions
- `client_id` filter always applied on retrieval — no cross-client data leakage
- Every skill invocation written to SQLite audit log before returning
- No external network calls

---

## Common Pitfalls

| Pitfall | Fix |
|---|---|
| Embedding dim mismatch on re-ingest | Delete Qdrant collection before re-ingesting with a different model |
| LangGraph interrupt not persisting | Confirm `RedisSaver` is passed to `StateGraph(checkpointer=...)` |
| Ollama unreachable from Docker | Use `host.docker.internal:11434`, not `localhost` |
| LLM returns prose instead of JSON | Add `format: json` to Ollama request + Pydantic schema in system prompt |
| Reranker scores compressed (vLLM) | Set `RERANKER_BACKEND=vllm` — wraps with chat template (same fix as compliance-bot) |
| `\n` in `.env` sent literally | Convert `\\n` to `\n` at runtime — same pattern as compliance-bot |
| Langfuse not initialised before graph run | Call `init_observability()` in FastAPI lifespan before graph compiles |
| `client_id` missing from filters | Intake node must resolve `client_id` from `user_id` — never enter graph without it |

---

## Open Items

- **Policy chunking strategy** — TBD, verify clause-based vs section-based against real policy documents
- **Auth** — simple `user_id` header for now, real auth when moving to multi-user
- **Long-term memory** — stubs in place, implement when usage patterns emerge
- **MCP tools** — future, no architectural changes needed
- **Spark migration** — LLM abstraction layer designed for it, implement when ready
