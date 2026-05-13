# Phase 4 — Shared Nodes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace all stub nodes with real implementations so the graph performs actual intent classification, retrieval, LLM generation, risk assessment, audit logging, and human-in-the-loop review.

**Architecture:** Each node is a pure function `(LegalAgentState) -> LegalAgentState`. Nodes call existing RAG layer and Ollama via httpx. SQLite audit log via aiosqlite (sync wrapper for now). Langfuse tracing wraps the graph build. Human review uses LangGraph `interrupt()` with Redis checkpointer for persistence.

**Tech Stack:** langgraph, httpx (Ollama), aiosqlite, langfuse, langgraph-checkpoint-redis, pytest

---

## File Structure

```
legal-plugin/
|-- memory/
|   |-- __init__.py
|   |-- audit.py              # CREATE — SQLite audit log (create table + write)
|   +-- session.py            # CREATE — placeholder for Redis session helpers
|-- graph/nodes/
|   |-- intake.py             # MODIFY — resolve client_id, set filters
|   |-- intent_router.py      # MODIFY — LLM classification of task_type
|   |-- rag_retriever.py      # MODIFY — call hybrid_search
|   |-- llm_caller.py         # MODIFY — call Ollama, get structured JSON
|   |-- risk_assessor.py      # MODIFY — citation check + risk evaluation
|   |-- output_formatter.py   # MODIFY — build report dict
|   |-- memory_writer.py      # MODIFY — write SQLite audit log
|   +-- human_review.py       # MODIFY — LangGraph interrupt()
|-- graph/
|   +-- graph.py              # MODIFY — add checkpointer support
|-- observability/
|   |-- __init__.py
|   +-- langfuse.py           # CREATE — init_observability() + trace helpers
+-- tests/
    |-- test_audit.py          # CREATE
    |-- test_nodes.py          # CREATE — unit tests for real node implementations
    +-- test_graph.py          # MODIFY — update for real node behavior
```

---

### Task 1: Create SQLite audit module

The audit log records every skill invocation. Written by `memory_writer` node before returning.

**Files:**
- Create: `memory/__init__.py`
- Create: `memory/audit.py`
- Create: `tests/test_audit.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_audit.py
import sqlite3
from pathlib import Path


def test_init_audit_db_creates_table(tmp_path):
    """init_audit_db creates the audit_log table."""
    from memory.audit import init_audit_db
    db_path = str(tmp_path / "test_legal.db")
    init_audit_db(db_path)

    conn = sqlite3.connect(db_path)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='audit_log'")
    assert cursor.fetchone() is not None
    conn.close()


def test_write_audit_log(tmp_path):
    """write_audit_log inserts a record."""
    from memory.audit import init_audit_db, write_audit_log
    db_path = str(tmp_path / "test_legal.db")
    init_audit_db(db_path)

    write_audit_log(
        db_path=db_path,
        session_id="sess-001",
        user_id="attorney-1",
        skill_name="legal_research",
        task_type="research",
        request_summary="What are indemnification standards?",
        risk_level="low",
        review_status="not_required",
        review_notes="",
        duration_ms=1200,
    )

    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT * FROM audit_log").fetchall()
    assert len(rows) == 1
    conn.close()


def test_write_audit_log_multiple(tmp_path):
    """Multiple audit records can be written."""
    from memory.audit import init_audit_db, write_audit_log
    db_path = str(tmp_path / "test_legal.db")
    init_audit_db(db_path)

    for i in range(3):
        write_audit_log(
            db_path=db_path,
            session_id=f"sess-{i}",
            user_id="attorney-1",
            skill_name="compliance_check",
            task_type="compliance",
            request_summary=f"Check {i}",
            risk_level="low",
            review_status="not_required",
            review_notes="",
            duration_ms=500,
        )

    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    assert count == 3
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
source .venv/bin/activate && python -m pytest tests/test_audit.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create memory package and audit module**

Create `memory/__init__.py` (empty) and:

```python
# memory/audit.py
"""SQLite audit log — records every skill invocation."""

import sqlite3
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    skill_name TEXT NOT NULL,
    task_type TEXT NOT NULL,
    request_summary TEXT NOT NULL,
    risk_level TEXT NOT NULL DEFAULT 'low',
    review_status TEXT NOT NULL DEFAULT 'not_required',
    review_notes TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0
)
"""


def init_audit_db(db_path: str) -> None:
    """Create the audit_log table if it doesn't exist."""
    conn = sqlite3.connect(db_path)
    conn.execute(_CREATE_TABLE)
    conn.commit()
    conn.close()
    logger.info("Audit DB initialized at %s", db_path)


def write_audit_log(
    db_path: str,
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
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO audit_log
           (timestamp, session_id, user_id, skill_name, task_type,
            request_summary, risk_level, review_status, review_notes, duration_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            datetime.now(timezone.utc).isoformat(),
            session_id,
            user_id,
            skill_name,
            task_type,
            request_summary,
            risk_level,
            review_status,
            review_notes,
            duration_ms,
        ),
    )
    conn.commit()
    conn.close()
    logger.info("Audit log: %s/%s for user %s", skill_name, task_type, user_id)
```

- [ ] **Step 4: Run tests**

```bash
source .venv/bin/activate && python -m pytest tests/test_audit.py -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add memory/__init__.py memory/audit.py tests/test_audit.py
git commit -m "feat: add SQLite audit log module"
```

---

### Task 2: Implement intake node

Resolves `client_id` from `user_id` and sets `filters`. For now, a simple mapping — real auth comes later.

**Files:**
- Modify: `graph/nodes/intake.py`
- Create: `tests/test_nodes.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_nodes.py
"""Unit tests for real node implementations."""


def _make_state(**overrides):
    base = {
        "request": "test request",
        "user_id": "attorney-1",
        "uploaded_docs": [],
        "task_type": "",
        "skill_plan": [],
        "retrieval_query": "",
        "retrieved_chunks": [],
        "filters": {},
        "messages": [],
        "llm_response": "",
        "risk_level": "",
        "risk_flags": [],
        "awaiting_review": False,
        "attorney_notes": "",
        "report": {},
        "session_id": "test-sess",
        "checkpoint_ref": "",
        "trace_id": "",
    }
    base.update(overrides)
    return base


def test_intake_sets_client_id_filter():
    """Intake resolves client_id and sets filters."""
    from graph.nodes.intake import intake

    state = _make_state(user_id="attorney-1", request="Review a contract")
    result = intake(state)

    assert "client_id" in result["filters"]
    assert result["filters"]["client_id"] != ""


def test_intake_sets_retrieval_query_from_request():
    """Intake sets retrieval_query to the request text."""
    from graph.nodes.intake import intake

    state = _make_state(request="What are indemnification standards?")
    result = intake(state)

    assert result["retrieval_query"] == "What are indemnification standards?"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
source .venv/bin/activate && python -m pytest tests/test_nodes.py::test_intake_sets_client_id_filter -v
```

Expected: FAIL — intake stub doesn't set filters

- [ ] **Step 3: Implement intake node**

```python
# graph/nodes/intake.py
"""Intake node — validates and enriches incoming request."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)

# Simple user → client mapping for now. Real auth replaces this.
_USER_CLIENT_MAP: dict[str, str] = {}
_DEFAULT_CLIENT_ID = "internal"


def intake(state: LegalAgentState) -> LegalAgentState:
    """Resolve client_id from user_id, set filters and retrieval_query."""
    user_id = state["user_id"]
    client_id = _USER_CLIENT_MAP.get(user_id, _DEFAULT_CLIENT_ID)

    state["filters"] = {
        "client_id": client_id,
        **{k: v for k, v in state.get("filters", {}).items() if k != "client_id"},
    }

    if not state.get("retrieval_query"):
        state["retrieval_query"] = state["request"]

    logger.info(
        "[intake] user=%s, client_id=%s, request=%s",
        user_id, client_id, state["request"][:80],
    )
    return state
```

- [ ] **Step 4: Run tests**

```bash
source .venv/bin/activate && python -m pytest tests/test_nodes.py -v
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add graph/nodes/intake.py tests/test_nodes.py
git commit -m "feat: implement intake node — client_id resolution and filters"
```

---

### Task 3: Implement intent_router node

Calls Ollama to classify the request into a `task_type`. Falls back to "research" if LLM is unavailable.

**Files:**
- Modify: `graph/nodes/intent_router.py`
- Modify: `tests/test_nodes.py`

- [ ] **Step 1: Write the test**

Add to `tests/test_nodes.py`:

```python
from unittest.mock import patch, MagicMock


def test_intent_router_classifies_via_llm(monkeypatch):
    """intent_router calls LLM and sets task_type from response."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")

    from config import get_settings
    get_settings.cache_clear()

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "message": {"content": '{"task_type": "contract_review"}'}
    }

    with patch("graph.nodes.intent_router.httpx.post", return_value=fake_response):
        from graph.nodes.intent_router import intent_router
        state = _make_state(request="Review clauses in this NDA")
        result = intent_router(state)

    assert result["task_type"] == "contract_review"
    assert result["skill_plan"] == ["contract_review"]


def test_intent_router_falls_back_on_error(monkeypatch):
    """intent_router defaults to 'research' when LLM fails."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")

    from config import get_settings
    get_settings.cache_clear()

    import httpx
    with patch("graph.nodes.intent_router.httpx.post", side_effect=httpx.ConnectError("down")):
        from graph.nodes.intent_router import intent_router
        state = _make_state(request="something")
        result = intent_router(state)

    assert result["task_type"] == "research"


def test_intent_router_preserves_existing_task_type():
    """If task_type already set, intent_router keeps it."""
    from graph.nodes.intent_router import intent_router
    state = _make_state(
        request="Generate a contract",
        task_type="contract_generation",
        skill_plan=["contract_generation"],
    )
    result = intent_router(state)
    assert result["task_type"] == "contract_generation"
```

- [ ] **Step 2: Implement intent_router node**

```python
# graph/nodes/intent_router.py
"""Intent router — classifies task_type from request via LLM."""

import json
import logging

import httpx

from config import get_settings
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)

VALID_TASK_TYPES = {
    "contract_generation", "contract_review", "compliance",
    "research", "drafting",
}

_CLASSIFICATION_PROMPT = """You are a legal task classifier. Given a user request, classify it into exactly one task type.

Valid task types:
- contract_generation: Generate a new contract or agreement
- contract_review: Review, analyze, or extract clauses from an existing contract
- compliance: Check documents against policies, regulations, or jurisdiction rules
- research: Answer legal questions, find precedents, or research legal topics
- drafting: Fill templates to produce NDAs, memos, briefs, or other documents

Respond with JSON only: {"task_type": "<type>"}

User request: {request}"""


def intent_router(state: LegalAgentState) -> LegalAgentState:
    """Classify task_type from request. Preserves existing task_type if valid."""
    # If already set and valid, keep it
    if state.get("task_type") and state["task_type"] in VALID_TASK_TYPES:
        if not state.get("skill_plan"):
            state["skill_plan"] = [state["task_type"]]
        logger.info("[intent_router] keeping task_type=%s", state["task_type"])
        return state

    # Call LLM to classify
    settings = get_settings()
    task_type = "research"  # fallback

    try:
        response = httpx.post(
            f"{settings.ollama_base_url}/api/chat",
            json={
                "model": settings.llm_model,
                "messages": [
                    {"role": "user", "content": _CLASSIFICATION_PROMPT.format(request=state["request"])}
                ],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.0},
            },
            timeout=30.0,
        )
        response.raise_for_status()
        content = response.json()["message"]["content"]
        parsed = json.loads(content)
        classified = parsed.get("task_type", "research")
        if classified in VALID_TASK_TYPES:
            task_type = classified
        logger.info("[intent_router] LLM classified: %s", task_type)
    except Exception as e:
        logger.warning("[intent_router] LLM classification failed: %s — defaulting to research", e)

    state["task_type"] = task_type
    state["skill_plan"] = [task_type]
    return state
```

- [ ] **Step 3: Run tests**

```bash
source .venv/bin/activate && python -m pytest tests/test_nodes.py -v
```

Expected: 5 passed

- [ ] **Step 4: Commit**

```bash
git add graph/nodes/intent_router.py tests/test_nodes.py
git commit -m "feat: implement intent_router — LLM task classification"
```

---

### Task 4: Implement rag_retriever node

Calls `hybrid_search` with query and filters from state.

**Files:**
- Modify: `graph/nodes/rag_retriever.py`
- Modify: `tests/test_nodes.py`

- [ ] **Step 1: Write the test**

Add to `tests/test_nodes.py`:

```python
def test_rag_retriever_calls_hybrid_search():
    """rag_retriever populates retrieved_chunks from hybrid_search."""
    fake_results = [
        {"chunk_id": "c1", "doc_id": "d1", "text": "relevant text",
         "doc_title": "Contract A", "rrf_score": 0.8},
    ]

    with patch("graph.nodes.rag_retriever.hybrid_search", return_value=fake_results):
        from graph.nodes.rag_retriever import rag_retriever
        state = _make_state(
            retrieval_query="indemnification clause",
            filters={"client_id": "test-client"},
        )
        result = rag_retriever(state)

    assert len(result["retrieved_chunks"]) == 1
    assert result["retrieved_chunks"][0]["chunk_id"] == "c1"


def test_rag_retriever_skips_when_no_query():
    """rag_retriever passes through when retrieval_query is empty."""
    from graph.nodes.rag_retriever import rag_retriever
    state = _make_state(retrieval_query="")
    result = rag_retriever(state)
    assert result["retrieved_chunks"] == []
```

- [ ] **Step 2: Implement rag_retriever node**

```python
# graph/nodes/rag_retriever.py
"""RAG retriever — runs hybrid search for the current request."""

import logging
from graph.state import LegalAgentState
from rag.hybrid_search import hybrid_search

logger = logging.getLogger(__name__)


def rag_retriever(state: LegalAgentState) -> LegalAgentState:
    """Search for relevant chunks using hybrid search."""
    query = state.get("retrieval_query", "")
    if not query:
        logger.info("[rag_retriever] no query — skipping retrieval")
        return state

    filters = state.get("filters")

    results = hybrid_search(
        query=query,
        top_k=10,
        collection="legal_docs",
        filters=filters,
    )

    state["retrieved_chunks"] = results
    logger.info("[rag_retriever] retrieved %d chunks for query: %s", len(results), query[:60])
    return state
```

- [ ] **Step 3: Run tests**

```bash
source .venv/bin/activate && python -m pytest tests/test_nodes.py -v
```

Expected: 7 passed

- [ ] **Step 4: Commit**

```bash
git add graph/nodes/rag_retriever.py tests/test_nodes.py
git commit -m "feat: implement rag_retriever — hybrid search integration"
```

---

### Task 5: Implement llm_caller node

Calls Ollama `/api/chat` with retrieved chunks as context plus the request.

**Files:**
- Modify: `graph/nodes/llm_caller.py`
- Modify: `tests/test_nodes.py`

- [ ] **Step 1: Write the test**

Add to `tests/test_nodes.py`:

```python
def test_llm_caller_sends_context_and_request(monkeypatch):
    """llm_caller builds prompt with chunks and calls Ollama."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")

    from config import get_settings
    get_settings.cache_clear()

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "message": {"content": "The indemnification clause typically protects..."}
    }

    with patch("graph.nodes.llm_caller.httpx.post", return_value=fake_response) as mock_post:
        from graph.nodes.llm_caller import llm_caller
        state = _make_state(
            request="What are indemnification standards?",
            retrieved_chunks=[
                {"chunk_id": "c1", "doc_id": "d1", "doc_title": "Contract A",
                 "text": "The buyer shall be indemnified against all claims."},
            ],
        )
        result = llm_caller(state)

    assert result["llm_response"] != ""
    # Verify Ollama was called with temperature=0
    call_body = mock_post.call_args[1]["json"]
    assert call_body["options"]["temperature"] == 0.0


def test_llm_caller_handles_no_chunks(monkeypatch):
    """llm_caller works even with no retrieved chunks."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")

    from config import get_settings
    get_settings.cache_clear()

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "message": {"content": "No context available."}
    }

    with patch("graph.nodes.llm_caller.httpx.post", return_value=fake_response):
        from graph.nodes.llm_caller import llm_caller
        state = _make_state(request="General question", retrieved_chunks=[])
        result = llm_caller(state)

    assert result["llm_response"] != ""
```

- [ ] **Step 2: Implement llm_caller node**

```python
# graph/nodes/llm_caller.py
"""LLM caller — sends prompt + retrieved context to Ollama."""

import logging

import httpx

from config import get_settings
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a legal assistant for an internal legal team. Answer the user's request using ONLY the provided context. For every claim, cite the source document (doc_title and doc_id). If the context is insufficient, say so explicitly — do not fabricate information."""


def _build_context(chunks: list[dict]) -> str:
    """Format retrieved chunks as numbered context."""
    if not chunks:
        return "No documents retrieved."
    parts = []
    for i, c in enumerate(chunks, 1):
        parts.append(
            f"[{i}] {c.get('doc_title', 'Unknown')} (doc_id: {c.get('doc_id', '?')})\n"
            f"{c.get('text', '')}"
        )
    return "\n\n---\n\n".join(parts)


def llm_caller(state: LegalAgentState) -> LegalAgentState:
    """Call Ollama with context + request. temperature=0.0 always."""
    settings = get_settings()
    chunks = state.get("retrieved_chunks", [])
    context = _build_context(chunks)

    user_message = f"Context:\n{context}\n\nRequest: {state['request']}"

    try:
        response = httpx.post(
            f"{settings.ollama_base_url}/api/chat",
            json={
                "model": settings.llm_model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                "stream": False,
                "options": {"temperature": 0.0},
            },
            timeout=120.0,
        )
        response.raise_for_status()
        content = response.json()["message"]["content"]
        state["llm_response"] = content
        logger.info("[llm_caller] got %d char response", len(content))
    except Exception as e:
        logger.error("[llm_caller] LLM call failed: %s", e)
        state["llm_response"] = f"Error: LLM call failed — {e}"

    return state
```

- [ ] **Step 3: Run tests**

```bash
source .venv/bin/activate && python -m pytest tests/test_nodes.py -v
```

Expected: 9 passed

- [ ] **Step 4: Commit**

```bash
git add graph/nodes/llm_caller.py tests/test_nodes.py
git commit -m "feat: implement llm_caller — Ollama with context and temperature=0"
```

---

### Task 6: Implement risk_assessor node

Checks if the LLM response cites sources. Sets risk_level based on task_type and citation presence.

**Files:**
- Modify: `graph/nodes/risk_assessor.py`
- Modify: `tests/test_nodes.py`

- [ ] **Step 1: Write the test**

Add to `tests/test_nodes.py`:

```python
def test_risk_assessor_flags_no_citations():
    """risk_assessor flags high risk when response lacks citations."""
    from graph.nodes.risk_assessor import risk_assessor
    state = _make_state(
        task_type="research",
        llm_response="The law says you should do this.",
        retrieved_chunks=[
            {"chunk_id": "c1", "doc_id": "d1", "doc_title": "Contract A", "text": "some text"},
        ],
    )
    result = risk_assessor(state)
    assert result["risk_level"] == "high"
    assert any("citation" in f.get("reason", "").lower() for f in result["risk_flags"])


def test_risk_assessor_low_risk_with_citations():
    """risk_assessor sets low risk when response cites doc_id."""
    from graph.nodes.risk_assessor import risk_assessor
    state = _make_state(
        task_type="research",
        llm_response="According to Contract A (doc_id: d1), indemnification applies.",
        retrieved_chunks=[
            {"chunk_id": "c1", "doc_id": "d1", "doc_title": "Contract A", "text": "indemnification"},
        ],
    )
    result = risk_assessor(state)
    assert result["risk_level"] == "low"


def test_risk_assessor_no_chunks_means_high_risk():
    """risk_assessor flags high risk when no chunks were retrieved."""
    from graph.nodes.risk_assessor import risk_assessor
    state = _make_state(
        task_type="research",
        llm_response="I think the answer is...",
        retrieved_chunks=[],
    )
    result = risk_assessor(state)
    assert result["risk_level"] == "high"
```

- [ ] **Step 2: Implement risk_assessor node**

```python
# graph/nodes/risk_assessor.py
"""Risk assessor — checks citations and evaluates risk level."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def _check_citations(llm_response: str, chunks: list[dict]) -> list[dict]:
    """Check if the response references retrieved documents."""
    flags = []
    if not chunks:
        flags.append({"reason": "No citation possible — no chunks retrieved", "severity": "high"})
        return flags

    # Check if any doc_id or doc_title from chunks appears in response
    cited = False
    for chunk in chunks:
        doc_id = chunk.get("doc_id", "")
        doc_title = chunk.get("doc_title", "")
        if doc_id and doc_id in llm_response:
            cited = True
            break
        if doc_title and doc_title.lower() in llm_response.lower():
            cited = True
            break

    if not cited:
        flags.append({"reason": "No citation found — response does not reference any retrieved document", "severity": "high"})

    return flags


def risk_assessor(state: LegalAgentState) -> LegalAgentState:
    """Evaluate risk based on citations, task type, and content."""
    llm_response = state.get("llm_response", "")
    chunks = state.get("retrieved_chunks", [])
    task_type = state.get("task_type", "")

    risk_flags = _check_citations(llm_response, chunks)
    state["risk_flags"] = risk_flags

    # Determine risk level
    if any(f["severity"] == "high" for f in risk_flags):
        state["risk_level"] = "high"
    elif risk_flags:
        state["risk_level"] = "medium"
    else:
        state["risk_level"] = "low"

    logger.info("[risk_assessor] risk_level=%s, flags=%d", state["risk_level"], len(risk_flags))
    return state


def route_risk(state: LegalAgentState) -> str:
    """Conditional edge: routes to human_review or output_formatter."""
    if state["task_type"] in ("contract_generation", "drafting"):
        return "human_review"
    if state["risk_level"] in ("high", "medium"):
        return "human_review"
    return "output_formatter"
```

- [ ] **Step 3: Run tests**

```bash
source .venv/bin/activate && python -m pytest tests/test_nodes.py -v
```

Expected: 12 passed

- [ ] **Step 4: Commit**

```bash
git add graph/nodes/risk_assessor.py tests/test_nodes.py
git commit -m "feat: implement risk_assessor — citation check and risk evaluation"
```

---

### Task 7: Implement output_formatter and memory_writer nodes

Output formatter builds a structured report. Memory writer writes the audit log.

**Files:**
- Modify: `graph/nodes/output_formatter.py`
- Modify: `graph/nodes/memory_writer.py`
- Modify: `tests/test_nodes.py`

- [ ] **Step 1: Write the tests**

Add to `tests/test_nodes.py`:

```python
def test_output_formatter_builds_report():
    """output_formatter creates a report dict."""
    from graph.nodes.output_formatter import output_formatter
    state = _make_state(
        task_type="research",
        llm_response="The answer is X.",
        risk_level="low",
        risk_flags=[],
    )
    result = output_formatter(state)
    assert "response" in result["report"]
    assert "task_type" in result["report"]
    assert result["report"]["response"] == "The answer is X."


def test_memory_writer_writes_audit(tmp_path, monkeypatch):
    """memory_writer writes to SQLite audit log."""
    db_path = str(tmp_path / "test_legal.db")
    monkeypatch.setenv("SQLITE_PATH", db_path)
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")

    from config import get_settings
    get_settings.cache_clear()

    from memory.audit import init_audit_db
    init_audit_db(db_path)

    from graph.nodes.memory_writer import memory_writer
    state = _make_state(
        task_type="research",
        risk_level="low",
        session_id="sess-test",
        user_id="attorney-1",
    )
    result = memory_writer(state)

    import sqlite3
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT * FROM audit_log").fetchall()
    conn.close()
    assert len(rows) == 1
```

- [ ] **Step 2: Implement output_formatter**

```python
# graph/nodes/output_formatter.py
"""Output formatter — structures the final response."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def output_formatter(state: LegalAgentState) -> LegalAgentState:
    """Build structured report from LLM response and metadata."""
    state["report"] = {
        "task_type": state.get("task_type", ""),
        "response": state.get("llm_response", ""),
        "risk_level": state.get("risk_level", "low"),
        "risk_flags": state.get("risk_flags", []),
        "awaiting_review": state.get("awaiting_review", False),
        "sources": [
            {"doc_id": c.get("doc_id"), "doc_title": c.get("doc_title")}
            for c in state.get("retrieved_chunks", [])
        ],
    }
    logger.info("[output_formatter] report built, task_type=%s", state["task_type"])
    return state
```

- [ ] **Step 3: Implement memory_writer**

```python
# graph/nodes/memory_writer.py
"""Memory writer — persists audit log to SQLite."""

import logging
import time

from config import get_settings
from graph.state import LegalAgentState
from memory.audit import init_audit_db, write_audit_log

logger = logging.getLogger(__name__)

_db_initialized = False


def memory_writer(state: LegalAgentState) -> LegalAgentState:
    """Write skill invocation to SQLite audit log."""
    global _db_initialized
    settings = get_settings()

    if not _db_initialized:
        init_audit_db(settings.sqlite_path)
        _db_initialized = True

    review_status = "pending" if state.get("awaiting_review") else "not_required"

    write_audit_log(
        db_path=settings.sqlite_path,
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

    logger.info("[memory_writer] audit log written for session=%s", state.get("session_id"))
    return state
```

- [ ] **Step 4: Run tests**

```bash
source .venv/bin/activate && python -m pytest tests/test_nodes.py -v
```

Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add graph/nodes/output_formatter.py graph/nodes/memory_writer.py tests/test_nodes.py
git commit -m "feat: implement output_formatter and memory_writer with audit log"
```

---

### Task 8: Implement human_review node with LangGraph interrupt

Uses LangGraph `interrupt()` for blocking review. The graph pauses and resumes when the attorney approves.

**Files:**
- Modify: `graph/nodes/human_review.py`
- Modify: `graph/graph.py` — add optional checkpointer param
- Modify: `tests/test_nodes.py`

- [ ] **Step 1: Write the test**

Add to `tests/test_nodes.py`:

```python
def test_human_review_sets_awaiting_review():
    """human_review marks state as awaiting review."""
    from graph.nodes.human_review import human_review
    state = _make_state(task_type="contract_generation")

    # Without a checkpointer, interrupt() won't work in tests.
    # Test the state mutation directly.
    result = human_review(state)
    assert result["awaiting_review"] is True
```

- [ ] **Step 2: Implement human_review node**

```python
# graph/nodes/human_review.py
"""Human review — pauses graph for attorney approval using LangGraph interrupt."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def human_review(state: LegalAgentState) -> LegalAgentState:
    """Pause for human review. Uses interrupt() when checkpointer is available."""
    state["awaiting_review"] = True
    logger.info(
        "[human_review] review required: task_type=%s, risk_level=%s",
        state.get("task_type"), state.get("risk_level"),
    )

    # LangGraph interrupt() only works with a checkpointer.
    # When running without one (tests, scripts), just mark and continue.
    try:
        from langgraph.types import interrupt
        review = interrupt({
            "type": "human_review",
            "task_type": state.get("task_type"),
            "risk_level": state.get("risk_level"),
            "llm_response": state.get("llm_response", "")[:500],
            "risk_flags": state.get("risk_flags", []),
        })
        # Attorney's response comes back as the interrupt return value
        if isinstance(review, dict):
            state["attorney_notes"] = review.get("notes", "")
            if review.get("approved", True):
                state["awaiting_review"] = False
                logger.info("[human_review] approved by attorney")
            else:
                state["llm_response"] = review.get("revised_response", state["llm_response"])
                state["awaiting_review"] = False
                logger.info("[human_review] revised by attorney")
    except Exception as e:
        logger.warning("[human_review] interrupt unavailable (%s) — marking and continuing", type(e).__name__)

    return state
```

- [ ] **Step 3: Update graph.py to accept checkpointer**

Modify `build_graph()` in `graph/graph.py` to accept an optional checkpointer:

Change the function signature and compile line:

```python
def build_graph(checkpointer=None) -> StateGraph:
    """Build and compile the supervisor graph. Returns compiled graph."""
    # ... (all existing code stays the same until the last line)
    return graph.compile(checkpointer=checkpointer)
```

- [ ] **Step 4: Run tests**

```bash
source .venv/bin/activate && python -m pytest tests/test_nodes.py tests/test_graph.py -v
```

Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add graph/nodes/human_review.py graph/graph.py tests/test_nodes.py
git commit -m "feat: implement human_review with LangGraph interrupt"
```

---

### Task 9: Add Langfuse observability

Create the observability module with `init_observability()`. This will be called in FastAPI lifespan (Phase 5) but we set it up now.

**Files:**
- Create: `observability/__init__.py`
- Create: `observability/langfuse.py`

- [ ] **Step 1: Create observability module**

```python
# observability/__init__.py
```

```python
# observability/langfuse.py
"""Langfuse integration for agent tracing."""

import logging

from config import get_settings

logger = logging.getLogger(__name__)

_initialized = False


def init_observability() -> None:
    """Initialize Langfuse client. Call once at startup before graph compiles."""
    global _initialized
    if _initialized:
        return

    settings = get_settings()

    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        logger.warning("Langfuse keys not set — tracing disabled")
        return

    try:
        from langfuse import Langfuse
        client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        # Verify connection
        client.auth_check()
        _initialized = True
        logger.info("Langfuse initialized: %s", settings.langfuse_host)
    except Exception as e:
        logger.warning("Langfuse init failed: %s — tracing disabled", e)
```

- [ ] **Step 2: Commit**

```bash
git add observability/__init__.py observability/langfuse.py
git commit -m "feat: add Langfuse observability module"
```

---

### Task 10: End-to-end integration test

Test the full graph with real nodes (mocked external calls) to verify audit log, risk routing, and report building.

**Files:**
- Modify: `tests/test_graph.py`

- [ ] **Step 1: Add integration test**

Add to `tests/test_graph.py`:

```python
def test_graph_full_flow_with_audit(tmp_path, monkeypatch):
    """Full graph flow: intake → ... → memory_writer writes audit log."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    db_path = str(tmp_path / "test_legal.db")
    monkeypatch.setenv("SQLITE_PATH", db_path)
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.setenv("BM25_ENABLED", "false")

    from config import get_settings
    get_settings.cache_clear()

    from memory.audit import init_audit_db
    init_audit_db(db_path)

    # Reset memory_writer init flag
    import graph.nodes.memory_writer as mw
    mw._db_initialized = True  # we already init'd above

    from unittest.mock import patch, MagicMock

    # Mock Ollama calls (intent_router + llm_caller)
    def fake_ollama_post(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        if "/api/chat" in url:
            body = kwargs.get("json", {})
            messages = body.get("messages", [])
            user_msg = messages[-1]["content"] if messages else ""
            if "classify" in user_msg.lower() or "task type" in user_msg.lower():
                resp.json.return_value = {"message": {"content": '{"task_type": "research"}'}}
            else:
                resp.json.return_value = {"message": {"content": "Based on Contract A (doc_id: d1), the answer is X."}}
        return resp

    # Mock hybrid_search to return a fake chunk
    fake_chunks = [
        {"chunk_id": "c1", "doc_id": "d1", "doc_title": "Contract A",
         "text": "relevant legal text", "rrf_score": 0.8,
         "doc_type": "contract", "client_id": "internal", "jurisdiction": "US"},
    ]

    with patch("graph.nodes.intent_router.httpx.post", side_effect=fake_ollama_post), \
         patch("graph.nodes.llm_caller.httpx.post", side_effect=fake_ollama_post), \
         patch("graph.nodes.rag_retriever.hybrid_search", return_value=fake_chunks):

        from graph.graph import build_graph
        graph = build_graph()

        result = graph.invoke(_make_state(
            request="What are indemnification standards?",
            session_id="integration-test",
        ))

    # Verify end state
    assert result["task_type"] == "research"
    assert result["llm_response"] != ""
    assert "response" in result["report"]

    # Verify audit log was written
    import sqlite3
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT * FROM audit_log").fetchall()
    conn.close()
    assert len(rows) >= 1
```

- [ ] **Step 2: Run full test suite**

```bash
source .venv/bin/activate && python -m pytest tests/ -v
```

Expected: all passed

- [ ] **Step 3: Commit**

```bash
git add tests/test_graph.py
git commit -m "feat: add full graph integration test with audit verification"
```

---

## Phase 4 Exit Criteria

- [ ] `python -m pytest tests/ -v` — all tests pass
- [ ] Intake resolves `client_id` and sets filters
- [ ] Intent router classifies via LLM (falls back to "research")
- [ ] RAG retriever calls hybrid_search with filters
- [ ] LLM caller sends context + request to Ollama at temperature=0.0
- [ ] Risk assessor checks citations — no citation = high risk
- [ ] Output formatter builds structured report dict
- [ ] Memory writer writes to SQLite audit log — every invocation
- [ ] Human review uses `interrupt()` when checkpointer available
- [ ] Langfuse observability module ready (init called in Phase 5)
- [ ] Contract generation and drafting always route to human_review
