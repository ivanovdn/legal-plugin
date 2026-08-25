# Context Counter and Attorney-Triggered Compaction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the attorney what the context budget is actually spending, and let them condense earlier conversation into validated verbatim quotes so the contract stops being the thing that gets cut.

**Architecture:** A new append-only `conversation_summary` Postgres table stores extractive segments, each covering a fixed `from_id`→`to_id` range of `conversation_store` rows. A `POST /api/compact` endpoint asks the local LLM for quote lines, runs a deterministic zero-LLM validation gate over them (every quote must match the row it cites), and writes nothing if the gate rejects. Chat injection reads summaries oldest-first plus verbatim rows after the last summarised id. Every turn returns a `context_breakdown` in the report, which the Word pane renders as a one-line gauge above the tabs.

**Tech Stack:** Python 3.12 / FastAPI / psycopg3 / LangChain ChatOllama; React + TypeScript + Vite (Office.js task pane); pytest + hand-rolled `*.test.ts` assertions.

**Spec:** [docs/superpowers/specs/2026-08-25-context-compaction-design.md](../specs/2026-08-25-context-compaction-design.md)

## Global Constraints

- **All imports at top of file.** No lazy imports inside functions. (CLAUDE.md hard rule 1.)
- **Don't add backwards-compat shims.** Change call sites instead. (CLAUDE.md hard rule 5.)
- **`tests/conftest.py` truncates a HARDCODED table list.** `conversation_summary` must be added there or it leaks state between tests and greens a lie.
- **Compaction must never cut the document, playbook, MSA, or prior review.** Only `conversation_store` rows are ever summarised.
- **Raw rows are never deleted.** A summary is a view, never the record.
- **Segments are append-only and never re-summarised.** Each covers a fixed `from_id`→`to_id`.
- **Failure is LOUD.** The attorney clicked and was told it happened, so a failed compaction writes no row and surfaces in the response — the same split as `save_review` (loud) vs `append_turn` (quiet).
- **A compaction failure must never break a subsequent chat turn.** If `conversation_summary` cannot be read, `_load_prior_conversation` degrades to raw rows and flags `memory_degraded`.
- **Summaries are built from `_sanitize_history`-cleaned text**, so fenced ` ```json ` / ` ```preference ` blocks can never be quoted into a summary and become few-shot examples (the `2ae99ecc` failure).
- **Config defaults, verbatim from the spec:** `compaction_enabled=True`, `compaction_keep_recent_messages=6`, `compaction_warn_pct=90`, `compaction_max_quotes=24`, `compaction_max_injected_segments=3`.
- **Config is `@lru_cache`'d in `get_settings`** ⇒ changing any `compaction_*` value needs a `bash scripts/start.sh` restart.
- **Identity comes from the `resolve_user_id` auth seam, never a request body.**
- **`skills/legal_research/` patching hazard:** a name imported into another module in this package exists on BOTH modules bound to the same object, and patching the wrong one silently no-ops. Patch the module whose globals the call path resolves through. Verify every new mock by mutation (break the code, confirm the test fails) — never by a green suite.
- **Import direction is one-way:** `compaction.py` may import from `legal_research.py`; `context.py` must NEVER import `compaction.py`, or the package cycles.
- **Word add-in:** `npx tsc --noEmit` is not sufficient verification. The pane needs a sideload smoke test in Word for Mac.
- **Restart `bash scripts/start.sh`** after changing anything under `skills/`, `graph/`, `api/`, or `config.py` — uvicorn does not auto-reload.

## Measured facts this plan depends on

Do not re-derive these; they were measured against live hardware on 2026-08-21.

| fact | value |
|---|---|
| chars per token, real legal text | **4.89** (`config.est_chars_per_token`) — chars/4 overstates by 22% |
| chat budget | 150,000 chars (`config.chat_context_max_chars`) |
| Ollama window | 131,072 (`config.ollama_num_ctx`) |
| grounded MSA case | doc 84,859 + playbook 38,587 + sys/review 8,000 = 131,446; history's whole allowance is **18,554 chars = 3,794 tokens** |
| prefill | 1,397 tok/s; generation 51.4 tok/s |

## File Structure

**Created (backend)**
- `memory/conversation_summary.py` — the segment store: `append_segment`, `load_segments`, `latest_to_id`.
- `skills/legal_research/compaction.py` — selection, generation, the validation gate, rendering. The only module that calls the LLM for compaction.
- `api/routes/compact.py` — `POST /api/compact`.

**Created (tests)**
- `tests/test_conversation_summary_store.py`
- `tests/test_compaction_gate.py` — the validation gate. Highest-value file in this plan.
- `tests/test_compaction_flow.py` — selection, generation, retry, loudness, round trip.
- `tests/test_context_breakdown.py`
- `tests/test_compact_api.py`

**Created (frontend)**
- `clients/word/src/contextGauge.ts` — pure types + formatting + threshold logic.
- `clients/word/src/contextGauge.test.ts` — 14 assertions.
- `clients/word/src/components/ContextMeter.tsx` — the header gauge and Condense action.

**Modified**
- `config.py` — 5 new fields.
- `.env.example` — document the 5 knobs.
- `memory/db.py` — `conversation_summary` table + index.
- `memory/conversation_store.py` — `after_id` floor on `load_recent`; new `load_rows_after`, `count_after`.
- `tests/conftest.py` — TRUNCATE list.
- `skills/legal_research/prompts.py` — `_COMPACTION_SYSTEM`.
- `skills/legal_research/context.py` — summary-aware `_load_prior_conversation`; `compressible_message_count`; `build_context_breakdown`.
- `skills/legal_research/legal_research.py` — set `state["context_breakdown"]`.
- `graph/state.py`, `graph/nodes/output_formatter.py`, `api/routes/query.py` — carry `context_breakdown` to the report.
- `api/models.py`, `api/main.py` — request model + router.
- `clients/word/src/api.ts` — `context_breakdown` type + `compactConversation()`.
- `clients/word/src/App.tsx` — lift breakdown state, live document watcher, render `<ContextMeter>`.
- `clients/word/src/components/ChatTab.tsx`, `FindingsTab.tsx` — report the breakdown up.
- `clients/word/src/styles.css` — `.context-meter` styles.
- `scripts/check.sh` — `EXPECTED_PASS_COUNT` 265 → 279.
- `docs/wiki.md`, `CLAUDE.md`.

## Deliberate deviations from the spec

Three, each with its reason. An implementer must not "fix" these back.

1. **Segment header counts messages, not turns.** The spec's example reads `(turns 1–14, condensed)`. A turn ordinal is not derivable from a row id without a per-row ordinal we do not store, and a wrong number in a header is exactly the class of small lie this feature exists to prevent. The header says `(18 earlier messages, condensed)` instead.
2. **`POST /api/compact` returns what was condensed, not a fresh breakdown.** The spec says "returns the new breakdown". The endpoint has no document text and no grounding decision, so any breakdown it synthesised would be a guess — and the counter's own honesty constraint is that its figures are *the last turn's real measured values*. The pane says the counter updates on the next message. This applies the spec's principle rather than its wording.
3. **The gate also checks the speaker label.** The spec names two checks (id in range, text matches the row). A quote labelled `[#419 attorney]` when row 419 is the assistant is precisely the risk row "a summary asserts something the attorney never said", and the check is free once the row is in hand.

---

### Task 1: Config, schema, and the segment store

**Files:**
- Modify: `config.py` (after the `conversation_max_messages` line, ~line 119)
- Modify: `.env.example` (append)
- Modify: `memory/db.py` (`_STATEMENTS`, and the `init_db` docstring)
- Modify: `tests/conftest.py:36-39` (the TRUNCATE list)
- Create: `memory/conversation_summary.py`
- Test: `tests/test_conversation_summary_store.py`

**Interfaces:**
- Consumes: `memory.db.get_pool` (existing).
- Produces:
  - `append_segment(document_id: str, attorney_id: str, from_id: int, to_id: int, content: str) -> int` — returns the new row id. Raises on failure.
  - `load_segments(document_id: str, attorney_id: str, max_segments: int) -> list[dict]` — the most recent `max_segments`, returned **oldest-first**, each `{"id": int, "from_id": int, "to_id": int, "content": str}`.
  - `latest_to_id(document_id: str, attorney_id: str) -> int` — `MAX(to_id)`, or `0` when no segment exists.
  - `config.Settings.compaction_enabled | compaction_keep_recent_messages | compaction_warn_pct | compaction_max_quotes | compaction_max_injected_segments`

- [ ] **Step 1: Write the failing test**

Create `tests/test_conversation_summary_store.py`:

```python
"""Append-only conversation summary segments — write, windowed read, boundary."""
from memory.conversation_summary import append_segment, latest_to_id, load_segments


def test_append_and_load_round_trip():
    seg_id = append_segment("doc-1", "atty-1", 1, 10, "[#3 attorney] \"use Suzy Quatro\"")
    assert seg_id > 0
    segs = load_segments("doc-1", "atty-1", 3)
    assert len(segs) == 1
    assert segs[0]["from_id"] == 1
    assert segs[0]["to_id"] == 10
    assert segs[0]["content"] == "[#3 attorney] \"use Suzy Quatro\""
    assert segs[0]["id"] == seg_id


def test_load_segments_is_oldest_first_and_windowed():
    append_segment("doc-1", "atty-1", 1, 10, "first")
    append_segment("doc-1", "atty-1", 11, 20, "second")
    append_segment("doc-1", "atty-1", 21, 30, "third")
    # The window keeps the MOST RECENT n, but hands them back chronologically —
    # a summary read out of order would misrepresent what happened when.
    assert [s["content"] for s in load_segments("doc-1", "atty-1", 2)] == ["second", "third"]
    assert [s["content"] for s in load_segments("doc-1", "atty-1", 9)] == [
        "first", "second", "third",
    ]


def test_latest_to_id_is_the_boundary_across_all_segments():
    assert latest_to_id("doc-1", "atty-1") == 0
    append_segment("doc-1", "atty-1", 1, 10, "first")
    append_segment("doc-1", "atty-1", 11, 26, "second")
    # Deliberately NOT max(injected window) — rows 1..26 are all condensed, so
    # the verbatim floor must be 26 even when only one segment is injected.
    assert latest_to_id("doc-1", "atty-1") == 26


def test_per_attorney_and_per_document_isolation():
    append_segment("doc-1", "atty-1", 1, 10, "mine")
    append_segment("doc-1", "atty-2", 1, 10, "theirs")
    append_segment("doc-2", "atty-1", 1, 10, "other doc")
    assert [s["content"] for s in load_segments("doc-1", "atty-1", 9)] == ["mine"]
    assert [s["content"] for s in load_segments("doc-1", "atty-2", 9)] == ["theirs"]
    assert [s["content"] for s in load_segments("doc-2", "atty-1", 9)] == ["other doc"]
    assert latest_to_id("doc-1", "atty-2") == 10


def test_empty_ids_and_nonpositive_window_return_empty():
    append_segment("doc-1", "atty-1", 1, 10, "seg")
    assert load_segments("", "atty-1", 3) == []
    assert load_segments("doc-1", "", 3) == []
    # Postgres, unlike SQLite, rejects a negative LIMIT — short-circuit before
    # the query (same hazard as conversation_store.load_recent).
    assert load_segments("doc-1", "atty-1", 0) == []
    assert load_segments("doc-1", "atty-1", -1) == []
    assert latest_to_id("", "atty-1") == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_conversation_summary_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'memory.conversation_summary'`

- [ ] **Step 3: Add the config fields**

In `config.py`, immediately after the `conversation_max_messages` line, insert:

```python

    # History compaction — condense earlier conversation into validated verbatim
    # quotes so the DOCUMENT stops being what gets cut. History is compressible;
    # the contract is not. See
    # docs/superpowers/specs/2026-08-25-context-compaction-design.md.
    compaction_enabled: bool = True
    compaction_keep_recent_messages: int = 6   # kept verbatim (three turns); a guess, tunable
    compaction_warn_pct: int = 90              # budget share at which the Condense action appears
    # 24 quotes holds one segment to roughly 400-500 tokens, and 3 injected
    # segments to ~1,500 tokens ~= 7,300 chars. Both bounds are load-bearing:
    # on the grounded-MSA case history's ENTIRE allowance is 18,554 chars =
    # 3,794 tokens, so unbounded segments would grow past the space history had
    # in the first place and start pushing the document toward truncation —
    # exactly the outcome compaction exists to prevent. Older segments stay in
    # the store, auditable, simply outside the injection window (the same
    # pattern conversation_max_messages already uses).
    compaction_max_quotes: int = 24
    compaction_max_injected_segments: int = 3
```

- [ ] **Step 4: Document the knobs in `.env.example`**

Append to `.env.example`:

```
# History compaction. Condensing earlier chat into validated verbatim quotes is
# how the DOCUMENT stops being the thing that gets truncated. Attorney-triggered
# (POST /api/compact); COMPACTION_WARN_PCT only decides when the pane offers it.
# COMPACTION_MAX_QUOTES and COMPACTION_MAX_INJECTED_SEGMENTS bound how much
# prompt the accumulated summaries can take back — raise them and the summaries
# start competing with the contract they exist to protect.
COMPACTION_ENABLED=true
COMPACTION_KEEP_RECENT_MESSAGES=6
COMPACTION_WARN_PCT=90
COMPACTION_MAX_QUOTES=24
COMPACTION_MAX_INJECTED_SEGMENTS=3
```

- [ ] **Step 5: Add the table to `memory/db.py`**

In `_STATEMENTS`, immediately after the `idx_conv` index line, insert:

```python
    """
    CREATE TABLE IF NOT EXISTS conversation_summary (
        id BIGSERIAL PRIMARY KEY,
        timestamp TEXT NOT NULL,
        document_id TEXT NOT NULL,
        attorney_id TEXT NOT NULL,
        from_id BIGINT NOT NULL,
        to_id BIGINT NOT NULL,
        content TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_conv_summary ON conversation_summary (document_id, attorney_id, id)",
```

Update the module docstring's first line and `init_db`'s docstring to say **six** store tables and name `conversation_summary`:

```python
def init_db() -> None:
    """Create all six store tables + indexes if absent (audit_log, review_store,
    conversation_store, conversation_summary, feedback, interaction_event).
    Idempotent."""
```

- [ ] **Step 6: Add the table to the conftest TRUNCATE list**

In `tests/conftest.py`, replace the TRUNCATE statement with:

```python
        conn.execute(
            "TRUNCATE audit_log, review_store, conversation_store, "
            "conversation_summary, feedback, interaction_event RESTART IDENTITY"
        )
```

- [ ] **Step 7: Write `memory/conversation_summary.py`**

```python
"""Postgres store for condensed conversation segments, keyed to (document_id, attorney_id).

Append-only and never re-summarised: each segment covers a FIXED from_id->to_id
range of conversation_store rows, so a fabrication cannot propagate into a later
generation and every block stays auditable against exactly the rows it came
from. A rolling summary would, by turn 100, have re-summarised itself several
times, leaving an error indistinguishable from fact.

Raw conversation_store rows are never deleted. A summary is a VIEW, never the
record — the same principle behind stripping fenced blocks on read rather than
on write. Any segment can be audited against its range, or discarded, without
touching history.

Writes raise at the module boundary (like save_review); the caller decides the
policy. Compaction's caller treats a failure as LOUD — the attorney clicked and
was told it happened.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from memory.db import get_pool

logger = logging.getLogger(__name__)


def append_segment(
    document_id: str, attorney_id: str, from_id: int, to_id: int, content: str
) -> int:
    """Append one condensed segment. Returns its row id. Raises on failure."""
    ts = datetime.now(timezone.utc).isoformat()
    with get_pool().connection() as conn:
        cur = conn.execute(
            """INSERT INTO conversation_summary
               (timestamp, document_id, attorney_id, from_id, to_id, content)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            (ts, document_id, attorney_id, from_id, to_id, content),
        )
        row_id = cur.fetchone()[0]
    logger.info(
        "Conversation segment saved: document_id=%s attorney_id=%s rows=%d-%d id=%s",
        document_id, attorney_id, from_id, to_id, row_id,
    )
    return int(row_id)


def load_segments(document_id: str, attorney_id: str, max_segments: int) -> list[dict]:
    """The most recent max_segments segments, returned oldest-first.

    Windowed, not complete: the store retains every segment; only this many
    reach the prompt (config.compaction_max_injected_segments). Callers that
    need the verbatim floor must use latest_to_id(), which spans ALL segments.
    """
    if not document_id or not attorney_id or max_segments <= 0:
        return []
    with get_pool().connection() as conn:
        cur = conn.execute(
            """SELECT id, from_id, to_id, content FROM conversation_summary
               WHERE document_id = %s AND attorney_id = %s
               ORDER BY id DESC LIMIT %s""",
            (document_id, attorney_id, max_segments),
        )
        rows = cur.fetchall()
    rows.reverse()  # DESC fetch -> chronological
    return [
        {"id": r[0], "from_id": r[1], "to_id": r[2], "content": r[3]} for r in rows
    ]


def latest_to_id(document_id: str, attorney_id: str) -> int:
    """Highest condensed row id across ALL segments, or 0 when none exist.

    This is the verbatim floor. It deliberately spans every segment, not just
    the injected window: rows at or below it are already condensed, and
    replaying them verbatim would undo the compaction that condensed them.
    """
    if not document_id or not attorney_id:
        return 0
    with get_pool().connection() as conn:
        cur = conn.execute(
            """SELECT COALESCE(MAX(to_id), 0) FROM conversation_summary
               WHERE document_id = %s AND attorney_id = %s""",
            (document_id, attorney_id),
        )
        return int(cur.fetchone()[0])
```

- [ ] **Step 8: Run the tests**

Run: `uv run pytest tests/test_conversation_summary_store.py tests/test_db.py tests/test_config.py -v`
Expected: PASS (all of `test_conversation_summary_store.py`, and no regression in the db/config suites)

- [ ] **Step 9: Commit**

```bash
git add config.py .env.example memory/db.py memory/conversation_summary.py tests/conftest.py tests/test_conversation_summary_store.py
git commit -m "feat: conversation_summary table, segment store, and compaction config"
```

---

### Task 2: Row-id reads on the conversation store

The compaction path needs row **ids** (the validation gate cites them) and a
floor (`id > after_id`) so a second compaction starts where the first stopped.

**Files:**
- Modify: `memory/conversation_store.py`
- Modify: `skills/legal_research/context.py:97-99` (the one `load_recent` call site)
- Test: `tests/test_conversation_store.py` (append)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `load_recent(document_id: str, attorney_id: str, max_messages: int, after_id: int = 0) -> list[dict]` — unchanged shape `{"role", "content"}`; now filtered to `id > after_id`.
  - `load_rows_after(document_id: str, attorney_id: str, after_id: int, limit: int) -> list[dict]` — **oldest-first**, each `{"id": int, "role": str, "content": str}`.
  - `count_after(document_id: str, attorney_id: str, after_id: int) -> int`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_conversation_store.py`:

```python
def test_load_recent_respects_the_after_id_floor():
    append_turn("doc-1", "atty-1", "q1", "a1")   # ids 1,2
    append_turn("doc-1", "atty-1", "q2", "a2")   # ids 3,4
    rows = load_rows_after("doc-1", "atty-1", 0, 100)
    boundary = rows[1]["id"]                      # everything through "a1"
    assert [m["content"] for m in load_recent("doc-1", "atty-1", 20, boundary)] == ["q2", "a2"]
    # No floor is still the whole conversation — the floor is an optional
    # filter, not a mode.
    assert [m["content"] for m in load_recent("doc-1", "atty-1", 20)] == ["q1", "a1", "q2", "a2"]


def test_load_rows_after_is_oldest_first_with_ids():
    append_turn("doc-1", "atty-1", "q1", "a1")
    append_turn("doc-1", "atty-1", "q2", "a2")
    rows = load_rows_after("doc-1", "atty-1", 0, 100)
    assert [r["content"] for r in rows] == ["q1", "a1", "q2", "a2"]
    assert [r["role"] for r in rows] == ["user", "assistant", "user", "assistant"]
    assert [r["id"] for r in rows] == sorted(r["id"] for r in rows)
    # Compaction reads from the OLD end (it condenses the oldest rows first),
    # which is why this truncates the tail and load_recent truncates the head.
    assert [r["content"] for r in load_rows_after("doc-1", "atty-1", 0, 3)] == ["q1", "a1", "q2"]


def test_load_rows_after_floor_and_isolation():
    append_turn("doc-1", "atty-1", "q1", "a1")
    append_turn("doc-1", "atty-2", "other", "reply")
    first = load_rows_after("doc-1", "atty-1", 0, 100)
    assert load_rows_after("doc-1", "atty-1", first[-1]["id"], 100) == []
    assert [r["content"] for r in load_rows_after("doc-1", "atty-2", 0, 100)] == ["other", "reply"]


def test_count_after_counts_only_rows_past_the_floor():
    append_turn("doc-1", "atty-1", "q1", "a1")
    append_turn("doc-1", "atty-1", "q2", "a2")
    assert count_after("doc-1", "atty-1", 0) == 4
    rows = load_rows_after("doc-1", "atty-1", 0, 100)
    assert count_after("doc-1", "atty-1", rows[1]["id"]) == 2
    assert count_after("doc-1", "atty-1", rows[-1]["id"]) == 0
    assert count_after("", "atty-1", 0) == 0


def test_load_rows_after_rejects_nonpositive_limit():
    append_turn("doc-1", "atty-1", "q", "a")
    # Postgres rejects a negative LIMIT — short-circuit before the query.
    assert load_rows_after("doc-1", "atty-1", 0, 0) == []
    assert load_rows_after("doc-1", "atty-1", 0, -5) == []
```

Extend the import at the top of that file to:

```python
from memory.conversation_store import append_turn, count_after, load_recent, load_rows_after
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_conversation_store.py -v`
Expected: FAIL with `ImportError: cannot import name 'count_after' from 'memory.conversation_store'`

- [ ] **Step 3: Implement the three readers**

In `memory/conversation_store.py`, replace `load_recent` and add the two new
functions:

```python
def load_recent(
    document_id: str, attorney_id: str, max_messages: int, after_id: int = 0
) -> list[dict]:
    """Up to max_messages most-recent messages for the pair, chronological (oldest first).

    after_id is the compaction floor: rows at or below it have already been
    condensed into a conversation_summary segment, so replaying them verbatim
    would undo the compaction. 0 (the default) means no floor, which is the
    honest state of a conversation that has never been condensed.
    """
    if not document_id or not attorney_id or max_messages <= 0:
        return []
    with get_pool().connection() as conn:
        cur = conn.execute(
            """SELECT role, content FROM conversation_store
               WHERE document_id = %s AND attorney_id = %s AND id > %s
               ORDER BY id DESC LIMIT %s""",
            (document_id, attorney_id, after_id, max_messages),
        )
        rows = cur.fetchall()
    rows.reverse()  # DESC fetch -> chronological
    return [{"role": r[0], "content": r[1]} for r in rows]


def load_rows_after(
    document_id: str, attorney_id: str, after_id: int, limit: int
) -> list[dict]:
    """Up to `limit` rows with id > after_id, OLDEST first, carrying their ids.

    The mirror image of load_recent: compaction condenses the oldest rows it
    has not condensed yet, so it truncates the tail where load_recent truncates
    the head. Ids travel because the validation gate cites them — a quote is
    only checkable against the row it names.
    """
    if not document_id or not attorney_id or limit <= 0:
        return []
    with get_pool().connection() as conn:
        cur = conn.execute(
            """SELECT id, role, content FROM conversation_store
               WHERE document_id = %s AND attorney_id = %s AND id > %s
               ORDER BY id ASC LIMIT %s""",
            (document_id, attorney_id, after_id, limit),
        )
        rows = cur.fetchall()
    return [{"id": r[0], "role": r[1], "content": r[2]} for r in rows]


def count_after(document_id: str, attorney_id: str, after_id: int) -> int:
    """How many messages sit past the compaction floor.

    Feeds the counter's "compressible history exists" condition — without it the
    Condense control would appear when there is nothing left to condense, and a
    control that offers a no-op teaches attorneys to ignore it.
    """
    if not document_id or not attorney_id:
        return 0
    with get_pool().connection() as conn:
        cur = conn.execute(
            """SELECT COUNT(*) FROM conversation_store
               WHERE document_id = %s AND attorney_id = %s AND id > %s""",
            (document_id, attorney_id, after_id),
        )
        return int(cur.fetchone()[0])
```

Update the module docstring's second paragraph to mention the floor:

```python
Append-per-turn: each chat turn inserts one 'user' row then one 'assistant' row.
Reads return the most-recent window in chronological order for the chat prompt,
optionally floored at the highest row already condensed into a
conversation_summary segment (see memory/conversation_summary.py).
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_conversation_store.py -v`
Expected: PASS (all, including the pre-existing tests, unchanged)

- [ ] **Step 5: Prove the floor is load-bearing (mutation check)**

Temporarily delete ` AND id > %s` from `load_recent`'s WHERE clause and drop the
matching parameter. Run `uv run pytest tests/test_conversation_store.py -v`.
Expected: `test_load_recent_respects_the_after_id_floor` FAILS. Restore the
clause and re-run to green. A floor that no test notices is a floor that will
silently stop working.

- [ ] **Step 6: Commit**

```bash
git add memory/conversation_store.py tests/test_conversation_store.py
git commit -m "feat: row-id reads and a compaction floor on the conversation store"
```

---

### Task 3: The validation gate

The highest-value file in this plan. Because summaries are quotes carrying row
ids, fabrication stops being merely *detectable* and becomes **rejectable** —
deterministically, with no second LLM in the loop.

**Files:**
- Create: `skills/legal_research/compaction.py` (parsing + gate only; generation lands in Task 4)
- Test: `tests/test_compaction_gate.py`

**Interfaces:**
- Consumes: nothing from Tasks 1-2.
- Produces:
  - `parse_quote_lines(body: str) -> tuple[list[dict], str]` — `(quotes, error)`. Each quote is `{"row_id": int, "speaker": "attorney" | "assistant", "text": str}`. `error` is `""` when every non-empty line parsed.
  - `validate_segment(body: str, rows: list[dict], from_id: int, to_id: int) -> str` — `""` when valid, else a one-line reason. `rows` are `{"id", "role", "content"}` as returned by `load_rows_after`.
  - `render_segment(quotes: list[dict], message_count: int) -> str` — the full injectable block.
  - Module constants `_MAX_ROWS_PER_COMPACTION = 200`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_compaction_gate.py`:

```python
"""The compaction validation gate — a deterministic, zero-LLM guard on an LLM's output.

Every quote carries the conversation_store row id it came from, so a fabricated
line is not merely detectable, it is REJECTABLE. Any failing quote invalidates
the whole segment: a summary is legal recall, and one invented line in it is
worse than no summary at all.
"""
from skills.legal_research.compaction import (
    parse_quote_lines,
    render_segment,
    validate_segment,
)

ROWS = [
    {"id": 412, "role": "user", "content": "Please use Suzy Quatro for all signature blocks."},
    {"id": 413, "role": "assistant", "content": "Done — the cap is Green under the playbook."},
    {"id": 414, "role": "user", "content": "We'll accept 12 months."},
]


def test_parses_both_speaker_forms():
    quotes, err = parse_quote_lines(
        '[#412 attorney] "use Suzy Quatro"\n'
        '[#413 assistant, said earlier] "the cap is Green"'
    )
    assert err == ""
    assert quotes == [
        {"row_id": 412, "speaker": "attorney", "text": "use Suzy Quatro"},
        {"row_id": 413, "speaker": "assistant", "text": "the cap is Green"},
    ]


def test_strips_a_code_fence_the_model_added():
    quotes, err = parse_quote_lines('```\n[#412 attorney] "use Suzy Quatro"\n```')
    assert err == ""
    assert [q["row_id"] for q in quotes] == [412]


def test_prose_alongside_quotes_is_rejected():
    # Unparsed prose would ride into the prompt unvalidated — the whole point of
    # the format is that EVERY line is checkable.
    _, err = parse_quote_lines(
        'Here is a summary of the conversation:\n[#412 attorney] "use Suzy Quatro"'
    )
    assert "not a quote line" in err


def test_empty_body_is_rejected():
    _, err = parse_quote_lines("   \n\n  ")
    assert "no quote lines" in err


def test_valid_segment_passes():
    body = (
        '[#412 attorney] "use Suzy Quatro for all signature blocks"\n'
        '[#413 assistant, said earlier] "the cap is Green under the playbook"\n'
        '[#414 attorney] "accept 12 months"'
    )
    assert validate_segment(body, ROWS, 412, 414) == ""


def test_quote_citing_a_row_outside_the_range_is_rejected():
    body = '[#999 attorney] "use Suzy Quatro"'
    err = validate_segment(body, ROWS, 412, 414)
    assert "outside the condensed range" in err


def test_quote_citing_a_row_not_in_the_transcript_is_rejected():
    # In range but absent from rows: the row was dropped by _sanitize_history
    # because it was pure machinery, so nothing can vouch for this quote.
    rows = [r for r in ROWS if r["id"] != 413]
    body = '[#413 assistant, said earlier] "the cap is Green"'
    err = validate_segment(body, rows, 412, 414)
    assert "not in the condensed transcript" in err


def test_misquoted_text_is_rejected():
    body = '[#414 attorney] "we will accept 24 months"'
    err = validate_segment(body, ROWS, 412, 414)
    assert "does not appear" in err


def test_one_bad_quote_invalidates_the_whole_segment():
    body = (
        '[#412 attorney] "use Suzy Quatro for all signature blocks"\n'
        '[#414 attorney] "we will accept 24 months"'
    )
    assert validate_segment(body, ROWS, 412, 414) != ""


def test_wrong_speaker_label_is_rejected():
    # Row 413 is the assistant. Attributing its words to the attorney is exactly
    # the "asserts something the attorney never said" risk, and free to catch.
    body = '[#413 attorney] "the cap is Green"'
    err = validate_segment(body, ROWS, 412, 414)
    assert "labelled attorney" in err


def test_normalisation_tolerates_curly_quotes_and_whitespace():
    rows = [{"id": 5, "role": "user", "content": "The parties’ cap is  12 months."}]
    body = '[#5 attorney] "The parties\' cap is 12 months."'
    assert validate_segment(body, rows, 5, 5) == ""


def test_empty_quote_text_is_rejected():
    body = '[#412 attorney] ""'
    assert "is empty" in validate_segment(body, ROWS, 412, 414)


def test_render_segment_adds_the_header_and_precedence_note():
    quotes = [{"row_id": 412, "speaker": "attorney", "text": "use Suzy Quatro"}]
    block = render_segment(quotes, message_count=18)
    assert block.startswith("--- EARLIER IN THIS CONVERSATION (18 earlier messages, condensed) ---")
    # The block must rank itself BELOW live grounding in its own words: recalled
    # discussion that reads as a current finding is the residual risk the gate
    # cannot catch.
    assert "take precedence over anything here" in block
    assert '[#412 attorney] "use Suzy Quatro"' in block
    assert block.rstrip().endswith("--- END EARLIER IN THIS CONVERSATION ---")


def test_render_segment_marks_assistant_lines_as_said_earlier():
    quotes = [{"row_id": 413, "speaker": "assistant", "text": "the cap is Green"}]
    # A position the document has since outgrown must read as history, not as a
    # live legal judgment.
    assert '[#413 assistant, said earlier] "the cap is Green"' in render_segment(quotes, 2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_compaction_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skills.legal_research.compaction'`

- [ ] **Step 3: Write the parsing and gate half of `skills/legal_research/compaction.py`**

```python
# skills/legal_research/compaction.py
"""Condense earlier conversation into validated verbatim quotes.

Compaction exists to protect the CONTRACT, not to make things fit. History is
compressible; the document, the playbook and the prior review are not. On a
grounded MSA turn the fixed content is 131,446 chars of a 150,000-char budget,
so history's entire allowance is 18,554 chars — and those chars are the
difference between the model reading the signature blocks and not.

The format is EXTRACTIVE — short verbatim quotes carrying the
conversation_store row id they came from — because the fabrication risk in
summarisation comes from abstraction, not from which side spoke. A paraphrased
legal conclusion drifts; a quoted one cannot. And because every line cites a
row, fabrication becomes REJECTABLE rather than merely detectable: see
validate_segment. A narrative summary would leave it undetectable by
construction.

IMPORT DIRECTION IS ONE-WAY. This module may import from legal_research.py;
context.py must never import this module, or the package cycles.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# One compaction pass reads at most this many rows. Not a config knob: it is a
# safety bound on a single read, not a tuning dial, and it composes correctly
# with the design — a longer backlog simply takes a second compaction, which
# starts after the first segment's to_id.
_MAX_ROWS_PER_COMPACTION = 200

_FENCE_LINE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*$")
_QUOTE_RE = re.compile(
    r'^\[#(\d+)\s+(attorney|assistant)(?:,\s*said\s+earlier)?\]\s*(.+)$'
)
# Straight and curly, plus the guillemets some models reach for.
_QUOTE_CHARS = "\"'“”‘’«»"

_NORMALISE = {
    "“": '"', "”": '"', "‘": "'", "’": "'",
    " ": " ", "–": "-", "—": "-",
}

_SEGMENT_HEADER = (
    "--- EARLIER IN THIS CONVERSATION ({count} earlier messages, condensed) ---\n"
    "This is recalled discussion, not a current finding. The attached document,\n"
    "the prior review and the playbook above take precedence over anything here."
)
_SEGMENT_FOOTER = "--- END EARLIER IN THIS CONVERSATION ---"


def _norm(text: str) -> str:
    """Fold the differences that are not differences: curly quotes, nbsp, en/em
    dashes, runs of whitespace, case. Applied to BOTH sides of the containment
    check, so a quote and its source row are compared on equal terms."""
    for src, dst in _NORMALISE.items():
        text = text.replace(src, dst)
    return " ".join(text.split()).casefold()


def parse_quote_lines(body: str) -> tuple[list[dict], str]:
    """Parse the model's output into quotes. Returns (quotes, error).

    error is "" only when at least one quote parsed AND every non-empty,
    non-fence line was a quote. Prose mixed in with the quotes is rejected
    rather than dropped: an unparsed line is an unvalidated line, and the whole
    point of the format is that every line is checkable against a row.
    """
    quotes: list[dict] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or _FENCE_LINE_RE.match(raw_line):
            continue
        m = _QUOTE_RE.match(line)
        if not m:
            return [], f"output contained a line that is not a quote line: {line[:80]!r}"
        quotes.append({
            "row_id": int(m.group(1)),
            "speaker": m.group(2),
            "text": m.group(3).strip().strip(_QUOTE_CHARS).strip(),
        })
    if not quotes:
        return [], "no quote lines found in the model's output"
    return quotes, ""


def validate_segment(body: str, rows: list[dict], from_id: int, to_id: int) -> str:
    """Check every quote against the row it cites. Returns "" when valid.

    Three checks per quote, all deterministic and zero-LLM:
      1. the cited id falls inside the segment's range;
      2. that row is present in the condensed transcript;
      3. the speaker label matches the row's role, and the quoted text actually
         appears in that row after normalisation.

    ANY failing quote invalidates the ENTIRE segment. A summary is legal recall;
    one invented line in it is worse than no summary at all, and there is no
    principled way to keep the rest of a block that demonstrably fabricated.
    """
    quotes, err = parse_quote_lines(body)
    if err:
        return err
    by_id = {r["id"]: r for r in rows}
    for q in quotes:
        rid = q["row_id"]
        if not (from_id <= rid <= to_id):
            return f"quote cites row #{rid}, outside the condensed range {from_id}-{to_id}"
        row = by_id.get(rid)
        if row is None:
            return f"quote cites row #{rid}, which is not in the condensed transcript"
        expected = "attorney" if row["role"] == "user" else "assistant"
        if q["speaker"] != expected:
            return (
                f"quote for row #{rid} is labelled {q['speaker']}, "
                f"but that row is the {expected}"
            )
        if not q["text"]:
            return f"quote for row #{rid} is empty"
        if _norm(q["text"]) not in _norm(row["content"]):
            return f"quote for row #{rid} does not appear in that message"
    return ""


def render_segment(quotes: list[dict], message_count: int) -> str:
    """Wrap validated quotes in the injectable block.

    The header, the precedence note and the footer are written HERE, by code —
    never by the model. The model supplies quotes it can be held to; it does not
    get to write the disclaimer that says how much weight they carry.

    The header counts MESSAGES, not turns: a turn ordinal is not derivable from
    a row id without a per-row ordinal we do not store, and a wrong number in a
    header is exactly the class of small lie this feature exists to prevent.
    """
    lines = [
        f'[#{q["row_id"]} {q["speaker"]}'
        + (", said earlier" if q["speaker"] == "assistant" else "")
        + f'] "{q["text"]}"'
        for q in quotes
    ]
    return "\n".join([
        _SEGMENT_HEADER.format(count=message_count),
        "",
        *lines,
        _SEGMENT_FOOTER,
    ])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_compaction_gate.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Mutation-prove the gate**

The spec names this explicitly: *"Mutation-proved: delete the check, confirm the
test fails."* A gate no test would notice losing is not a gate.

Run each of these four mutations, confirm the named test FAILS, then restore:

| mutation in `validate_segment` | test that must fail |
|---|---|
| delete the `from_id <= rid <= to_id` check | `test_quote_citing_a_row_outside_the_range_is_rejected` |
| replace `row is None` check with `continue` | `test_quote_citing_a_row_not_in_the_transcript_is_rejected` |
| delete the `_norm(...) not in _norm(...)` check | `test_misquoted_text_is_rejected` |
| delete the speaker-label check | `test_wrong_speaker_label_is_rejected` |

Run after restoring: `uv run pytest tests/test_compaction_gate.py -v` → PASS.

- [ ] **Step 6: Commit**

```bash
git add skills/legal_research/compaction.py tests/test_compaction_gate.py
git commit -m "feat: deterministic validation gate for compaction quotes"
```

---

### Task 4: Segment selection and generation

**Files:**
- Modify: `skills/legal_research/prompts.py` (append `_COMPACTION_SYSTEM`)
- Modify: `skills/legal_research/compaction.py` (append selection + generation)
- Test: `tests/test_compaction_flow.py`

**Interfaces:**
- Consumes: `memory.conversation_summary.append_segment | latest_to_id`; `memory.conversation_store.load_rows_after`; `skills.legal_research.edit_parsing._sanitize_history`; `skills.legal_research.legal_research._build_llm`; `observability.tracing.traced_invoke`; `skills.legal_research.compaction.validate_segment | render_segment | parse_quote_lines | _MAX_ROWS_PER_COMPACTION`.
- Produces:
  - `select_compactable_rows(document_id: str, attorney_id: str) -> tuple[int, int, list[dict]]` — `(from_id, to_id, sanitised_rows)`, or `(0, 0, [])` when there is nothing to condense.
  - `_generate_quote_lines(rows: list[dict], max_quotes: int) -> str` — one LLM call, isolated so tests replace it.
  - `compact_conversation(document_id: str, attorney_id: str) -> dict` — `{"compacted": bool, "from_id": int, "to_id": int, "messages": int, "quotes": int, "segment_id": int, "reason": str, "error": str}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_compaction_flow.py`:

```python
"""Segment selection, generation, the retry, and loud failure.

The LLM call is replaced at the _generate_quote_lines seam. That name is
resolved in compaction.py's OWN globals by compact_conversation, so patching
skills.legal_research.compaction._generate_quote_lines is the target that
actually takes effect — the package's re-export hazard makes the wrong target
silently no-op (see CLAUDE.md).
"""
import pytest

import skills.legal_research.compaction as compaction
from config import get_settings
from memory.conversation_store import append_turn, load_rows_after
from memory.conversation_summary import latest_to_id, load_segments


def _seed(turns: int, document_id: str = "doc-1", attorney_id: str = "atty-1") -> list[dict]:
    for i in range(turns):
        append_turn(document_id, attorney_id, f"question {i}", f"answer {i}")
    return load_rows_after(document_id, attorney_id, 0, 500)


def _quotes_for(rows, ids):
    by_id = {r["id"]: r for r in rows}
    out = []
    for rid in ids:
        row = by_id[rid]
        speaker = "attorney" if row["role"] == "user" else "assistant, said earlier"
        out.append(f'[#{rid} {speaker}] "{row["content"]}"')
    return "\n".join(out)


def test_selection_keeps_the_recent_window_verbatim():
    rows = _seed(5)                      # 10 messages
    keep = get_settings().compaction_keep_recent_messages   # 6
    from_id, to_id, selected = compaction.select_compactable_rows("doc-1", "atty-1")
    assert len(selected) == len(rows) - keep
    assert from_id == rows[0]["id"]
    assert to_id == rows[len(rows) - keep - 1]["id"]


def test_selection_returns_nothing_when_only_the_recent_window_exists():
    _seed(3)                             # 6 messages == keep_recent
    assert compaction.select_compactable_rows("doc-1", "atty-1") == (0, 0, [])


def test_selection_starts_after_the_previous_segment(monkeypatch):
    rows = _seed(6)                      # 12 messages
    monkeypatch.setattr(
        compaction, "_generate_quote_lines",
        lambda r, n: _quotes_for(r, [r[0]["id"]]),
    )
    first = compaction.compact_conversation("doc-1", "atty-1")
    assert first["compacted"] is True
    _seed(3)                             # 6 more messages
    from_id, _to_id, selected = compaction.select_compactable_rows("doc-1", "atty-1")
    # Ranges never overlap and never gap.
    assert from_id == first["to_id"] + 1
    assert all(r["id"] > first["to_id"] for r in selected)


def test_happy_path_writes_one_validated_segment(monkeypatch):
    rows = _seed(5)
    calls = []

    def fake(r, n):
        calls.append((len(r), n))
        return _quotes_for(r, [r[0]["id"], r[1]["id"]])

    monkeypatch.setattr(compaction, "_generate_quote_lines", fake)
    result = compaction.compact_conversation("doc-1", "atty-1")
    assert result["compacted"] is True
    assert result["quotes"] == 2
    assert result["messages"] == len(rows) - get_settings().compaction_keep_recent_messages
    assert calls[0][1] == get_settings().compaction_max_quotes
    segs = load_segments("doc-1", "atty-1", 5)
    assert len(segs) == 1
    assert "EARLIER IN THIS CONVERSATION" in segs[0]["content"]
    assert latest_to_id("doc-1", "atty-1") == result["to_id"]


def test_a_fabricated_quote_is_retried_once_then_written(monkeypatch):
    rows = _seed(5)
    attempts = {"n": 0}

    def flaky(r, n):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return '[#999 attorney] "something nobody said"'
        return _quotes_for(r, [r[0]["id"]])

    monkeypatch.setattr(compaction, "_generate_quote_lines", flaky)
    result = compaction.compact_conversation("doc-1", "atty-1")
    assert attempts["n"] == 2
    assert result["compacted"] is True
    assert len(load_segments("doc-1", "atty-1", 5)) == 1


def test_two_fabricated_attempts_write_nothing_and_report_loudly(monkeypatch):
    _seed(5)
    monkeypatch.setattr(
        compaction, "_generate_quote_lines",
        lambda r, n: '[#999 attorney] "something nobody said"',
    )
    result = compaction.compact_conversation("doc-1", "atty-1")
    assert result["compacted"] is False
    assert result["error"]
    # LOUD, and nothing written: a failed compaction must change nothing.
    assert load_segments("doc-1", "atty-1", 5) == []
    assert latest_to_id("doc-1", "atty-1") == 0


def test_nothing_to_condense_is_not_an_error(monkeypatch):
    _seed(2)
    monkeypatch.setattr(
        compaction, "_generate_quote_lines",
        lambda r, n: pytest.fail("must not call the LLM with nothing to condense"),
    )
    result = compaction.compact_conversation("doc-1", "atty-1")
    assert result["compacted"] is False
    assert result["error"] == ""
    assert result["reason"]


def test_excess_quotes_are_capped_not_rejected(monkeypatch):
    rows = _seed(30)                     # 60 messages, 54 compactable
    _, _, selected = compaction.select_compactable_rows("doc-1", "atty-1")
    monkeypatch.setattr(
        compaction, "_generate_quote_lines",
        lambda r, n: _quotes_for(r, [row["id"] for row in r[:40]]),
    )
    result = compaction.compact_conversation("doc-1", "atty-1")
    cap = get_settings().compaction_max_quotes
    assert result["compacted"] is True
    # Over-production is not fabrication — every extra line still validated, so
    # trim rather than burn a retry.
    assert result["quotes"] == cap
    assert load_segments("doc-1", "atty-1", 5)[0]["content"].count("[#") == cap
    assert len(selected) > cap


def test_machinery_only_assistant_rows_are_never_quotable(monkeypatch):
    append_turn("doc-1", "atty-1", "fill the blanks", '```json\n{"action":"replace"}\n```')
    for i in range(4):
        append_turn("doc-1", "atty-1", f"q{i}", f"a{i}")
    _from, _to, selected = compaction.select_compactable_rows("doc-1", "atty-1")
    # _sanitize_history drops an assistant turn that was ONLY a fenced block, so
    # a ```json``` example can never be quoted into a summary and replayed as a
    # few-shot example (the 2ae99ecc failure).
    assert all("```" not in r["content"] for r in selected)
    assert all('"action"' not in r["content"] for r in selected)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_compaction_flow.py -v`
Expected: FAIL — `AttributeError: module 'skills.legal_research.compaction' has no attribute 'select_compactable_rows'`

- [ ] **Step 3: Add the compaction prompt**

Append to `skills/legal_research/prompts.py`:

```python
# Compaction. Deliberately a FORMAT specification with placeholder ids, not a
# worked legal example: this file's own history records the local LLM copying a
# worked example verbatim (a \t-joined two-column target, stacked objects) and
# emitting unparseable output. A format template cannot be copied as a scenario.
# The rules are principle-based and model-neutral for the same reason — the
# validation gate, not the prompt, is what makes fabrication impossible.
_COMPACTION_SYSTEM = """You are condensing an earlier stretch of a conversation between an attorney and a legal assistant so it can be recalled later in a much shorter form.

Output ONLY quote lines, one per line, in exactly this shape:

[#<row id> attorney] "<the attorney's exact words>"
[#<row id> assistant, said earlier] "<the assistant's exact words>"

RULES
- Every quote must be copied VERBATIM from the numbered message it cites. Never paraphrase, summarise, correct, complete, translate or merge quotes.
- Only cite row ids that appear in the transcript below. Never invent an id.
- Label each line with the speaker of the row it cites, never the other one.
- Keep each quote short: the sentence or clause carrying the decision, not the whole message.
- Keep what a later turn would need — instructions the attorney gave, values and names they chose, positions they accepted or rejected, and conclusions the assistant reached.
- Drop pleasantries, restatements of the question, and anything a later message in this range superseded.
- At most {max_quotes} lines. Prefer decisions over discussion.
- Output nothing else: no preamble, no heading, no explanation, no code fences.
"""
```

- [ ] **Step 4: Append selection and generation to `skills/legal_research/compaction.py`**

Extend the import block at the top of the file (all imports at top — hard rule 1):

```python
from config import get_settings
from memory.conversation_store import load_rows_after
from memory.conversation_summary import append_segment, latest_to_id
from observability.tracing import traced_invoke
from skills.legal_research.edit_parsing import _sanitize_history
from skills.legal_research.legal_research import _build_llm
from skills.legal_research.prompts import _COMPACTION_SYSTEM
```

Append to the end of the module:

```python
def select_compactable_rows(document_id: str, attorney_id: str) -> tuple[int, int, list[dict]]:
    """(from_id, to_id, sanitised rows) for the next segment, or (0, 0, []).

    Starts after the highest already-condensed row, so a second compaction picks
    up exactly where the first stopped: ranges never overlap and never gap.
    Leaves the most recent compaction_keep_recent_messages messages verbatim —
    recent nuance should be read, not quoted.

    from_id/to_id come from the ORIGINAL selection, before sanitising. That is
    deliberate: the range must cover every row it consumed, including rows
    _sanitize_history dropped as pure machinery. A quote citing one of those
    dropped rows then fails validation, which is the correct outcome — nothing
    can vouch for it.
    """
    boundary = latest_to_id(document_id, attorney_id)
    available = load_rows_after(
        document_id, attorney_id, boundary, _MAX_ROWS_PER_COMPACTION
    )
    keep = get_settings().compaction_keep_recent_messages
    if len(available) <= keep:
        return 0, 0, []
    selected = available[: len(available) - keep]
    from_id, to_id = selected[0]["id"], selected[-1]["id"]
    return from_id, to_id, _sanitize_history(selected)


def _render_transcript(rows: list[dict]) -> str:
    return "\n\n".join(
        f"[#{r['id']} {'attorney' if r['role'] == 'user' else 'assistant'}]\n{r['content']}"
        for r in rows
    )


def _generate_quote_lines(rows: list[dict], max_quotes: int) -> str:
    """One LLM call: numbered transcript in, quote lines out.

    Isolated behind this seam so the flow tests replace it without a live model.
    Reuses the doc-chat LLM (temperature 0, num_predict 2048) — a capped segment
    is roughly 500 tokens, comfortably inside that.
    """
    response = traced_invoke(
        _build_llm(),
        [
            {"role": "system", "content": _COMPACTION_SYSTEM.format(max_quotes=max_quotes)},
            {"role": "user", "content": _render_transcript(rows)},
        ],
        name="conversation_compaction",
    )
    return response.content if hasattr(response, "content") else str(response)


def compact_conversation(document_id: str, attorney_id: str) -> dict:
    """Condense the oldest un-condensed stretch of this conversation.

    Returns a result dict; never raises for an ordinary failure. Distinguishes
    two non-success cases on purpose:
      - reason  — nothing to condense. Not a failure; the caller answers 200.
      - error   — the gate rejected two attempts, or the write failed. LOUD:
                  the attorney clicked and was told it happened, so a silent
                  failure would be a lie (the same split as save_review vs
                  append_turn).
    A failure changes nothing: no segment row, no deleted turns.
    """
    empty = {
        "compacted": False, "from_id": 0, "to_id": 0, "messages": 0,
        "quotes": 0, "segment_id": 0, "reason": "", "error": "",
    }
    from_id, to_id, rows = select_compactable_rows(document_id, attorney_id)
    if not rows:
        return {**empty, "reason": "nothing earlier to condense yet"}

    settings = get_settings()
    max_quotes = settings.compaction_max_quotes
    last_error = ""
    for attempt in (1, 2):
        try:
            body = _generate_quote_lines(rows, max_quotes)
        except Exception as e:
            last_error = f"the model could not be reached ({e.__class__.__name__})"
            logger.error("[compaction] generation failed on attempt %d: %s", attempt, e)
            continue
        last_error = validate_segment(body, rows, from_id, to_id)
        if last_error:
            logger.warning(
                "[compaction] attempt %d rejected by the gate: %s", attempt, last_error
            )
            continue
        quotes, _ = parse_quote_lines(body)
        # Over-production is not fabrication: every line here already passed the
        # gate, so trim to the cap rather than burn a retry on a valid segment.
        if len(quotes) > max_quotes:
            logger.info(
                "[compaction] %d quotes returned, capping at %d", len(quotes), max_quotes
            )
            quotes = quotes[:max_quotes]
        content = render_segment(quotes, message_count=len(rows))
        try:
            segment_id = append_segment(document_id, attorney_id, from_id, to_id, content)
        except Exception as e:
            logger.error("[compaction] segment write failed: %s", e)
            return {**empty, "error": f"the condensed segment could not be saved ({e.__class__.__name__})"}
        logger.info(
            "[compaction] condensed rows %d-%d (%d messages) into %d quotes",
            from_id, to_id, len(rows), len(quotes),
        )
        return {
            "compacted": True, "from_id": from_id, "to_id": to_id,
            "messages": len(rows), "quotes": len(quotes),
            "segment_id": segment_id, "reason": "", "error": "",
        }

    return {**empty, "error": f"the condensed summary could not be verified: {last_error}"}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_compaction_flow.py -v`
Expected: PASS (9 tests)

- [ ] **Step 6: Prove the mock is actually taking effect**

Patching the wrong module in this package silently no-ops, and the test still
passes while testing nothing. Confirm the seam is live: temporarily change
`compact_conversation` to call `_generate_quote_lines(rows, 1)` instead of
`max_quotes`. Run `uv run pytest tests/test_compaction_flow.py -v`.
Expected: `test_happy_path_writes_one_validated_segment` FAILS on the
`calls[0][1] == compaction_max_quotes` assertion. Restore and re-run to green.

- [ ] **Step 7: Commit**

```bash
git add skills/legal_research/compaction.py skills/legal_research/prompts.py tests/test_compaction_flow.py
git commit -m "feat: segment selection, quote generation, and retry-once compaction"
```

---

### Task 5: Inject summaries into the chat prompt

**Files:**
- Modify: `skills/legal_research/context.py:85-103` (`_load_prior_conversation`) and its imports
- Test: `tests/test_legal_research_conversation.py` (append)

**Interfaces:**
- Consumes: `memory.conversation_summary.load_segments | latest_to_id`; `memory.conversation_store.load_recent(…, after_id=…)`.
- Produces: `_load_prior_conversation(state) -> list[dict]` — now returns injected segments (as `{"role": "system", "content": …}`) **oldest-first**, followed by verbatim rows after the highest condensed id.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_legal_research_conversation.py`:

```python
def test_injection_puts_summaries_before_verbatim_rows(monkeypatch):
    from memory.conversation_store import append_turn, load_rows_after
    from memory.conversation_summary import append_segment
    import skills.legal_research.context as ctx

    for i in range(5):
        append_turn("doc-c", "atty-c", f"q{i}", f"a{i}")
    rows = load_rows_after("doc-c", "atty-c", 0, 100)
    append_segment("doc-c", "atty-c", rows[0]["id"], rows[3]["id"], "SEGMENT ONE")

    state = {"document_id": "doc-c", "user_id": "atty-c"}
    out = ctx._load_prior_conversation(state)

    assert out[0] == {"role": "system", "content": "SEGMENT ONE"}
    # Rows 1-4 are condensed; replaying them verbatim would undo the compaction.
    assert [m["content"] for m in out[1:]] == ["q2", "a2", "q3", "a3", "q4", "a4"]


def test_injection_is_oldest_first_and_windowed(monkeypatch):
    from memory.conversation_store import append_turn, load_rows_after
    from memory.conversation_summary import append_segment
    import skills.legal_research.context as ctx

    for i in range(10):
        append_turn("doc-d", "atty-d", f"q{i}", f"a{i}")
    rows = load_rows_after("doc-d", "atty-d", 0, 100)
    for n in range(4):
        append_segment("doc-d", "atty-d", rows[n * 2]["id"], rows[n * 2 + 1]["id"], f"SEG{n}")

    monkeypatch.setattr(
        ctx, "get_settings",
        lambda: type("S", (), {
            "conversation_store_enabled": True, "conversation_max_messages": 20,
            "compaction_enabled": True, "compaction_max_injected_segments": 3,
        })(),
    )
    out = ctx._load_prior_conversation({"document_id": "doc-d", "user_id": "atty-d"})
    injected = [m["content"] for m in out if m["role"] == "system"]
    # The most recent 3 segments, chronological.
    assert injected == ["SEG1", "SEG2", "SEG3"]
    # The verbatim floor spans ALL segments, not just the injected window —
    # otherwise SEG0's rows would come back verbatim and the compaction that
    # condensed them would be undone.
    assert [m["content"] for m in out if m["role"] != "system"][0] == "q4"


def test_a_summary_read_failure_degrades_to_raw_rows(monkeypatch):
    from memory.conversation_store import append_turn
    import skills.legal_research.context as ctx

    append_turn("doc-e", "atty-e", "q", "a")

    def boom(*_a, **_k):
        raise RuntimeError("app-db unavailable")

    monkeypatch.setattr(ctx, "latest_to_id", boom)
    state = {"document_id": "doc-e", "user_id": "atty-e"}
    out = ctx._load_prior_conversation(state)
    # Degrades to the raw conversation and says so — a compaction failure must
    # never break a subsequent chat turn.
    assert [m["content"] for m in out] == ["q", "a"]
    assert state["memory_degraded"] is True


def test_a_conversation_read_failure_returns_nothing_and_flags(monkeypatch):
    import skills.legal_research.context as ctx

    def boom(*_a, **_k):
        raise RuntimeError("app-db unavailable")

    monkeypatch.setattr(ctx, "load_recent", boom)
    state = {"document_id": "doc-g", "user_id": "atty-g"}
    # The OTHER failure path: with no rows readable there is nothing to degrade
    # to, so the turn answers from the Redis fallback in _run_doc_chat. Two
    # distinct paths, deliberately: losing the summaries is survivable, losing
    # the conversation is not.
    assert ctx._load_prior_conversation(state) == []
    assert state["memory_degraded"] is True


def test_compaction_disabled_ignores_existing_segments(monkeypatch):
    from memory.conversation_store import append_turn, load_rows_after
    from memory.conversation_summary import append_segment
    import skills.legal_research.context as ctx

    append_turn("doc-f", "atty-f", "q0", "a0")
    append_turn("doc-f", "atty-f", "q1", "a1")
    rows = load_rows_after("doc-f", "atty-f", 0, 100)
    append_segment("doc-f", "atty-f", rows[0]["id"], rows[1]["id"], "SEG")

    monkeypatch.setattr(
        ctx, "get_settings",
        lambda: type("S", (), {
            "conversation_store_enabled": True, "conversation_max_messages": 20,
            "compaction_enabled": False, "compaction_max_injected_segments": 3,
        })(),
    )
    out = ctx._load_prior_conversation({"document_id": "doc-f", "user_id": "atty-f"})
    # The master switch is a real off: no segment injected AND no floor applied.
    assert [m["content"] for m in out] == ["q0", "a0", "q1", "a1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_legal_research_conversation.py -k injection -v`
Expected: FAIL — the first assertion, because `out[0]` is `{"role": "user", "content": "q0"}` (no summary injected yet)

- [ ] **Step 3: Implement summary-aware injection**

In `skills/legal_research/context.py`, add to the import block:

```python
from memory.conversation_summary import latest_to_id, load_segments
```

Replace `_load_prior_conversation` with:

```python
def _load_prior_conversation(state: LegalAgentState) -> list[dict]:
    """Durable per-(document, attorney) chat history for the doc-chat prompt.

    Condensed segments first (oldest-first, as system messages), then the
    verbatim rows after the highest condensed id. Splicing them into the history
    slot puts the summaries BELOW every grounding system message and above the
    user's question, so recalled discussion never outranks the live document,
    playbook, MSA or prior review — which is what the block's own header claims.

    The verbatim floor is latest_to_id (ALL segments), not the injected window:
    a segment that has aged out of the prompt still condensed its rows, and
    replaying them verbatim would undo that.

    Empty when disabled, keys missing, or on a store-read failure (which flags
    memory_degraded) — memory must never break the chat turn.
    """
    settings = get_settings()
    if not settings.conversation_store_enabled:
        return []
    document_id = state.get("document_id", "")
    attorney_id = state.get("user_id", "")
    if not document_id or not attorney_id:
        return []
    boundary = 0
    summaries: list[dict] = []
    if settings.compaction_enabled:
        try:
            boundary = latest_to_id(document_id, attorney_id)
            summaries = [
                {"role": "system", "content": s["content"]}
                for s in load_segments(
                    document_id, attorney_id, settings.compaction_max_injected_segments
                )
            ]
        except Exception as e:
            # A summary-read failure must not cost the attorney their raw
            # conversation: conversation_store is a different table and is very
            # likely fine. Fall back to no floor and no summaries — which
            # replays the rows verbatim, so nothing is lost and nothing is
            # duplicated (the summaries that would have covered them did not
            # load either).
            logger.error("[legal_research] summary load failed: %s", e)
            state["memory_degraded"] = True
            boundary, summaries = 0, []
    try:
        verbatim = load_recent(
            document_id, attorney_id, settings.conversation_max_messages, boundary,
        )
    except Exception as e:
        logger.error("[legal_research] prior-conversation load failed: %s", e)
        state["memory_degraded"] = True
        return []
    return [*summaries, *verbatim]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_legal_research_conversation.py tests/test_history_priming.py -v`
Expected: PASS

- [ ] **Step 5: Confirm sanitising leaves summaries untouched**

`_run_doc_chat` calls `_sanitize_history` on whatever this returns.
`_sanitize_history` transforms only `role == "assistant"` messages, so the
`role: "system"` segments pass through byte-identical. Verify with:

Run: `uv run python -c "from skills.legal_research.edit_parsing import _sanitize_history; s=[{'role':'system','content':'--- EARLIER ---\n[#1 attorney] \"x\"'}]; assert _sanitize_history(s)==s; print('summaries survive sanitising')"`
Expected: `summaries survive sanitising`

- [ ] **Step 6: Commit**

```bash
git add skills/legal_research/context.py tests/test_legal_research_conversation.py
git commit -m "feat: inject condensed segments below grounding, verbatim rows after the floor"
```

---

### Task 6: The context breakdown on every turn

**Files:**
- Modify: `skills/legal_research/context.py` (append two functions)
- Modify: `skills/legal_research/legal_research.py` (`_run_doc_chat` + the per-turn reset in `legal_research`)
- Modify: `graph/state.py` (after the `token_usage` field, ~line 64)
- Modify: `graph/nodes/output_formatter.py:32`
- Modify: `api/routes/query.py:187` region (`initial_state`)
- Test: `tests/test_context_breakdown.py`

**Interfaces:**
- Consumes: `memory.conversation_store.count_after`; `memory.conversation_summary.latest_to_id`.
- Produces:
  - `compressible_message_count(state: LegalAgentState) -> int` — best-effort, 0 on any failure or when compaction is off.
  - `build_context_breakdown(*, doc_chars: int, playbook_chars: int, msa_chars: int, review_chars: int, history_chars: int, system_chars: int, compressible_messages: int) -> dict`
  - `state["context_breakdown"] -> report["context_breakdown"]`

The breakdown dict, exactly:

```python
{
  "budget_chars": int, "budget_tokens": int, "chars_per_token": float,
  "total_chars": int, "total_tokens": int, "pct": int,
  "warn_pct": int, "can_compact": bool, "compressible_messages": int,
  "parts": [ {"key": str, "chars": int, "tokens": int, "pct": int, "compactable": bool}, ... ],
}
```

`parts` keys, in display order: `document`, `playbook`, `msa`, `review`,
`history`, `system`. Only `history` has `compactable: True`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_context_breakdown.py`:

```python
"""The counter's arithmetic and its threshold.

The figures are the LAST TURN'S REAL MEASURED VALUES, never a forecast: grounding
is question-dependent (_needs_grounding keys off wording), so the same contract
costs 49k or 131k depending on what is asked.
"""
from config import get_settings
from skills.legal_research.context import build_context_breakdown


def _bd(**kw):
    base = dict(
        doc_chars=0, playbook_chars=0, msa_chars=0, review_chars=0,
        history_chars=0, system_chars=0, compressible_messages=0,
    )
    return build_context_breakdown(**{**base, **kw})


def test_parts_sum_to_the_total_and_carry_display_order():
    b = _bd(doc_chars=84859, playbook_chars=38587, review_chars=5000,
            history_chars=18554, system_chars=3000)
    assert [p["key"] for p in b["parts"]] == [
        "document", "playbook", "msa", "review", "history", "system",
    ]
    assert sum(p["chars"] for p in b["parts"]) == b["total_chars"] == 150000
    # Only history is compactable. The document is the source of truth and the
    # playbook is the ceiling — neither is ever summarised.
    assert [p["key"] for p in b["parts"] if p["compactable"]] == ["history"]


def test_tokens_use_the_measured_chars_per_token_not_chars_over_four():
    cpt = get_settings().est_chars_per_token
    assert cpt == 4.89
    b = _bd(doc_chars=48900)
    # chars/4 would say 12,225 — an overstatement of 22%, which is why every
    # budget comment that used it was wrong.
    assert b["parts"][0]["tokens"] == 10000
    assert b["total_tokens"] == 10000
    assert b["chars_per_token"] == cpt


def test_percentages_are_of_the_budget_not_of_the_total():
    budget = get_settings().chat_context_max_chars
    b = _bd(doc_chars=budget // 2)
    assert b["budget_chars"] == budget
    assert b["pct"] == 50
    assert b["parts"][0]["pct"] == 50


def test_no_compressible_history_means_no_action_offered():
    budget = get_settings().chat_context_max_chars
    # Over the threshold, but nothing older than the verbatim window: offering
    # the control here would offer a no-op, and a control that cries wolf gets
    # ignored.
    b = _bd(doc_chars=budget, compressible_messages=0)
    assert b["pct"] >= b["warn_pct"]
    assert b["can_compact"] is False


def test_below_the_threshold_means_no_action_offered():
    b = _bd(doc_chars=1000, compressible_messages=40)
    assert b["can_compact"] is False


def test_over_threshold_with_compressible_history_offers_the_action():
    budget = get_settings().chat_context_max_chars
    b = _bd(doc_chars=int(budget * 0.95), compressible_messages=14)
    assert b["warn_pct"] == get_settings().compaction_warn_pct
    assert b["can_compact"] is True
    assert b["compressible_messages"] == 14


def test_an_empty_turn_does_not_divide_by_zero():
    b = _bd()
    assert b["total_chars"] == 0
    assert b["pct"] == 0
    assert all(p["pct"] == 0 for p in b["parts"])
    assert b["can_compact"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context_breakdown.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_context_breakdown'`

- [ ] **Step 3: Implement the two functions**

Append to `skills/legal_research/context.py` (and add
`from memory.conversation_store import count_after, load_recent` to the existing
import — `load_recent` is already imported, extend that line):

```python
def compressible_message_count(state: LegalAgentState) -> int:
    """How many stored messages sit past the compaction floor AND outside the
    verbatim window — i.e. what a Condense action would actually condense.

    Best-effort: 0 on any failure, and 0 when compaction is off. This feeds a
    UI affordance, so a store hiccup must cost the attorney a button, never a
    turn. It does NOT flag memory_degraded — that is reserved for reads the
    answer depends on.
    """
    settings = get_settings()
    if not settings.compaction_enabled:
        return 0
    document_id = state.get("document_id", "")
    attorney_id = state.get("user_id", "")
    if not document_id or not attorney_id:
        return 0
    try:
        boundary = latest_to_id(document_id, attorney_id)
        total = count_after(document_id, attorney_id, boundary)
    except Exception as e:
        logger.warning("[legal_research] compressible-count failed: %s", e)
        return 0
    return max(0, total - settings.compaction_keep_recent_messages)


# Display order. Only `history` is compactable: the document is the source of
# truth, the playbook is the ceiling, and neither is ever summarised.
_BREAKDOWN_PARTS = ("document", "playbook", "msa", "review", "history", "system")


def build_context_breakdown(
    *,
    doc_chars: int,
    playbook_chars: int,
    msa_chars: int,
    review_chars: int,
    history_chars: int,
    system_chars: int,
    compressible_messages: int,
) -> dict:
    """What this turn actually spent, as the pane's counter renders it.

    Measured, never predicted: the caller passes the POST-truncation document
    size, so the counter reports what was sent rather than what was asked for.
    A forecast is impossible anyway — _needs_grounding keys off the question's
    wording, so the same contract costs 49k or 131k depending on what is asked.

    history_chars covers everything in the history slot, injected summary
    segments included. That is deliberate: summaries are a real prompt cost, and
    watching the history line drop after a compaction is the attorney's proof
    the feature did something.
    """
    settings = get_settings()
    budget = settings.chat_context_max_chars
    cpt = settings.est_chars_per_token
    sizes = {
        "document": doc_chars, "playbook": playbook_chars, "msa": msa_chars,
        "review": review_chars, "history": history_chars, "system": system_chars,
    }
    total = sum(sizes.values())
    parts = [
        {
            "key": key,
            "chars": sizes[key],
            "tokens": int(sizes[key] / cpt),
            "pct": (sizes[key] * 100 // budget) if budget else 0,
            "compactable": key == "history",
        }
        for key in _BREAKDOWN_PARTS
    ]
    pct = (total * 100 // budget) if budget else 0
    return {
        "budget_chars": budget,
        "budget_tokens": int(budget / cpt),
        "chars_per_token": cpt,
        "total_chars": total,
        "total_tokens": int(total / cpt),
        "pct": pct,
        "warn_pct": settings.compaction_warn_pct,
        # Both conditions, always. The threshold alone would offer a no-op on a
        # short conversation with a huge document; compressible history alone
        # would nag on every routine chat.
        "can_compact": bool(
            settings.compaction_enabled
            and compressible_messages > 0
            and pct >= settings.compaction_warn_pct
        ),
        "compressible_messages": compressible_messages,
        "parts": parts,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_context_breakdown.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Add the state field**

In `graph/state.py`, immediately after the `token_usage` line, add:

```python
    context_breakdown: dict | None          # NEW — what this turn's context actually spent, for the pane's counter
```

- [ ] **Step 6: Wire the breakdown through `_run_doc_chat`**

In `skills/legal_research/legal_research.py`, extend the `context` import block
to include the two new names:

```python
from skills.legal_research.context import (
    _build_chat_grounding,
    _cap_chat_context,
    _load_prior_conversation,
    _load_prior_review_block,
    _needs_grounding,
    build_context_breakdown,
    compressible_message_count,
)
```

Replace the single `state["context_truncated"] = ...` line with:

```python
    truncation = _cap_chat_context(messages, uploaded_text, request)
    state["context_truncated"] = truncation
    # Report what was SENT, not what was asked for: when the document was cut,
    # the counter's document line must show the kept size or it contradicts the
    # truncation notice sitting right beside it.
    state["context_breakdown"] = build_context_breakdown(
        doc_chars=truncation["kept_chars"] if truncation else len(uploaded_text),
        playbook_chars=len(playbook),
        msa_chars=len(msa_block),
        review_chars=len(review_block),
        history_chars=sum(len(m["content"]) for m in chat_history),
        system_chars=len(CHAT_SYSTEM_PROMPT) + len(prefs_block) + len(request),
        compressible_messages=compressible_message_count(state),
    )
```

In `legal_research()`, add to the per-turn reset block (after
`state["token_usage"] = None`):

```python
    state["context_breakdown"] = None
```

- [ ] **Step 7: Add it to the report and seed it in `initial_state`**

In `graph/nodes/output_formatter.py`, after the `"tokens"` line:

```python
        "context_breakdown": state.get("context_breakdown"),
```

In `api/routes/query.py`'s `initial_state`, beside the existing
`"context_truncated": None, "token_usage": None` seeds:

```python
        "context_breakdown": None,
```

Seeding here, not only in the skill, is what stops a stale value: skills that
set `llm_response` without `messages` (`contract_generation.py`, `base.py`) take
`llm_caller`'s early return and never reach the skill-level reset.

- [ ] **Step 8: Run the affected suites**

Run: `uv run pytest tests/test_context_breakdown.py tests/test_nodes.py tests/test_state.py tests/test_api.py tests/test_query_memory.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add skills/legal_research/context.py skills/legal_research/legal_research.py graph/state.py graph/nodes/output_formatter.py api/routes/query.py tests/test_context_breakdown.py
git commit -m "feat: measured context breakdown on every chat turn"
```

---

### Task 7: `POST /api/compact`

A distinct endpoint, not a flag on `/api/query`: compaction produces no answer,
carries its own latency, and must not sit on the turn path.

**Files:**
- Modify: `api/models.py` (append `CompactRequest` before `ApiResponse`)
- Create: `api/routes/compact.py`
- Modify: `api/main.py:82-92` (import + `include_router`)
- Test: `tests/test_compact_api.py`

**Interfaces:**
- Consumes: `skills.legal_research.compaction.compact_conversation`; `api.auth.resolve_user_id`; `api.models.ApiResponse`.
- Produces: `POST /api/compact` with body `{"document_id": str}` → `ApiResponse`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_compact_api.py`:

```python
"""POST /api/compact — identity from the auth seam, loud failure, quiet no-op."""
import pytest
from fastapi.testclient import TestClient

import skills.legal_research.compaction as compaction
from api.main import app
from memory.conversation_store import append_turn, load_rows_after
from memory.conversation_summary import load_segments

client = TestClient(app)


def _quotes_for(rows, ids):
    by_id = {r["id"]: r for r in rows}
    out = []
    for rid in ids:
        row = by_id[rid]
        speaker = "attorney" if row["role"] == "user" else "assistant, said earlier"
        out.append(f'[#{rid} {speaker}] "{row["content"]}"')
    return "\n".join(out)


def test_compacts_for_the_header_identity_not_a_body_field(monkeypatch):
    for i in range(5):
        append_turn("doc-x", "atty-x", f"q{i}", f"a{i}")
    monkeypatch.setattr(
        compaction, "_generate_quote_lines",
        lambda r, n: _quotes_for(r, [r[0]["id"]]),
    )
    res = client.post(
        "/api/compact",
        json={"document_id": "doc-x", "attorney_id": "somebody-else"},
        headers={"X-User-ID": "atty-x"},
    )
    assert res.status_code == 200
    assert res.json()["data"]["compacted"] is True
    # The body's attorney_id is ignored entirely — identity comes from the auth
    # seam, so the O365 cutover reaches this endpoint with no change here.
    assert load_segments("doc-x", "atty-x", 5)
    assert load_segments("doc-x", "somebody-else", 5) == []


def test_nothing_to_condense_is_a_quiet_200(monkeypatch):
    append_turn("doc-y", "atty-y", "q", "a")
    monkeypatch.setattr(
        compaction, "_generate_quote_lines",
        lambda r, n: pytest.fail("must not call the LLM with nothing to condense"),
    )
    res = client.post(
        "/api/compact", json={"document_id": "doc-y"}, headers={"X-User-ID": "atty-y"},
    )
    assert res.status_code == 200
    assert res.json()["data"]["compacted"] is False
    assert res.json()["data"]["reason"]


def test_a_rejected_summary_is_a_500_and_writes_nothing(monkeypatch):
    for i in range(5):
        append_turn("doc-z", "atty-z", f"q{i}", f"a{i}")
    monkeypatch.setattr(
        compaction, "_generate_quote_lines",
        lambda r, n: '[#999 attorney] "never said"',
    )
    res = client.post(
        "/api/compact", json={"document_id": "doc-z"}, headers={"X-User-ID": "atty-z"},
    )
    # LOUD: the attorney clicked and was told it happened, so a silent failure
    # would be a lie (the same split as /api/feedback vs /api/events).
    assert res.status_code == 500
    assert load_segments("doc-z", "atty-z", 5) == []


def test_missing_document_id_is_a_400():
    res = client.post("/api/compact", json={"document_id": "  "}, headers={"X-User-ID": "a"})
    assert res.status_code == 400


def test_disabled_is_a_403(monkeypatch):
    import api.routes.compact as route

    monkeypatch.setattr(
        route, "get_settings",
        lambda: type("S", (), {"compaction_enabled": False})(),
    )
    res = client.post("/api/compact", json={"document_id": "doc-q"}, headers={"X-User-ID": "a"})
    assert res.status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_compact_api.py -v`
Expected: FAIL — all assert 404 (route not registered)

- [ ] **Step 3: Add the request model**

In `api/models.py`, immediately before `class ApiResponse`:

```python
class CompactRequest(BaseModel):
    """Condense the earlier part of one document's conversation.

    attorney_id is deliberately absent — identity comes from the auth seam, the
    same rule as FeedbackSubmission.
    """
    document_id: str = Field(..., description="Stable document id whose conversation to condense")
```

- [ ] **Step 4: Write the route**

Create `api/routes/compact.py`:

```python
# api/routes/compact.py
"""Condense earlier conversation into validated verbatim quotes.

A distinct endpoint rather than a flag on /api/query: compaction produces no
answer, carries its own latency (~10 s of generation for a capped segment at the
measured 51.4 tok/s, plus prefill of the range), and must not sit on the turn
path.

Failure is LOUD — a 500. The attorney clicked and was told it happened, so a
silent failure is a lie. "Nothing to condense" is not a failure and answers 200.

It returns what was condensed, NOT a fresh breakdown: this endpoint has no
document text and no grounding decision, so any breakdown it synthesised would
be a guess — and the counter's own honesty constraint is that its figures are
the last turn's real measured values. The pane's counter updates on the next
message.
"""
from fastapi import APIRouter, Depends, HTTPException

from api.auth import resolve_user_id
from api.models import ApiResponse, CompactRequest
from config import get_settings
from skills.legal_research.compaction import compact_conversation

router = APIRouter(prefix="/api")


@router.post("/compact", response_model=ApiResponse)
def post_compact(
    body: CompactRequest, user_id: str = Depends(resolve_user_id)
) -> ApiResponse:
    if not get_settings().compaction_enabled:
        raise HTTPException(status_code=403, detail="compaction is disabled")
    document_id = body.document_id.strip()
    if not document_id:
        raise HTTPException(status_code=400, detail="document_id is required")
    result = compact_conversation(document_id, user_id)
    if result["error"]:
        raise HTTPException(status_code=500, detail=result["error"])
    return ApiResponse(status="ok", data=result)
```

- [ ] **Step 5: Register the router**

In `api/main.py`, add the import beside the others and the `include_router` call
beside the others:

```python
from api.routes.compact import router as compact_router
```
```python
app.include_router(compact_router)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_compact_api.py -v`
Expected: PASS (5 tests)

- [ ] **Step 7: Run the round trip — the test that pins the feature's purpose**

Append to `tests/test_compaction_flow.py`:

```python
def test_round_trip_shrinks_the_injected_history(monkeypatch):
    """Compaction's whole purpose: the next turn's assembled history is smaller.

    This is the test that pins WHY the feature exists. Everything else checks
    that compaction is safe; this checks that it works.
    """
    import skills.legal_research.context as ctx

    for i in range(12):
        append_turn("doc-rt", "atty-rt", f"question number {i} " * 20, f"answer number {i} " * 20)
    state = {"document_id": "doc-rt", "user_id": "atty-rt"}
    before = sum(len(m["content"]) for m in ctx._load_prior_conversation(state))

    monkeypatch.setattr(
        compaction, "_generate_quote_lines",
        lambda r, n: _quotes_for(r, [row["id"] for row in r[:3]]),
    )
    assert compaction.compact_conversation("doc-rt", "atty-rt")["compacted"] is True

    after = sum(len(m["content"]) for m in ctx._load_prior_conversation(state))
    assert after < before
```

Run: `uv run pytest tests/test_compaction_flow.py -v`
Expected: PASS (10 tests)

- [ ] **Step 8: Commit**

```bash
git add api/models.py api/routes/compact.py api/main.py tests/test_compact_api.py tests/test_compaction_flow.py
git commit -m "feat: POST /api/compact with loud failure and a round-trip test"
```

---

### Task 8: The Word pane counter and Condense action

**Files:**
- Create: `clients/word/src/contextGauge.ts`
- Create: `clients/word/src/contextGauge.test.ts`
- Create: `clients/word/src/components/ContextMeter.tsx`
- Modify: `clients/word/src/api.ts`
- Modify: `clients/word/src/App.tsx`
- Modify: `clients/word/src/components/ChatTab.tsx:90` region and its `Props`
- Modify: `clients/word/src/components/FindingsTab.tsx:81` region and its `Props`
- Modify: `clients/word/src/styles.css` (append)
- Modify: `scripts/check.sh` (`EXPECTED_PASS_COUNT` 265 → 279)

**Interfaces:**
- Consumes: `report.context_breakdown` from Task 6; `POST /api/compact` from Task 7.
- Produces:
  - `contextGauge.ts`: `ContextPart`, `ContextBreakdown`, `PART_LABELS`, `formatTokens(n)`, `gaugeLine(b)`, `isWarning(b)`, `withLiveDocument(b, docChars)`.
  - `api.ts`: `compactConversation(documentId: string): Promise<CompactResponse>`.
  - `<ContextMeter breakdown={…} onCompacted={() => void} />`.
  - `ChatTab` / `FindingsTab` gain `onBreakdown?: (b: ContextBreakdown | null) => void`.

- [ ] **Step 1: Write the failing test**

Create `clients/word/src/contextGauge.test.ts`:

```typescript
// Assertions for the context gauge's formatting and threshold logic.
// Run: npx tsx src/contextGauge.test.ts

// `declare const process` rather than @types/node: tsconfig pins
// "types": ["office-js", "vite/client"], and pulling in Node globals would
// change typing across the browser/Office.js sources for no benefit here.
declare const process: { exit(code?: number): never };

import {
  PART_LABELS,
  formatTokens,
  gaugeLine,
  isWarning,
  withLiveDocument,
  type ContextBreakdown,
} from "./contextGauge";

function assert(cond: boolean, name: string) {
  if (!cond) {
    console.error(`FAIL: ${name}`);
    process.exit(1);
  }
  console.log(`PASS: ${name}`);
}

// The measured grounded-MSA case: 131,446 fixed chars of a 150,000 budget.
const MSA: ContextBreakdown = {
  budget_chars: 150000,
  budget_tokens: 30674,
  chars_per_token: 4.89,
  total_chars: 150000,
  total_tokens: 30674,
  pct: 100,
  warn_pct: 90,
  can_compact: true,
  compressible_messages: 14,
  parts: [
    { key: "document", chars: 84859, tokens: 17353, pct: 56, compactable: false },
    { key: "playbook", chars: 38587, tokens: 7890, pct: 25, compactable: false },
    { key: "msa", chars: 0, tokens: 0, pct: 0, compactable: false },
    { key: "review", chars: 5000, tokens: 1022, pct: 3, compactable: false },
    { key: "history", chars: 18554, tokens: 3794, pct: 12, compactable: true },
    { key: "system", chars: 3000, tokens: 613, pct: 2, compactable: false },
  ],
};

assert(gaugeLine(null) === null, "no breakdown -> no gauge line");
assert(
  gaugeLine(MSA) === "context  31k / 31k tokens · history 12%",
  "gauge line reads total / budget and the compactable share",
);
assert(formatTokens(613) === "613", "sub-1k token counts are not abbreviated");
assert(formatTokens(17353) === "17k", "large token counts round to k");

assert(isWarning(null) === false, "no breakdown -> not warning");
assert(isWarning(MSA) === true, "at or above warn_pct -> warning");
assert(
  isWarning({ ...MSA, pct: 89 }) === false,
  "below warn_pct -> not warning",
);

// Every backend part key must have a label, or the expanded view renders a
// raw key at an attorney.
assert(
  MSA.parts.every((p) => typeof PART_LABELS[p.key] === "string"),
  "every part key has a display label",
);

// The document is the ONE line the client can watch change between turns —
// readBody() is all it knows. Every other line holds its last measured value.
const live = withLiveDocument(MSA, 42000);
assert(live.parts[0].chars === 42000, "live document char count replaces the measured one");
assert(live.total_chars === 150000 - 84859 + 42000, "total follows the live document");
assert(live.parts[1].chars === 38587, "non-document parts are untouched");
assert(live.pct === 71, "pct recomputes from the live total");
assert(
  withLiveDocument(MSA, 42000).can_compact === false,
  "a shrunken document drops below the threshold, so no action is offered",
);
assert(
  withLiveDocument({ ...MSA, compressible_messages: 0 }, 200000).can_compact === false,
  "no compressible history -> no action even when far over budget",
);

console.log("contextGauge: all assertions passed");
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd clients/word && npx tsx src/contextGauge.test.ts`
Expected: FAIL — `Cannot find module './contextGauge'`

- [ ] **Step 3: Write `clients/word/src/contextGauge.ts`**

```typescript
// The attorney-facing shape of the context budget.
//
// Kept as pure functions, separate from JSX, so the arithmetic can be asserted
// on. The backend sends the breakdown; only this module decides how to say it.
//
// Why the breakdown is backend-fed: the client knows the document text from
// readBody() and nothing else — not the playbook size, not whether grounding
// attached, not the prior-review block, not the injected history. All of that
// is assembled in _run_doc_chat. The ONE line the client can watch change
// between turns is the document, which is what withLiveDocument is for.

export interface ContextPart {
  key: string;
  chars: number;
  tokens: number;
  pct: number;
  compactable: boolean;
}

export interface ContextBreakdown {
  budget_chars: number;
  budget_tokens: number;
  chars_per_token: number;
  total_chars: number;
  total_tokens: number;
  pct: number;
  warn_pct: number;
  can_compact: boolean;
  compressible_messages: number;
  parts: ContextPart[];
}

export const PART_LABELS: Record<string, string> = {
  document: "document",
  playbook: "playbook",
  msa: "governing MSA",
  review: "prior review",
  history: "history",
  system: "system & question",
};

/** Never compacted — said in the expanded view so the trade-off is legible. */
export const NEVER_COMPACTED = new Set(["document", "playbook", "msa", "review"]);

export function formatTokens(n: number): string {
  return n >= 1000 ? `${Math.round(n / 1000)}k` : `${n}`;
}

/** The collapsed one-liner, or null when no turn has been measured yet. */
export function gaugeLine(b: ContextBreakdown | null | undefined): string | null {
  if (!b) return null;
  const history = b.parts.find((p) => p.compactable);
  const share = history ? ` · history ${history.pct}%` : "";
  return `context  ${formatTokens(b.total_tokens)} / ${formatTokens(b.budget_tokens)} tokens${share}`;
}

export function isWarning(b: ContextBreakdown | null | undefined): boolean {
  return Boolean(b && b.pct >= b.warn_pct);
}

/**
 * Recompute the document line (and only it) from a live readBody() length.
 *
 * `can_compact` recomputes its threshold half here — editing the document
 * genuinely changes whether the budget is under pressure — but takes
 * `compressible_messages` as backend truth, since the client cannot know what
 * is in the store. The backend reports 0 when compaction is disabled, so the
 * master switch survives this path.
 */
export function withLiveDocument(b: ContextBreakdown, docChars: number): ContextBreakdown {
  const parts = b.parts.map((p) =>
    p.key === "document"
      ? {
          ...p,
          chars: docChars,
          tokens: Math.trunc(docChars / b.chars_per_token),
          pct: b.budget_chars ? Math.trunc((docChars * 100) / b.budget_chars) : 0,
        }
      : p,
  );
  const total = parts.reduce((sum, p) => sum + p.chars, 0);
  const pct = b.budget_chars ? Math.trunc((total * 100) / b.budget_chars) : 0;
  return {
    ...b,
    parts,
    total_chars: total,
    total_tokens: Math.trunc(total / b.chars_per_token),
    pct,
    can_compact: b.compressible_messages > 0 && pct >= b.warn_pct,
  };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd clients/word && npx tsx src/contextGauge.test.ts`
Expected: 14 `PASS:` lines, then `contextGauge: all assertions passed`

If the count differs from 14, fix the test file to match this plan rather than
adjusting `EXPECTED_PASS_COUNT` to whatever came out — that constant exists to
catch exactly this drift.

- [ ] **Step 5: Add the API client call**

In `clients/word/src/api.ts`, extend the `report` type with the breakdown and
append the endpoint. Add to the imports:

```typescript
import type { ContextBreakdown } from "./contextGauge";
```

Add inside `report?: { … }`:

```typescript
      context_breakdown?: ContextBreakdown | null;
```

Append at the end of the file:

```typescript
export interface CompactResponse {
  status: "ok" | "error";
  data?: {
    compacted?: boolean;
    from_id?: number;
    to_id?: number;
    messages?: number;
    quotes?: number;
    segment_id?: number;
    reason?: string;
    error?: string;
  };
  errors?: string[];
}

/**
 * Condense the earlier part of this document's conversation.
 *
 * Its own endpoint, not a flag on /api/query: compaction produces no answer and
 * carries its own latency. A failure comes back as a non-2xx and is thrown —
 * the attorney clicked, so a silent failure would be a lie.
 */
export async function compactConversation(documentId: string): Promise<CompactResponse> {
  const res = await fetch("/api/compact", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...userHeaders() },
    body: JSON.stringify({ document_id: documentId }),
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* a non-JSON error body is still an error — keep the status line */
    }
    throw new Error(detail);
  }
  return res.json();
}
```

- [ ] **Step 6: Write `clients/word/src/components/ContextMeter.tsx`**

```typescript
import { useState } from "react";
import { compactConversation } from "../api";
import { resolveDocumentId } from "../docIdentity";
import {
  NEVER_COMPACTED,
  PART_LABELS,
  gaugeLine,
  isWarning,
  type ContextBreakdown,
} from "../contextGauge";

interface Props {
  breakdown: ContextBreakdown | null;
  /** Called after a successful compaction so the pane can note what changed. */
  onCompacted?: (messages: number) => void;
}

/**
 * The shared-header context counter.
 *
 * Two honesty constraints, both load-bearing:
 *  - the figures are the LAST TURN'S real measured values, labelled as such.
 *    Not a prediction: grounding is question-dependent and unknowable ahead of
 *    the question.
 *  - the Condense action appears only when the budget is under pressure AND
 *    there is compressible history. Offering it with nothing to condense would
 *    offer a no-op, and a control that cries wolf gets ignored.
 */
export default function ContextMeter({ breakdown, onCompacted }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const line = gaugeLine(breakdown);
  if (!breakdown || !line) return null;

  const condense = async () => {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const documentId = await resolveDocumentId();
      const res = await compactConversation(documentId);
      if (res.data?.compacted) {
        const n = res.data.messages ?? 0;
        setNote(`${n} earlier messages condensed. The counter updates on your next message.`);
        onCompacted?.(n);
      } else {
        setNote(res.data?.reason || "Nothing earlier to condense yet.");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={`context-meter ${isWarning(breakdown) ? "warn" : ""}`}>
      <button
        className="context-meter-line"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
      >
        <span>{line}</span>
        <span className="context-meter-caret">{expanded ? "▾" : "▸"}</span>
      </button>
      {expanded && (
        <>
          <p className="context-meter-caption">Measured on your last message.</p>
          <table className="context-meter-table">
            <tbody>
              {breakdown.parts
                .filter((p) => p.chars > 0)
                .map((p) => (
                  <tr key={p.key}>
                    <td>{PART_LABELS[p.key] ?? p.key}</td>
                    <td className="num">{p.chars.toLocaleString("en-US")}</td>
                    <td className="num">{p.pct}%</td>
                    <td className="context-meter-note">
                      {NEVER_COMPACTED.has(p.key)
                        ? "never condensed"
                        : p.compactable
                          ? "what condensing reclaims"
                          : ""}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </>
      )}
      {breakdown.can_compact && (
        <div className="context-meter-actions">
          <button className="secondary" onClick={condense} disabled={busy}>
            {busy ? "Condensing… (10–30 s)" : "Condense earlier turns"}
          </button>
          <span className="context-meter-note">
            Condenses {breakdown.compressible_messages} earlier messages into verbatim
            quotes. Your most recent messages stay word-for-word, and nothing is deleted.
          </span>
        </div>
      )}
      {note && <p className="context-meter-note" role="status">{note}</p>}
      {error && <p className="status error">Couldn't condense: {error}</p>}
    </div>
  );
}
```

- [ ] **Step 7: Report the breakdown up from both tabs**

In `clients/word/src/components/ChatTab.tsx`, add to the imports:

```typescript
import type { ContextBreakdown } from "../contextGauge";
```

Add to `interface Props`:

```typescript
  onBreakdown?: (b: ContextBreakdown | null) => void;
```

Change the component signature to destructure it:

```typescript
export default function ChatTab({ sessionId, messages, setMessages, onPreferenceAdded, onBreakdown }: Props) {
```

Immediately after the existing `setTruncated(truncationNotice(...))` line:

```typescript
      onBreakdown?.(res.data?.report?.context_breakdown ?? null);
```

In `clients/word/src/components/FindingsTab.tsx`, make the same three additions
(import, `Props` field, destructured parameter — the signature becomes
`export default function FindingsTab({ sessionId, result, setResult, onBreakdown }: Props)`)
and add the same line after its `setTruncated(...)` call.

- [ ] **Step 8: Wire the header in `App.tsx`**

Add to the imports:

```typescript
import ContextMeter from "./components/ContextMeter";
import { withLiveDocument, type ContextBreakdown } from "./contextGauge";
```

Add a module-scope constant above the component, and state beside the others:

```typescript
// Debounce for the live document measurement. onParagraphChanged fires per
// keystroke; readBody() on a real contract is a full getReviewedText round trip.
const LIVE_DOC_DEBOUNCE_MS = 2000;
```

```typescript
  const [breakdown, setBreakdown] = useState<ContextBreakdown | null>(null);
  const [liveDocChars, setLiveDocChars] = useState<number | null>(null);
```

Add the live-document watcher after the existing `unsaved` effect. The document
is the one component the client can watch change between turns; every other line
holds its last measured value until the next turn.

```typescript
  // ~2 s debounce: onParagraphChanged fires per keystroke, and readBody() on an
  // 85,000-char contract is not free.
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const measure = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        readBody()
          .then((t) => { if (alive) setLiveDocChars(t.length); })
          .catch(() => { /* never let a probe break the pane */ });
      }, LIVE_DOC_DEBOUNCE_MS);
    };
    measure();   // seed the line before the first edit
    // onParagraphChanged needs WordApi 1.5 and is absent from some office-js
    // typing releases, hence the narrow cast rather than a direct call. On a
    // host without the event, registration rejects and we fall back to
    // re-reading on focus — the same signal the unsaved check already uses.
    const useFocus = () => window.addEventListener("focus", measure);
    Word.run(async (context) => {
      const doc = context.document as unknown as {
        onParagraphChanged: { add: (h: () => Promise<void>) => void };
      };
      doc.onParagraphChanged.add(async () => { measure(); });
      await context.sync();
    }).catch(useFocus);
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
      // Harmless when it was never added, and the alternative is tracking a
      // flag that the async .catch() above may set after cleanup has run.
      window.removeEventListener("focus", measure);
    };
  }, []);
```

Render the meter directly above `<Tabs>` (a shared header, so both tabs see it),
and pass the callback to both tabs:

```typescript
      <ContextMeter
        breakdown={
          breakdown && liveDocChars !== null
            ? withLiveDocument(breakdown, liveDocChars)
            : breakdown
        }
      />
      <Tabs active={tab} onChange={setTab} />
```

Add `onBreakdown={setBreakdown}` to both `<FindingsTab …>` and `<ChatTab …>`.

- [ ] **Step 9: Add the styles**

Append to `clients/word/src/styles.css`:

```css
/* Context counter. Neutral by default and amber only past warn_pct — this line
   is present on every turn, so a permanently coloured box would be wallpaper
   within a day. Deliberately NOT the red .context-truncated treatment: that one
   means "this answer may be legally unsound", this one means "you are heading
   toward that". */
.context-meter {
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 6px 8px;
  margin: 8px 0 0;
  font-size: 11px;
  color: #605e5c;
}
.context-meter.warn {
  border-color: #a97400;
  background: #fff8e6;
  color: #6b4a00;
}
.context-meter-line {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
  background: none;
  border: none;
  padding: 0;
  font: inherit;
  color: inherit;
  cursor: pointer;
}
.context-meter-caret { opacity: 0.7; }
.context-meter-caption { margin: 6px 0 2px; font-style: italic; opacity: 0.85; }
.context-meter-table { width: 100%; border-collapse: collapse; margin-top: 4px; }
.context-meter-table td { padding: 1px 0; }
.context-meter-table td.num { text-align: right; padding-left: 8px; font-variant-numeric: tabular-nums; }
.context-meter-note { opacity: 0.85; margin: 4px 0 0; }
.context-meter-actions {
  display: flex;
  flex-direction: column;
  gap: 4px;
  align-items: flex-start;
  margin-top: 6px;
}
```

- [ ] **Step 10: Typecheck and run every add-in assertion**

Run: `cd clients/word && npx tsc --noEmit`
Expected: no output (clean)

Run: `cd clients/word && for f in src/*.test.ts; do npx tsx "$f"; done | grep -c '^PASS: '`
Expected: `279`

- [ ] **Step 11: Update the pre-push gate**

In `scripts/check.sh`, change:

```bash
EXPECTED_PASS_COUNT=279
```

Run: `bash scripts/check.sh`
Expected: all checks pass

- [ ] **Step 12: Commit**

```bash
git add clients/word/src/contextGauge.ts clients/word/src/contextGauge.test.ts clients/word/src/components/ContextMeter.tsx clients/word/src/api.ts clients/word/src/App.tsx clients/word/src/components/ChatTab.tsx clients/word/src/components/FindingsTab.tsx clients/word/src/styles.css scripts/check.sh
git commit -m "feat: context counter and Condense action in the Word pane"
```

---

### Task 9: Documentation

**Files:**
- Modify: `docs/wiki.md` (API Endpoints table; Shipped Since Last Update; Follow-ups / Roadmap)
- Modify: `CLAUDE.md` (Backend section — currently 143 of a 150-line cap)

**Interfaces:**
- Consumes: everything above.
- Produces: no code.

- [ ] **Step 1: Add the endpoint to the wiki's API table**

In `docs/wiki.md`, in the API Endpoints table, after the `/api/query/{session_id}/status` row:

```
| POST | `/api/compact` | **Working** | Condense this document's earlier conversation into validated verbatim quotes. Body `{document_id}`; attorney from the auth seam. 200 with `compacted:false` + `reason` when there is nothing to condense; **500** when the validation gate rejects the summary (and nothing is written). |
```

- [ ] **Step 2: Add a Shipped row**

At the top of the "Shipped Since Last Update" table body in `docs/wiki.md`:

```
| **Context counter + attorney-triggered compaction** | `feat/context-compaction` | Every chat turn returns `report.context_breakdown` (measured, post-truncation) → a one-line gauge in the shared pane header, amber past `compaction_warn_pct`. `POST /api/compact` condenses earlier `conversation_store` rows into an append-only `conversation_summary` segment of **extractive verbatim quotes carrying their row ids** — so a deterministic zero-LLM gate can reject any quote that does not match the row it cites (range, presence, speaker, text; one bad quote invalidates the whole segment; retry once, then write nothing and report). Injection is summaries-oldest-first below all grounding, then verbatim rows after the highest condensed id. Raw rows are never deleted. Bounded by `compaction_max_quotes=24` and `compaction_max_injected_segments=3` — on the grounded-MSA case history's entire allowance is 3,794 tokens, so unbounded summaries would starve the contract they exist to protect. Follow-ups: automatic ("visible but automatic") compaction, re-summarising past ~60 turns, self-calibrating chars/token for the live document line. |
```

- [ ] **Step 3: Add the follow-ups**

In the "Follow-ups / Roadmap" table of `docs/wiki.md`, add three rows:

```
| Medium | **Automatic compaction** ("visible but automatic") | Same trigger logic, fired without a click, pane showing "N earlier turns condensed" with the summary readable. A config flag over the shipped design, not a rewrite. |
| Low | **Re-summarising older segments** | `compaction_max_injected_segments=3` bounds the prompt but means discussion past roughly sixty turns is no longer represented at all — it stays in the store and is auditable, but the model does not see it. Only worth building if a pilot shows attorneys reaching back that far. Explicit action, never silent. |
| Low | **Self-calibrating chars/token for the live document line** | `charsPerToken = charsSent / tokens.input`, seeded 4.89, EWMA-smoothed, persisted. Auto-adapts if the backend model changes. Backend-fed lines are already exact; only the live estimate drifts. |
```

- [ ] **Step 4: Add the CLAUDE.md entry**

`CLAUDE.md` is 143 lines against a 150-line cap. Add exactly one bullet to the
**Backend** subsection, after the "Chat context is capped" bullet:

```markdown
- **History compaction protects the CONTRACT, not the fit.** On a grounded MSA the fixed content (doc 84,859 + playbook 38,587 + sys/review 8,000 = 131,446) leaves history **18,554 chars = 3,794 tokens** of a 150,000 budget — so past ~12 messages every further message of chat is paid for in contract text. `POST /api/compact` (attorney-triggered; `skills/legal_research/compaction.py`) condenses the oldest un-condensed `conversation_store` rows into an **append-only, never-re-summarised** `conversation_summary` segment covering a fixed `from_id`→`to_id`. The format is **extractive quotes carrying their row ids**, which is what makes fabrication *rejectable* rather than merely detectable: `validate_segment` checks range, presence, speaker and text against the cited row, and **any failing quote invalidates the whole segment** (retry once, then write nothing — LOUD, like `save_review`). Raw rows are never deleted; a summary is a view. Bounded by `compaction_max_quotes=24` + `compaction_max_injected_segments=3` — uncapped, accumulated summaries exceed history's entire MSA allowance and start starving the document. Injection: summaries oldest-first as system messages BELOW all grounding, then verbatim rows after `latest_to_id` (**all** segments, not the injected window). Summaries are built from `_sanitize_history`-cleaned text so a fenced block can never be quoted back as a few-shot example. `report.context_breakdown` feeds the pane's counter — **measured last-turn values, never a forecast** (`_needs_grounding` keys off wording, so the same contract costs 49k or 131k). `compaction_*` is `@lru_cache`'d ⇒ restart `bash scripts/start.sh`.
```

Then verify the cap:

Run: `wc -l CLAUDE.md`
Expected: 144 (≤ 150). If it exceeds 150, consolidate the lowest-value existing
line rather than trimming this one.

- [ ] **Step 5: Run the full gate**

Run: `bash scripts/check.sh`
Expected: all checks pass

- [ ] **Step 6: Commit**

```bash
git add docs/wiki.md CLAUDE.md
git commit -m "docs: context counter and compaction in the wiki and CLAUDE.md"
```

---

## Manual verification (not automatable — do not mark the branch done without it)

The spec names two things no test covers. Both need a human.

1. **Sideload smoke test in Word for Mac.** `npx tsc --noEmit` is not sufficient
   (CLAUDE.md rule). Specifically verify:
   - the counter appears in the header and shows plausible numbers after the
     first chat turn;
   - the document line moves when you type into the contract (this is the
     `onParagraphChanged` path — the one piece most likely to be wrong, and
     invisible to the typechecker);
   - the expanded breakdown renders and the numbers reconcile;
   - past ~90% with a long conversation, the line turns amber and **Condense
     earlier turns** appears; clicking it reports how many messages were
     condensed;
   - the next chat turn still answers correctly and the history line has
     dropped.
2. **Whether the summaries are useful.** Read one. This needs an attorney and no
   test substitutes for it — it is the thing to watch in a pilot.

Also outstanding from the predecessor slice and still unverified:
`curl http://<ollama>:11434/api/ps` must report `ctx` equal to
`ollama_num_ctx` (131072) after deploy, and one full MSA review should be
spot-checked now that reviews send ~25k tokens where the server previously
dropped some.

## Self-review

**Spec coverage** — every section maps to a task:

| spec section | task |
|---|---|
| The counter (backend-fed breakdown, live document line, tokens at 4.89) | 6, 8 |
| The trigger (both conditions, 5 config fields) | 1, 6, 8 |
| Bounding accumulation (`max_quotes`, `max_injected_segments`) | 1, 4, 5 |
| What a summary contains (extractive, both sides, "said earlier", sanitised) | 3, 4 |
| The validation gate | 3 |
| Storage (`conversation_summary`, index, conftest, append-only) | 1 |
| Trigger mechanics and injection (`POST /api/compact`, order) | 5, 7 |
| Failure behaviour (loud; degrades to raw rows) | 4, 5, 7 |
| Testing items 1–6 | 3 (gate), 4 (selection, loudness), 5 (injection order), 6 (breakdown + threshold), 7 (round trip) |
| Not-automatable items | Manual verification section |
| Follow-ups 1–3 | 9 |

Follow-up 4 (compaction in the review path) is correctly absent: reviews carry no
conversational history, so there is nothing to compact.

**Type consistency** — checked across tasks:
`{"id", "role", "content"}` rows flow from `load_rows_after` (Task 2) into
`select_compactable_rows` and `validate_segment` (Tasks 3–4) unchanged.
`{"row_id", "speaker", "text"}` quotes flow from `parse_quote_lines` into
`render_segment`. `{"id", "from_id", "to_id", "content"}` segments flow from
`load_segments` (Task 1) into `_load_prior_conversation` (Task 5). The breakdown
dict defined in Task 6 matches `ContextBreakdown` in Task 8 field for field,
including `compressible_messages` and the six `parts` keys. `compact_conversation`'s
result keys match `CompactResponse` in Task 8 and the assertions in Task 7.

**Known cross-task hazards, ruled in advance:**
- `compaction.py` imports `_build_llm` from `legal_research.py`; `context.py`
  must never import `compaction.py`. Task 5 imports only `memory.conversation_summary`,
  so the direction holds.
- Task 2 changes `load_recent`'s signature by adding an optional `after_id`.
  This is an optional filter, not a compat shim: `0` means "no floor", which is
  the honest state of a never-condensed conversation, and the existing tests
  that omit it keep exercising a real path.
- Task 5's `test_a_summary_read_failure_degrades_to_raw_rows` patches
  `ctx.latest_to_id`. That name is imported INTO `context.py`, so it resolves in
  that module's globals — the correct target. Patching
  `memory.conversation_summary.latest_to_id` would not take effect.
