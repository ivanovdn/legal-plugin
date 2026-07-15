# Server-Side Per-Attorney Conversation Store — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make in-Word chat conversations durable and per-attorney — surviving pane reopen, a new `session_id`, and a different machine — by persisting each turn in a SQLite store keyed by `(document_id, attorney_id)`.

**Architecture:** A new SQLite store (`memory/conversation_store.py`, colocated in `data/legal.db` with the review/audit stores) is appended per chat turn by the graph's terminal `memory_writer` node, and read back by the doc-chat path (`_run_doc_chat`) which prefers it over the session-scoped Redis history. The Word client supplies a per-install attorney id from `localStorage` via the `X-User-ID` header. Additive to Redis — the checkpointer still owns interrupt/resume and within-session state.

**Tech Stack:** Python 3.12 (uv), plain `sqlite3`, LangGraph, pytest; Word add-in React + TypeScript + Vite, standalone `tsx` tests.

**Spec:** `docs/superpowers/specs/2026-07-15-conversation-store-design.md`

## Global Constraints

- **All imports at top of file.** No lazy imports inside functions (so tests can `monkeypatch` module-level names).
- **Python 3.12 + uv.** Tests run with `uv run pytest`.
- **No backwards-compat shims.** Change call sites directly.
- **`memory_writer` is where durable SQLite writes live** (audit + review today); the conversation write joins them there, not in a new node.
- **Conversation writes are best-effort:** log on failure, never raise, never fail the turn. (Contrast the review write, which surfaces `review_persist_error`.) Conversation reads are graceful: empty on failure + set `state["memory_degraded"]`.
- **Reviews stay keyed by `document_id` only** (shared across attorneys); conversations are keyed by `(document_id, attorney_id)`. Do not change `review_store`.
- **`attorney_id` is a partitioning key, not authentication.** No auth work.
- **Attorney id lives in `localStorage`, never `document.settings`** (the latter is per-document; slice 1 used it for `document_id`).
- **Frontend: never let `tsc` emit `.js` into `clients/word/src/`** (`noEmit:true`). Typecheck with `npx tsc --noEmit`.
- **uvicorn does NOT auto-reload Python** — the human smoke step must restart `bash scripts/start.sh` first.

---

## File Structure

- `memory/conversation_store.py` — NEW. The store: `init_conversation_db`, `append_turn`, `load_recent`. One responsibility: persist/retrieve conversation rows.
- `tests/test_conversation_store.py` — NEW. Unit tests for the store.
- `config.py` — MODIFY. Two settings fields.
- `graph/nodes/memory_writer.py` — MODIFY. Append the conversation turn on research turns.
- `tests/test_memory_writer.py` — MODIFY. Add conversation-write cases.
- `skills/legal_research.py` — MODIFY. `_load_prior_conversation` helper + source it in `_run_doc_chat`.
- `tests/test_legal_research_conversation.py` — NEW. Tests for `_load_prior_conversation`.
- `clients/word/src/attorneyIdentity.ts` — NEW. `resolveAttorneyId()`.
- `clients/word/src/attorneyIdentity.test.ts` — NEW. Standalone `tsx` test.
- `clients/word/src/api.ts` — MODIFY. Send `X-User-ID: resolveAttorneyId()`.
- `docs/wiki.md` — MODIFY. Shipped row + follow-up updates.

---

## Task 1: Conversation store module

**Files:**
- Create: `memory/conversation_store.py`
- Test: `tests/test_conversation_store.py`

**Interfaces:**
- Consumes: nothing (leaf module).
- Produces:
  - `init_conversation_db(db_path: str) -> None`
  - `append_turn(db_path: str, document_id: str, attorney_id: str, user_text: str, assistant_text: str) -> None` — two rows (user, then assistant), one transaction; raises on DB failure.
  - `load_recent(db_path: str, document_id: str, attorney_id: str, max_messages: int) -> list[dict]` — up to `max_messages` most-recent messages, chronological (oldest first), each `{"role", "content"}`; `[]` on empty ids / no rows.

- [ ] **Step 1: Write the failing tests**

`tests/test_conversation_store.py`:

```python
"""Per-attorney conversation store — append-per-turn, recent window, isolation."""
import pytest

from memory.conversation_store import (
    init_conversation_db, append_turn, load_recent,
)


def _db(tmp_path):
    p = str(tmp_path / "conv.db")
    init_conversation_db(p)
    return p


def test_append_turn_writes_two_rows_in_order(tmp_path):
    db = _db(tmp_path)
    append_turn(db, "doc-1", "atty-1", "hello?", "hi there")
    msgs = load_recent(db, "doc-1", "atty-1", 20)
    assert msgs == [
        {"role": "user", "content": "hello?"},
        {"role": "assistant", "content": "hi there"},
    ]


def test_load_recent_is_chronological_and_capped(tmp_path):
    db = _db(tmp_path)
    append_turn(db, "doc-1", "atty-1", "q1", "a1")
    append_turn(db, "doc-1", "atty-1", "q2", "a2")
    append_turn(db, "doc-1", "atty-1", "q3", "a3")
    # cap to the last 2 messages -> only the newest turn survives
    msgs = load_recent(db, "doc-1", "atty-1", 2)
    assert msgs == [
        {"role": "user", "content": "q3"},
        {"role": "assistant", "content": "a3"},
    ]
    # full window stays in chronological order
    full = load_recent(db, "doc-1", "atty-1", 20)
    assert [m["content"] for m in full] == ["q1", "a1", "q2", "a2", "q3", "a3"]


def test_per_attorney_isolation(tmp_path):
    db = _db(tmp_path)
    append_turn(db, "doc-1", "atty-1", "mine", "yours")
    append_turn(db, "doc-1", "atty-2", "hers", "his")
    assert [m["content"] for m in load_recent(db, "doc-1", "atty-1", 20)] == ["mine", "yours"]
    assert [m["content"] for m in load_recent(db, "doc-1", "atty-2", 20)] == ["hers", "his"]


def test_per_document_isolation(tmp_path):
    db = _db(tmp_path)
    append_turn(db, "doc-1", "atty-1", "on one", "r1")
    append_turn(db, "doc-2", "atty-1", "on two", "r2")
    assert [m["content"] for m in load_recent(db, "doc-1", "atty-1", 20)] == ["on one", "r1"]
    assert [m["content"] for m in load_recent(db, "doc-2", "atty-1", 20)] == ["on two", "r2"]


def test_load_recent_empty_ids_return_empty(tmp_path):
    db = _db(tmp_path)
    append_turn(db, "doc-1", "atty-1", "q", "a")
    assert load_recent(db, "", "atty-1", 20) == []
    assert load_recent(db, "doc-1", "", 20) == []


def test_load_recent_unknown_key_returns_empty(tmp_path):
    db = _db(tmp_path)
    assert load_recent(db, "no-doc", "no-atty", 20) == []


def test_append_raises_loudly_on_bad_path():
    # Parent dir does not exist -> cannot open -> must raise, never silent.
    with pytest.raises(Exception):
        append_turn("/no/such/dir/conv.db", "doc-1", "atty-1", "q", "a")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_conversation_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'memory.conversation_store'`.

- [ ] **Step 3: Implement the module**

`memory/conversation_store.py`:

```python
"""SQLite store for per-attorney chat conversations, keyed to (document_id, attorney_id).

Mirrors memory/review_store.py (plain sqlite3, db_path-per-call). Append-per-turn:
each chat turn inserts one 'user' row then one 'assistant' row. Reads return the
most-recent window in chronological order for injection into the chat prompt.

Writes raise on failure at the module boundary (like save_review); the caller
(memory_writer) applies a best-effort policy — a lost conversation turn is a
convenience loss, not a lost legal record, so it must not break the turn.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS conversation_store (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    document_id TEXT NOT NULL,
    attorney_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL
)
"""

_CREATE_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_conv "
    "ON conversation_store (document_id, attorney_id, id)"
)


def init_conversation_db(db_path: str) -> None:
    """Create the conversation_store table + index if absent."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(_CREATE_TABLE)
        conn.execute(_CREATE_INDEX)
        conn.commit()
    finally:
        conn.close()
    logger.info("Conversation store initialized at %s", db_path)


def append_turn(
    db_path: str, document_id: str, attorney_id: str,
    user_text: str, assistant_text: str,
) -> None:
    """Append one turn: a 'user' row then an 'assistant' row. Raises on failure."""
    ts = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(
            """INSERT INTO conversation_store
               (timestamp, document_id, attorney_id, role, content)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (ts, document_id, attorney_id, "user", user_text),
                (ts, document_id, attorney_id, "assistant", assistant_text),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    logger.info(
        "Conversation turn saved: document_id=%s attorney_id=%s",
        document_id, attorney_id,
    )


def load_recent(
    db_path: str, document_id: str, attorney_id: str, max_messages: int
) -> list[dict]:
    """Up to max_messages most-recent messages for the pair, chronological (oldest first)."""
    if not document_id or not attorney_id:
        return []
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """SELECT role, content FROM conversation_store
               WHERE document_id = ? AND attorney_id = ?
               ORDER BY id DESC LIMIT ?""",
            (document_id, attorney_id, max_messages),
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    rows.reverse()  # DESC fetch -> chronological
    return [{"role": r[0], "content": r[1]} for r in rows]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_conversation_store.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add memory/conversation_store.py tests/test_conversation_store.py
git commit -m "feat: add per-attorney conversation store (SQLite)"
```

---

## Task 2: Config fields + memory_writer conversation write

**Files:**
- Modify: `config.py` (Memory / checkpointer section, after `msa_max_chars`)
- Modify: `graph/nodes/memory_writer.py`
- Test: `tests/test_memory_writer.py` (append cases)

**Interfaces:**
- Consumes: `init_conversation_db`, `append_turn` (Task 1); `config.Settings`.
- Produces: `settings.conversation_store_enabled: bool`, `settings.conversation_max_messages: int`; `memory_writer` appends `(request, llm_response)` to the store on `task_type == "research"` turns with `document_id` + `user_id` + `llm_response`.

- [ ] **Step 1: Add the config fields**

In `config.py`, in the `# Memory / checkpointer` block, immediately after the `msa_max_chars` line, add:

```python
    conversation_store_enabled: bool = True   # durable per-(document,attorney) chat store; False = Redis-only history
    conversation_max_messages: int = 20       # messages injected from the durable store (~10 turns); store retains all
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_memory_writer.py`:

```python
def test_persists_conversation_for_research_turn(monkeypatch):
    monkeypatch.setattr(mod, "write_audit_log", lambda **kw: None)
    monkeypatch.setattr(mod, "init_audit_db", lambda p: None)
    monkeypatch.setattr(mod, "init_conversation_db", lambda p: None)
    saved = {}
    monkeypatch.setattr(
        mod, "append_turn",
        lambda db_path, document_id, attorney_id, user_text, assistant_text:
        saved.update(document_id=document_id, attorney_id=attorney_id,
                     user_text=user_text, assistant_text=assistant_text),
    )
    mod.memory_writer(_state(
        task_type="research", user_id="atty-1",
        request="who signs?", llm_response="Boris signs.",
    ))
    assert saved == {
        "document_id": "doc-1", "attorney_id": "atty-1",
        "user_text": "who signs?", "assistant_text": "Boris signs.",
    }


def test_does_not_persist_conversation_for_review_turn(monkeypatch):
    monkeypatch.setattr(mod, "write_audit_log", lambda **kw: None)
    monkeypatch.setattr(mod, "init_audit_db", lambda p: None)
    monkeypatch.setattr(mod, "init_review_db", lambda p: None)
    monkeypatch.setattr(mod, "save_review", lambda **kw: None)
    monkeypatch.setattr(mod, "init_conversation_db", lambda p: None)
    called = {"n": 0}
    monkeypatch.setattr(mod, "append_turn",
                        lambda **kw: called.__setitem__("n", called["n"] + 1))
    mod.memory_writer(_state(task_type="contract_review"))
    assert called["n"] == 0


def test_skips_conversation_when_no_document_id(monkeypatch):
    monkeypatch.setattr(mod, "write_audit_log", lambda **kw: None)
    monkeypatch.setattr(mod, "init_audit_db", lambda p: None)
    monkeypatch.setattr(mod, "init_conversation_db", lambda p: None)
    called = {"n": 0}
    monkeypatch.setattr(mod, "append_turn",
                        lambda **kw: called.__setitem__("n", called["n"] + 1))
    mod.memory_writer(_state(task_type="research", user_id="atty-1", document_id=""))
    assert called["n"] == 0


def test_conversation_write_failure_is_non_fatal(monkeypatch):
    monkeypatch.setattr(mod, "write_audit_log", lambda **kw: None)
    monkeypatch.setattr(mod, "init_audit_db", lambda p: None)
    monkeypatch.setattr(mod, "init_conversation_db", lambda p: None)
    def _boom(**kw):
        raise RuntimeError("disk full")
    monkeypatch.setattr(mod, "append_turn", _boom)
    out = mod.memory_writer(_state(task_type="research", user_id="atty-1"))
    assert "review_persist_error" not in (out.get("report") or {})
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_memory_writer.py -v`
Expected: FAIL — `AttributeError: <module 'graph.nodes.memory_writer'> has no attribute 'append_turn'` (and `init_conversation_db`).

- [ ] **Step 4: Implement the write path**

In `graph/nodes/memory_writer.py`:

Add to the imports (top of file, next to the review-store import):

```python
from memory.conversation_store import init_conversation_db, append_turn
```

Add a module flag next to `_review_db_initialized`:

```python
_conversation_db_initialized = False
```

Insert this block immediately before the final `return {}` of `memory_writer`:

```python
    # Persist the doc-chat conversation, keyed to (document, attorney). Best-effort:
    # a lost turn is a convenience loss, not a legal record — never fail the turn.
    if state.get("task_type") == "research" and settings.conversation_store_enabled:
        document_id = state.get("document_id", "")
        attorney_id = state.get("user_id", "")
        if document_id and attorney_id and state.get("llm_response"):
            global _conversation_db_initialized
            if not _conversation_db_initialized:
                init_conversation_db(settings.sqlite_path)
                _conversation_db_initialized = True
            try:
                append_turn(
                    db_path=settings.sqlite_path,
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

(`settings` is already bound at the top of `memory_writer` as `settings = get_settings()`.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_memory_writer.py -v`
Expected: PASS (original 3 + new 4 = 7 tests).

- [ ] **Step 6: Commit**

```bash
git add config.py graph/nodes/memory_writer.py tests/test_memory_writer.py
git commit -m "feat: persist doc-chat turns to conversation store in memory_writer"
```

---

## Task 3: Read path — durable history in _run_doc_chat

**Files:**
- Modify: `skills/legal_research.py`
- Test: `tests/test_legal_research_conversation.py`

**Interfaces:**
- Consumes: `load_recent` (Task 1); `settings.conversation_store_enabled`, `settings.conversation_max_messages`, `settings.sqlite_path` (Task 2).
- Produces: `_load_prior_conversation(state) -> list[dict]`; `_run_doc_chat` sources chat history from the durable store, falling back to `state["chat_history"]` when empty.

- [ ] **Step 1: Write the failing tests**

`tests/test_legal_research_conversation.py`:

```python
"""_load_prior_conversation — durable history load, guards, degraded posture."""
from types import SimpleNamespace

import skills.legal_research as lr
from memory.conversation_store import init_conversation_db, append_turn


def _settings(tmp_path, enabled=True, max_messages=20):
    return SimpleNamespace(
        conversation_store_enabled=enabled,
        sqlite_path=str(tmp_path / "conv.db"),
        conversation_max_messages=max_messages,
    )


def test_loads_durable_history(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    init_conversation_db(s.sqlite_path)
    append_turn(s.sqlite_path, "doc-1", "atty-1", "q1", "a1")
    monkeypatch.setattr(lr, "get_settings", lambda: s)
    msgs = lr._load_prior_conversation({"document_id": "doc-1", "user_id": "atty-1"})
    assert [m["content"] for m in msgs] == ["q1", "a1"]


def test_empty_when_disabled(tmp_path, monkeypatch):
    s = _settings(tmp_path, enabled=False)
    init_conversation_db(s.sqlite_path)
    append_turn(s.sqlite_path, "doc-1", "atty-1", "q1", "a1")
    monkeypatch.setattr(lr, "get_settings", lambda: s)
    assert lr._load_prior_conversation({"document_id": "doc-1", "user_id": "atty-1"}) == []


def test_empty_when_ids_missing(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    init_conversation_db(s.sqlite_path)
    monkeypatch.setattr(lr, "get_settings", lambda: s)
    assert lr._load_prior_conversation({"document_id": "", "user_id": "atty-1"}) == []
    assert lr._load_prior_conversation({"document_id": "doc-1", "user_id": ""}) == []


def test_read_failure_flags_degraded(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    monkeypatch.setattr(lr, "get_settings", lambda: s)
    def _boom(*a, **k):
        raise RuntimeError("db gone")
    monkeypatch.setattr(lr, "load_recent", _boom)
    state = {"document_id": "doc-1", "user_id": "atty-1"}
    assert lr._load_prior_conversation(state) == []
    assert state["memory_degraded"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_legal_research_conversation.py -v`
Expected: FAIL — `AttributeError: module 'skills.legal_research' has no attribute '_load_prior_conversation'`.

- [ ] **Step 3: Implement the helper + wire it in**

In `skills/legal_research.py`:

Add to the imports (next to `from memory.review_store import load_latest_review`):

```python
from memory.conversation_store import load_recent
```

Add the helper immediately after `_load_prior_review_block` (before `_GROUNDING_TRIGGER_RE`):

```python
def _load_prior_conversation(state: LegalAgentState) -> list[dict]:
    """Durable per-(document, attorney) chat history for the doc-chat prompt.
    Empty when disabled, keys missing, or on a store-read failure (which flags
    memory_degraded) — memory must never break the chat turn."""
    settings = get_settings()
    if not settings.conversation_store_enabled:
        return []
    document_id = state.get("document_id", "")
    attorney_id = state.get("user_id", "")
    if not document_id or not attorney_id:
        return []
    try:
        return load_recent(
            settings.sqlite_path, document_id, attorney_id,
            settings.conversation_max_messages,
        )
    except Exception as e:
        logger.error("[legal_research] prior-conversation load failed: %s", e)
        state["memory_degraded"] = True
        return []
```

In `_run_doc_chat`, replace this line:

```python
    chat_history = state.get("chat_history", []) or []
```

with:

```python
    chat_history = _load_prior_conversation(state)
    if not chat_history:
        chat_history = state.get("chat_history", []) or []
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_legal_research_conversation.py -v`
Expected: PASS (5 tests: load, disabled, two missing-id cases in one test, degraded).

- [ ] **Step 5: Run the full backend suite (no regressions)**

Run: `uv run pytest tests/ -v`
Expected: all pass (prior suite + Task 1/2/3 additions).

- [ ] **Step 6: Commit**

```bash
git add skills/legal_research.py tests/test_legal_research_conversation.py
git commit -m "feat: source doc-chat history from the durable conversation store"
```

---

## Task 4: Client per-install attorney id

**Files:**
- Create: `clients/word/src/attorneyIdentity.ts`
- Create: `clients/word/src/attorneyIdentity.test.ts`
- Modify: `clients/word/src/api.ts`

**Interfaces:**
- Consumes: browser `localStorage`, `crypto.randomUUID`.
- Produces: `resolveAttorneyId(): string`; `api.ts` `postQuery` sends `X-User-ID: resolveAttorneyId()`.

- [ ] **Step 1: Write the failing test**

`clients/word/src/attorneyIdentity.test.ts`:

```ts
// Read-or-create attorney id. Run with: npx tsx src/attorneyIdentity.test.ts
import { resolveAttorneyId } from "./attorneyIdentity";

const pass = (cond: boolean, label: string) =>
  console.log(cond ? `PASS: ${label}` : `FAIL: ${label}`);

// in-memory localStorage mock
class MemStore {
  private m = new Map<string, string>();
  getItem(k: string) { return this.m.has(k) ? this.m.get(k)! : null; }
  setItem(k: string, v: string) { this.m.set(k, v); }
}
(globalThis as { localStorage?: unknown }).localStorage = new MemStore();

const first = resolveAttorneyId();
pass(typeof first === "string" && first.length > 0, "mints a non-empty id");

const second = resolveAttorneyId();
pass(first === second, "reuses the stored id on subsequent calls");

// throwing localStorage -> safe fallback
(globalThis as { localStorage?: unknown }).localStorage = {
  getItem() { throw new Error("blocked"); },
  setItem() { throw new Error("blocked"); },
};
pass(resolveAttorneyId() === "word-addin", "falls back to word-addin when localStorage throws");
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd clients/word && npx tsx src/attorneyIdentity.test.ts`
Expected: FAIL — cannot resolve module `./attorneyIdentity`.

- [ ] **Step 3: Implement `attorneyIdentity.ts`**

`clients/word/src/attorneyIdentity.ts`:

```ts
// Per-install attorney identity. Stored in localStorage (per add-in origin, so
// it is the SAME id across all of this attorney's documents — unlike
// document.settings, which is per-document). Confirmed to survive the Word-for-Mac
// task-pane teardown. Sent as the X-User-ID header; O365 SSO (slice 3) will
// overwrite this value at the same seam.
const KEY = "legalTriageAttorneyId";

export function resolveAttorneyId(): string {
  try {
    const existing = localStorage.getItem(KEY);
    if (existing) return existing;
    const id =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `atty-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    localStorage.setItem(KEY, id);
    return id;
  } catch {
    return "word-addin"; // fail-safe: never break a request over identity
  }
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd clients/word && npx tsx src/attorneyIdentity.test.ts`
Expected: three `PASS:` lines.

- [ ] **Step 5: Wire it into `api.ts`**

In `clients/word/src/api.ts`, add to the imports (next to `resolveDocumentId`):

```ts
import { resolveAttorneyId } from "./attorneyIdentity";
```

In `postQuery`, change the headers line from:

```ts
    headers: { "Content-Type": "application/json", "X-User-ID": "word-addin" },
```

to:

```ts
    headers: { "Content-Type": "application/json", "X-User-ID": resolveAttorneyId() },
```

- [ ] **Step 6: Typecheck (no regressions, no stray emit)**

Run: `cd clients/word && npx tsc --noEmit`
Expected: no errors; no `.js` written under `src/`.

- [ ] **Step 7: Run the existing frontend test scripts (no regressions)**

Run: `cd clients/word && for t in src/*.test.ts; do npx tsx "$t"; done`
Expected: all `PASS:` lines, no `FAIL:`.

- [ ] **Step 8: Commit**

```bash
git add clients/word/src/attorneyIdentity.ts clients/word/src/attorneyIdentity.test.ts clients/word/src/api.ts
git commit -m "feat: send per-install attorney id as X-User-ID from Word add-in"
```

---

## Task 5: Docs — wiki shipped row + follow-ups

**Files:**
- Modify: `docs/wiki.md`

**Interfaces:**
- Consumes: nothing.
- Produces: updated shipped log + follow-up list (smoke marked pending until the human step).

- [ ] **Step 1: Update the header and shipped log**

In `docs/wiki.md`, bump the header date to `2026-07-15` and the test count to the new backend total (run `uv run pytest tests/ -q` to get the number). In the "Shipped Since Last Update" section, add a row:

```
| Server-side per-attorney conversation store (slice 2) | Chat conversations persist per `(document_id, attorney_id)` in SQLite (`memory/conversation_store.py`), appended per turn by `memory_writer` and read by `_run_doc_chat` (prefers the durable store over Redis). Per-install attorney id from `localStorage` via `X-User-ID` (`clients/word/src/attorneyIdentity.ts`). Additive to Redis. **Smoke: pending** human sideload. |
```

- [ ] **Step 2: Update the follow-ups**

In the follow-ups table, strike through the "Server-side per-attorney conversation store" follow-up as DONE (adapted), and leave the "O365 SSO attorney identity" and "Chat: reconcile a recalled review against the current doc (stale-recall)" follow-ups in place (both still open). Add, if not present, a follow-up:

```
| Conversation retention / pruning policy | Low | `conversation_store` grows unbounded; only the injected window (`conversation_max_messages`) is capped. Revisit if `data/legal.db` size becomes a concern. |
```

- [ ] **Step 3: Commit**

```bash
git add docs/wiki.md
git commit -m "docs: log slice-2 conversation store (shipped, smoke pending)"
```

---

## Human sideload smoke (after Task 5, before finishing)

Not an automated task — the reviewer/controller hands this to the human.

1. **Restart the backend first** — `bash scripts/start.sh` (uvicorn does not auto-reload Python).
2. Sideload the add-in; open an NDA. Run a review, then a chat follow-up.
3. **Close and reopen the task pane** (forces a new `session_id`). Ask another follow-up → the prior conversation should be recalled.
4. **Deterministic DB check** (the slice-1 lesson — do not trust the chat text alone):
   ```bash
   uv run python -c "import sqlite3; \
     [print(r) for r in sqlite3.connect('data/legal.db').execute( \
     'SELECT id, document_id, attorney_id, role, substr(content,1,40) FROM conversation_store ORDER BY id DESC LIMIT 8')]"
   ```
   Confirm rows are keyed by a real `attorney_id` UUID and the `document_id` UUID.
5. (Optional) Clear `localStorage` (or a second install) → a fresh `attorney_id` → the same document starts a separate thread. Confirms per-attorney isolation end-to-end.

---

## Self-Review (completed)

- **Spec coverage:** store (Task 1), identity (Task 4), write (Task 2), read (Task 3), config (Task 2), docs (Task 5), smoke (final section) — all spec sections mapped.
- **Placeholder scan:** none — every code step carries full code.
- **Type consistency:** `append_turn` / `load_recent` / `resolveAttorneyId` / `_load_prior_conversation` signatures match across the tasks that consume them; `load_recent` returns `list[{"role","content"}]` everywhere; `attorney_id` sourced from `state["user_id"]` in both write and read.
