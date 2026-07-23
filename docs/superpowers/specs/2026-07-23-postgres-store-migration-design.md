# Postgres store migration — design

- **Date:** 2026-07-23
- **Status:** Draft — awaiting review
- **Spec 1 of 2** in the "run on company O365 / SharePoint" effort. Spec 2 (deferred) = VM deployment + hosting (reverse proxy, cert, remote Ollama, manifest, sideload). This spec is intentionally self-contained and testable on the dev machine — it does not depend on spec 2.

## Context & motivation

The three relational stores in `memory/` are backed by SQLite (`data/legal.db`). The plugin is moving from a single-user local setup to **multiple legal-team users hitting one backend** (on `SRV-AGENT-01`). Concurrent writes are exactly where SQLite raises `database is locked`, and contract reviews are now real work product that needs centralized, backed-up storage.

Postgres is the right target here — not because "SQLite is bad for prod" (it is fine for single-node, low-concurrency), but for concrete local reasons:

- The stack **already runs Postgres** (Langfuse uses `postgres:17` in compose) — no new technology.
- The workload is now genuinely **multi-user** (concurrent writes).
- It gives one place to back up the legal work product.

## Goals

- Replace SQLite with Postgres for the three relational stores: **audit log, review store, conversation store**.
- Preserve every existing behavior byte-for-byte at the call-site level: loud review writes, best-effort conversation writes, chronological/capped conversation reads, list-shaped review history.
- Add a **dedicated `app-db` Postgres service** to compose, isolated from Langfuse's Postgres.
- Keep the store modules as the single seam so the graph/skills code changes minimally.
- Tests run against real Postgres (test-what-you-ship) via ephemeral containers.

## Non-goals (out of scope for this spec)

- VM deployment, reverse proxy, cert, remote Ollama, manifest, sideload → **spec 2**.
- Moving **Redis** (LangGraph checkpointer — a KV/state role, stays) or **Qdrant** (RAG, stays).
- Moving **preferences** (`memory/preferences.py` = `USER.md` **files**) or **document_id** (`memory/document_id.py` = hashing) — neither touches a DB.
- Migrating existing dev SQLite data (it contains known junk rows). The VM starts fresh; local dev also points at compose Postgres.
- Alembic / a formal migration framework — the idempotent `CREATE TABLE IF NOT EXISTS` pattern is kept at this scale.
- Switching timestamp columns to `TIMESTAMPTZ` (see "Future").

## Current state (verified against the code)

Three modules use `sqlite3` directly, all with the same `db_path`-per-call pattern (open a fresh `sqlite3.connect(db_path)` on every call, no pool):

| Module | Table | Functions |
|---|---|---|
| `memory/audit.py` | `audit_log` | `init_audit_db`, `write_audit_log` |
| `memory/review_store.py` | `review_store` | `init_review_db`, `save_review`, `load_latest_review`, `load_history` |
| `memory/conversation_store.py` | `conversation_store` | `init_conversation_db`, `append_turn`, `load_recent` |

**Production call sites** (all pass `settings.sqlite_path` as the first `db_path` arg):

- `api/main.py:25-26` — `Path(...).parent.mkdir(...)` then `init_audit_db(settings.sqlite_path)` (lifespan).
- `graph/nodes/memory_writer.py` — `init_audit_db` + `write_audit_log`; and, conditionally, `init_review_db` + `save_review`, `init_conversation_db` + `append_turn` (lazy init before each write).
- `skills/legal_research.py:651` — `load_latest_review(get_settings().sqlite_path, document_id)`.
- `skills/legal_research.py:686-687` — `load_recent(settings.sqlite_path, document_id, attorney_id, max)`.

**Config:** `config.py:89` — `sqlite_path: str = "data/legal.db"` (asserted by `tests/test_config.py:70`).

**Compose:** Langfuse's Postgres is `postgres:17`, host-mapped `127.0.0.1:5433:5432`, db/user/pass all `postgres`.

**Behavioral contracts to preserve:**
- Review writes are **LOUD** — `save_review` lets exceptions propagate; `memory_writer` surfaces `report['review_persist_error']`.
- Conversation writes are **best-effort** — `memory_writer` wraps init+append in one try/except, logs, never raises / fails the turn.
- `load_recent` returns chronological (oldest-first), capped at `max_messages`; `<= 0` → `[]` (never SQLite's `LIMIT -1` = unlimited). This guard must survive the port.
- `load_latest_review` / `load_history` order by `id DESC` (newest first).

## Target architecture

### New module: `memory/db.py` (connection pool)

Owns one app-wide `psycopg_pool.ConnectionPool` built lazily from `config.database_url`. Exposes a `connection()` context manager that the stores use. psycopg 3's connection context manager commits on clean exit and rolls back on exception, so stores drop their explicit `commit()`/`close()` bookkeeping.

```python
# memory/db.py  (sketch — imports at top, per project rule #1)
from psycopg_pool import ConnectionPool
from config import get_settings

_pool: ConnectionPool | None = None

def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(get_settings().database_url, min_size=1, max_size=10, open=True)
    return _pool

def reset_pool() -> None:   # for tests: close + drop so a new DSN takes effect
    ...
```

### The three stores

Each store keeps its **module role** but its functions are ported:

- **Drop the `db_path` first parameter** — stores acquire from `get_pool()`. (See "Key decision: signatures" — recommended over threading a DSN.)
- Placeholders `?` → `%s`; `executemany` stays (psycopg 3 supports it).
- DDL dialect: `INTEGER PRIMARY KEY AUTOINCREMENT` → `BIGSERIAL PRIMARY KEY`; `duration_ms INTEGER` → `BIGINT`. Everything else (`TEXT`, `DEFAULT`, indexes) is identical.
- **Timestamps stay `TEXT`** (ISO-8601 strings). All ordering is by `id`, so behavior is byte-identical; no `TIMESTAMPTZ` conversion needed now.
- `LIMIT %s` with the `<= 0 → []` guard preserved in `load_recent`.

DDL (unchanged column semantics, Postgres types):

```sql
CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    timestamp TEXT NOT NULL, session_id TEXT NOT NULL, user_id TEXT NOT NULL,
    skill_name TEXT NOT NULL, task_type TEXT NOT NULL, request_summary TEXT NOT NULL,
    risk_level TEXT NOT NULL DEFAULT 'low',
    review_status TEXT NOT NULL DEFAULT 'not_required',
    review_notes TEXT NOT NULL DEFAULT '', duration_ms BIGINT NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS review_store (
    id BIGSERIAL PRIMARY KEY, timestamp TEXT NOT NULL,
    document_id TEXT NOT NULL, session_id TEXT NOT NULL,
    contract_type TEXT NOT NULL DEFAULT '', review_markdown TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_review_doc ON review_store (document_id, id);
CREATE TABLE IF NOT EXISTS conversation_store (
    id BIGSERIAL PRIMARY KEY, timestamp TEXT NOT NULL,
    document_id TEXT NOT NULL, attorney_id TEXT NOT NULL,
    role TEXT NOT NULL, content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv ON conversation_store (document_id, attorney_id, id);
```

### Init moves to startup

All three `init_*_db()` run **once in `api/main.py` lifespan** (audit already does; add review + conversation). `memory_writer` **drops its lazy `init_*` calls** — with a real DB server, per-write table creation is a SQLite-era habit. If the app-db is unreachable at startup the app fails to boot loudly (matches today's audit-init behavior; the app-db is a core dependency). Mid-session failures keep their existing loud/best-effort semantics at the write functions.

### Config

- Remove `sqlite_path`. Add `database_url: str = "postgresql://legal:legal@localhost:5434/legal"` (dev default; env-overridden on the VM).
- `api/main.py` drops the `Path(...).mkdir(...)` line (no file to create).
- `tests/test_config.py:70` updates to assert the new default.

### Compose: dedicated `app-db` service

Separate container + volume from Langfuse's Postgres, so legal work product has its own lifecycle and backups. Langfuse's DB is left untouched.

```yaml
  app-db:
    image: postgres:17
    environment:
      POSTGRES_DB: legal
      POSTGRES_USER: legal
      POSTGRES_PASSWORD: ${APP_DB_PASSWORD:-legal}
    ports: ["127.0.0.1:5434:5432"]   # 5433 is taken by Langfuse's postgres
    volumes: ["app_db_data:/var/lib/postgresql/data"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U legal"]
      interval: 5s
      timeout: 5s
      retries: 5
# volumes: app_db_data:
```

## Key decision: function signatures (needs your call at review)

**Recommended — Option A: drop `db_path`, use the app-wide pool.** Cleanest end state; matches project rule #5 ("don't add backwards-compat shims — change call sites"). Stores become `save_review(document_id, session_id, markdown, contract_type)` etc. Cost: the `db_path`/DSN arg disappears from ~7 test files and their monkeypatch arities (`lambda p: None` → `lambda: None`; `lambda db, doc_id:` → `lambda doc_id:`). Since every store test is being reworked for Postgres anyway (below), this incremental churn is small.

**Alternative — Option C: first arg becomes a DSN string** (stores look up a pool cached by DSN, like `api/auth.py::_jwks_client`). Call sites pass `config.database_url` instead of `config.sqlite_path`; test monkeypatch arities are unchanged. Lower churn, but threads a DSN through callers that don't need it and keeps a per-DSN pool cache. Rejected as the default because it's a weaker end state for a one-time cost.

This spec is written around **Option A**. Flip at review if you prefer C.

## Interface changes & call sites to update (Option A)

- `api/main.py` — init the pool + run all three `init_*_db()` in lifespan; drop `mkdir`.
- `graph/nodes/memory_writer.py` — drop `db_path=` args and the lazy `init_*` calls; keep the loud-review / best-effort-conversation try/except structure exactly.
- `skills/legal_research.py:651,686` — drop the `sqlite_path` first arg from `load_latest_review` / `load_recent`.
- `config.py` — `sqlite_path` → `database_url`.

## Error handling & behavior preservation

- **Loud review write:** `save_review` still lets exceptions propagate; `memory_writer` still surfaces `review_persist_error`. A dropped connection mid-session → raises → surfaced (unchanged contract).
- **Best-effort conversation write:** `memory_writer`'s single try/except around the conversation write stays; a Postgres error there logs and never fails the turn.
- **Degraded memory:** unchanged. The Redis-down "loud degraded" path (`api/routes/query.py`) is orthogonal — this migration doesn't touch it. (Note: Postgres-down is a *core* failure, not the Redis "degrade and continue" case.)
- **`load_recent` guard:** `max_messages <= 0 → []` preserved (never `LIMIT -1`).

## Testing strategy

The main cost of the migration. Today store tests use `tmp_path` → throwaway SQLite, zero infra. Postgres needs a running server.

- **`testcontainers[postgres]`** (dev dependency): a **session-scoped** conftest fixture starts an ephemeral `postgres:17`, points `config.database_url` at it, resets + opens the pool, runs the three `init_*_db()`.
- A **function-scoped** fixture `TRUNCATE`s the three tables between tests for isolation.
- **Store tests** (`test_audit.py`, `test_review_store.py`, `test_conversation_store.py`, `test_legal_research_conversation.py`) drop `tmp_path`/`db_path` and call the new no-arg-locator signatures against the shared pool.
- **`test_memory_writer.py`** monkeypatches update to the new arities (init lambdas lose `p`; write lambdas keep `**kw`). The critical existing invariant stays covered: a `task_type=="research"` test must still monkeypatch the conversation-write path so no live write escapes (per the CLAUDE.md memory_writer test rule — now the risk is a live *Postgres* write, caught the same way).
- **`test_skills.py` / `test_stale_recall_reconciliation.py`** — `load_latest_review` monkeypatch lambdas drop the db arg.
- **`test_graph.py` / `test_nodes.py`** — `init_audit_db(db_path)` calls become `init_audit_db()` against the fixture pool.
- **`test_config.py:70`** — assert the new `database_url` default.

If testcontainers proves heavy in CI, fallback is a dedicated test database on the compose `app-db` with truncation between tests (documented, not preferred).

## Rollout / migration

- **No data migration.** Fresh `app-db` on the VM; local dev repoints at compose `app-db`. SQLite is dropped entirely (delete `sqlite_path`, remove `data/legal.db` from the dev flow).
- **Dependencies:** add `psycopg[binary]` + `psycopg-pool` to `requirements.txt`; `testcontainers[postgres]` to the test/dev deps.
- **`.env`:** add `APP_DB_PASSWORD` and `DATABASE_URL` (VM override).
- **Order:** land the code + tests behind the new compose service on dev, verify `uv run pytest tests/ -v` green, then it ships with spec 2's VM bring-up.

## Risks & trade-offs

- **Test infra weight** — biggest cost; mitigated by session-scoped container reuse.
- **Two Postgres containers on the VM** (Langfuse's + app-db) — small resource cost, bought deliberately for isolation and independent backups. Lighter alternative (separate `legal` database on Langfuse's server) rejected to avoid entangling work product with Langfuse's managed schema.
- **Startup coupling** — app now hard-fails at boot if app-db is down. Acceptable: it's a core dependency, and audit-init already behaves this way.
- **Sync pool** — matches the existing sync store code; if routes are async, the sync pool runs fine under FastAPI's threadpool. No async rewrite in scope.

## Future (explicitly deferred)

- `TIMESTAMPTZ` columns + native datetime (currently TEXT/ISO; ordering is by `id`, so no functional need yet).
- Alembic once the schema starts evolving.
- FTS cross-matter precedent layer on `review_store` (the store docstring already anticipates it) — unaffected by this migration.

## Task checklist (for the implementation plan)

1. Add `app-db` service + `app_db_data` volume to `docker-compose.yml`; `APP_DB_PASSWORD` to `.env`.
2. Add `psycopg[binary]`, `psycopg-pool` (runtime) and `testcontainers[postgres]` (dev) to requirements.
3. `config.py`: `sqlite_path` → `database_url`.
4. New `memory/db.py`: pool + `connection()` + `reset_pool()`.
5. Port `memory/audit.py`, `memory/review_store.py`, `memory/conversation_store.py` (drop `db_path`, `%s`, `BIGSERIAL`, pool).
6. `api/main.py`: init pool + all three `init_*_db()` in lifespan; drop `mkdir`.
7. `graph/nodes/memory_writer.py`: drop `db_path=` + lazy inits; preserve try/except structure.
8. `skills/legal_research.py`: drop `sqlite_path` arg from the two read calls.
9. conftest Postgres fixtures (session container + per-test truncate); update all affected tests.
10. `uv run pytest tests/ -v` green; update `docs/wiki.md` Shipped + CLAUDE.md backend notes.
```
