# Legal Plugin — Operations

> Part of the [project wiki](wiki.md).

## How to Run

```bash
# 1. Start Docker services (Qdrant, Redis, app-db Postgres, Langfuse v3 + its Postgres, ClickHouse, MinIO)
docker compose up -d

# 2. Start both backend + frontend
bash scripts/start.sh

# Or start individually with auto-reload for development:
uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload
chainlit run clients/web/app.py --port 8080 --host 0.0.0.0 -w

# Access points:
#   Frontend:  http://localhost:8080  (Chainlit chat UI)
#   Word add-in: https://localhost:3001/taskpane.html  (separate terminal: cd clients/word && npm run dev)
#   Backend:   http://localhost:8000  (FastAPI API)
#   API docs:  http://localhost:8000/docs
#   Langfuse:  http://localhost:3000  (admin@local.dev / admin1234)
```

### Prerequisites

- Python 3.12 (venv managed via `uv venv .venv --python 3.12`; install the pinned deps with `uv pip sync requirements.lock`; `cp .env.example .env`)
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

## Docker Services (docker-compose.yml)

| Service | Port | Purpose |
|---|---|---|
| **qdrant** | 6333, 6334 | Vector database: `legal_docs`, `case_history`, `memory`, and the reference books' `legal_books` |
| **redis** | 6379 | Session checkpointer + Langfuse cache (shared, password: `myredissecret`) |
| **langfuse-web** | 3000 | Agent tracing UI |
| **langfuse-worker** | 3030 | Background trace processing |
| **postgres** | 5433 | Langfuse transactional DB |
| **clickhouse** | 8123, 9000 | Langfuse analytics |
| **minio** | 9090, 9091 | Langfuse blob storage |
| **app-db** | 5434 (127.0.0.1) | The app's own Postgres (`legal` DB, created by `memory/db.py`): `audit_log` (with a `sources` JSONB column: [below](#reference-books)), `review_store`, `conversation_store`, `conversation_summary`, `feedback`, `interaction_event`, `turn_log`, `document_version`, `document_snapshot`. Separate from the Langfuse `postgres` on 5433 |

**Running outside Docker (local dev):** Ollama (:11434), the reranker server if one is configured, FastAPI (:8000), Chainlit (:8080), the Word add-in dev server (:3001). Local traces go to the Langfuse services above.

**VM overlay (`docker-compose.remote.yml`):** adds `backend` (the FastAPI image, :8000 in-network), `phoenix` (OTLP sink + UI, in-network `phoenix:6006`, published on host `6007`) and `caddy` (80/443; serves the Word pane built into its image and reverse-proxies `/api/*` to `backend`). The VM runs `redis app-db backend caddy` (+ `phoenix` via `depends_on`); Qdrant (`QDRANT_REMOTE_URL`) and Ollama are on Spark, and the local Langfuse services are not started. See [docs/deploy-vm.md](deploy-vm.md) Step 4.

---

## Configuration

All config via `.env` loaded by `pydantic-settings` in `config.py`. Import anywhere: `from config import get_settings`.

Key settings:
- `LLM_MODEL` — Ollama model name (e.g., `qwen3.6:latest`)
- `EMBEDDING_MODEL` — embedding model (e.g., `embeddinggemma:latest`)
- `QDRANT_VECTOR_DIM` — must match embedding model (768 for embeddinggemma)
- `RERANKER_ENABLED` — toggle reranker on/off; it also needs `RERANKER_BACKEND`, `RERANKER_URL` and `RERANKER_MODEL` ([rag.md](rag.md#where-each-piece-runs))
- `BM25_ENABLED` — toggle the pure-Python BM25 keyword search; keep it off ([why](rag.md#why-keyword-search-is-off))
- `DATABASE_URL` — Postgres DSN for the audit/review/conversation stores (default: `postgresql://legal:legal@localhost:5434/legal`, the `app-db` container)
- `DB_POOL_TIMEOUT` — seconds to wait for a pooled connection before a store call fails and the turn degrades (default: `3.0`). `psycopg_pool`'s own default is 30s; measured against an unreachable host, that is 30.00s of dead pane per store call versus 3.00s. `@lru_cache`'d in `get_settings` → needs a `start.sh` restart.
- `CHAT_CONTEXT_MAX_CHARS` — the doc-chat character budget (default `150000`, deliberately below `OLLAMA_NUM_CTX` × chars-per-token). This is the number the pane's gauge is a percentage of, and the only thing `_cap_chat_context` truncates when it is exceeded is the **document** — never the playbook, MSA or findings. Lowering it is the fastest way to exercise the truncation and compaction paths by hand; it is also how the fabricated-completion bug in the follow-ups below was reproduced deterministically, so put it back to `150000` when done.
- `COMPACTION_*` — `COMPACTION_ENABLED`, `COMPACTION_KEEP_RECENT_MESSAGES` (floor kept verbatim, default `2`), `COMPACTION_MIN_QUOTES` / `COMPACTION_MAX_QUOTES` (`4`/`24`), `COMPACTION_WARN_PCT` (`90` — the share of budget at which the Condense action appears, and the line the pane's reclaim target is measured to), `COMPACTION_MAX_INJECTED_SEGMENTS` (`3`), `COMPACTION_AUTO` (`true` — fire without a click), `COMPACTION_AUTO_MIN_CHARS` (`4000` — the anti-churn floor for firing unasked, which the manual button does not share; derived from segment overhead, and it must stay well under what history can actually hold or auto cannot arm until the document has already been truncated). All `@lru_cache`'d in `get_settings` → a `start.sh` restart is required. **`COMPACTION_AUTO=false` is the switch to set on a machine that is about to be demoed on**, since automatic firing puts a background 10–30s summarisation on the shared Ollama.
- `BOOK_*` — the reference books ([rag.md](rag.md#book-settings) has each default and what it does): `BOOK_COLLECTION` (`legal_books`), `BOOK_CHUNK_MAX_TOKENS` (`800`), `BOOK_SEARCH_TOP_K` (`8`), `BOOK_DENSE_CANDIDATES` / `BOOK_SPARSE_CANDIDATES` (`30` / `30`), `BOOK_MIN_SCORE` (`0` = off) and `BOOK_LOOKUP_MAX_TOKENS` (`6000`). All are recorded on every `turn_log` row, and `@lru_cache`'d in `get_settings` → restart.
- `OTEL_EXPORTER_OTLP_ENDPOINT` / `OTEL_EXPORTER_OTLP_HEADERS` — where spans go. Defaults to local Langfuse v3 (`http://localhost:3000/api/public/otel`); the headers carry its basic auth, `Authorization=Basic <base64 of pk-lf-local:sk-lf-local>` (pre-seeded, see `.env.example`). The VM sets the endpoint to `http://phoenix:6006` in `docker-compose.remote.yml` and sends no header. Also `TRACING_ENABLED` (default `true`), `OTEL_SERVICE_NAME` (`legal-triage`), `OTEL_INSTRUMENT_REDIS` (default `false`). **There is no `LANGFUSE_*` setting: `config.py` rejects unknown `.env` keys, so adding one stops the backend starting.** All `@lru_cache`'d → restart.

---

## Reference books

The reference books (Circular 92 first; design in [rag.md](rag.md#reference-books)) are ingested by an admin command, never by the add-in's upload or `POST /api/ingest`.

```bash
# dry run: parses and chunks, writes the report and the chunk list, never touches Qdrant
uv run python -m scripts.ingest_book circ92 tests/fixtures/books/circular-92-2025-12.pdf --dry-run
# the real ingest: refuses unless the report's bar passes
uv run python -m scripts.ingest_book circ92 tests/fixtures/books/circular-92-2025-12.pdf
```

- **Needs:** Ollama (token counts and embeddings) and Qdrant, with `QDRANT_VECTOR_DIM` set to the embedding model's dimension (a real ingest with `0` refuses).
- **Exit codes:** `0` done; `1` the bar failed, a vector of the wrong size, an embedding over the model's window, or a point count that doesn't match; `2` refused (unregistered book, no PDF, or a PDF whose SHA-256 isn't the registered edition's).
- **Files:** a dry run writes `<ref_prefix>-<UTC stamp>-{chunks.jsonl, chunks.tsv, report.json, sample20.tsv}` to `data/deploy-logs/` (for example `circ92-2025-12-20261103T101500Z-report.json`). `data/` is excluded from git and from the backend image.
- **Re-running** replaces the points of that book and edition only. The embeddings are all computed before anything is deleted, so a failure midway leaves the earlier ingest as it was.
- **On the VM** the command runs inside the backend container, whose image excludes `data/`. The PDF must be at `data/books/` on the host and mounted into the container, and `data/` mounted for the dry-run files, which the overlay doesn't do yet. What it takes and the order: [deploy-vm.md](deploy-vm.md#reference-books-not-yet-rolled-out).
- **The audit column.** Every `audit_log` row has `sources` JSONB: `{"doc_ids": […], "ref_ids_retrieved": […], "ref_ids_cited": […]}`, for that turn only, `{}` on rows from before it existed. `memory/db.py` adds it with `ALTER TABLE … ADD COLUMN IF NOT EXISTS` at startup, so a deploy needs no migration step. Which book passages a turn cited: `SELECT turn_id, sources->'ref_ids_cited' FROM audit_log WHERE sources->'ref_ids_cited' <> '[]' ORDER BY id DESC LIMIT 20;`
- **Retrieval scores:** `BOOK_SEARCH_TOP_K=10 uv run python -m evals.books.run_retrieval <questions file>` (Ollama and Qdrant; not part of `check.sh`).

---

## Tests

The backend suite needs Docker running (`tests/conftest.py` starts an ephemeral Postgres via testcontainers for the whole session, and an ephemeral Qdrant, at the image `docker-compose.yml` pins, once the first test that needs one runs: the book-store, book-search and book-ingest tests). `bash scripts/check.sh` is the pre-push gate, and the only one (there is no CI): the locks, the backend tests, the degradation-vocabulary, wiki and docs-link checks, the add-in typecheck and assertions, the evals, the backend image build and the Caddyfile check.

```bash
uv run pytest tests/ -q      # backend tests only
bash scripts/check.sh        # everything
```

Coverage includes: graph compilation + routing + audit, end-to-end interrupt/resume/loop/cap with `MemorySaver`, Redis checkpointer factory, FastAPI endpoints (submit + resume + interrupt detection via `__interrupt__` key), all five skills with mocked agents, RAG layer (embeddings, vector store, BM25, hybrid search, reranker), DOCX/PDF parsers, ingest pipeline, and config loading.
