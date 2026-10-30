# Legal Plugin — Architecture

> Part of the [project wiki](wiki.md).

## Architecture

```
Attorney → Chainlit (:8080) / Word add-in (:3001) → FastAPI (:8000) → LangGraph graph
                                                    ├── Skills (5 capabilities)
                                                    ├── RAG layer → Qdrant (dense search; Spark on the VM)
                                                    ├── Embeddings → Ollama (embeddinggemma)
                                                    ├── LLM → Ollama
                                                    ├── Reranker → Qwen3-Reranker-4B (llama.cpp local / vLLM VM)
                                                    ├── Checkpointer → Redis
                                                    ├── Audit/Review/Conversation → Postgres (app-db)
                                                    └── Tracing → OTel (Langfuse local / Phoenix VM)
```

Retrieval in detail: which model runs where, the search pipeline, and the move to Qdrant's built-in hybrid search are covered in [rag.md](rag.md).

**Two-process model:**
- **Backend** (FastAPI, port 8000) — owns all business logic, graph execution, RAG, LLM calls, audit
- **Frontends** — Chainlit (:8080) and the Word add-in (:3001 Vite dev server, Office.js task pane); both thin clients calling the backend over HTTP (`POST /api/query`). On the VM the built pane is served by Caddy (HTTPS :443, `docker-compose.remote.yml`) from the same origin, with `/api/*` proxied to the backend.

---

## Project Structure

```
legal-plugin/
├── api/                        # FastAPI backend
│   ├── main.py                 # App entry point, lifespan, CORS
│   ├── models.py               # Pydantic request/response models
│   ├── auth.py                 # resolve_user_id — attorney identity seam (X-User-ID; O365 SSO dormant)
│   ├── client_info.py          # Word host / pane build from request headers
│   ├── turn_record.py          # start_turn/finish_turn — one turn_log row per request
│   ├── runtime_facts.py        # settings + model digest recorded on each turn_log row
│   └── routes/
│       ├── query.py            # POST /api/query, resume, status
│       ├── compact.py          # POST /api/compact (condense a document's chat history)
│       ├── feedback.py         # POST /api/feedback, POST /api/events
│       ├── preferences.py      # GET/PUT /api/preferences (attorney USER.md)
│       ├── document_snapshot.py # POST /api/document-snapshot
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
│   ├── checkpointer.py         # RedisSaver factory (None → stateless run)
│   └── nodes/                  # Shared processing nodes (all @traced)
│       ├── intake.py           # Resolve client_id, set filters, set trace attributes
│       ├── intent_router.py    # LLM classifies task_type, records an LLM span
│       ├── planner.py          # LLM decomposes multi-skill requests
│       ├── skill_dispatcher.py # Routes to correct skill node
│       ├── rag_retriever.py    # Calls hybrid_search (skips if agent skill already ran)
│       ├── llm_caller.py       # Calls Ollama with context, records prompt+response on an LLM span
│       ├── risk_assessor.py    # contract_review: verdict-based risk; other skills: citation check
│       ├── human_review.py     # LangGraph interrupt() for blocking review
│       ├── output_formatter.py # Builds structured report dict
│       ├── history_appender.py # Appends the turn to chat_history (capped)
│       └── memory_writer.py    # Writes audit log + review + conversation (Postgres)
│
├── skills/                     # Legal capabilities — each in its own folder
│   ├── base.py                 # load_skill_prompt() / load_bundle() with ceiling constraint
│   ├── grounding.py            # Shared: type detection, playbook bundle, parent-MSA attach, preferences block
│   ├── contract_generation/    # ReAct agent — searches + generates contracts
│   │   ├── contract_generation.py
│   │   └── SKILL.md            # Playbook — editable by legal team
│   ├── contract_review/        # Clause analysis with uploaded contract support
│   │   ├── contract_review.py
│   │   └── playbook/           # GENERATED per-type bundle (scripts/build_playbook.py) — do not hand-edit
│   ├── compliance_check.py     # Policy verification prompt → shared nodes
│   ├── legal_research/         # Direct ChatOllama (doc-chat) + ReAct agent (KB research)
│   │   ├── legal_research.py   # Entry — run paths + LLM builders
│   │   ├── prompts.py          # Prompt constants (no imports)
│   │   ├── edit_parsing.py     # JSON edit-block extraction
│   │   ├── review_recall.py    # Prior-review reconciliation + gate verdict
│   │   ├── context.py          # Grounding gate, prior-review/conversation load, context cap
│   │   └── compaction.py       # Chat-history condensing into validated quote segments
│   └── drafting.py             # Document generation prompt → shared nodes
│
├── rag/                        # RAG layer (ported from compliance-bot) — see docs/rag.md
│   ├── embeddings.py           # Ollama /api/embed (embeddinggemma, 768-d)
│   ├── vector_store.py         # Qdrant client, dense vectors only — all ops take collection param
│   ├── bm25_index.py           # Pure-Python BM25, off by default — to be replaced by Qdrant hybrid search
│   ├── hybrid_search.py        # Dense search (+ BM25 if on), RRF fusion, then the reranker
│   ├── reranker.py             # Qwen3-Reranker-4B client: llama-server or vLLM (/v1/rerank, /v1/score)
│   ├── related_docs.py         # get_parent_msa — governing MSA for SOW reviews
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
│       ├── pdf_parser.py       # PDF parser (pdfplumber, section-based)
│       └── plain_text.py       # Lossless full-text extraction (DOCX/PDF/TXT) for the review path
│
├── memory/                     # Persistence (Postgres, `app-db`)
│   ├── db.py                   # Connection pool + init_db() (all store tables)
│   ├── audit.py                # Audit log store
│   ├── review_store.py         # Review markdown store, keyed by document_id
│   ├── conversation_store.py   # Per-attorney chat history store
│   ├── conversation_summary.py # Condensed conversation segments (append-only)
│   ├── feedback_store.py       # feedback + interaction_event tables
│   ├── turn_log.py             # One row per accepted request
│   ├── document_store.py       # document_version / document_snapshot (SP4)
│   ├── document_id.py          # Preamble-hash fallback document id
│   ├── fingerprint.py          # The one hash of a document's text
│   └── preferences.py          # Per-attorney USER.md (a file under data/attorneys/, not Postgres)
│
├── observability/              # Tracing (OpenTelemetry)
│   ├── otel.py                 # init_observability() — TracerProvider + OTLP exporter + auto-instrumentors
│   ├── spans.py                # THE SEAM — @traced, set_trace_attributes/set_gen_attributes,
│   │                           #   mark_failed, record_degradation, set_outcome, degradations
│   ├── degradations.py         # the closed 23-code vocabulary + derive_outcome()
│   ├── log_context.py          # current turn id on every app log line ([turn=...])
│   └── tracing.py              # traced_invoke/traced_agent_invoke (LangChain) +
│                               #   ollama_usage / ollama_timings — token counts + ns→ms latencies
│
├── scripts/                    # Utility scripts
│   ├── create_collections.py   # Create 3 Qdrant collections
│   ├── generate_demo_contracts.py  # Generate + ingest 5 synthetic contracts
│   ├── ingest_all.py           # Batch ingest from directory
│   ├── start.sh                # Start backend + frontend
│   ├── test_api.sh             # Curl-based API verification
│   ├── test_contract_gen.py    # Contract generation e2e test
│   ├── test_graph_flow.py      # Graph routing verification
│   ├── check.sh                # Pre-push gate: locks + tests + typecheck + add-in assertions + evals + image build
│   ├── eval.sh                 # Deterministic zero-LLM eval harness (evals/)
│   ├── build_playbook.py       # .docx + team skill folders → skills/contract_review/playbook/
│   ├── build_manifest.py       # Word add-in manifest generation
│   ├── ingest_demo_msa.py      # Ingest the demo MSA as doc_type="msa" (SOW-vs-MSA demo)
│   ├── feedback_report.py      # Read feedback / turn_log / activity tables
│   └── check_locks.py · check_degradation_vocabulary.py · check_wiki.py · check_docs_links.py · document_changes.py · delete_document_data.py · test-windows-install.ps1
│
├── evals/                      # Zero-LLM regression harness (parse + compaction-gate cases, baseline.json)
├── config.py                   # pydantic-settings — single config import
├── docker-compose.yml          # Local stack: Qdrant, Redis, app-db, Langfuse v3 (+ its Postgres/ClickHouse/MinIO)
├── docker-compose.remote.yml   # VM overlay: backend, Phoenix, Caddy (serves the built Word pane)
├── Dockerfile · Caddyfile      # Backend image · reverse proxy + pane
├── requirements*.txt           # Hand-edited inputs (ranges); requirements*.lock = exact pins (runtime lock = image)
└── tests/                      # pytest suite (needs Docker — testcontainers Postgres)
```

---

## Skills Architecture

### SKILL.md Pattern

Playbook-driven skills pair a Python function with a playbook the legal team edits:
- `contract_generation/` — `contract_generation.py` + `SKILL.md`, loaded by `load_skill_prompt()`
- `contract_review/` — `contract_review.py` + the generated per-type bundle in `playbook/` (built by `scripts/build_playbook.py` from `data/contract_review_skills/`, loaded by `load_bundle()` via `skills/grounding.py`)
- `compliance_check.py`, `drafting.py` and `legal_research/` have no SKILL.md — their prompts live in code (`prompts.py` for legal_research)

The playbook is loaded via `load_skill_prompt()` / `load_bundle()` from `skills/base.py`, which wrap it with a **ceiling constraint**: the LLM will ONLY follow the playbook instructions, never improvise from its pretraining knowledge. If the playbook doesn't cover something, the LLM says "Not covered by current playbook" and stops.

This means: **the legal team controls exactly what the LLM outputs by editing SKILL.md**. No code changes needed.

### Skill Types

**Agent Subgraphs** — have their own tools, handle retrieval and LLM calls internally:

| Skill | Tools | What it does |
|---|---|---|
| **contract_generation** | search_legal, get_document, extract_clauses, escalate | Searches case history for clause patterns, generates contracts with deviation report. Always → human_review. |
| **legal_research** | search_legal, get_document, escalate (KB path only) | No document attached: multi-hop retrieval to answer legal questions, cites sources, flags conflicts. Document attached (Word Chat tab): direct LLM call, no tools, grounded on the document + prior review. |

**Plain Skills** — set a system prompt, then the shared `rag_retriever` + `llm_caller` nodes handle retrieval and generation:

| Skill | What it does |
|---|---|
| **contract_review** | Clause-by-clause analysis against the per-type playbook bundle (NDA/MSA/SOW/BAA, auto-detected) with Red/Yellow/Green/Missing Context ratings. Supports uploaded contract files (PDF/DOCX/TXT). |
| **compliance_check** | Policy verification prompt → shared nodes. |
| **drafting** | Document generation prompt → shared nodes. Always → human_review. |

### Agent vs Plain Skill — Graph Behavior

For **agent skills** (contract_generation, legal_research): the skill function calls the LLM and tools itself, sets `llm_response`. The shared `rag_retriever` and `llm_caller` nodes detect this and skip.

For **plain skills** (contract_review, compliance_check, drafting): the skill function sets `messages` (system prompt from the playbook bundle for contract_review, an inline prompt for compliance_check/drafting, plus the user request). The shared nodes then do retrieval and call the LLM with those messages.

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
         │        Sets trace attributes (user_id, session_id)
         ▼
  ┌──────────────┐
  │ INTENT_ROUTER│  Calls Ollama to classify task_type:
  │              │  contract_generation | contract_review | compliance
  └──────┬───────┘  research | drafting
         │          Falls back to "research" if LLM unavailable
         │          Records prompt + classification on an LLM span
         │          Skipped (no LLM call) when the request already carries a valid
         │          task_type — the Word add-in always sends one (contract_review / research)
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
           │        Records full prompt + response on an LLM span
           ▼
    ┌──────────────┐
    │RISK_ASSESSOR │  contract_review: reads the review verdict (Red / Missing
    │              │  Context / "Do not send" = high, Yellow = medium, else low)
    │              │  other skills: no cited source = high risk (an attached
    │              │  document counts as the source) → human_review
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
                │MEMORY_WRITER │  Writes to Postgres audit_log table
                └──────┬───────┘  Every invocation; if app-db is unreachable the entry is logged in full and the turn flagged memory_degraded, never fatal
                       │
                       ▼
                      END → Response to attorney (or interrupt_payload if paused)
```

### Routing Rules

| Condition | Route |
|---|---|
| `task_type` = contract_generation | **always** → human_review |
| `task_type` = drafting | **always** → human_review |
| `task_type` = contract_review | risk from the review verdict (Red / Missing Context or "Do not send for signature" = high, Yellow = medium); → human_review only when high/medium **and** the caller set `interactive_review` (Chainlit does; the Word add-in never interrupts) |
| other `task_type`, `risk_level` = high or medium | → human_review |
| `risk_level` = low | → output_formatter (skip review) |
| `skill_plan` has multiple skills | → planner first (wired, but nothing populates a multi-skill plan today — `intent_router` and `POST /api/query` always set a single entry, so every turn goes straight to skill_dispatcher) |

### human_review verdicts (after resume)

`route_review` reads state after `human_review` returns and decides whether to exit or loop:

| Resume payload | What `human_review` does | Where `route_review` routes |
|---|---|---|
| `approved=True` | Saves notes, clears `awaiting_review` | → output_formatter (terminal approve) |
| `approved=False` + `revised_response` non-empty | Replaces `llm_response` with revised text | → output_formatter (terminal revise) |
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
- Contract generation and drafting always show review buttons; a contract review or any other answer rated high/medium risk does too (Chainlit sends `interactive_review: true`)
- Three actions: **Approve Draft** (generates PDF), **Request Changes**, **Reject**
- **Request Changes** loops back: skill revises previous draft using attorney notes (contract_generation: single-shot LLM call with `reasoning=False`, not the full ReAct agent — ~5x faster; drafting: re-runs through the shared `llm_caller` with the previous draft + notes in the prompt), then re-pauses for review. Capped at 3 iterations; unincorporated notes attached to final report on cap.
- Uses `cl.AskActionMessage` for per-iteration review prompts so buttons stay live across loop-backs (Chainlit disables same-named `cl.Action` after click — `AskActionMessage` blocks inline with fresh buttons each round).
- Session state persisted via RedisSaver checkpointer with 24h TTL refresh on every interaction.

### Development Mode
- Web client: `chainlit run clients/web/app.py -w` (auto-reload on file changes)
- Backend: `uvicorn api.main:app --reload` (auto-reload on file changes)

---

## API Endpoints

| Method | Path | Status | Purpose |
|---|---|---|---|
| GET | `/health` | **Working** | Health check |
| POST | `/api/query` | **Working** | Submit legal request → graph execution (supports `uploaded_text` for review). Returns `interrupt_payload` if graph pauses at `human_review`. |
| POST | `/api/query/{session_id}/resume` | **Working** | Resume after human review with `{approved, notes, revised_response}`. Iterates loop-back (request changes) until approve/reject/cap. |
| GET | `/api/query/{session_id}/status` | Placeholder | Check execution status |
| POST | `/api/compact` | **Working** | Condense this document's earlier conversation into validated verbatim quotes. Body `{document_id, reclaim_chars}` — `reclaim_chars` is how many characters the pane needs freed, measured to the **warn line** rather than the budget, and it tightens the summary without ever affecting what passes the gate; attorney comes from the auth seam, never the body. 200 with `compacted:false` + `reason` when there is nothing to condense **or when the segment would be no smaller than the rows it replaces**; **500** when the validation gate rejects the summary (and nothing is written). |
| POST | `/api/ingest` | **Working** | Upload PDF/DOCX for ingestion |
| GET/PUT | `/api/preferences` | **Working** | Read / replace the calling attorney's `USER.md` (attorney comes from the auth seam; PUT 403s when `PREFERENCES_ENABLED` is off, 413 over `PREFERENCES_MAX_CHARS`) |
| POST | `/api/feedback` | **Working** | An attorney's written report about one turn, keyed to `turn_id`. **Loud**: a store failure is a 500, because the pane showed "Sent" |
| POST | `/api/events` | **Working** | Batched interaction events (Apply / dismiss / pane lifecycle). **Quiet**: always 200 with `recorded: N` |
| POST | `/api/document-snapshot` | **Working** | The pane's copy of the document at a moment (open / apply / idle / finalize). **Quiet**: always 200 with `recorded: 0\|1` |
| GET | `/api/documents` | Not built | List ingested documents |
| GET/DELETE | `/api/documents/{doc_id}` | Not built | Get/delete document |
| GET | `/api/sessions` | Not built | List sessions |
| GET/DELETE | `/api/sessions/{id}` | Not built | Get/delete session |
| GET/PUT | `/api/skills` | Not built | List/toggle skills |
| GET | `/api/reviews/pending` | Not built | Pending human reviews |
| GET | `/api/audit` | Not built | Query audit log |

**Auth:** the `X-User-ID` header (a per-install attorney UUID from the Word pane; a partitioning key, not auth), resolved by `resolve_user_id` in `api/auth.py`. An O365 Bearer-JWT path (`sso_enabled`, validates `aud`/`iss`/`exp`, attorney id = `oid` claim) is shipped but dormant until an Azure app exists.

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
review_iterations,             # loop-backs from human_review (capped by max_review_iterations)
report_notes_unincorporated,   # attorney notes that couldn't be applied (set on iteration cap)
previous_draft,                # llm_response from prior iteration, preserved across loop-back for revise-not-regenerate
proposed_edits,                # structured edit proposals parsed from chat skill output
contract_type_detected,        # nda | msa | sow | baa (set by contract_review when uploaded text present)
requires_attorney,             # server verdict: doc needs attorney sign-off (blocker / yellow)
interactive_review,            # caller can handle a human_review interrupt + resume (Chainlit True, Word False)
document_id,                   # stable doc id — client Office-settings UUID, preamble-hash fallback (review + conversation store key)
memory_degraded,               # True when a memory read/store was unavailable this turn
proposed_preferences,          # plain-text preference suggestions parsed from chat output
user_name,                     # display-only name (self-entered now; SSO `name` claim later); never a key
context_truncated,             # dict set when the document was cut to fit the budget; None on a healthy turn
token_usage,                   # real prompt/completion counts from the LLM response
context_breakdown,             # what this turn's context spent, for the pane's context counter
turn_started_at,               # epoch secs the request began; audit duration_ms derives from it
turn_id                        # the request's turn id; audit_log carries it so its row joins turn_log
```
