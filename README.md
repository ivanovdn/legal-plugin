# Legal Plugin — AI Contract Assistant

> An **air-gapped**, locally-hosted AI assistant that helps an in-house legal team triage, review, and draft contracts against a firm's own playbook — with no data ever leaving the machine.

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-0.6-1C3C3C)
![LLM](https://img.shields.io/badge/LLM-Ollama%20(local)-000000)
![FastAPI](https://img.shields.io/badge/API-FastAPI-009688?logo=fastapi&logoColor=white)
![Word Add-in](https://img.shields.io/badge/Client-Word%20Add--in-2B579A?logo=microsoftword&logoColor=white)
![License](https://img.shields.io/badge/License-Proprietary-lightgrey)

---

## What it is

Attorneys submit a contract or a question through one of three surfaces — a **Chainlit chat UI**, a **Microsoft Word task-pane add-in**, or the **REST API**. A **LangGraph** supervisor graph classifies the request, retrieves grounding context via **hybrid RAG** over the firm's document corpus, runs a **local LLM** (via Ollama), and returns a structured legal deliverable: a clause-by-clause review with risk ratings, redline suggestions, missing-context blockers, and business questions.

The whole system runs on a single workstation. **No external API calls** — contracts, playbooks, and client data stay local.

### Highlights

- **Playbook-grounded, not improvised.** Reviews follow the legal team's own contract playbook verbatim (the `SKILL.md` files are the ceiling — the model may not fall back on pretraining). Per-type playbook bundles auto-detect NDA / MSA / SOW / BAA.
- **Word add-in with native tracked changes.** Runs the review in-pane, applies redlines as real Office tracked changes (Accept/Reject preserved), fills placeholders, and offers a grounded doc-chat for follow-up edits.
- **Hybrid RAG retrieval.** Dense vectors (Qdrant) + BM25 + an optional cross-encoder reranker, always tenant-filtered by `client_id`.
- **SOW ↔ MSA cross-checking.** A SOW review automatically pulls in the governing MSA and flags conflicts per the playbook's precedence rules.
- **Durable memory & audit.** Redis checkpointer for resumable sessions, SQLite audit log of every turn, and persisted reviews the chat tab remembers across turns. Degraded-memory states are surfaced loudly, never silent.
- **Full observability.** Every node and LLM call is traced to Langfuse with token usage.

---

## Architecture

```
Attorney
   │
   ├── Chainlit web UI  (:8080)  ┐
   ├── Word add-in      (:3001)  ├──► FastAPI backend (:8000)  ──►  LangGraph supervisor graph
   └── REST API         (:8000)  ┘                                    │
                                                                      ├── Skills (review, generation, research, compliance, drafting)
                                                                      ├── RAG layer ────► Qdrant (+ BM25 + reranker)
                                                                      ├── LLM ──────────► Ollama (local model)
                                                                      ├── Checkpointer ─► Redis
                                                                      ├── Audit ────────► SQLite
                                                                      └── Tracing ──────► Langfuse
```

**Two-process model.** The FastAPI **backend** owns all business logic — graph execution, RAG, LLM calls, and audit. The **clients** (Chainlit and the Word add-in) are thin surfaces that talk to it over HTTP. `POST /api/query` is the single entry point.

---

## Tech stack

| Layer            | Technology                                                        |
| ---------------- | ----------------------------------------------------------------- |
| Orchestration    | LangGraph + LangChain                                             |
| LLM & embeddings | Ollama (local) — e.g. `qwen3.6`, `embeddinggemma`                 |
| Backend          | FastAPI + Uvicorn (Python 3.12)                                   |
| Vector store     | Qdrant (hybrid: dense + BM25 + optional reranker)                 |
| Web client       | Chainlit                                                          |
| Word client      | Office.js task pane — React + TypeScript + Vite                   |
| Sessions         | Redis (LangGraph checkpointer)                                    |
| Audit            | SQLite                                                            |
| Tracing          | Langfuse (Postgres + ClickHouse + MinIO)                          |
| Tooling          | `uv` for venv/deps, `docker compose` for infra                    |

---

## Repository layout

```
legal-plugin/
├── api/                  FastAPI backend — POST /api/query is the single entry
│   └── routes/           query.py · documents.py (ingest) · health.py
├── graph/                LangGraph supervisor graph
│   ├── state.py          LegalAgentState (TypedDict)
│   ├── graph.py          StateGraph wiring — nodes + edges
│   └── nodes/            intake · intent_router · planner · rag_retriever ·
│                         llm_caller · risk_assessor · human_review · …
├── skills/               Legal capabilities (each with an editable SKILL.md)
│   ├── contract_review/  Per-type playbook bundle (NDA/MSA/SOW/BAA)
│   ├── contract_generation/
│   ├── legal_research.py compliance_check.py  drafting.py  grounding.py
├── rag/                  Qdrant hybrid search, reranker, tenant filtering
├── ingest/               Document parsers + ingestion pipeline
├── memory/               Audit log, review store, document-id resolution
├── observability/        Langfuse integration
├── clients/
│   ├── web/              Chainlit frontend
│   └── word/             Word add-in (see clients/word/README.md)
├── data/                 Corpus, demo contracts, playbook source  (git-ignored)
├── scripts/              start.sh · build_playbook.py · ingestion helpers
├── docs/                 wiki.md (architecture + changelog) + design notes
└── tests/                pytest suite
```

---

## Getting started

### Prerequisites

- **Python 3.12** (managed via [`uv`](https://github.com/astral-sh/uv))
- **Docker** + Docker Compose
- **Ollama** running natively, with the chat and embedding models pulled
  (e.g. `ollama pull qwen3.6` and `ollama pull embeddinggemma`)
- **Node.js** (for the Word add-in only)

### 1 · Configure

```bash
cp .env.example .env
# Set LLM_MODEL, EMBEDDING_MODEL, and QDRANT_VECTOR_DIM to match your Ollama models.
```

### 2 · Start infrastructure

```bash
docker compose up -d      # Qdrant, Redis, Langfuse (+ Postgres, ClickHouse, MinIO)
```

### 3 · Install Python deps

```bash
uv venv .venv --python 3.12
source .venv/bin/activate
uv pip install -r requirements.txt
```

### 4 · Run the backend + web UI

```bash
bash scripts/start.sh
```

| Surface   | URL                             |
| --------- | ------------------------------- |
| Web UI    | http://localhost:8080           |
| API       | http://localhost:8000           |
| API docs  | http://localhost:8000/docs      |
| Langfuse  | http://localhost:3000           |

### 5 · (Optional) Run the Word add-in

```bash
cd clients/word
npm install
npm run dev        # https://localhost:3001/taskpane.html
```

Then sideload the manifest into Word. See **[`clients/word/README.md`](clients/word/README.md)** for certificate setup and the Mac/Windows sideload steps.

---

## Usage

**Web / Word:** upload or open a contract, run a review, and iterate through chat. The review returns risk-rated findings, redline suggestions, missing-context blockers, and business questions. In Word, suggested edits are applied as native tracked changes.

**API:**

```bash
curl -s http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -H "X-User-ID: anonymous" \
  -d '{"query": "Review the attached NDA.", "client_id": "acme"}'
```

Ingest documents into the RAG corpus via `POST /api/ingest` (see `scripts/ingest_all.py` and the ingestion helpers in `scripts/`).

---

## Configuration

All settings live in `.env` (loaded by `config.py` via `pydantic-settings`). Key groups:

- **LLM** — `OLLAMA_BASE_URL`, `LLM_MODEL`, `EMBEDDING_MODEL`, `OLLAMA_NUM_CTX`
- **Retrieval** — `RETRIEVAL_TOP_K`, `MIN_CONFIDENCE_SCORE`, hybrid + reranker knobs
- **Memory** — Redis URL, checkpointer TTL, chat-history depth, context caps
- **Observability** — Langfuse host + keys
- **App** — `API_PORT`, `CHAINLIT_PORT`, `DATABASE_URL` (e.g. `postgresql://legal:legal@localhost:5434/legal`)

See `.env.example` for the full list and `config.py` for defaults.

---

## Development

```bash
# Run the test suite
uv run pytest tests/ -v

# Backend with auto-reload (Uvicorn does NOT auto-reload without --reload)
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# Rebuild the playbook bundle after editing the legal-team source
uv run python scripts/build_playbook.py
```

> ⚠️ **Do not hand-edit `skills/contract_review/playbook/`** — it is generated by `scripts/build_playbook.py` from the canonical legal-team source. Edit the source, then re-run the script.

Contributor conventions and hard-won gotchas (Word add-in quirks, RAG rules, prompt-vs-code fix policy) are documented in **[`CLAUDE.md`](CLAUDE.md)** and **[`docs/wiki.md`](docs/wiki.md)**.

---

## Roadmap

Currently deferred (see `docs/wiki.md` follow-ups):

- Structured JSON output for `contract_review` (unlocks in-Word playbook citations)
- Generate-clause tab in the Word add-in
- Real authentication (currently an `X-User-ID` header)
- Streaming responses (WebSocket/SSE)
- AppSource publishing (sideload-only for now)

---

## License

Proprietary — internal Trinetix project. All rights reserved. Not licensed for external distribution.
