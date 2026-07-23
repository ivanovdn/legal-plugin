# Postgres Store Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the three SQLite-backed relational stores (audit log, review store, conversation store) to a dedicated Postgres `app-db`, with an app-wide psycopg connection pool, ahead of the multi-user VM deployment.

**Architecture:** A new `memory/db.py` owns one autocommit `psycopg_pool.ConnectionPool` built from `config.database_url`, plus a single `init_db()` that creates all three tables (idempotent). The three store modules drop their per-call `db_path` argument and their individual `init_*_db()` functions, acquiring connections from the pool instead. Callers (`memory_writer`, `legal_research`, `api/main.py`) stop threading `sqlite_path`. Tests run against a real ephemeral Postgres via testcontainers.

**Tech Stack:** Python 3.12 · psycopg 3 (`psycopg[binary]`) + `psycopg-pool` · testcontainers[postgres] · Postgres 17 · pytest.

> **Deviation from spec (approved shape unchanged in intent):** The spec listed "three `init_*_db()` run in lifespan." This plan consolidates them into **one `memory/db.py::init_db()`** (single DDL home — DRY, and it removes a test-fixture ordering problem). Init still happens once at startup, exactly as the spec intends.

## Global Constraints

- **Python 3.12**; use **uv** for deps (`uv pip install -r requirements.txt`, `uv run pytest`).
- **All imports at top of file** — never inside functions (project rule #1).
- **No backwards-compat shims — change call sites** (project rule #5). `config.sqlite_path` and `config.database_url` may coexist *during* the migration (an in-progress branch state), but `sqlite_path` MUST be gone by the final task.
- **psycopg 3 only** (not psycopg2). Pool connections are **autocommit** — stores issue single-statement writes/reads, so no explicit transaction/commit code.
- **Docker must be running for the test suite** (new — testcontainers starts one Postgres per test session). This is a deliberate, documented cost.
- **Behavior contracts to preserve byte-for-byte:**
  - Review writes are **LOUD** — `save_review` lets exceptions propagate; `memory_writer` surfaces `report['review_persist_error']`.
  - Conversation writes are **best-effort** — `memory_writer` wraps them in try/except, logs, never raises / fails the turn.
  - `load_recent` returns chronological (oldest-first), capped; `max_messages <= 0 → []` (never unlimited).
  - `load_latest_review` / `load_history` order by `id DESC` (newest first).
- After changing `config.py` or `docker-compose.yml`, a running backend needs `bash scripts/start.sh` restart (does not affect tests).

## File Structure

**Created:**
- `memory/db.py` — the pool + `get_pool()` / `connection()` / `init_db()` / `reset_pool()`; owns all DDL.
- `tests/conftest.py` — session-scoped testcontainers Postgres fixture + autouse per-test table truncation.
- `tests/test_db.py` — pool + `init_db` smoke tests.

**Modified (source):**
- `config.py` — `sqlite_path` → `database_url`.
- `requirements.txt` — add psycopg/psycopg-pool/testcontainers; drop unused `aiosqlite`.
- `docker-compose.yml` — add `app-db` service + `app_db_data` volume.
- `.env.example` — add `DATABASE_URL` / `APP_DB_PASSWORD`.
- `memory/audit.py` — port `write_audit_log`; remove `init_audit_db`.
- `memory/review_store.py` — port `save_review` / `load_latest_review` / `load_history`; remove `init_review_db`.
- `memory/conversation_store.py` — port `append_turn` / `load_recent`; remove `init_conversation_db`.
- `graph/nodes/memory_writer.py` — drop `db_path` args, module-init globals, and lazy `init_*` calls.
- `skills/legal_research.py` — drop `sqlite_path` arg from the two read calls.
- `api/main.py` — call `init_db()` in lifespan; drop `mkdir` + `init_audit_db`.

**Modified (tests):** `test_config.py`, `test_audit.py`, `test_review_store.py`, `test_conversation_store.py`, `test_memory_writer.py`, `test_legal_research_conversation.py`, `test_review_roundtrip.py`, `test_skills.py`, `test_stale_recall_reconciliation.py`, `test_graph.py`, `test_nodes.py`.

**Docs (final task):** `docs/wiki.md`, `CLAUDE.md`.

---

### Task 1: Foundation — deps, config, compose, pool, test harness

Additive. Stores stay on SQLite this task; the pool + container come online and are proven by a smoke test. `sqlite_path` is kept (removed in Task 5) so nothing else breaks yet.

**Files:**
- Modify: `requirements.txt`
- Modify: `config.py:89`
- Modify: `docker-compose.yml` (add service + volume)
- Modify: `.env.example`
- Create: `memory/db.py`
- Create: `tests/conftest.py`
- Create: `tests/test_db.py`
- Modify: `tests/test_config.py:40,70`

**Interfaces:**
- Produces:
  - `memory.db.get_pool() -> psycopg_pool.ConnectionPool`
  - `memory.db.init_db() -> None` (creates `audit_log`, `review_store` (+`idx_review_doc`), `conversation_store` (+`idx_conv`); idempotent)
  - `memory.db.reset_pool() -> None`
  - `config.Settings.database_url: str` (default `"postgresql://legal:legal@localhost:5434/legal"`)

- [ ] **Step 1: Update `requirements.txt`**

Replace the `# Audit` block (the `aiosqlite` line — confirmed unused via `grep -rn aiosqlite`) and add the DB driver + test container deps. Final relevant sections:

```
# App relational store (Postgres)
psycopg[binary]>=3.2,<4.0
psycopg-pool>=3.2,<4.0

# Observability
langfuse>=2.0,<3.0

# Frontend
chainlit>=2.0,<3.0

# HTTP client (for reranker, Ollama direct calls)
httpx>=0.28,<1.0

# Auth (O365 SSO — slice 3; dormant until sso_enabled)
PyJWT[crypto]>=2.9,<3.0

# Testing
pytest>=8.0,<9.0
pytest-asyncio>=0.24,<1.0
testcontainers[postgres]>=4.0,<5.0
```

(Delete the `# Audit\naiosqlite>=0.20,<1.0` lines.) Then install:

Run: `uv pip install -r requirements.txt`

- [ ] **Step 2: Update `tests/test_config.py` (write the config expectation first)**

Additive — KEEP the existing `SQLITE_PATH` env (line 40) and `sqlite_path` assertion (line 70); they are removed later in Task 5. ADD, alongside them, a `DATABASE_URL` env and a `database_url` assertion in `test_config_loads_from_env`:

```python
# among the other setenv calls (near line 40):
    monkeypatch.setenv("DATABASE_URL", "postgresql://legal:legal@localhost:5434/legal")
# among the assertions (near line 70):
    assert settings.database_url == "postgresql://legal:legal@localhost:5434/legal"
```

- [ ] **Step 3: Run the config test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_config_loads_from_env -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'database_url'`.

- [ ] **Step 4: Update `config.py`**

Additive — ADD `database_url` next to the existing `sqlite_path` line (line 89); KEEP `sqlite_path` (it is still used by the unported stores/callers until Task 5 removes it):

```python
    sqlite_path: str = "data/legal.db"
    database_url: str = "postgresql://legal:legal@localhost:5434/legal"
```

The config test now passes; the rest of the suite is unaffected — the stores still take `db_path` (SQLite) and `sqlite_path` still resolves.

Run: `uv run pytest tests/test_config.py::test_config_loads_from_env -v`
Expected: PASS.

- [ ] **Step 5: Add the `app-db` service to `docker-compose.yml`**

Add this service (host port **5434** — 5433 is Langfuse's Postgres) after the existing `postgres:` block:

```yaml
  app-db:
    image: postgres:17
    restart: unless-stopped
    environment:
      POSTGRES_DB: "legal"
      POSTGRES_USER: "legal"
      POSTGRES_PASSWORD: "${APP_DB_PASSWORD:-legal}"
      TZ: "UTC"
      PGTZ: "UTC"
    ports:
      - "127.0.0.1:5434:5432"
    volumes:
      - app_db_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U legal"]
      interval: 3s
      timeout: 3s
      retries: 10
```

And add `app_db_data:` under the top-level `volumes:` block (alongside `redis_data:` etc.):

```yaml
volumes:
  redis_data:
  postgres_data:
  clickhouse_data:
  clickhouse_logs:
  minio_data:
  app_db_data:
```

Run: `docker compose config -q`
Expected: no output, exit 0 (compose file is valid).

- [ ] **Step 6: Update `.env.example`**

Add (and remove any existing `SQLITE_PATH=` line if present):

```
# App relational store (Postgres)
DATABASE_URL=postgresql://legal:legal@localhost:5434/legal
APP_DB_PASSWORD=legal
```

- [ ] **Step 7: Create `memory/db.py`**

```python
# memory/db.py
"""Postgres connection pool for the relational stores (audit, review, conversation).

One app-wide psycopg pool built from config.database_url. Connections are
autocommit — the stores issue single-statement writes/reads, so no explicit
transaction management is needed. init_db() creates every store table
(idempotent) and is called once at startup (api/main.py lifespan) and by the
test fixture.
"""
from __future__ import annotations

import logging

from psycopg_pool import ConnectionPool

from config import get_settings

logger = logging.getLogger(__name__)

_pool: ConnectionPool | None = None

_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        id BIGSERIAL PRIMARY KEY,
        timestamp TEXT NOT NULL,
        session_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        skill_name TEXT NOT NULL,
        task_type TEXT NOT NULL,
        request_summary TEXT NOT NULL,
        risk_level TEXT NOT NULL DEFAULT 'low',
        review_status TEXT NOT NULL DEFAULT 'not_required',
        review_notes TEXT NOT NULL DEFAULT '',
        duration_ms BIGINT NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS review_store (
        id BIGSERIAL PRIMARY KEY,
        timestamp TEXT NOT NULL,
        document_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        contract_type TEXT NOT NULL DEFAULT '',
        review_markdown TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_review_doc ON review_store (document_id, id)",
    """
    CREATE TABLE IF NOT EXISTS conversation_store (
        id BIGSERIAL PRIMARY KEY,
        timestamp TEXT NOT NULL,
        document_id TEXT NOT NULL,
        attorney_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_conv ON conversation_store (document_id, attorney_id, id)",
]


def get_pool() -> ConnectionPool:
    """Return the app-wide pool, opening it on first use."""
    global _pool
    if _pool is None:
        dsn = get_settings().database_url
        _pool = ConnectionPool(dsn, min_size=1, max_size=10,
                               kwargs={"autocommit": True}, open=True)
        logger.info("Postgres pool opened")
    return _pool


def init_db() -> None:
    """Create all store tables + indexes if absent. Idempotent."""
    with get_pool().connection() as conn:
        for stmt in _STATEMENTS:
            conn.execute(stmt)
    logger.info("Store schema initialized")


def reset_pool() -> None:
    """Close and drop the pool so a new DSN (e.g. in tests) takes effect on next get_pool()."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
```

- [ ] **Step 8: Create `tests/conftest.py`**

```python
# tests/conftest.py
"""Shared test fixtures: an ephemeral Postgres for the store layer.

A single container is started for the whole test session; every test runs
against clean tables (truncated before each test). Docker must be running.
"""
import os

import pytest
from testcontainers.postgres import PostgresContainer


@pytest.fixture(scope="session", autouse=True)
def _pg_container():
    import memory.db as db
    from config import get_settings

    with PostgresContainer("postgres:17") as pg:
        # testcontainers defaults to the psycopg2 driver in the URL; psycopg 3
        # wants a plain postgresql:// DSN.
        dsn = pg.get_connection_url().replace("+psycopg2", "")
        os.environ["DATABASE_URL"] = dsn
        get_settings.cache_clear()
        db.reset_pool()
        db.init_db()
        yield pg
        db.reset_pool()
        os.environ.pop("DATABASE_URL", None)


@pytest.fixture(autouse=True)
def _clean_tables(_pg_container):
    import memory.db as db
    with db.get_pool().connection() as conn:
        conn.execute(
            "TRUNCATE audit_log, review_store, conversation_store RESTART IDENTITY"
        )
    yield
```

- [ ] **Step 9: Create `tests/test_db.py`**

```python
# tests/test_db.py
def test_pool_roundtrips_a_query():
    from memory.db import get_pool
    with get_pool().connection() as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1


def test_init_db_creates_all_tables():
    from memory.db import get_pool
    with get_pool().connection() as conn:
        for table in ("audit_log", "review_store", "conversation_store"):
            row = conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()
            assert row[0] is not None, f"{table} missing"
```

- [ ] **Step 10: Run the new tests (Docker must be running)**

Run: `uv run pytest tests/test_db.py tests/test_config.py -v`
Expected: PASS (container starts once, both files green).

- [ ] **Step 11: Run the full suite to confirm nothing regressed**

Run: `uv run pytest tests/ -q`
Expected: PASS — SQLite store tests still use `tmp_path` and are unaffected; the container just runs alongside.

- [ ] **Step 12: Commit**

```bash
git add requirements.txt config.py docker-compose.yml .env.example memory/db.py tests/conftest.py tests/test_db.py tests/test_config.py
git commit -m "feat(db): Postgres pool + app-db service + test container harness

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Port the audit store

Port `write_audit_log` to the pool; remove `init_audit_db` (now `init_db()`). Update its callers (`memory_writer`, `api/main.py`) and the audit/graph/node tests. After this task the audit path is on Postgres; review + conversation remain on SQLite (still driven by `settings`-free `db_path` args they own).

**Files:**
- Modify: `memory/audit.py`
- Modify: `graph/nodes/memory_writer.py:16,25-30,34-35,10` (audit init + call)
- Modify: `api/main.py:12,25-26`
- Modify: `tests/test_audit.py`
- Modify: `tests/test_memory_writer.py` (audit monkeypatches)
- Modify: `tests/test_graph.py` (remove audit-DB setup; port the one audit read)
- Modify: `tests/test_nodes.py` (remove audit-DB setup)

**Interfaces:**
- Consumes: `memory.db.get_pool` (Task 1).
- Produces: `memory.audit.write_audit_log(session_id, user_id, skill_name, task_type, request_summary, risk_level="low", review_status="not_required", review_notes="", duration_ms=0) -> None`. `init_audit_db` no longer exists.

- [ ] **Step 1: Rewrite `tests/test_audit.py` (failing test first)**

```python
# tests/test_audit.py
from memory.audit import write_audit_log
from memory.db import get_pool


def test_write_audit_log_inserts_a_record():
    write_audit_log(
        session_id="sess-001", user_id="attorney-1", skill_name="legal_research",
        task_type="research", request_summary="What are indemnification standards?",
        risk_level="low", review_status="not_required", review_notes="", duration_ms=1200,
    )
    with get_pool().connection() as conn:
        rows = conn.execute("SELECT * FROM audit_log").fetchall()
    assert len(rows) == 1


def test_write_audit_log_multiple():
    for i in range(3):
        write_audit_log(
            session_id=f"sess-{i}", user_id="attorney-1", skill_name="compliance_check",
            task_type="compliance", request_summary=f"Check {i}",
            risk_level="low", review_status="not_required", review_notes="", duration_ms=500,
        )
    with get_pool().connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    assert count == 3
```

Run: `uv run pytest tests/test_audit.py -v`
Expected: FAIL — `write_audit_log` still requires `db_path` (TypeError).

- [ ] **Step 2: Port `memory/audit.py`**

```python
# memory/audit.py
"""Audit log — records every skill invocation (Postgres-backed)."""

import logging
from datetime import datetime, timezone

from memory.db import get_pool

logger = logging.getLogger(__name__)


def write_audit_log(
    session_id: str,
    user_id: str,
    skill_name: str,
    task_type: str,
    request_summary: str,
    risk_level: str = "low",
    review_status: str = "not_required",
    review_notes: str = "",
    duration_ms: int = 0,
) -> None:
    """Write a single audit log entry."""
    with get_pool().connection() as conn:
        conn.execute(
            """INSERT INTO audit_log
               (timestamp, session_id, user_id, skill_name, task_type,
                request_summary, risk_level, review_status, review_notes, duration_ms)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                datetime.now(timezone.utc).isoformat(),
                session_id, user_id, skill_name, task_type,
                request_summary, risk_level, review_status, review_notes, duration_ms,
            ),
        )
    logger.info("Audit log: %s/%s for user %s", skill_name, task_type, user_id)
```

Run: `uv run pytest tests/test_audit.py -v`
Expected: PASS.

- [ ] **Step 3: Update `graph/nodes/memory_writer.py` (audit portion only)**

Change the import on line 10 and remove the audit init. Edits:

- Line 10: `from memory.audit import init_audit_db, write_audit_log` → `from memory.audit import write_audit_log`
- Line 16: delete `_db_initialized = False`
- Lines 25-31 (inside the function): delete the `global _db_initialized` line and the `if not _db_initialized: init_audit_db(...) ; _db_initialized = True` block.
- Lines 34-45: remove the `db_path=settings.sqlite_path,` argument from the `write_audit_log(...)` call (keep every other argument).

Resulting top of function:

```python
@observe(name="memory_writer")
def memory_writer(state: LegalAgentState) -> dict:
    settings = get_settings()

    review_status = "pending" if state.get("awaiting_review") else "not_required"

    write_audit_log(
        session_id=state.get("session_id", ""),
        user_id=state.get("user_id", ""),
        skill_name=state.get("task_type", "unknown"),
        task_type=state.get("task_type", ""),
        request_summary=state.get("request", "")[:200],
        risk_level=state.get("risk_level", "low"),
        review_status=review_status,
        review_notes=state.get("attorney_notes", ""),
        duration_ms=0,
    )
```

(Leave the review + conversation blocks below untouched this task — they still call `init_review_db`/`save_review`/`init_conversation_db`/`append_turn` with `db_path=settings.sqlite_path`.)

- [ ] **Step 4: Update `api/main.py`**

- Line 12: `from memory.audit import init_audit_db` → `from memory.db import init_db`
- Lines 25-26: replace

```python
    Path(settings.sqlite_path).parent.mkdir(parents=True, exist_ok=True)
    init_audit_db(settings.sqlite_path)
```

with

```python
    init_db()
```

Also remove the now-unused `from pathlib import Path` import (line 6) if `Path` is used nowhere else in the file (grep to confirm). `settings` may now be unused in lifespan except for the log line — keep `settings = get_settings()` and the `settings.api_port` log; it is still referenced there.

- [ ] **Step 5: Update `tests/test_memory_writer.py` (audit + init monkeypatches)**

Across every test in this file, **remove** these monkeypatch lines (audit init no longer exists in the module):
- `monkeypatch.setattr(mod, "init_audit_db", lambda p: None)` — delete all occurrences.

Keep `monkeypatch.setattr(mod, "write_audit_log", lambda **kw: None)` (the call is now all-kwargs — still matches `**kw`). Leave the review/conversation monkeypatches for Tasks 3/4.

Run: `uv run pytest tests/test_memory_writer.py::test_persists_review_for_contract_review_turn -v`
Expected: PASS (this test still monkeypatches `init_review_db`/`save_review`, unchanged this task).

- [ ] **Step 6: Update `tests/test_graph.py` and `tests/test_nodes.py` (remove audit-DB setup)**

- `tests/test_graph.py` line 9: delete `from memory.audit import init_audit_db`.
- In **every** test, delete the audit-DB setup lines wherever they appear together:
  - `db_path = str(tmp_path / "...")`  (delete — now unused; see the one exception below)
  - `monkeypatch.setenv("SQLITE_PATH", db_path)`  (delete)
  - `init_audit_db(db_path)`  (delete)
  - `mw._db_initialized = True`  (delete)
  Keep any `get_settings.cache_clear()` (still needed for other env like `LLM_MODEL`).
  There are 14 `init_audit_db(` call sites (grep to find them all). Also delete the local `from memory.audit import init_audit_db` imports inside individual test functions.
- **The one audit-read test** (around line 355, currently reading via `sqlite3`): replace

```python
    import sqlite3
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT * FROM audit_log").fetchall()
    conn.close()
    assert len(rows) >= 1
```

with

```python
    from memory.db import get_pool
    with get_pool().connection() as conn:
        rows = conn.execute("SELECT * FROM audit_log").fetchall()
    assert len(rows) >= 1
```

- `tests/test_nodes.py` (around lines 530-540): delete the same setup triplet (`monkeypatch.setenv("SQLITE_PATH", ...)`, `init_audit_db(db_path)`, `mw._db_initialized = True`) and the local `from memory.audit import init_audit_db` import. Delete the now-unused `db_path = str(tmp_path / "test_legal.db")` line.

Verify no stragglers:

Run: `grep -rn "init_audit_db\|SQLITE_PATH\|_db_initialized" tests/`
Expected: no matches.

- [ ] **Step 7: Run the affected suites**

Run: `uv run pytest tests/test_audit.py tests/test_memory_writer.py tests/test_graph.py tests/test_nodes.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add memory/audit.py graph/nodes/memory_writer.py api/main.py tests/test_audit.py tests/test_memory_writer.py tests/test_graph.py tests/test_nodes.py
git commit -m "feat(db): port audit store to Postgres pool

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Port the review store

Port `save_review` / `load_latest_review` / `load_history`; remove `init_review_db`. Update `memory_writer` (review block) and `legal_research` (recall read), plus the review tests. Conversation store stays on SQLite until Task 4.

**Files:**
- Modify: `memory/review_store.py`
- Modify: `graph/nodes/memory_writer.py` (review block: lines ~12, 51-63)
- Modify: `skills/legal_research.py:651`
- Modify: `tests/test_review_store.py`
- Modify: `tests/test_review_roundtrip.py`
- Modify: `tests/test_memory_writer.py` (review monkeypatches)
- Modify: `tests/test_skills.py` (load_latest_review monkeypatch arities)
- Modify: `tests/test_stale_recall_reconciliation.py` (load_latest_review monkeypatch arities)

**Interfaces:**
- Consumes: `memory.db.get_pool`.
- Produces:
  - `save_review(document_id, session_id, markdown, contract_type) -> None`
  - `load_latest_review(document_id) -> dict | None`  (dict keys: `timestamp`, `session_id`, `contract_type`, `markdown`)
  - `load_history(document_id) -> list[dict]`  (newest first)
  - `init_review_db` no longer exists.

- [ ] **Step 1: Rewrite `tests/test_review_store.py` (failing tests first)**

```python
# tests/test_review_store.py
"""Persisted review store — append-per-session, latest lookup, loud writes."""
import pytest

import memory.review_store as rs
from memory.review_store import save_review, load_latest_review, load_history


def test_save_then_load_latest():
    save_review("doc-1", "sess-1", "# Review\nFinding A", "sow")
    latest = load_latest_review("doc-1")
    assert latest["markdown"] == "# Review\nFinding A"
    assert latest["session_id"] == "sess-1"
    assert latest["contract_type"] == "sow"


def test_append_keeps_history_latest_wins():
    save_review("doc-1", "sess-1", "first", "sow")
    save_review("doc-1", "sess-2", "second", "sow")
    assert load_latest_review("doc-1")["markdown"] == "second"
    history = load_history("doc-1")
    assert [h["markdown"] for h in history] == ["second", "first"]  # newest first


def test_load_latest_returns_none_when_absent():
    assert load_latest_review("no-such-doc") is None


def test_save_raises_loudly_on_store_failure(monkeypatch):
    # A store failure must propagate — never a silent no-op (the user believes
    # the review saved). We simulate the failure at the pool boundary.
    def _boom():
        raise RuntimeError("pool down")
    monkeypatch.setattr(rs, "get_pool", _boom)
    with pytest.raises(Exception):
        save_review("doc-1", "s", "md", "sow")
```

Run: `uv run pytest tests/test_review_store.py -v`
Expected: FAIL — signatures still require `db_path`.

- [ ] **Step 2: Port `memory/review_store.py`**

```python
# memory/review_store.py
"""Postgres store for persisted contract reviews, keyed to a document_id.

Stores the FULL markdown review, one row per (document_id, session_id) — list-
shaped so a re-review appends a new row rather than overwriting. The natural
home for the later FTS cross-matter precedent layer.

Writes are LOUD: save_review lets exceptions propagate. A lost review write must
never be silent — the user believes their review was saved.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from memory.db import get_pool

logger = logging.getLogger(__name__)


def save_review(document_id: str, session_id: str, markdown: str, contract_type: str) -> None:
    """Append one review row. Raises on any failure — never a silent no-op."""
    with get_pool().connection() as conn:
        conn.execute(
            """INSERT INTO review_store
               (timestamp, document_id, session_id, contract_type, review_markdown)
               VALUES (%s, %s, %s, %s, %s)""",
            (
                datetime.now(timezone.utc).isoformat(),
                document_id, session_id, contract_type, markdown,
            ),
        )
    logger.info("Review saved: document_id=%s session=%s", document_id, session_id)


def _row_to_dict(row: tuple) -> dict:
    return {
        "timestamp": row[0], "session_id": row[1],
        "contract_type": row[2], "markdown": row[3],
    }


def load_latest_review(document_id: str) -> dict | None:
    """Most recent review for this document, or None."""
    if not document_id:
        return None
    with get_pool().connection() as conn:
        cur = conn.execute(
            """SELECT timestamp, session_id, contract_type, review_markdown
               FROM review_store WHERE document_id = %s ORDER BY id DESC LIMIT 1""",
            (document_id,),
        )
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def load_history(document_id: str) -> list[dict]:
    """All reviews for this document, newest first."""
    if not document_id:
        return []
    with get_pool().connection() as conn:
        cur = conn.execute(
            """SELECT timestamp, session_id, contract_type, review_markdown
               FROM review_store WHERE document_id = %s ORDER BY id DESC""",
            (document_id,),
        )
        rows = cur.fetchall()
    return [_row_to_dict(r) for r in rows]
```

Run: `uv run pytest tests/test_review_store.py -v`
Expected: PASS.

- [ ] **Step 3: Update `graph/nodes/memory_writer.py` (review block)**

- Line 12: `from memory.review_store import init_review_db, save_review` → `from memory.review_store import save_review`
- Line 17: delete `_review_db_initialized = False`
- Inside the review block: delete `global _review_db_initialized` and the `if not _review_db_initialized: init_review_db(...) ; _review_db_initialized = True` lines.
- In the `save_review(...)` call: remove `db_path=settings.sqlite_path,`.

Resulting review block:

```python
    if state.get("task_type") == "contract_review" and state.get("llm_response"):
        try:
            save_review(
                document_id=state.get("document_id", ""),
                session_id=state.get("session_id", ""),
                markdown=state.get("llm_response", ""),
                contract_type=state.get("contract_type_detected", ""),
            )
        except Exception as e:
            logger.error("[memory_writer] FAILED to persist review: %s", e)
            report = {**(state.get("report") or {}), "review_persist_error": str(e)}
            return {"report": report}
```

- [ ] **Step 4: Update `skills/legal_research.py:651`**

Replace:

```python
        latest = load_latest_review(get_settings().sqlite_path, document_id)
```

with:

```python
        latest = load_latest_review(document_id)
```

(The `try/except` around it, the `memory_degraded` flag, and everything else stay.)

- [ ] **Step 5: Update `tests/test_memory_writer.py` (review monkeypatches)**

- Delete every `monkeypatch.setattr(mod, "init_review_db", lambda p: None)` line.
- In `test_persists_review_for_contract_review_turn`, update the `save_review` monkeypatch signature (drop `db_path`):

```python
    monkeypatch.setattr(mod, "save_review",
                        lambda document_id, session_id, markdown, contract_type:
                        saved.update(document_id=document_id, markdown=markdown,
                                     contract_type=contract_type))
```

The `_boom(**kw)` and `lambda **kw:` save_review stubs elsewhere still match. Keep them.

- [ ] **Step 6: Update `tests/test_review_roundtrip.py`**

Rewrite to drop the SQLite path juggling and read from the pool:

```python
"""End-to-end: a review persisted via intake+memory_writer is retrievable by document_id."""
import graph.nodes.memory_writer as mw
from graph.nodes.intake import intake
from memory.review_store import load_latest_review


def test_review_persisted_on_review_turn_is_recallable_by_document_id():
    text = "STATEMENT OF WORK\n\nAcme and Globex.\n\n1. Scope: build a site."
    state = {
        "request": "Review this contract.", "user_id": "word-addin",
        "uploaded_docs": [{"text": text}], "filters": {},
        "task_type": "contract_review",
    }
    state = intake(state)
    doc_id = state["document_id"]
    assert doc_id

    state.update({
        "session_id": "s1", "risk_level": "low", "attorney_notes": "",
        "llm_response": "# Review\nFinding: IP clause is Red.",
        "contract_type_detected": "sow",
        "report": {"response": "# Review\nFinding: IP clause is Red."},
        "awaiting_review": False,
    })
    mw.memory_writer(state)

    latest = load_latest_review(doc_id)
    assert latest is not None
    assert "IP clause is Red" in latest["markdown"]
    assert latest["contract_type"] == "sow"
```

(Tables exist via the conftest session fixture; `_clean_tables` guarantees isolation. `memory_writer` also writes the audit row and, since `task_type != "research"`, skips the conversation path.)

- [ ] **Step 7: Update `load_latest_review` monkeypatches in `tests/test_skills.py` and `tests/test_stale_recall_reconciliation.py`**

Every monkeypatch of `lr.load_latest_review` currently takes two args (`db`/`db_path`, `document_id`). Drop the first. Apply this transform to all occurrences:

- `lambda db_path, document_id: ...` → `lambda document_id: ...`
- `lambda db, doc_id: ...` → `lambda doc_id: ...`
- `_fake_latest` / `_load` / `_boom` helper defs that take `(db_path, document_id)` → take `(document_id)` (or `(*a, **k)` for the boom/raise helpers).

Occurrence line hints (grep to confirm): `test_skills.py` ~1154, 1193, 1217, 1240, 1274, 1305, 1380, 1432, 1457, 1482; `test_stale_recall_reconciliation.py` ~110, 118. Also update the assertion comment at `test_skills.py:1226` (`# load_latest_review was not called`) — logic unchanged.

Run: `grep -rn "load_latest_review" tests/ | grep -E "db_path|db, doc_id|db,doc"`
Expected: no matches (all arities updated).

- [ ] **Step 8: Run the affected suites**

Run: `uv run pytest tests/test_review_store.py tests/test_review_roundtrip.py tests/test_memory_writer.py tests/test_skills.py tests/test_stale_recall_reconciliation.py -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add memory/review_store.py graph/nodes/memory_writer.py skills/legal_research.py tests/test_review_store.py tests/test_review_roundtrip.py tests/test_memory_writer.py tests/test_skills.py tests/test_stale_recall_reconciliation.py
git commit -m "feat(db): port review store to Postgres pool

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Port the conversation store

Port `append_turn` / `load_recent`; remove `init_conversation_db`. Update `memory_writer` (conversation block) and `legal_research` (`_load_prior_conversation`), plus the conversation tests. After this task all three stores are on Postgres.

**Files:**
- Modify: `memory/conversation_store.py`
- Modify: `graph/nodes/memory_writer.py` (conversation block: lines ~11, 71-90)
- Modify: `skills/legal_research.py:686-688`
- Modify: `tests/test_conversation_store.py`
- Modify: `tests/test_legal_research_conversation.py`
- Modify: `tests/test_memory_writer.py` (conversation monkeypatches)

**Interfaces:**
- Consumes: `memory.db.get_pool`.
- Produces:
  - `append_turn(document_id, attorney_id, user_text, assistant_text) -> None`
  - `load_recent(document_id, attorney_id, max_messages) -> list[dict]`  (chronological; `max_messages<=0 → []`)
  - `init_conversation_db` no longer exists.

- [ ] **Step 1: Rewrite `tests/test_conversation_store.py` (failing tests first)**

```python
# tests/test_conversation_store.py
"""Per-attorney conversation store — append-per-turn, recent window, isolation."""
import pytest

import memory.conversation_store as cs
from memory.conversation_store import append_turn, load_recent


def test_append_turn_writes_two_rows_in_order():
    append_turn("doc-1", "atty-1", "hello?", "hi there")
    assert load_recent("doc-1", "atty-1", 20) == [
        {"role": "user", "content": "hello?"},
        {"role": "assistant", "content": "hi there"},
    ]


def test_load_recent_is_chronological_and_capped():
    append_turn("doc-1", "atty-1", "q1", "a1")
    append_turn("doc-1", "atty-1", "q2", "a2")
    append_turn("doc-1", "atty-1", "q3", "a3")
    assert load_recent("doc-1", "atty-1", 2) == [
        {"role": "user", "content": "q3"},
        {"role": "assistant", "content": "a3"},
    ]
    full = load_recent("doc-1", "atty-1", 20)
    assert [m["content"] for m in full] == ["q1", "a1", "q2", "a2", "q3", "a3"]


def test_per_attorney_isolation():
    append_turn("doc-1", "atty-1", "mine", "yours")
    append_turn("doc-1", "atty-2", "hers", "his")
    assert [m["content"] for m in load_recent("doc-1", "atty-1", 20)] == ["mine", "yours"]
    assert [m["content"] for m in load_recent("doc-1", "atty-2", 20)] == ["hers", "his"]


def test_per_document_isolation():
    append_turn("doc-1", "atty-1", "on one", "r1")
    append_turn("doc-2", "atty-1", "on two", "r2")
    assert [m["content"] for m in load_recent("doc-1", "atty-1", 20)] == ["on one", "r1"]
    assert [m["content"] for m in load_recent("doc-2", "atty-1", 20)] == ["on two", "r2"]


def test_load_recent_empty_ids_return_empty():
    append_turn("doc-1", "atty-1", "q", "a")
    assert load_recent("", "atty-1", 20) == []
    assert load_recent("doc-1", "", 20) == []


def test_load_recent_unknown_key_returns_empty():
    assert load_recent("no-doc", "no-atty", 20) == []


def test_load_recent_nonpositive_max_returns_empty():
    append_turn("doc-1", "atty-1", "q", "a")
    assert load_recent("doc-1", "atty-1", 0) == []
    assert load_recent("doc-1", "atty-1", -1) == []


def test_append_raises_loudly_on_store_failure(monkeypatch):
    def _boom():
        raise RuntimeError("pool down")
    monkeypatch.setattr(cs, "get_pool", _boom)
    with pytest.raises(Exception):
        append_turn("doc-1", "atty-1", "q", "a")
```

Run: `uv run pytest tests/test_conversation_store.py -v`
Expected: FAIL — signatures still require `db_path`.

- [ ] **Step 2: Port `memory/conversation_store.py`**

```python
# memory/conversation_store.py
"""Postgres store for per-attorney chat conversations, keyed to (document_id, attorney_id).

Append-per-turn: each chat turn inserts one 'user' row then one 'assistant' row.
Reads return the most-recent window in chronological order for the chat prompt.

Writes raise on failure at the module boundary (like save_review); the caller
(memory_writer) applies a best-effort policy — a lost conversation turn is a
convenience loss, not a lost legal record, so it must not break the turn.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from memory.db import get_pool

logger = logging.getLogger(__name__)


def append_turn(document_id: str, attorney_id: str, user_text: str, assistant_text: str) -> None:
    """Append one turn: a 'user' row then an 'assistant' row. Raises on failure."""
    ts = datetime.now(timezone.utc).isoformat()
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO conversation_store
                   (timestamp, document_id, attorney_id, role, content)
                   VALUES (%s, %s, %s, %s, %s)""",
                [
                    (ts, document_id, attorney_id, "user", user_text),
                    (ts, document_id, attorney_id, "assistant", assistant_text),
                ],
            )
    logger.info("Conversation turn saved: document_id=%s attorney_id=%s", document_id, attorney_id)


def load_recent(document_id: str, attorney_id: str, max_messages: int) -> list[dict]:
    """Up to max_messages most-recent messages for the pair, chronological (oldest first)."""
    if not document_id or not attorney_id or max_messages <= 0:
        return []
    with get_pool().connection() as conn:
        cur = conn.execute(
            """SELECT role, content FROM conversation_store
               WHERE document_id = %s AND attorney_id = %s
               ORDER BY id DESC LIMIT %s""",
            (document_id, attorney_id, max_messages),
        )
        rows = cur.fetchall()
    rows.reverse()  # DESC fetch -> chronological
    return [{"role": r[0], "content": r[1]} for r in rows]
```

Run: `uv run pytest tests/test_conversation_store.py -v`
Expected: PASS.

- [ ] **Step 3: Update `graph/nodes/memory_writer.py` (conversation block)**

- Line 11: `from memory.conversation_store import init_conversation_db, append_turn` → `from memory.conversation_store import append_turn`
- Line 18: delete `_conversation_db_initialized = False`
- Inside the conversation block: delete `global _conversation_db_initialized` and the `if not _conversation_db_initialized: init_conversation_db(...) ; _conversation_db_initialized = True` lines.
- In the `append_turn(...)` call: remove `db_path=settings.sqlite_path,`.

Resulting conversation block:

```python
    if state.get("task_type") == "research" and settings.conversation_store_enabled:
        document_id = state.get("document_id", "")
        attorney_id = state.get("user_id", "")
        if document_id and attorney_id and state.get("llm_response"):
            try:
                append_turn(
                    document_id=document_id,
                    attorney_id=attorney_id,
                    user_text=state.get("request", ""),
                    assistant_text=state.get("llm_response", ""),
                )
            except Exception as e:
                logger.error(
                    "[memory_writer] conversation append failed (non-fatal): %s", e
                )
```

- [ ] **Step 4: Update `skills/legal_research.py:686-688`**

Replace:

```python
        return load_recent(
            settings.sqlite_path, document_id, attorney_id,
            settings.conversation_max_messages,
        )
```

with:

```python
        return load_recent(
            document_id, attorney_id, settings.conversation_max_messages,
        )
```

- [ ] **Step 5: Update `tests/test_legal_research_conversation.py`**

The `_settings` helper still needs a `sqlite_path` attribute removed; setup now uses the real store against the pool:

```python
"""_load_prior_conversation — durable history load, guards, degraded posture."""
from types import SimpleNamespace

import skills.legal_research as lr
from memory.conversation_store import append_turn


def _settings(enabled=True, max_messages=20):
    return SimpleNamespace(
        conversation_store_enabled=enabled,
        conversation_max_messages=max_messages,
    )


def test_loads_durable_history(monkeypatch):
    append_turn("doc-1", "atty-1", "q1", "a1")
    monkeypatch.setattr(lr, "get_settings", lambda: _settings())
    msgs = lr._load_prior_conversation({"document_id": "doc-1", "user_id": "atty-1"})
    assert [m["content"] for m in msgs] == ["q1", "a1"]


def test_empty_when_disabled(monkeypatch):
    append_turn("doc-1", "atty-1", "q1", "a1")
    monkeypatch.setattr(lr, "get_settings", lambda: _settings(enabled=False))
    assert lr._load_prior_conversation({"document_id": "doc-1", "user_id": "atty-1"}) == []


def test_empty_when_ids_missing(monkeypatch):
    monkeypatch.setattr(lr, "get_settings", lambda: _settings())
    assert lr._load_prior_conversation({"document_id": "", "user_id": "atty-1"}) == []
    assert lr._load_prior_conversation({"document_id": "doc-1", "user_id": ""}) == []


def test_read_failure_flags_degraded(monkeypatch):
    monkeypatch.setattr(lr, "get_settings", lambda: _settings())
    def _boom(*a, **k):
        raise RuntimeError("db gone")
    monkeypatch.setattr(lr, "load_recent", _boom)
    state = {"document_id": "doc-1", "user_id": "atty-1"}
    assert lr._load_prior_conversation(state) == []
    assert state["memory_degraded"] is True
```

- [ ] **Step 6: Update `tests/test_memory_writer.py` (conversation monkeypatches)**

- Delete every `monkeypatch.setattr(mod, "init_conversation_db", lambda p: None)` line.
- Delete `test_conversation_init_failure_is_non_fatal` entirely — it tested the lazy `init_conversation_db` path, which no longer exists (init is a startup concern). The non-fatal-append contract is still covered by `test_conversation_write_failure_is_non_fatal`.
- In `test_persists_conversation_for_research_turn`, update the `append_turn` monkeypatch signature (drop `db_path`):

```python
    monkeypatch.setattr(
        mod, "append_turn",
        lambda document_id, attorney_id, user_text, assistant_text:
        saved.update(document_id=document_id, attorney_id=attorney_id,
                     user_text=user_text, assistant_text=assistant_text),
    )
```

- Remove the `mod._conversation_db_initialized = False` line in the (now-deleted) init test — confirm no remaining references:

Run: `grep -rn "_conversation_db_initialized\|_review_db_initialized\|_db_initialized\|init_conversation_db\|init_review_db" tests/ graph/ api/`
Expected: no matches.

- [ ] **Step 7: Run the affected suites**

Run: `uv run pytest tests/test_conversation_store.py tests/test_legal_research_conversation.py tests/test_memory_writer.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add memory/conversation_store.py graph/nodes/memory_writer.py skills/legal_research.py tests/test_conversation_store.py tests/test_legal_research_conversation.py tests/test_memory_writer.py
git commit -m "feat(db): port conversation store to Postgres pool

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Remove SQLite remnants + full-suite verification + docs

All callers are off `sqlite_path`. Remove it, sweep for any `sqlite3`/`sqlite_path` remnants, run the full suite, and update docs.

**Files:**
- Verify/clean: repo-wide grep
- Modify: `docs/wiki.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Remove `sqlite_path` from `config.py` and `tests/test_config.py`**

By now all callers are off `sqlite_path` (Tasks 2-4). Delete the `sqlite_path: str = "data/legal.db"` line from `config.py` (keep `database_url`). In `tests/test_config.py` delete the `monkeypatch.setenv("SQLITE_PATH", "data/legal.db")` line and the `assert settings.sqlite_path == "data/legal.db"` line (the `DATABASE_URL` env + `database_url` assertion added in Task 1 stay).

- [ ] **Step 2: Confirm `sqlite_path` and `sqlite3` are gone from app code**

Run: `grep -rn "sqlite_path\|import sqlite3\|sqlite3\." --include="*.py" config.py api/ graph/ memory/ skills/ tests/`
Expected: no matches. (If any remain, fix them — they are dangling references.)

- [ ] **Step 3: Run the full test suite**

Run: `uv run pytest tests/ -q`
Expected: PASS (all green). If a test still references removed symbols, fix per the patterns in Tasks 2-4.

- [ ] **Step 4: Manual smoke — real backend against `app-db`**

```bash
docker compose up -d app-db
# .env has DATABASE_URL=postgresql://legal:legal@localhost:5434/legal
bash scripts/start.sh
```

Then verify a review persists and recalls (via the Word add-in or `uv run python -m scripts.debug_query "..."` on a contract-review turn), and inspect:

```bash
docker compose exec app-db psql -U legal -d legal -c "SELECT count(*) FROM review_store;"
```

Expected: the review row count increments after a review turn.

- [ ] **Step 5: Update `docs/wiki.md`**

Add to "Shipped Since Last Update": the SQLite→Postgres store migration (dedicated `app-db` on 5434, `memory/db.py` pool, testcontainers). Note spec 2 (VM deployment/hosting) is the remaining follow-up.

- [ ] **Step 6: Update `CLAUDE.md` (Backend section)**

Replace SQLite references in the backend notes with the Postgres reality: the three relational stores now live in the `app-db` Postgres (host 5434) via `memory/db.py`'s autocommit pool; init is `memory.db.init_db()` at lifespan startup; **tests require Docker** (testcontainers spins an ephemeral Postgres per session; conftest truncates between tests). Keep the loud-review / best-effort-conversation contract notes. Remove the now-obsolete "memory_writer test MUST monkeypatch init_conversation_db + append_turn to avoid a live write to data/legal.db" note and replace it with: memory_writer tests monkeypatch `write_audit_log` / `save_review` / `append_turn`; live writes now hit the ephemeral test container, and `_clean_tables` isolates them. Stay within the 150-line cap (consolidate/drop the lowest-value line if needed).

- [ ] **Step 7: Commit**

```bash
git add config.py docs/wiki.md CLAUDE.md
git commit -m "chore(db): drop SQLite remnants; docs for Postgres store migration

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage** — every spec section maps to a task:
- New `memory/db.py` pool → Task 1. Three stores ported (`%s`, `BIGSERIAL`, TEXT timestamps, `LIMIT` guard) → Tasks 2-4. Dedicated `app-db` on 5434 → Task 1. Init at startup (consolidated `init_db()`) → Tasks 1-2 (main.py). Config `sqlite_path`→`database_url` → Task 1, removal Task 5. Call-site updates (memory_writer, legal_research, main.py) → Tasks 2-4. Testcontainers + fixtures → Task 1, applied 2-4. No data migration / drop SQLite → Task 5. Deps (psycopg, psycopg-pool, testcontainers; drop aiosqlite) → Task 1. Behavior contracts (loud review, best-effort conversation, `max<=0`) → preserved and asserted in Tasks 3-4. Docs → Task 5.
- Gap check: the spec's "Option A signatures" is realized (all `db_path` args dropped). The one intentional deviation (single `init_db()` vs three `init_*_db()`) is called out in the header.

**2. Placeholder scan** — no TBD/TODO; every code step shows full code; repeated mechanical test edits give the exact before/after plus an explicit grep to prove completion.

**3. Type consistency** — `get_pool()` / `init_db()` / `reset_pool()` names match across `memory/db.py`, `conftest.py`, `api/main.py`, and every store. Store signatures used by `memory_writer` / `legal_research` (`write_audit_log(...)`, `save_review(document_id, session_id, markdown, contract_type)`, `load_latest_review(document_id)`, `append_turn(document_id, attorney_id, user_text, assistant_text)`, `load_recent(document_id, attorney_id, max_messages)`) match the "Produces" interface blocks and the updated call sites.
