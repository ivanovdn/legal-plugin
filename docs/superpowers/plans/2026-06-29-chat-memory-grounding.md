# Chat Memory & Grounding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Word Chat tab the memory and grounding the Findings tab already has — persist each review, recall it in chat, attach the playbook + governing MSA on the chat path, and make degraded storage observable — without re-architecting the review output.

**Architecture:** Kill the surface asymmetry at its source with two shared building blocks — `skills/grounding.py` (type detection + playbook bundle + parent-MSA attach, used by both `contract_review` and `legal_research`) and a SQLite `memory/review_store.py` (full markdown reviews keyed to a `document_id`). The chat path loads the stored review + grounding and assembles the prompt stable-grounding-first / question-last so Ollama's prefix cache reuses the expensive grounding across turns.

**Tech Stack:** Python 3.12, LangGraph 0.6, `langchain-ollama` ChatOllama, SQLite (`sqlite3`, already used by `memory/audit.py`), Qdrant via `rag/related_docs.py`, FastAPI, React/Vite Word add-in.

## Global Constraints

- Run tests with `./.venv/bin/python -m pytest tests/ -v` (uv venv, Python 3.12).
- **All imports at top of file** — never inside functions.
- **SKILL.md is the ceiling** — any prompt addition must be structural/model-neutral, never a legal position.
- **Always filter by `client_id`** in RAG calls.
- **No backwards-compat shims** — change call sites directly (the `contract_review` → `grounding.py` refactor does this).
- `uvicorn` does **not** hot-reload Python — restart `bash scripts/start.sh` before any live smoke.
- Word add-in changes need `npx tsc --noEmit` **and** a sideload smoke in Word for Mac — `tsc` alone is insufficient.
- Branch: `feat/chat-memory-grounding` (already exists, spec committed). Commit trailer on every commit:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- Spec: `docs/superpowers/specs/2026-06-29-chat-memory-grounding-design.md`.

---

## File structure

- **Create:** `memory/document_id.py`, `memory/review_store.py`, `skills/grounding.py`,
  `tests/test_document_id.py`, `tests/test_review_store.py`, `tests/test_grounding.py`,
  `tests/test_query_memory.py`, `tests/test_memory_writer.py`.
- **Modify:** `graph/state.py`, `graph/nodes/intake.py`, `graph/nodes/memory_writer.py`,
  `graph/nodes/output_formatter.py`, `api/routes/query.py`, `skills/contract_review/contract_review.py`,
  `skills/legal_research.py`, `config.py`, `clients/word/src/api.ts`,
  `clients/word/src/components/ChatTab.tsx`, and extend `tests/test_skills.py`.

---

## Task 1: `document_id` resolver

**Files:**
- Create: `memory/document_id.py`
- Test: `tests/test_document_id.py`

**Interfaces:**
- Produces: `resolve_document_id(text: str) -> str` — SHA-256 hex of a normalized ~800-char preamble; `""` for empty/whitespace input.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_document_id.py
"""document_id resolution — stable across body redlines, distinct across docs."""
from memory.document_id import resolve_document_id

_PREAMBLE = (
    "STATEMENT OF WORK\n\nThis SOW is entered into by Acme Corp and Globex LLC "
    "pursuant to the Master Services Agreement dated 2025-01-01.\n\n"
)


def test_same_preamble_same_id():
    a = resolve_document_id(_PREAMBLE + "1. Scope: build a website.")
    b = resolve_document_id(_PREAMBLE + "1. Scope: build a website. 2. Added clause.")
    assert a == b  # body redlines below the preamble must not change the id


def test_different_preamble_different_id():
    other = resolve_document_id("MUTUAL NDA\n\nBetween Foo Inc and Bar Ltd.\n\nbody")
    assert resolve_document_id(_PREAMBLE + "body") != other


def test_normalization_ignores_whitespace_and_case():
    a = resolve_document_id("Statement   of\tWork  between A and B")
    b = resolve_document_id("statement of work between a and b")
    assert a == b


def test_empty_text_returns_empty_id():
    assert resolve_document_id("") == ""
    assert resolve_document_id("   \n\t ") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_document_id.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'memory.document_id'`.

- [ ] **Step 3: Write minimal implementation**

```python
# memory/document_id.py
"""Resolve a stable document identifier from contract text.

Keys the persisted review store. Hashes a NORMALIZED PREAMBLE REGION (title +
parties) rather than the whole body, because the body is actively redlined and a
full-body hash would change on every edit and orphan the stored review.

Interim implementation. The durable upgrade is an Office.js custom document
property (a UUID written into the file on first open); that is a swap of this one
function. See docs/superpowers/specs/2026-06-29-chat-memory-grounding-design.md.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

# Title + parties usually sit in the opening block. 800 chars comfortably covers
# them while staying above the clauses that get redlined.
_PREAMBLE_CHARS = 800


def resolve_document_id(text: str) -> str:
    """SHA-256 hex of the normalized preamble. Empty string for empty input."""
    if not text or not text.strip():
        return ""
    region = text[:_PREAMBLE_CHARS]
    region = unicodedata.normalize("NFC", region).lower()
    region = re.sub(r"\s+", " ", region).strip()
    return hashlib.sha256(region.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest tests/test_document_id.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add memory/document_id.py tests/test_document_id.py
git commit -m "feat(memory): resolve_document_id — stable preamble-hash document id

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Resolve `document_id` in `intake`; add state fields

**Files:**
- Modify: `graph/state.py` (add `document_id`, `memory_degraded` to `LegalAgentState`)
- Modify: `graph/nodes/intake.py` (resolve and store `document_id`)
- Modify: `api/routes/query.py` (default the two new keys in `initial_state`)
- Test: `tests/test_intake_document_id.py`

**Interfaces:**
- Consumes: `resolve_document_id` (Task 1).
- Produces: `state["document_id"]` populated for every turn; `state["memory_degraded"]` defaulting `False`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_intake_document_id.py
"""intake resolves a document_id from the uploaded contract text."""
from graph.nodes.intake import intake
from memory.document_id import resolve_document_id


def _state(**kw):
    base = {"request": "Review this", "user_id": "word-addin", "uploaded_docs": [],
            "filters": {}, "task_type": "contract_review"}
    base.update(kw)
    return base


def test_intake_sets_document_id_from_uploaded_text():
    text = "STATEMENT OF WORK\n\nAcme and Globex.\n\n1. Scope."
    state = _state(uploaded_docs=[{"text": text}])
    out = intake(state)
    assert out["document_id"] == resolve_document_id(text)


def test_intake_empty_document_id_when_no_doc():
    out = intake(_state(uploaded_docs=[]))
    assert out["document_id"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_intake_document_id.py -v`
Expected: FAIL — `KeyError: 'document_id'` (intake does not set it yet).

- [ ] **Step 3a: Add state fields** in `graph/state.py` — after the `interactive_review` line (end of `LegalAgentState`):

```python
    document_id: str                       # NEW — stable id for the open document (review-store key)
    memory_degraded: bool                  # NEW — True when a memory read/store was unavailable this turn
```

- [ ] **Step 3b: Resolve it in `graph/nodes/intake.py`.** Add the import at the top:

```python
from memory.document_id import resolve_document_id
```

Then inside `intake`, after the `state["filters"] = {...}` block and before the `retrieval_query` block, add:

```python
    docs = state.get("uploaded_docs") or []
    text = "\n\n".join(
        (d.get("text", "") if isinstance(d, dict) else getattr(d, "text", ""))
        for d in docs
    )
    state["document_id"] = resolve_document_id(text)
```

- [ ] **Step 3c: Default the keys in `api/routes/query.py`** — in `initial_state`, after `"interactive_review": body.interactive_review,`:

```python
        "document_id": "",
        "memory_degraded": False,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest tests/test_intake_document_id.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add graph/state.py graph/nodes/intake.py api/routes/query.py tests/test_intake_document_id.py
git commit -m "feat(graph): resolve document_id in intake; add memory_degraded state

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Phase 0 — surface checkpointer-absent as `memory_degraded` (API)

**Files:**
- Modify: `api/routes/query.py` (track checkpointer availability; add `memory_degraded` to every payload)
- Test: `tests/test_query_memory.py`

**Interfaces:**
- Produces: response payload key `data["memory_degraded"]: bool`, True when the checkpointer is enabled but unavailable, OR the graph reported `report["memory_degraded"]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_query_memory.py
"""Phase 0: a missing checkpointer surfaces as memory_degraded, not silence."""
import api.routes.query as q


def test_payload_flags_degraded_when_checkpointer_absent(monkeypatch):
    monkeypatch.setattr(q, "_checkpointer_active", False)
    monkeypatch.setattr(q.get_settings(), "checkpointer_enabled", True, raising=False)
    payload = q._payload_from_result({"task_type": "research", "report": {}}, "sess-1")
    assert payload["memory_degraded"] is True


def test_payload_not_degraded_when_checkpointer_active(monkeypatch):
    monkeypatch.setattr(q, "_checkpointer_active", True)
    payload = q._payload_from_result({"task_type": "research", "report": {}}, "sess-1")
    assert payload["memory_degraded"] is False


def test_payload_degraded_when_report_says_so(monkeypatch):
    monkeypatch.setattr(q, "_checkpointer_active", True)
    payload = q._payload_from_result(
        {"task_type": "research", "report": {"memory_degraded": True}}, "sess-1"
    )
    assert payload["memory_degraded"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_query_memory.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute '_checkpointer_active'`.

- [ ] **Step 3a: Track checkpointer availability in `api/routes/query.py`.** Add a module global beside `_graph = None`:

```python
_graph = None
_checkpointer_active = False
```

In `_get_graph`, set it and log loudly when degraded:

```python
def _get_graph():
    """Lazy-init compiled graph with optional Redis checkpointer."""
    global _graph, _checkpointer_active
    if _graph is None:
        settings = get_settings()
        cp = build_checkpointer() if settings.checkpointer_enabled else None
        _checkpointer_active = cp is not None
        if settings.checkpointer_enabled and cp is None:
            logger.error(
                "Checkpointer ENABLED but unavailable — sessions are stateless this run. "
                "Responses will report memory_degraded=True."
            )
        _graph = build_graph(checkpointer=cp)
    return _graph
```

- [ ] **Step 3b: Add a degraded helper + include the flag in `_payload_from_result`.** Add the helper above `_payload_from_result`:

```python
def _memory_degraded(report: dict) -> bool:
    """True when this turn's memory was degraded — either the report flagged it
    (in-graph read failure) or the checkpointer is enabled but unavailable."""
    if report.get("memory_degraded"):
        return True
    return get_settings().checkpointer_enabled and not _checkpointer_active
```

Then add `"memory_degraded": _memory_degraded(...)` to **all three** return dicts in `_payload_from_result`:
- interrupt branch and legacy `awaiting_review` branch: `"memory_degraded": _memory_degraded(result.get("report", {}) or {})`,
- final branch: compute `report = result.get("report", {})` once and use `"memory_degraded": _memory_degraded(report)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest tests/test_query_memory.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add api/routes/query.py tests/test_query_memory.py
git commit -m "feat(api): surface checkpointer-absent as memory_degraded (Phase 0)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Phase 0 — Word add-in "memory unavailable" banner

**Files:**
- Modify: `clients/word/src/api.ts` (add `memory_degraded` to `QueryResponse.data`)
- Modify: `clients/word/src/components/ChatTab.tsx` (render a banner when degraded)

**Interfaces:**
- Consumes: `res.data.memory_degraded` (Task 3).

> Frontend: no unit-test harness in this repo. Verify with `npx tsc --noEmit` and a Word sideload smoke.

- [ ] **Step 1: Extend the response type** in `clients/word/src/api.ts` — inside `QueryResponse.data`, add after `awaiting_review?: boolean;`:

```typescript
    memory_degraded?: boolean;
```

- [ ] **Step 2: Render the banner** in `clients/word/src/components/ChatTab.tsx`. Add state near the other `useState` calls:

```typescript
  const [memoryDegraded, setMemoryDegraded] = useState(false);
```

In `send`, after the `if (res.status === "error")` guard, before reading `rawAnswer`:

```typescript
      setMemoryDegraded(Boolean(res.data?.memory_degraded));
```

In the JSX, immediately above the `{error && ...}` line:

```tsx
      {memoryDegraded && (
        <div className="status warning">
          Memory unavailable this turn — this reply and any review won't be remembered.
        </div>
      )}
```

- [ ] **Step 3: Verify the type-check passes**

Run: `cd clients/word && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add clients/word/src/api.ts clients/word/src/components/ChatTab.tsx
git commit -m "feat(word): show a banner when memory is degraded (Phase 0)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `memory/review_store.py` — persist + load reviews (SQLite)

**Files:**
- Create: `memory/review_store.py`
- Test: `tests/test_review_store.py`

**Interfaces:**
- Produces:
  - `init_review_db(db_path: str) -> None`
  - `save_review(db_path: str, document_id: str, session_id: str, markdown: str, contract_type: str) -> None` — **raises** on failure (loud).
  - `load_latest_review(db_path: str, document_id: str) -> dict | None` — keys: `timestamp, session_id, contract_type, markdown`.
  - `load_history(db_path: str, document_id: str) -> list[dict]` — newest first.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_review_store.py
"""Persisted review store — append-per-session, latest lookup, loud writes."""
import pytest

from memory.review_store import (
    init_review_db, save_review, load_latest_review, load_history,
)


def _db(tmp_path):
    p = str(tmp_path / "reviews.db")
    init_review_db(p)
    return p


def test_save_then_load_latest(tmp_path):
    db = _db(tmp_path)
    save_review(db, "doc-1", "sess-1", "# Review\nFinding A", "sow")
    latest = load_latest_review(db, "doc-1")
    assert latest["markdown"] == "# Review\nFinding A"
    assert latest["session_id"] == "sess-1"
    assert latest["contract_type"] == "sow"


def test_append_keeps_history_latest_wins(tmp_path):
    db = _db(tmp_path)
    save_review(db, "doc-1", "sess-1", "first", "sow")
    save_review(db, "doc-1", "sess-2", "second", "sow")
    assert load_latest_review(db, "doc-1")["markdown"] == "second"
    history = load_history(db, "doc-1")
    assert [h["markdown"] for h in history] == ["second", "first"]  # newest first


def test_load_latest_returns_none_when_absent(tmp_path):
    db = _db(tmp_path)
    assert load_latest_review(db, "no-such-doc") is None


def test_save_raises_loudly_on_bad_path():
    # A path whose parent directory does not exist cannot be opened — must raise,
    # never silently no-op (the user would believe the review saved).
    with pytest.raises(Exception):
        save_review("/no/such/dir/reviews.db", "doc-1", "s", "md", "sow")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_review_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'memory.review_store'`.

- [ ] **Step 3: Write minimal implementation**

```python
# memory/review_store.py
"""SQLite store for persisted contract reviews, keyed to a document_id.

Mirrors memory/audit.py (plain sqlite3, db_path-per-call). Stores the FULL
markdown review, one row per (document_id, session_id) — list-shaped so a
re-review appends a new session rather than overwriting. The schema is the
natural home for the later FTS cross-matter precedent layer.

Writes are LOUD: save_review lets exceptions propagate. A lost review write must
never be silent — the user believes their review was saved.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS review_store (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    document_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    contract_type TEXT NOT NULL DEFAULT '',
    review_markdown TEXT NOT NULL
)
"""

_CREATE_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_review_doc ON review_store (document_id, id)"
)


def init_review_db(db_path: str) -> None:
    """Create the review_store table + index if absent."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(_CREATE_TABLE)
        conn.execute(_CREATE_INDEX)
        conn.commit()
    finally:
        conn.close()
    logger.info("Review store initialized at %s", db_path)


def save_review(
    db_path: str, document_id: str, session_id: str, markdown: str, contract_type: str
) -> None:
    """Append one review row. Raises on any failure — never a silent no-op."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """INSERT INTO review_store
               (timestamp, document_id, session_id, contract_type, review_markdown)
               VALUES (?, ?, ?, ?, ?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                document_id, session_id, contract_type, markdown,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    logger.info("Review saved: document_id=%s session=%s", document_id, session_id)


def _row_to_dict(row: tuple) -> dict:
    return {
        "timestamp": row[0], "session_id": row[1],
        "contract_type": row[2], "markdown": row[3],
    }


def load_latest_review(db_path: str, document_id: str) -> dict | None:
    """Most recent review for this document, or None."""
    if not document_id:
        return None
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """SELECT timestamp, session_id, contract_type, review_markdown
               FROM review_store WHERE document_id = ? ORDER BY id DESC LIMIT 1""",
            (document_id,),
        )
        row = cur.fetchone()
    finally:
        conn.close()
    return _row_to_dict(row) if row else None


def load_history(db_path: str, document_id: str) -> list[dict]:
    """All reviews for this document, newest first."""
    if not document_id:
        return []
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """SELECT timestamp, session_id, contract_type, review_markdown
               FROM review_store WHERE document_id = ? ORDER BY id DESC""",
            (document_id,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest tests/test_review_store.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add memory/review_store.py tests/test_review_store.py
git commit -m "feat(memory): SQLite review_store — append-per-session, loud writes

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Persist the review in `memory_writer` (loud on failure)

**Files:**
- Modify: `graph/nodes/memory_writer.py`
- Test: `tests/test_memory_writer.py`

**Interfaces:**
- Consumes: `save_review` (Task 5), `state["document_id"]` (Task 2).
- Produces: on a review turn, a row in `review_store`; on write failure, `report["review_persist_error"]` set (surfaced, not raised).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_memory_writer.py
"""memory_writer persists contract reviews; failures are surfaced, not silent."""
import graph.nodes.memory_writer as mod


def _state(**kw):
    base = {
        "session_id": "s1", "user_id": "u1", "task_type": "contract_review",
        "request": "Review this", "risk_level": "low", "attorney_notes": "",
        "document_id": "doc-1", "llm_response": "# Review\nFinding",
        "contract_type_detected": "sow", "report": {"response": "# Review\nFinding"},
        "awaiting_review": False,
    }
    base.update(kw)
    return base


def test_persists_review_for_contract_review_turn(monkeypatch):
    monkeypatch.setattr(mod, "write_audit_log", lambda **kw: None)
    monkeypatch.setattr(mod, "init_audit_db", lambda p: None)
    monkeypatch.setattr(mod, "init_review_db", lambda p: None)
    saved = {}
    monkeypatch.setattr(mod, "save_review",
                        lambda db_path, document_id, session_id, markdown, contract_type:
                        saved.update(document_id=document_id, markdown=markdown,
                                     contract_type=contract_type))
    mod.memory_writer(_state())
    assert saved["document_id"] == "doc-1"
    assert saved["markdown"] == "# Review\nFinding"
    assert saved["contract_type"] == "sow"


def test_does_not_persist_for_non_review_turn(monkeypatch):
    monkeypatch.setattr(mod, "write_audit_log", lambda **kw: None)
    monkeypatch.setattr(mod, "init_audit_db", lambda p: None)
    monkeypatch.setattr(mod, "init_review_db", lambda p: None)
    called = {"n": 0}
    monkeypatch.setattr(mod, "save_review", lambda **kw: called.__setitem__("n", called["n"] + 1))
    mod.memory_writer(_state(task_type="research"))
    assert called["n"] == 0


def test_write_failure_is_surfaced_in_report(monkeypatch):
    monkeypatch.setattr(mod, "write_audit_log", lambda **kw: None)
    monkeypatch.setattr(mod, "init_audit_db", lambda p: None)
    monkeypatch.setattr(mod, "init_review_db", lambda p: None)
    def _boom(**kw):
        raise RuntimeError("disk full")
    monkeypatch.setattr(mod, "save_review", _boom)
    out = mod.memory_writer(_state())
    assert "review_persist_error" in out["report"]
    assert "disk full" in out["report"]["review_persist_error"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_memory_writer.py -v`
Expected: FAIL — `save_review` not imported in the module (`AttributeError`), and no `review_persist_error` handling.

- [ ] **Step 3: Implement.** In `graph/nodes/memory_writer.py`, extend the import line:

```python
from memory.audit import init_audit_db, write_audit_log
from memory.review_store import init_review_db, save_review
```

Add a second init guard beside `_db_initialized`:

```python
_db_initialized = False
_review_db_initialized = False
```

At the end of `memory_writer`, replace the final `return {}` with the persistence block:

```python
    # Persist the full markdown review, keyed to the document. Loud on failure:
    # a lost write must not look like a save (the user believes it persisted).
    if state.get("task_type") == "contract_review" and state.get("llm_response"):
        global _review_db_initialized
        if not _review_db_initialized:
            init_review_db(settings.sqlite_path)
            _review_db_initialized = True
        try:
            save_review(
                db_path=settings.sqlite_path,
                document_id=state.get("document_id", ""),
                session_id=state.get("session_id", ""),
                markdown=state.get("llm_response", ""),
                contract_type=state.get("contract_type_detected", ""),
            )
        except Exception as e:
            logger.error("[memory_writer] FAILED to persist review: %s", e)
            report = {**(state.get("report") or {}), "review_persist_error": str(e)}
            return {"report": report}

    return {}
```

(Move the `global _review_db_initialized` declaration to the top of the function with the existing `global _db_initialized` if your linter prefers; either is fine functionally.)

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest tests/test_memory_writer.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add graph/nodes/memory_writer.py tests/test_memory_writer.py
git commit -m "feat(graph): persist reviews to SQLite in memory_writer; loud on failure

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Inject the stored review on the chat path; surface `memory_degraded`

**Files:**
- Modify: `skills/legal_research.py` (`_run_doc_chat` loads + injects the latest review; degrade on failure)
- Modify: `graph/nodes/output_formatter.py` (copy `memory_degraded` into report)
- Test: extend `tests/test_skills.py`

**Interfaces:**
- Consumes: `load_latest_review` (Task 5), `state["document_id"]` (Task 2).
- Produces: chat prompt includes a `--- PRIOR REVIEW ---` system block when a stored review exists; `state["memory_degraded"]=True` on load failure; `report["memory_degraded"]` populated.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_skills.py`:

```python
def test_doc_chat_injects_stored_review(monkeypatch):
    import skills.legal_research as lr

    captured = {}

    class FakeResp:
        content = "Per the prior review, the IP clause is the risk."

    def fake_traced_invoke(llm, messages, name="doc_chat"):
        captured["messages"] = messages
        return FakeResp()

    monkeypatch.setattr(lr, "_build_llm", lambda: object())
    monkeypatch.setattr(lr, "traced_invoke", fake_traced_invoke)
    monkeypatch.setattr(lr, "load_latest_review",
                        lambda db_path, document_id: {"markdown": "# Review\nIP clause is risky."})

    state = _make_state(
        request="expand on the IP risk you flagged", task_type="research",
        uploaded_docs=[{"text": "MUTUAL NDA\n\nbody"}], document_id="doc-1",
    )
    lr.legal_research(state)

    contents = "\n".join(m["content"] for m in captured["messages"] if m["role"] == "system")
    assert "PRIOR REVIEW" in contents
    assert "IP clause is risky" in contents


def test_doc_chat_degrades_when_review_load_fails(monkeypatch):
    import skills.legal_research as lr

    class FakeResp:
        content = "answer"

    monkeypatch.setattr(lr, "_build_llm", lambda: object())
    monkeypatch.setattr(lr, "traced_invoke", lambda llm, messages, name="doc_chat": FakeResp())
    def _boom(db_path, document_id):
        raise RuntimeError("redis/sqlite down")
    monkeypatch.setattr(lr, "load_latest_review", _boom)

    state = _make_state(
        request="summarize", task_type="research",
        uploaded_docs=[{"text": "MUTUAL NDA\n\nbody"}], document_id="doc-1",
    )
    out = lr.legal_research(state)
    assert out["memory_degraded"] is True
    assert out["llm_response"] == "answer"   # still answers
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_skills.py -k doc_chat_injects_stored_review -v`
Expected: FAIL — `load_latest_review` not imported in `skills.legal_research`; no PRIOR REVIEW block.

- [ ] **Step 3a: Implement in `skills/legal_research.py`.** Add imports at the top:

```python
from config import get_settings
from memory.review_store import load_latest_review
```

(`get_settings` may already be imported — if so, don't duplicate.)

In `_run_doc_chat`, after computing `attorney_notes` and before building `messages`, add the review lookup. Replace the existing `messages` assembly:

```python
    chat_history = state.get("chat_history", []) or []
    messages: list[dict] = [
        {"role": "system", "content": CHAT_SYSTEM_PROMPT},
        *chat_history,
        {"role": "user", "content": user_message},
    ]
```

with:

```python
    review_block = _load_prior_review_block(state)

    chat_history = state.get("chat_history", []) or []
    system_messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
    if review_block:
        system_messages.append({"role": "system", "content": review_block})
    messages: list[dict] = [
        *system_messages,
        *chat_history,
        {"role": "user", "content": user_message},
    ]
```

Add the helper above `_run_doc_chat`:

```python
def _load_prior_review_block(state: LegalAgentState) -> str:
    """Latest stored review for this document, as a system block. Empty string
    when none exists. On a store-read failure, flags memory_degraded and returns
    empty — tracing/memory must never break the chat turn."""
    document_id = state.get("document_id", "")
    if not document_id:
        return ""
    try:
        latest = load_latest_review(get_settings().sqlite_path, document_id)
    except Exception as e:
        logger.error("[legal_research] prior-review load failed: %s", e)
        state["memory_degraded"] = True
        return ""
    if not latest:
        return ""
    return (
        "--- PRIOR REVIEW (most recent, this document) ---\n"
        "Answer recall questions from this review; do not re-derive or contradict it.\n\n"
        f"{latest['markdown']}\n"
        "--- END PRIOR REVIEW ---"
    )
```

- [ ] **Step 3b: Surface the flag in `graph/nodes/output_formatter.py`** — add to the `report` dict (after `requires_attorney`):

```python
        "memory_degraded": state.get("memory_degraded", False),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_skills.py -k "doc_chat_injects_stored_review or doc_chat_degrades" -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/legal_research.py graph/nodes/output_formatter.py tests/test_skills.py
git commit -m "feat(chat): inject the stored review on the chat path; degrade-with-warning

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Extract shared `skills/grounding.py`; refactor `contract_review` onto it

**Files:**
- Create: `skills/grounding.py`
- Modify: `skills/contract_review/contract_review.py` (call the shared helpers)
- Test: `tests/test_grounding.py` (new) + the existing `contract_review` tests must stay green

**Interfaces:**
- Produces:
  - `detect_contract_type(text: str) -> tuple[str, bool]` — (type, was_ambiguous).
  - `load_playbook_bundle(contract_type: str) -> str`.
  - `attach_parent_msa(text: str, client_id: str, max_chars: int) -> tuple[str, str] | None` — (title, possibly-truncated text); None when not applicable.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_grounding.py
"""Shared grounding helpers used by both contract_review and the chat path."""
import skills.grounding as g


def test_detect_sow():
    ctype, ambiguous = g.detect_contract_type("STATEMENT OF WORK\n\nbody about the project")
    assert ctype == "sow"
    assert ambiguous is False


def test_detect_defaults_nda_when_ambiguous():
    ctype, ambiguous = g.detect_contract_type("Some text with no contract keywords at all.")
    assert ctype == "nda"
    assert ambiguous is True


def test_load_playbook_bundle_returns_text():
    bundle = g.load_playbook_bundle("sow")
    assert isinstance(bundle, str) and len(bundle) > 100


def test_attach_parent_msa_none_without_client(monkeypatch):
    monkeypatch.setattr(g, "get_parent_msa", lambda client_id: None)
    assert g.attach_parent_msa("SOW text", "", max_chars=1000) is None


def test_attach_parent_msa_truncates(monkeypatch):
    monkeypatch.setattr(g, "get_parent_msa", lambda client_id: ("Model MSA", "X" * 5000))
    title, text = g.attach_parent_msa("SOW text", "internal", max_chars=1000)
    assert title == "Model MSA"
    assert len(text) <= 1000 + 60   # truncation marker allowance
    assert "truncated" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_grounding.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skills.grounding'`.

- [ ] **Step 3a: Create `skills/grounding.py`** by moving the detection logic verbatim out of `contract_review.py` (the `_TYPE_PATTERNS`, `_DEFAULT_TYPE`, `_TITLE_REGION_CHARS`, `_TITLE_WEIGHT`, `_detect_contract_type` body) and adding the bundle + MSA helpers:

```python
# skills/grounding.py
"""Shared contract grounding — type detection, playbook bundle, parent-MSA attach.

Single source of truth used by BOTH surfaces: the Findings path
(skills/contract_review) and the Chat path (skills/legal_research). Keeping it
here is what prevents the two surfaces from drifting back into asymmetry.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from rag.related_docs import get_parent_msa
from skills.base import load_bundle

logger = logging.getLogger(__name__)

_PLAYBOOK_DIR = Path(__file__).parent / "contract_review" / "playbook"

_TYPE_PATTERNS: tuple[tuple[str, tuple[re.Pattern, ...]], ...] = (
    ("baa", (
        re.compile(r"\bbusiness associate agreement\b", re.I),
        re.compile(r"\bhipaa\b", re.I),
        re.compile(r"\bprotected health information\b", re.I),
        re.compile(r"\bphi\b"),
    )),
    ("msa", (
        re.compile(r"\bmaster\s+services?\s+agreement\b", re.I),
        re.compile(r"\bmsa\b", re.I),
    )),
    ("sow", (
        re.compile(r"\bstatement\s+of\s+work\b", re.I),
        re.compile(r"\bwork\s+order\b", re.I),
        re.compile(r"\bsow\b", re.I),
    )),
    ("nda", (
        re.compile(r"\bnon[-\s]?disclosure\s+agreement\b", re.I),
        re.compile(r"\bmutual\s+nda\b", re.I),
        re.compile(r"\bmnda\b", re.I),
        re.compile(r"\bconfidentiality\s+agreement\b", re.I),
        re.compile(r"\bnda\b", re.I),
    )),
)
_DEFAULT_TYPE = "nda"
_TITLE_REGION_CHARS = 200
_TITLE_WEIGHT = 100


def detect_contract_type(text: str) -> tuple[str, bool]:
    """Detect contract type. Returns (type, was_ambiguous). Title region dominates;
    whole-document counts break ties. Defaults to NDA when nothing matches."""
    title = text[:_TITLE_REGION_CHARS]
    scores: dict[str, int] = {}
    for ctype, patterns in _TYPE_PATTERNS:
        title_hits = sum(len(p.findall(title)) for p in patterns)
        body_hits = sum(len(p.findall(text)) for p in patterns)
        scores[ctype] = _TITLE_WEIGHT * title_hits + body_hits
    best_type = max(scores, key=lambda t: scores[t])
    if scores[best_type] == 0:
        return _DEFAULT_TYPE, True
    return best_type, False


def load_playbook_bundle(contract_type: str) -> str:
    """The assembled per-type playbook bundle (role → … → No-Signature Gate)."""
    return load_bundle(_PLAYBOOK_DIR, contract_type)


def attach_parent_msa(text: str, client_id: str, max_chars: int) -> tuple[str, str] | None:
    """Return (title, possibly-truncated MSA text) for the governing MSA, or None.

    `text` is accepted for a future party-name match; today it selects the single
    MSA on file for the client. Returns None when no MSA / no client_id.
    """
    parent = get_parent_msa(client_id)
    if not parent:
        return None
    title, msa_text = parent
    if len(msa_text) > max_chars:
        logger.warning("[grounding] MSA %r is %d chars — truncating to %d",
                       title, len(msa_text), max_chars)
        msa_text = msa_text[:max_chars] + f"\n\n[MSA truncated to {max_chars} chars]"
    return title, msa_text
```

- [ ] **Step 3b: Refactor `skills/contract_review/contract_review.py`.** Remove the moved code (`_TYPE_PATTERNS`, `_DEFAULT_TYPE`, `_TITLE_REGION_CHARS`, `_TITLE_WEIGHT`, `_detect_contract_type`) and the `from rag.related_docs import get_parent_msa` import. Add:

```python
from skills.grounding import attach_parent_msa, detect_contract_type, load_playbook_bundle
```

Replace `contract_type, was_ambiguous = _detect_contract_type(detect_source)` with:

```python
    contract_type, was_ambiguous = detect_contract_type(detect_source)
```

Replace `playbook = load_bundle(_PLAYBOOK_DIR, contract_type)` with:

```python
    playbook = load_playbook_bundle(contract_type)
```

Replace the SOW MSA block (the `try/except` around `get_parent_msa` + the truncation `if len(msa_text) > _MSA_MAX_CHARS`) with the helper call:

```python
    if contract_type == "sow" and uploaded_text:
        client_id = (state.get("filters") or {}).get("client_id", "")
        try:
            parent = attach_parent_msa(uploaded_text, client_id, _MSA_MAX_CHARS)
        except Exception:
            logger.exception("[contract_review] parent-MSA lookup failed — reviewing SOW standalone")
            parent = None
        if parent:
            msa_doc_title, msa_text = parent
            user_content += (
                f"\n\n--- GOVERNING MSA ({msa_doc_title}) ---\n{msa_text}\n--- END GOVERNING MSA ---"
            )
            msa_attached = True
            logger.info("[contract_review] attached governing MSA %r (%d chars)",
                        msa_doc_title, len(msa_text))
        else:
            logger.info("[contract_review] no governing MSA on file for client_id=%s — "
                        "reviewing SOW standalone", client_id)
```

Keep `from skills.base import load_bundle` only if still used elsewhere; otherwise remove it (no dead imports). Keep `_MSA_MAX_CHARS`, `_OUTPUT_CONSTRAINTS`, `_MSA_COMPARISON_DIRECTIVE`, `_PLAYBOOK_DIR` (still used for nothing now — remove `_PLAYBOOK_DIR` if unused after the `load_playbook_bundle` swap).

- [ ] **Step 4: Run the new + existing tests to verify all pass**

Run: `./.venv/bin/python -m pytest tests/test_grounding.py tests/test_skills.py tests/test_related_docs.py -v`
Expected: new grounding tests PASS; all existing `contract_review` tests still PASS (behavior-preserving refactor).

- [ ] **Step 5: Commit**

```bash
git add skills/grounding.py skills/contract_review/contract_review.py tests/test_grounding.py
git commit -m "refactor(skills): extract shared grounding.py; contract_review uses it

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Phase 2 — attach playbook + MSA on the chat path, cache-ordered

**Files:**
- Modify: `skills/legal_research.py` (`_run_doc_chat`: build grounding, reorder stable-first/question-last)
- Test: extend `tests/test_skills.py`

**Interfaces:**
- Consumes: `detect_contract_type`, `load_playbook_bundle`, `attach_parent_msa` (Task 8), the review block (Task 7).
- Produces: chat messages ordered `[chat rules][playbook][MSA note + MSA][prior review][history][user: doc + question]`; the **user question is the last message** and the document precedes the question within it.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_skills.py`:

```python
def test_doc_chat_attaches_playbook_and_msa_ordered(monkeypatch):
    import skills.legal_research as lr

    captured = {}

    class FakeResp:
        content = "answer"

    monkeypatch.setattr(lr, "_build_llm", lambda: object())
    monkeypatch.setattr(lr, "traced_invoke",
                        lambda llm, messages, name="doc_chat": captured.update(messages=messages) or FakeResp())
    monkeypatch.setattr(lr, "load_latest_review", lambda db_path, document_id: None)
    monkeypatch.setattr(lr, "detect_contract_type", lambda text: ("sow", False))
    monkeypatch.setattr(lr, "load_playbook_bundle", lambda ctype: "PLAYBOOK_BUNDLE_TEXT")
    monkeypatch.setattr(lr, "attach_parent_msa",
                        lambda text, client_id, max_chars: ("Model MSA", "MSA_BODY_TEXT"))

    state = _make_state(
        request="does this SOW conflict with the MSA?", task_type="research",
        uploaded_docs=[{"text": "STATEMENT OF WORK\n\nbody"}],
        filters={"client_id": "internal"}, document_id="doc-1",
    )
    lr.legal_research(state)

    msgs = captured["messages"]
    joined = "\n".join(m["content"] for m in msgs)
    assert "PLAYBOOK_BUNDLE_TEXT" in joined
    assert "MSA_BODY_TEXT" in joined
    # Question is LAST and the document precedes it inside that message.
    assert msgs[-1]["role"] == "user"
    assert msgs[-1]["content"].index("STATEMENT OF WORK") < msgs[-1]["content"].index("does this SOW conflict")
    # Grounding precedes the user turn (cache-friendly ordering).
    assert joined.index("PLAYBOOK_BUNDLE_TEXT") < joined.index("does this SOW conflict")


def test_doc_chat_no_msa_for_nda(monkeypatch):
    import skills.legal_research as lr

    class FakeResp:
        content = "answer"

    seen = {}
    monkeypatch.setattr(lr, "_build_llm", lambda: object())
    monkeypatch.setattr(lr, "traced_invoke",
                        lambda llm, messages, name="doc_chat": seen.update(messages=messages) or FakeResp())
    monkeypatch.setattr(lr, "load_latest_review", lambda db_path, document_id: None)
    monkeypatch.setattr(lr, "detect_contract_type", lambda text: ("nda", False))
    monkeypatch.setattr(lr, "load_playbook_bundle", lambda ctype: "NDA_BUNDLE")
    def _no_msa_for_nda(text, client_id, max_chars):
        raise AssertionError("attach_parent_msa must not be called for an NDA")
    monkeypatch.setattr(lr, "attach_parent_msa", _no_msa_for_nda)

    state = _make_state(
        request="why is this risky?", task_type="research",
        uploaded_docs=[{"text": "MUTUAL NDA\n\nbody"}],
        filters={"client_id": "internal"}, document_id="doc-2",
    )
    lr.legal_research(state)
    assert "NDA_BUNDLE" in "\n".join(m["content"] for m in seen["messages"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_skills.py -k "doc_chat_attaches_playbook or doc_chat_no_msa" -v`
Expected: FAIL — grounding names not imported in `legal_research`; question still first; no playbook in messages.

- [ ] **Step 3a: Add imports + a chat MSA note** to `skills/legal_research.py` (top):

```python
from skills.grounding import attach_parent_msa, detect_contract_type, load_playbook_bundle
```

Add the model-neutral note constant near `CHAT_SYSTEM_PROMPT`:

```python
# Structural, model-neutral note added when a governing MSA is attached on the
# chat path. Mirrors the review path's directive; SKILL.md stays the ceiling.
_CHAT_MSA_NOTE = (
    "The Master Services Agreement below GOVERNS this document. Ground any "
    "MSA-conflict answer in its actual text; if the MSA is silent on a point, say "
    "so rather than assuming. Do not invent MSA terms."
)
```

- [ ] **Step 3b: Build grounding + reorder in `_run_doc_chat`.** First, move the document into the user message *before* the question. Replace the existing `user_message` assembly:

```python
    request = state["request"]
    user_message = (
        f"User request: {request}\n\n"
        f"--- ATTACHED DOCUMENT (the source of truth — answer from this) ---\n"
        f"{uploaded_text}\n"
        f"--- END ATTACHED DOCUMENT ---"
    )
```

with (document first, question last — so the changing question is the trailing tokens):

```python
    request = state["request"]
    user_message = (
        f"--- ATTACHED DOCUMENT (the source of truth — answer from this) ---\n"
        f"{uploaded_text}\n"
        f"--- END ATTACHED DOCUMENT ---\n\n"
        f"User request: {request}"
    )
```

Then replace the `system_messages = [...]` assembly built in Task 7 with the grounded, cache-ordered version:

```python
    review_block = _load_prior_review_block(state)
    playbook, msa_block = _build_chat_grounding(state, uploaded_text)

    chat_history = state.get("chat_history", []) or []
    system_messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
    if playbook:
        system_messages.append({"role": "system", "content": playbook})
    if msa_block:
        system_messages.append({"role": "system", "content": msa_block})
    if review_block:
        system_messages.append({"role": "system", "content": review_block})
    messages: list[dict] = [
        *system_messages,        # stable across turns → cached prefix
        *chat_history,
        {"role": "user", "content": user_message},   # changes → trailing tokens
    ]
```

Add the grounding helper above `_run_doc_chat`:

```python
def _build_chat_grounding(state: LegalAgentState, uploaded_text: str) -> tuple[str, str]:
    """(playbook_bundle, msa_block) for the chat path. Empty strings on failure —
    grounding must never break the chat turn. MSA only for SOWs."""
    playbook = ""
    msa_block = ""
    try:
        contract_type, _ = detect_contract_type(uploaded_text)
        playbook = load_playbook_bundle(contract_type)
        if contract_type == "sow":
            client_id = (state.get("filters") or {}).get("client_id", "")
            parent = attach_parent_msa(uploaded_text, client_id, _MSA_CHAT_MAX_CHARS)
            if parent:
                title, msa_text = parent
                msa_block = (
                    f"{_CHAT_MSA_NOTE}\n\n--- GOVERNING MSA ({title}) ---\n"
                    f"{msa_text}\n--- END GOVERNING MSA ---"
                )
    except Exception as e:
        logger.warning("[legal_research] chat grounding failed: %s — answering ungrounded", e)
    return playbook, msa_block
```

Add the MSA cap constant near the top (a temporary module constant; Task 10 promotes it to config):

```python
_MSA_CHAT_MAX_CHARS = 24000
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_skills.py -k "doc_chat" -v`
Expected: all `doc_chat*` tests PASS (Task 7 + Task 9).

- [ ] **Step 5: Commit**

```bash
git add skills/legal_research.py tests/test_skills.py
git commit -m "feat(chat): attach playbook + MSA on chat, cache-ordered (Phase 2)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Phase 3 — context cap on the chat path

**Files:**
- Modify: `config.py` (`chat_context_max_chars`, `msa_max_chars`)
- Modify: `skills/legal_research.py` (truncate the document when the assembled context exceeds budget)
- Modify: `skills/contract_review/contract_review.py` (read `msa_max_chars` from config)
- Test: extend `tests/test_skills.py`

**Interfaces:**
- Consumes: assembled `messages` (Task 9).
- Produces: a document-only truncation when total content exceeds `chat_context_max_chars`; grounding untouched; truncation logged + marked.

- [ ] **Step 1: Write the failing test** — append to `tests/test_skills.py`:

```python
def test_doc_chat_caps_document_not_grounding(monkeypatch):
    import skills.legal_research as lr
    from config import get_settings
    monkeypatch.setenv("CHAT_CONTEXT_MAX_CHARS", "2000")
    get_settings.cache_clear()

    captured = {}

    class FakeResp:
        content = "answer"

    monkeypatch.setattr(lr, "_build_llm", lambda: object())
    monkeypatch.setattr(lr, "traced_invoke",
                        lambda llm, messages, name="doc_chat": captured.update(messages=messages) or FakeResp())
    monkeypatch.setattr(lr, "load_latest_review", lambda db_path, document_id: None)
    monkeypatch.setattr(lr, "detect_contract_type", lambda text: ("sow", False))
    monkeypatch.setattr(lr, "load_playbook_bundle", lambda ctype: "PLAYBOOK")
    monkeypatch.setattr(lr, "attach_parent_msa",
                        lambda text, client_id, max_chars: ("Model MSA", "MSA_BODY"))

    big_doc = "STATEMENT OF WORK\n\n" + ("clause text " * 1000)   # ~12k chars
    state = _make_state(
        request="summarize", task_type="research",
        uploaded_docs=[{"text": big_doc}], filters={"client_id": "internal"},
        document_id="doc-1",
    )
    lr.legal_research(state)
    get_settings.cache_clear()

    total = sum(len(m["content"]) for m in captured["messages"])
    assert total <= 2000 + 500            # within budget (+ small overhead)
    joined = "\n".join(m["content"] for m in captured["messages"])
    assert "PLAYBOOK" in joined and "MSA_BODY" in joined   # grounding preserved
    assert "[document truncated" in joined                 # doc was the one cut
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_skills.py -k caps_document -v`
Expected: FAIL — no cap applied; total exceeds budget.

- [ ] **Step 3a: Add config** in `config.py` (in the Memory / checkpointer section):

```python
    chat_context_max_chars: int = 120000   # assembled chat-context budget (~30k tokens); tune via ollama_usage
    msa_max_chars: int = 24000             # MSA cap, shared by review + chat paths
```

- [ ] **Step 3b: Read config for the MSA cap** — in `skills/legal_research.py`, delete the temporary `_MSA_CHAT_MAX_CHARS = 24000` constant and change the `attach_parent_msa` call inside `_build_chat_grounding` to:

```python
                parent = attach_parent_msa(uploaded_text, client_id, get_settings().msa_max_chars)
```

In `skills/contract_review/contract_review.py`, replace the `_MSA_MAX_CHARS = 24000` constant usage with `get_settings().msa_max_chars` at the call site (add `from config import get_settings` at top if absent), and delete the `_MSA_MAX_CHARS` constant.

- [ ] **Step 3c: Apply the cap in `_run_doc_chat`.** After the `messages` list is assembled and before `response = traced_invoke(...)`, add:

```python
    _cap_chat_context(messages, uploaded_text, request)
```

Add the helper above `_run_doc_chat`:

```python
def _cap_chat_context(messages: list[dict], uploaded_text: str, request: str) -> None:
    """If total assembled content exceeds the budget, truncate ONLY the document
    portion of the trailing user message — never the grounding. Mutates messages
    in place; marks + logs the truncation. Crude on purpose (Phase 3)."""
    budget = get_settings().chat_context_max_chars
    total = sum(len(m["content"]) for m in messages)
    if total <= budget:
        return
    overflow = total - budget
    keep = max(0, len(uploaded_text) - overflow - len("\n\n[document truncated for context budget]"))
    truncated_doc = uploaded_text[:keep] + "\n\n[document truncated for context budget]"
    messages[-1]["content"] = (
        f"--- ATTACHED DOCUMENT (the source of truth — answer from this) ---\n"
        f"{truncated_doc}\n"
        f"--- END ATTACHED DOCUMENT ---\n\n"
        f"User request: {request}"
    )
    logger.warning("[legal_research] chat context %d > budget %d — truncated document to %d chars",
                   total, budget, keep)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_skills.py -k "doc_chat or caps_document" tests/test_grounding.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add config.py skills/legal_research.py skills/contract_review/contract_review.py tests/test_skills.py
git commit -m "feat(chat): context cap truncates the document, preserves grounding (Phase 3)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Full suite, live smoke, docs

**Files:**
- Modify: `docs/wiki.md`, `CLAUDE.md`

- [ ] **Step 1: Full suite green**

Run: `./.venv/bin/python -m pytest tests/ -v`
Expected: all prior tests + the new memory/grounding tests pass; no regressions.

- [ ] **Step 2: Restart backend and ingest the demo MSA if needed**

```bash
bash scripts/start.sh          # uvicorn does NOT hot-reload — required to load the changes
uv run python -m scripts.ingest_demo_msa   # only if Qdrant was reset
```

- [ ] **Step 3: Live sideload smoke in Word for Mac** (`cd clients/word && npm run dev`), on the demo SOW:
  - Run a **Findings** review → confirm it completes (review is now persisted to SQLite).
  - Switch to **Chat**, ask *"expand on the IP risk you flagged"* → answer recalls the prior review (not re-derived).
  - Ask *"does this SOW conflict with the MSA?"* → answer is grounded in the actual MSA.
  - Close and reopen the task pane on the same document → the prior review is still recalled in chat (proves `document_id` survives pane close).
  - Stop Redis (`docker compose stop redis`) and send a chat turn → the "memory unavailable this turn" banner appears; the turn still answers. Restart Redis.

- [ ] **Step 4: Update `docs/wiki.md`** — add a "Shipped Since Last Update" row for `feat/chat-memory-grounding` (chat now persists + recalls reviews via SQLite `review_store` keyed to `document_id`; playbook + MSA attached on chat, cache-ordered; degraded storage surfaces a banner; chat context cap). Bump the header test count by the number of new tests. Update follow-ups: mark "MSA + playbook on chat" done; leave "structured-JSON findings / selective injection", "clause segmentation", "FTS precedent recall", and "Office.js custom-property document_id" as open, measured-need follow-ups.

- [ ] **Step 5: Update `CLAUDE.md`** — add Backend bullets:
  - "Chat path is grounded + remembers reviews: `skills/grounding.py` (shared with `contract_review`) attaches playbook + MSA; `memory/review_store.py` (SQLite) persists the markdown review keyed to `memory/document_id.py::resolve_document_id` (preamble hash); `legal_research._run_doc_chat` injects the latest review and assembles stable-grounding-first / question-last for Ollama prefix-cache reuse."
  - "Degraded memory is loud: checkpointer-absent → `memory_degraded` in the query payload + a Word banner; a failed review write surfaces `report['review_persist_error']` (never silent)."
  - "Chat context is capped (`config.chat_context_max_chars`) by truncating the **document**, never the grounding."

- [ ] **Step 6: Commit**

```bash
git add docs/wiki.md CLAUDE.md
git commit -m "docs: chat memory & grounding shipped — wiki + CLAUDE.md notes

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 7: Finish the branch** — use the `superpowers:finishing-a-development-branch` skill (merge `--no-ff` into `main` after the live smoke passes; delete the branch). Do not push unless asked.

---

## Self-review

**Spec coverage:**
- Problem A (recall findings) → Tasks 5, 7. ✅
- Problem B (document-keyed, survives pane close) → Tasks 1, 2, 6, 7 (+ smoke step 3). ✅
- Problem C (playbook + MSA on chat) → Tasks 8, 9. ✅
- Problem D (context cap) → Task 10. ✅
- Problem E (fail loud) → Tasks 3 (checkpointer), 6 (review write), 7 (review read → `memory_degraded`), 4 (banner). ✅
- Shared `grounding.py` + `contract_review` refactor (architecture) → Task 8. ✅
- SQLite review store, append-per-session, list-shaped → Task 5. ✅
- `document_id` preamble hash → Task 1; resolved in intake → Task 2. ✅
- Cache-ordered prompt (stable-first/question-last) → Task 9. ✅
- Defer structured JSON → honored (we persist markdown; no output-format change). ✅
- Confirmed decisions (append, truncate-doc-first, degrade-reads/loud-writes, model-neutral MSA note) → Tasks 5/6, 10, 3/6/7, 9. ✅

**Placeholder scan:** no TBD/TODO; every code step shows complete code; commands have expected output. ✅

**Type consistency:** `resolve_document_id(text)->str`, `save_review(db_path, document_id, session_id, markdown, contract_type)`, `load_latest_review(db_path, document_id)->dict|None`, `detect_contract_type(text)->tuple[str,bool]`, `load_playbook_bundle(contract_type)->str`, `attach_parent_msa(text, client_id, max_chars)->tuple[str,str]|None` — used identically in every consuming task. `memory_degraded` set in `legal_research`/intake, surfaced by `output_formatter` and `query._memory_degraded`. ✅
