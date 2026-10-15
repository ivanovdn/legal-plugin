# What Attorneys Do to the Document (SP4) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record the document as attorneys work on it — each turn's text, pane captures at open / after editing pauses / before Finalize with tracked changes, and the full text of every Apply — and read it back per document with `feedback_report --changes`.

**Architecture:** The backend stores each distinct text of a document once in `document_version` (keyed by the SHA-256 `turn_log` already records, through one shared `memory/fingerprint.py`), one `document_snapshot` row per pane capture, and each Apply's text in `interaction_event.applied`. The pane's new `documentCapture.ts` reads both views of the document plus its tracked changes and posts them quietly to `POST /api/document-snapshot`. Pure functions in `scripts/document_changes.py` turn the rows into verdicts and changed paragraphs; `feedback_report --changes` prints them, and `scripts.delete_document_data` removes them on request.

**Tech Stack:** Python 3.12 · FastAPI · psycopg 3 on the `app-db` Postgres · pytest + testcontainers · Word add-in TypeScript (Office.js, React) with hand-rolled `tsx` assertion scripts.

**Spec:** `docs/superpowers/specs/2026-10-15-document-changes-design.md` — read it first; this plan argues from it.

## Global Constraints

- Branch `feat/document-changes`, cut from `main` (Setup below). One commit per task, each ending with the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never push, never touch the VM — the deploy is the owner's call (CLAUDE.md hard rule 6).
- Python 3.12 via `uv run`. All imports at the top of the file (CLAUDE.md hard rule 1). Standard library only — no new dependency (an import reachable from `api.main` would have to be declared in both requirement files and re-locked).
- Caps, verbatim from the spec: stored text **1,000,000** characters (`truncated` flag; the SHA-256 is of the full text); **500** tracked changes per capture, **2,000** characters of text each (`cut: true` on a cut item); `tracked_total` = the count before the cap, **-1** = unreadable; **20,000** characters per `applied` field; idle capture **30 s** after the last change notification; Finalize waits for its capture at most **3 s**; fallback poll every **2 minutes** while the pane is visible.
- Never log document text. A failed write logs the document id, the SHA-256, the size and the exception **type** — never `str(e)`: Postgres quotes offending input back in some error messages.
- Quiet everywhere: no capture failure reaches the attorney, blocks typing or Finalize, sets `memory_degraded`, or records a degradation code (class 4 — telemetry's own failure).
- Capture code reads the document id with `readDocumentId()` only — never `resolveDocumentId()`, which writes to the file and marks it modified.
- Every new `Settings` field goes on `RECORDED` or `NEVER_RECORDED` in `api/runtime_facts.py`. Every new store table goes on `STORE_TABLES` in `tests/conftest.py`. A column on an existing table comes by `ALTER TABLE … ADD COLUMN IF NOT EXISTS`, never only inside a `CREATE`.
- Timestamps are written with `datetime.now(timezone.utc).isoformat()` (server clock), so comparing them as strings orders them.
- Pane: `scripts/check.sh` counts `PASS:` lines — raise `EXPECTED_PASS_COUNT` by exactly the assertions a task adds. `tsc` must never emit into `clients/word/src/` (tsconfig is `noEmit`).
- Before each commit, the task's own tests pass; before the last task's commit, `bash scripts/check.sh` exits 0.

## Review Focus

1. **A lone surrogate or a NUL in a document text or a tracked change** (broken UTF-16 from a Word paste) → still stored, cleaned, under the hash of what was sent. Tests: Task 1 `test_a_lone_surrogate_or_a_nul_does_not_lose_the_version`, Task 4 `test_a_lone_surrogate_or_a_nul_in_a_tracked_change_does_not_lose_the_capture`.
2. **A Postgres error message that quotes contract text** → never reaches the log. Tests: Task 1 `test_a_failed_write_logs_what_identifies_the_text_and_never_the_text`, Task 4 `test_a_store_failure_is_quiet_and_keeps_the_text_out_of_the_log`.
3. **Word's `\r` paragraph separators** (and `\r\n`, blank lines) → paragraphs split correctly. Test: Task 5 `test_paragraphs_split_on_words_carriage_returns`.
4. **An empty view** (a document that is all tracked insertions has an empty original) → no version row, sha `""`, and the report still reads. Tests: Task 1 `test_an_empty_text_stores_nothing`, Task 5 `test_empty_texts_have_no_paragraphs_and_change_nothing`, `test_sightings_without_a_stored_text_are_counted_not_compared`.
5. **A long, heavily negotiated contract** (2,000 paragraphs, 100 applied edits) → `--changes` still finishes in seconds. Test: Task 5 `test_a_long_negotiated_contract_reads_back_in_seconds`.

## Rulings made while planning (the spec was amended to match)

- **Change signal (D3):** the idle capture listens to *both* `onParagraphChanged` (already registered by `App.tsx`) and `DocumentSelectionChanged`; the 2-minute poll runs only when neither registers. Neither event is recorded as verified in Word for Mac's pane, so the design depends on neither, and no sideload probe gates the start of the work — the end-to-end sideload checks it.
- **Changed paragraphs:** a `+` line per paragraph only in the last text and a `-` line per paragraph only in the first; never paired into one `~` line, because a clause typed where an applied deletion was would inherit the deletion's label.
- **"Closest now":** word-set overlap, not difflib's ratio — ratio against every paragraph of a 2,000-paragraph contract takes tens of seconds. Display only.
- **The off switch** (`document_capture_enabled`) also stops the per-turn copy, not just the endpoint.

## File map

| File | Task | Responsibility |
|---|---|---|
| `memory/fingerprint.py` (new) | 1 | The one hash of a text, and text Postgres can store |
| `memory/document_store.py` (new) | 1, 4, 6, 7 | Versions, snapshots, the reads behind `--changes`, deletion |
| `memory/db.py` | 1, 3 | The two new tables; `interaction_event.applied` |
| `memory/turn_log.py` | 1 | Uses the shared hash |
| `config.py`, `api/runtime_facts.py` | 2 | `document_capture_enabled` |
| `api/turn_record.py` | 2 | Stores each pane turn's text after the response |
| `memory/feedback_store.py`, `api/models.py` | 3, 4 | `applied` on events; the snapshot request models |
| `api/routes/document_snapshot.py` (new), `api/main.py` | 4 | `POST /api/document-snapshot` |
| `scripts/document_changes.py` (new) | 5 | Pure: verdicts, changed paragraphs, the printed block |
| `scripts/feedback_report.py` | 6 | `--changes`, `--full`, capture lines in `--turns` |
| `scripts/delete_document_data.py` (new) | 7 | Deletion on request |
| `clients/word/src/documentCapture.ts` (new), `word.ts` | 8 | Reading, deciding and sending a capture |
| `clients/word/src/feedback.ts`, `EditProposalCard.tsx`, `FindingCard.tsx` | 9 | The applied text on Apply events |
| `clients/word/src/App.tsx`, `FinalizeBar.tsx` | 10 | Wiring; the disclosure line |
| Docs | 11 | Tester disclosure, feedback loop, runbook, wiki, CLAUDE.md |

---

## Setup

- [ ] **Cut the branch** (the plan and the amended spec are already committed on `main`):

```bash
cd /Users/dmytroivanov/projects/legal-plugin
git checkout main && git status --short     # expect: clean
git checkout -b feat/document-changes
```

- [ ] **Baseline:** `uv run pytest tests/ -q` → all pass (887 at the time of writing). Docker must be running (testcontainers).

---

### Task 1: Version store

**Files:**
- Create: `memory/fingerprint.py`, `memory/document_store.py`, `tests/test_document_store.py`
- Modify: `memory/turn_log.py` (imports; drop `_fingerprint` / `_storable`), `memory/db.py` (two tables, docstrings), `tests/conftest.py` (`STORE_TABLES`), `tests/test_db.py`, `tests/test_turn_log.py`

**Interfaces:**
- Produces: `memory.fingerprint.fingerprint(text: str) -> tuple[int, str]` — `(0, "")` for no text; `memory.fingerprint.storable(text: str) -> str`; `memory.document_store.TEXT_CAP = 1_000_000`; `save_version(document_id: str, text: str, *, attorney_id: str, source: str) -> str` (RAISES; returns the SHA-256, `""` for an empty text, which stores nothing); `save_version_quietly(document_id: str, text: str, *, attorney_id: str, source: str) -> None` (never raises); tables `document_version`, `document_snapshot`; `tests.conftest.STORE_TABLES`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_document_store.py`:

```python
"""SP4's version store: each distinct text of a document once, under the hash
turn_log already records — and nothing of the text in a failure's log line
(spec 2026-10-15, D1, D2, D6)."""
from __future__ import annotations

import hashlib
import logging

import memory.document_store as ds
from memory.db import get_pool
from memory.fingerprint import fingerprint
from memory.turn_log import build_turn_row


def _versions() -> list[dict]:
    with get_pool().connection() as conn:
        cur = conn.execute("SELECT * FROM document_version ORDER BY document_id, first_seen")
        names = [column.name for column in cur.description]
        return [dict(zip(names, values)) for values in cur.fetchall()]


def _turn_row(doc_text: str) -> dict:
    return build_turn_row(
        kind="query", turn_id="t-1", trace_id="", arrived_at="2026-10-16T09:00:00+00:00",
        duration_ms=1, attorney_id="atty-1", user_name="", session_id="s-1",
        document_id="doc-1", task_type="research", request="who signs?", doc_text=doc_text,
        status="ok", outcome="ok", degradations=[], api_error="", awaiting_review=False,
        facts={}, result=None, compaction=None, client={}, backend_build="dev", runtime={},
    )


def test_a_repeated_text_is_stored_once():
    """Mutation: drop ON CONFLICT … DO NOTHING, or key the row on first_seen."""
    ds.save_version("doc-1", "NDA v1", attorney_id="atty-1", source="turn")
    ds.save_version("doc-1", "NDA v1", attorney_id="atty-1", source="pane")
    (version,) = _versions()
    assert (version["source"], version["text"], version["chars"]) == ("turn", "NDA v1", 6)


def test_the_same_text_in_two_documents_is_two_versions():
    ds.save_version("doc-1", "same words", attorney_id="atty-1", source="turn")
    ds.save_version("doc-2", "same words", attorney_id="atty-1", source="turn")
    assert [v["document_id"] for v in _versions()] == ["doc-1", "doc-2"]


def test_a_turn_finds_its_text_under_the_hash_it_recorded():
    """turn_log and the store share memory/fingerprint.py. A lone surrogate is
    where two hash functions would part (surrogatepass vs replace).
    Mutation: give turn_log its own hash again."""
    text = "pay \ud83d now"
    sha = ds.save_version("doc-1", text, attorney_id="atty-1", source="turn")
    assert sha == _turn_row(text)["doc_sha256"] == fingerprint(text)[1]
    assert _versions()[0]["sha256"] == sha


def test_the_hash_is_sha256_of_the_utf8_text():
    assert fingerprint("abc") == (3, hashlib.sha256(b"abc").hexdigest())
    assert fingerprint("") == (0, "")


def test_a_capped_text_keeps_the_full_texts_hash():
    """Mutation: hash after capping."""
    text = "x" * (ds.TEXT_CAP + 1)
    sha = ds.save_version("doc-1", text, attorney_id="atty-1", source="pane")
    (version,) = _versions()
    assert sha == fingerprint(text)[1] == version["sha256"]
    assert len(version["text"]) == ds.TEXT_CAP
    assert version["chars"] == ds.TEXT_CAP + 1 and version["truncated"] is True


def test_an_empty_text_stores_nothing():
    """Review Focus 4: a document that is all tracked insertions has an empty
    original view; that is not a version. Mutation: drop the early return."""
    assert ds.save_version("doc-1", "", attorney_id="atty-1", source="pane") == ""
    assert _versions() == []


def test_a_lone_surrogate_or_a_nul_does_not_lose_the_version():
    """Review Focus 1: Postgres refuses both; the text is stored cleaned, under
    the hash of what was sent. Mutation: store the text without storable()."""
    text = "pay \ud83d now\x00"
    sha = ds.save_version("doc-1", text, attorney_id="atty-1", source="pane")
    (version,) = _versions()
    assert version["text"] == "pay ? now" and sha == fingerprint(text)[1]


def test_a_failed_write_logs_what_identifies_the_text_and_never_the_text(monkeypatch, caplog):
    """Review Focus 2: Postgres quotes offending input back in some messages,
    so the exception's message stays out of the log too.
    Mutation: log `e` instead of type(e).__name__, or log the text."""
    secret = "The Supplier shall indemnify the Client"

    def boom():
        raise RuntimeError(f"invalid input syntax: {secret}")

    monkeypatch.setattr(ds, "get_pool", boom)
    with caplog.at_level(logging.WARNING, logger="memory.document_store"):
        ds.save_version_quietly("doc-1", secret, attorney_id="atty-1", source="turn")
    assert "doc-1" in caplog.text and fingerprint(secret)[1] in caplog.text
    assert "RuntimeError" in caplog.text and "indemnify" not in caplog.text
```

Append to `tests/test_db.py` (add the import at the top of the file, next to the existing one):

```python
from tests.conftest import STORE_TABLES


def test_every_store_table_is_truncated_between_tests():
    """conftest truncates a hardcoded list: a table init_db() creates and the
    list misses would carry rows from one test into the next — and green a lie.
    Mutation: drop a name from STORE_TABLES."""
    with get_pool().connection() as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        ).fetchall()}
    assert tables == set(STORE_TABLES)
```

Append to `tests/test_turn_log.py`:

```python
def test_a_nul_in_the_question_does_not_lose_the_row():
    """Postgres TEXT refuses NUL; memory/fingerprint.py's storable() drops it.
    Mutation: storable() without the NUL replacement."""
    row = _row(turn_id="t-nul", request="is \x00 fine?")
    assert row["request"] == "is  fine?"
    write_turn(row)                                     # must not raise
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_document_store.py tests/test_db.py tests/test_turn_log.py -q`
Expected: collection errors — `ModuleNotFoundError: No module named 'memory.fingerprint'` / `ImportError: cannot import name 'STORE_TABLES'`.

- [ ] **Step 3: Write the shared hash**

Create `memory/fingerprint.py`:

```python
"""The one hash of a document's text, and text Postgres can store.

turn_log keeps a document's fingerprint and document_version keeps its text;
both go through fingerprint() so a turn's doc_sha256 always finds its stored
version (spec 2026-10-15, D2). The hash is of the FULL text, before any cap.
"""
from __future__ import annotations

import hashlib


def fingerprint(text: str) -> tuple[int, str]:
    """(chars, hex SHA-256 of the UTF-8 text); (0, "") for no text.
    surrogatepass, because broken UTF-16 from Word reaches Python as lone
    surrogates that plain UTF-8 refuses to encode."""
    if not text:
        return 0, ""
    return len(text), hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def storable(text: str) -> str:
    """Free text Postgres can store: a lone surrogate becomes '?' and a NUL is
    dropped — TEXT and JSONB refuse both, and the write would be lost."""
    return (text or "").encode("utf-8", "replace").decode("utf-8").replace("\x00", "")
```

In `memory/turn_log.py`: delete the line `import hashlib`; add `from memory.fingerprint import fingerprint, storable` below `from memory.feedback_store import _filters`; delete the two functions `_fingerprint` and `_storable` (with their docstrings); then in `build_turn_row` replace the three call sites:

```python
    doc_chars, doc_sha256 = fingerprint(doc_text)
```
```python
        "request": storable(request)[:_REQUEST_CAP],
```
```python
        "error": storable(facts.get("error") or api_error)[:_ERROR_CAP],
```

Check nothing else used them: `grep -rn "_fingerprint\|_storable" api graph memory skills scripts tests` → no output.

- [ ] **Step 4: Add the tables**

In `memory/db.py`, append to the end of `_STATEMENTS` (after `"CREATE INDEX IF NOT EXISTS idx_turn_log_doc ON turn_log (document_id, id)",`):

```python
    # SP4 (spec 2026-10-15): each distinct text of a document once, keyed by the
    # SHA-256 turn_log already records (memory/fingerprint.py), so a turn finds
    # its text; and each capture the pane made of the document as the attorney
    # worked on it. Both hold contract text — scripts/delete_document_data.py
    # removes them on request.
    """
    CREATE TABLE IF NOT EXISTS document_version (
        document_id TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        text TEXT NOT NULL,
        chars INTEGER NOT NULL,
        truncated BOOLEAN NOT NULL DEFAULT FALSE,
        first_seen TEXT NOT NULL,
        attorney_id TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL,
        PRIMARY KEY (document_id, sha256)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_document_version_attorney ON document_version (attorney_id)",
    """
    CREATE TABLE IF NOT EXISTS document_snapshot (
        id BIGSERIAL PRIMARY KEY,
        timestamp TEXT NOT NULL,
        attorney_id TEXT NOT NULL DEFAULT '',
        session_id TEXT NOT NULL DEFAULT '',
        document_id TEXT NOT NULL,
        moment TEXT NOT NULL,
        current_sha256 TEXT NOT NULL,
        original_sha256 TEXT NOT NULL,
        tracked_changes JSONB NOT NULL DEFAULT '[]',
        tracked_total INTEGER NOT NULL DEFAULT 0,
        office_platform TEXT NOT NULL DEFAULT '',
        office_version TEXT NOT NULL DEFAULT '',
        pane_build TEXT NOT NULL DEFAULT '',
        backend_build TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_document_snapshot_doc ON document_snapshot (document_id, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_document_snapshot_session ON document_snapshot (session_id)",
```

Update the two docstrings in `memory/db.py`: the module docstring's list becomes `(audit, review, conversation, conversation_summary, feedback, interaction_event, turn_log, document_version, document_snapshot)`, and `init_db`'s first line becomes `"""Create all nine store tables + indexes if absent (audit_log, review_store, conversation_store, conversation_summary, feedback, interaction_event, turn_log, document_version, document_snapshot).`

In `tests/conftest.py`, replace the `_clean_tables` fixture with:

```python
# Every store table init_db() creates. The truncate below names them all, and
# tests/test_db.py checks this list against the database: a new table missing
# here fails there instead of carrying rows from one test into the next.
STORE_TABLES = (
    "audit_log", "review_store", "conversation_store", "conversation_summary",
    "feedback", "interaction_event", "turn_log", "document_version", "document_snapshot",
)


@pytest.fixture(autouse=True)
def _clean_tables(_pg_container):
    with db.get_pool().connection() as conn:
        conn.execute(f"TRUNCATE {', '.join(STORE_TABLES)} RESTART IDENTITY")
    yield
```

- [ ] **Step 5: Write the version store**

Create `memory/document_store.py`:

```python
"""SP4: the document's text as the attorney's work changed it (spec 2026-10-15).

document_version holds each distinct text of a document once, keyed by
(document_id, sha256) — the same SHA-256 turn_log records — so a turn finds its
text and a document that goes A → B → A stores A once. document_snapshot (one
row per pane capture) and the reads behind feedback_report --changes live here
too.

Every write here holds contract text, so a failure logs the document id, the
hash, the size and the exception TYPE — never the text, and never the
exception's message: Postgres quotes offending input back in some of them.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from memory.db import get_pool
from memory.fingerprint import fingerprint, storable

logger = logging.getLogger(__name__)

TEXT_CAP = 1_000_000


def save_version(document_id: str, text: str, *, attorney_id: str, source: str) -> str:
    """Store this text of the document unless it is already stored; return its
    SHA-256 — "" for an empty text, which stores nothing. RAISES."""
    chars, sha = fingerprint(text)
    if not text:
        return ""
    with get_pool().connection() as conn:
        conn.execute(
            """INSERT INTO document_version
               (document_id, sha256, text, chars, truncated, first_seen, attorney_id, source)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (document_id, sha256) DO NOTHING""",
            (document_id, sha, storable(text[:TEXT_CAP]), chars, chars > TEXT_CAP,
             datetime.now(timezone.utc).isoformat(), attorney_id, source),
        )
    return sha


def save_version_quietly(document_id: str, text: str, *, attorney_id: str, source: str) -> None:
    """save_version that never raises — for the turn path, where the attorney
    already has their answer. Logs what identifies the text, never the text."""
    try:
        save_version(document_id, text, attorney_id=attorney_id, source=source)
    except Exception as e:  # noqa: BLE001 — telemetry's own failure (class 4)
        chars, sha = fingerprint(text)
        logger.warning(
            "document_version write failed (%s) — document %s, sha256 %s, %d chars; text not logged",
            type(e).__name__, document_id, sha, chars,
        )
```

- [ ] **Step 6: Run the tests to see them pass**

Run: `uv run pytest tests/test_document_store.py tests/test_db.py tests/test_turn_log.py -q` → all pass.
Run: `uv run pytest tests/ -q` → all pass (nothing else depended on the moved helpers).

- [ ] **Step 7: Commit**

```bash
git add memory/fingerprint.py memory/document_store.py memory/turn_log.py memory/db.py \
        tests/conftest.py tests/test_db.py tests/test_turn_log.py tests/test_document_store.py
git commit -m "feat(document-store): each distinct text of a document, under the hash turn_log records

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Each pane turn keeps the text it was sent

**Files:**
- Modify: `config.py`, `api/runtime_facts.py`, `api/turn_record.py`
- Test: `tests/test_turn_log_handlers.py`

**Interfaces:**
- Consumes: `memory.document_store.save_version_quietly(document_id, text, *, attorney_id, source)` (Task 1).
- Produces: `Settings.document_capture_enabled: bool = True` (env `DOCUMENT_CAPTURE_ENABLED`); `api.turn_record._record_quietly(row: dict, document: dict | None) -> None` — the one after-response task; `api.turn_record._turn_document(turn: dict) -> dict | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_turn_log_handlers.py`:

```python
def _versions() -> list[dict]:
    with get_pool().connection() as conn:
        cur = conn.execute(
            "SELECT document_id, sha256, text, source, attorney_id FROM document_version")
        names = [column.name for column in cur.description]
        return [dict(zip(names, values)) for values in cur.fetchall()]


def _word_turn(monkeypatch):
    _wire(monkeypatch, _Graph(invoke=_clean))
    return call_handler(
        q.submit_query,
        QueryRequest(request="who signs?", uploaded_text="MUTUAL NDA", document_uuid="doc-uuid-1"),
        user_id="atty-1", user_name="A. Tester",
    )


def test_a_word_turn_stores_the_text_it_was_sent_under_its_rows_hash(monkeypatch):
    """SP4: a turn's doc_sha256 finds its text. Mutation: drop the version write
    from finish_turn."""
    _word_turn(monkeypatch)
    (row,) = _rows()
    assert _versions() == [{"document_id": "doc-uuid-1", "sha256": row["doc_sha256"],
                            "text": "MUTUAL NDA", "source": "turn", "attorney_id": "atty-1"}]


def test_a_turn_without_a_document_uuid_stores_no_text(monkeypatch):
    """Chainlit sends no UUID (spec D5). Mutation: drop the document_id guard."""
    _submit(monkeypatch, **SUBMIT_EXITS["ok"])
    assert len(_rows()) == 1 and _versions() == []


def test_capture_switched_off_stores_no_text(monkeypatch):
    """Mutation: ignore document_capture_enabled in finish_turn."""
    monkeypatch.setenv("DOCUMENT_CAPTURE_ENABLED", "false")
    get_settings.cache_clear()
    _word_turn(monkeypatch)
    get_settings.cache_clear()
    assert len(_rows()) == 1 and _versions() == []


def test_a_failed_version_write_changes_nothing_the_attorney_sees(monkeypatch):
    """Quiet (D6): the attorney has their answer and the turn its row.
    Mutation: call save_version (which raises) instead of save_version_quietly."""
    def boom():
        raise RuntimeError("pool timeout")

    monkeypatch.setattr("memory.document_store.get_pool", boom)
    resp = _word_turn(monkeypatch)
    assert resp.status == "ok" and len(_rows()) == 1 and _versions() == []
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_turn_log_handlers.py -q -k "stores or switched or version_write"`
Expected: `test_a_word_turn_stores…` FAILS (`[] == [{…}]`); `test_capture_switched_off…` passes vacuously for now (nothing writes yet) — it bites once Step 3 lands.

- [ ] **Step 3: Add the setting**

In `config.py`, below the two `feedback_*` lines:

```python
    # SP4 — copies of the document as the attorney works on it (spec 2026-10-15)
    document_capture_enabled: bool = True       # False = no copies at all: /api/document-snapshot records nothing and tells the pane to stop, and turns store no text
```

In `api/runtime_facts.py`, add `"document_capture_enabled"` to `RECORDED` (e.g. after `"feedback_snapshot_max_chars",`).

Run: `uv run pytest tests/test_runtime_facts.py -q` → passes (it fails with `unclassified: ['document_capture_enabled']` if the `RECORDED` line is missing).

- [ ] **Step 4: Store the text after the response**

In `api/turn_record.py`, add `from memory.document_store import save_version_quietly` to the imports, then add these two functions above `finish_turn`:

```python
def _turn_document(turn: dict) -> dict | None:
    """SP4: the text a pane turn was sent, keyed by the request's document UUID.
    Chainlit sends no UUID, and resume and compaction carry no text."""
    if not (turn["document_id"] and turn["doc_text"] and get_settings().document_capture_enabled):
        return None
    return {"document_id": turn["document_id"], "text": turn["doc_text"],
            "attorney_id": turn["attorney_id"], "source": "turn"}


def _record_quietly(row: dict, document: dict | None) -> None:
    """Everything a turn leaves behind after the response: its turn_log row and,
    for a pane turn, its document's text. Neither can raise."""
    write_turn_quietly(row)
    if document is not None:
        save_version_quietly(**document)
```

In `finish_turn`, two edits. First, inside the existing `try`, add one line directly after the closing parenthesis of the `row = build_turn_row(...)` call (so a failure there is caught by the same `except` and logged the same way):

```python
        document = _turn_document(turn)
```

Second, replace the scheduling block at the end of `finish_turn`:

```python
    if background_tasks is not None:
        # Background tasks run in the request task's context, not this
        # thread's: carry this one so a failed write still logs [turn=<id>].
        background_tasks.add_task(contextvars.copy_context().run, write_turn_quietly, row)
    else:
        write_turn_quietly(row)
```

with:

```python
    if background_tasks is not None:
        # Background tasks run in the request task's context, not this
        # thread's: carry this one so a failed write still logs [turn=<id>].
        background_tasks.add_task(contextvars.copy_context().run, _record_quietly, row, document)
    else:
        _record_quietly(row, document)
```

(One task, not two: `test_a_submit_exit_schedules_exactly_one_write_and_does_not_make_it` pins that every exit schedules exactly one.)

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest tests/test_turn_log_handlers.py tests/test_runtime_facts.py -q` → all pass.
Run: `uv run pytest tests/ -q` → all pass.

- [ ] **Step 6: Commit**

```bash
git add config.py api/runtime_facts.py api/turn_record.py tests/test_turn_log_handlers.py
git commit -m "feat(turn-log): each pane turn keeps the text it was sent

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Each Apply keeps the text it put in

**Files:**
- Modify: `memory/db.py`, `memory/feedback_store.py`, `api/models.py`
- Test: `tests/test_feedback_store.py`, `tests/test_feedback_api.py`

**Interfaces:**
- Consumes: `memory.fingerprint.storable` (Task 1).
- Produces: column `interaction_event.applied JSONB`; `memory.feedback_store.APPLIED_TEXT_CAP = 20_000`; `cap_applied(applied: dict | None) -> dict | None` (keys kept: `action`, `target_text`, `new_text`, `proposed_text`, `position`); `record_events` reads `event["applied"]`; `record_event(..., applied: dict | None = None)`; `InteractionEvent.applied: dict | None`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_feedback_store.py`, replace the whole existing test `test_a_database_from_before_sp3_gains_the_client_columns` (its `@pytest.mark.parametrize("table, shipped", […])` decorator through its `finally:` block) with:

```python
def _columns_after_upgrade(table: str, shipped: tuple[str, ...]) -> set[str]:
    """Rebuild `table` as it shipped (every column TEXT), start up as the VM
    does, and return the columns it then has — restoring the real table after."""
    with get_pool().connection() as conn:
        conn.execute(f"DROP TABLE {table}")
        conn.execute(
            f"CREATE TABLE {table} (id BIGSERIAL PRIMARY KEY, "
            + ", ".join(f"{c} TEXT" for c in shipped) + ")"
        )
    try:
        init_db()
        with get_pool().connection() as conn:
            return {r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = %s AND column_name <> 'id'", (table,)
            ).fetchall()}
    finally:
        # Every other test needs the real column types back.
        with get_pool().connection() as conn:
            conn.execute(f"DROP TABLE {table}")
        init_db()


@pytest.mark.parametrize("table, shipped, added", [
    ("interaction_event", _EVENT_COLUMNS_2026_10_14, _CLIENT_COLUMNS | {"applied"}),
    ("feedback", _FEEDBACK_COLUMNS_2026_10_14, _CLIENT_COLUMNS),
])
def test_a_database_from_before_sp3_gains_the_client_columns(table, shipped, added):
    """The VM's tables exist, so CREATE TABLE IF NOT EXISTS never runs there
    again: a column added only inside the CREATE fails every write in
    production, quietly for events. Rebuild the table as it shipped, start up
    as the VM does, and require the new columns.

    Mutation: move one client column into the CREATE and delete its ALTER."""
    assert _columns_after_upgrade(table, shipped) == set(shipped) | added


# interaction_event as deployed on 2026-10-15 — after SP3, before SP4. Frozen:
# never edit — it stands for every database created before this change.
_EVENT_COLUMNS_2026_10_15 = _EVENT_COLUMNS_2026_10_14 + (
    "office_platform", "office_version", "pane_build", "backend_build")


def test_a_database_from_before_sp4_gains_the_applied_column():
    """Mutation: put `applied` in interaction_event's CREATE only."""
    assert _columns_after_upgrade("interaction_event", _EVENT_COLUMNS_2026_10_15) == \
        set(_EVENT_COLUMNS_2026_10_15) | {"applied"}


def _applied_column() -> list:
    with get_pool().connection() as conn:
        return [r[0] for r in conn.execute(
            "SELECT applied FROM interaction_event ORDER BY id").fetchall()]


def test_an_apply_keeps_the_full_text_it_put_in():
    """SP4 D7. Mutation: drop `applied` from _insert_event's INSERT."""
    applied = {"action": "replace", "target_text": "the Seller", "new_text": "Acme Ltd " * 40,
               "proposed_text": "Acme Limited"}
    fs.record_events([{"action": "edit_applied", "applied": applied}], attorney_id="atty-1")
    assert _applied_column() == [applied]


def test_the_applied_text_is_capped_and_unknown_keys_are_dropped():
    """A client-controlled payload gets a bound. Mutation: store it as sent."""
    huge = "x" * (fs.APPLIED_TEXT_CAP + 5)
    fs.record_events([{"action": "edit_applied",
                       "applied": {"action": "replace", "new_text": huge, "extra": "?"}}],
                     attorney_id="atty-1")
    (stored,) = _applied_column()
    assert len(stored["new_text"]) == fs.APPLIED_TEXT_CAP and "extra" not in stored


def test_an_event_without_applied_text_stores_null():
    fs.record_events([{"action": "edit_discarded"}], attorney_id="atty-1")
    assert _applied_column() == [None]
```

Append to `tests/test_feedback_api.py`:

```python
def test_events_carry_the_full_applied_text(client):
    """Mutation: drop `applied` from InteractionEvent — pydantic then ignores it."""
    c, _ = client
    applied = {"action": "insert", "target_text": "4. Term", "new_text": "4.1 Renewal is automatic.",
               "position": "after"}
    c.post("/api/events", json={"events": [
        {"surface": "chat", "action": "edit_applied", "applied": applied}]},
        headers={"X-User-ID": "atty-1"})
    with get_pool().connection() as conn:
        assert conn.execute("SELECT applied FROM interaction_event").fetchone()[0] == applied
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_feedback_store.py tests/test_feedback_api.py -q`
Expected: the upgrade tests FAIL (`applied` missing), `test_an_apply_keeps…` fails (`AttributeError`/`[None] == […]`), `APPLIED_TEXT_CAP` missing.

- [ ] **Step 3: Add the column**

In `memory/db.py`, right after `"ALTER TABLE interaction_event ADD COLUMN IF NOT EXISTS backend_build TEXT NOT NULL DEFAULT ''",`:

```python
    # The full text an Apply put into the document, and the model's own wording
    # when the attorney edited the card first (SP4) — NULL on every other action.
    "ALTER TABLE interaction_event ADD COLUMN IF NOT EXISTS applied JSONB",
```

- [ ] **Step 4: Store it**

In `memory/feedback_store.py`, add `from memory.fingerprint import storable` to the imports, and below `COUNTER_ACTIONS`:

```python
# What an Apply put into the document (SP4, spec 2026-10-15 D7): only these
# keys, each string capped; anything else a client sends is dropped.
_APPLIED_KEYS = ("action", "target_text", "new_text", "proposed_text", "position")
APPLIED_TEXT_CAP = 20_000


def cap_applied(applied: dict | None) -> dict | None:
    if not applied:
        return None
    return {key: storable(str(applied[key]))[:APPLIED_TEXT_CAP]
            for key in _APPLIED_KEYS if applied.get(key) is not None}
```

Change `_insert_event` to take `applied: dict | None` (after `backend_build`) and write it:

```python
    capped = cap_applied(applied)
    with get_pool().connection() as conn:
        conn.execute(
            """INSERT INTO interaction_event
               (timestamp, turn_id, session_id, document_id, attorney_id,
                surface, action, target_kind, target_ref, detail, request,
                office_platform, office_version, pane_build, backend_build, applied)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                datetime.now(timezone.utc).isoformat(),
                turn_id, session_id, document_id, attorney_id,
                surface, action, target_kind, target_ref, detail, request,
                *_client_values(client), backend_build,
                Jsonb(capped) if capped is not None else None,
            ),
        )
```

`record_event` gains `applied: dict | None = None` (last keyword) and passes `applied=applied`; `record_events` passes `applied=event.get("applied")`.

In `api/models.py`, `InteractionEvent` gains, after `request`:

```python
    applied: dict | None = Field(
        None,
        description="SP4: on edit_applied / redline_applied, what went into the document — "
        "action, target_text, new_text, and proposed_text when the attorney edited the "
        "model's wording first. Capped server-side.",
    )
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest tests/test_feedback_store.py tests/test_feedback_api.py -q` → all pass.
Run: `uv run pytest tests/ -q` → all pass.

- [ ] **Step 6: Commit**

```bash
git add memory/db.py memory/feedback_store.py api/models.py tests/test_feedback_store.py tests/test_feedback_api.py
git commit -m "feat(events): each Apply keeps the full text it put into the document

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `POST /api/document-snapshot`

**Files:**
- Create: `api/routes/document_snapshot.py`, `tests/test_document_snapshot_api.py`
- Modify: `memory/document_store.py`, `api/models.py`, `api/main.py`
- Test: `tests/test_document_store.py` (append)

**Interfaces:**
- Consumes: `save_version` (Task 1), `Settings.document_capture_enabled` (Task 2).
- Produces: `memory.document_store.TRACKED_CAP = 500`, `TRACKED_TEXT_CAP = 2_000`; `cap_tracked_changes(items: list[dict]) -> list[dict]`; `save_snapshot(*, attorney_id, session_id, document_id, moment, current_text, original_text, tracked_changes: list[dict], tracked_total: int, client: dict, backend_build: str) -> None` (RAISES); models `TrackedChangeItem`, `DocumentSnapshot`; route `POST /api/document-snapshot` → `{"status": "ok", "data": {"recorded": 0|1, "enabled": bool}}` (422 for an unknown `moment` or an empty `document_id`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_document_store.py`:

```python
def _snapshots() -> list[dict]:
    with get_pool().connection() as conn:
        cur = conn.execute("SELECT * FROM document_snapshot ORDER BY id")
        names = [column.name for column in cur.description]
        return [dict(zip(names, values)) for values in cur.fetchall()]


def _snap(**over):
    kw = dict(attorney_id="atty-1", session_id="s-1", document_id="doc-1", moment="idle",
              current_text="NDA with Acme Ltd", original_text="NDA with [__]",
              tracked_changes=[{"type": "Added", "author": "Jane Smith",
                                "date": "2026-10-16T09:00:00.000Z", "text": "Acme Ltd"}],
              tracked_total=1, client={"office_platform": "Mac"}, backend_build="b1")
    kw.update(over)
    ds.save_snapshot(**kw)


def test_a_snapshot_stores_both_views_and_points_at_them():
    _snap()
    (s,) = _snapshots()
    assert s["current_sha256"] == fingerprint("NDA with Acme Ltd")[1]
    assert s["original_sha256"] == fingerprint("NDA with [__]")[1]
    assert {v["text"] for v in _versions()} == {"NDA with Acme Ltd", "NDA with [__]"}
    assert (s["office_platform"], s["backend_build"]) == ("Mac", "b1")


def test_tracked_changes_are_capped_and_marked_cut():
    """Mutation: trust the pane's caps."""
    long = {"type": "Added", "author": "A", "date": "", "text": "x" * (ds.TRACKED_TEXT_CAP + 1)}
    _snap(tracked_changes=[long] + [{"type": "Deleted", "author": "B", "date": "", "text": "y"}]
          * ds.TRACKED_CAP, tracked_total=ds.TRACKED_CAP + 1)
    (s,) = _snapshots()
    assert len(s["tracked_changes"]) == ds.TRACKED_CAP and s["tracked_total"] == ds.TRACKED_CAP + 1
    assert len(s["tracked_changes"][0]["text"]) == ds.TRACKED_TEXT_CAP
    assert s["tracked_changes"][0]["cut"] is True and s["tracked_changes"][1]["cut"] is False


def test_a_total_below_what_arrived_is_raised_and_unreadable_stays_minus_one():
    _snap(tracked_total=0)
    _snap(tracked_changes=[], tracked_total=-1)
    first, second = _snapshots()
    assert first["tracked_total"] == 1
    assert second["tracked_total"] == -1 and second["tracked_changes"] == []


def test_a_lone_surrogate_or_a_nul_in_a_tracked_change_does_not_lose_the_capture():
    """Review Focus 1: JSONB refuses both. Mutation: store the items without storable()."""
    _snap(tracked_changes=[{"type": "Added", "author": "J\ud83d", "date": "", "text": "a\x00b"}])
    (s,) = _snapshots()
    assert (s["tracked_changes"][0]["author"], s["tracked_changes"][0]["text"]) == ("J?", "ab")
```

Create `tests/test_document_snapshot_api.py`:

```python
"""POST /api/document-snapshot (SP4): quiet like /api/events, identity and
client facts from the headers, never the body (spec 2026-10-15)."""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.main import app
from memory.db import get_pool


@pytest.fixture
def client(monkeypatch):
    fake = SimpleNamespace(document_capture_enabled=True, build_sha="test-build")
    monkeypatch.setattr("api.routes.document_snapshot.get_settings", lambda: fake)
    # raise_server_exceptions=False so a 500 would arrive as a response, not a raise.
    return TestClient(app, raise_server_exceptions=False), fake


def _body(**over):
    body = {"session_id": "s-1", "document_id": "doc-1", "moment": "idle",
            "current_text": "NDA with Acme Ltd", "original_text": "NDA with [__]",
            "tracked_changes": [{"type": "Added", "author": "Jane Smith",
                                 "date": "2026-10-16T09:00:00.000Z", "text": "Acme Ltd"}],
            "tracked_total": 1}
    body.update(over)
    return body


def _snapshots() -> list[dict]:
    with get_pool().connection() as conn:
        cur = conn.execute("SELECT * FROM document_snapshot ORDER BY id")
        names = [column.name for column in cur.description]
        return [dict(zip(names, values)) for values in cur.fetchall()]


def _versions() -> int:
    with get_pool().connection() as conn:
        return conn.execute("SELECT count(*) FROM document_version").fetchone()[0]


def test_a_capture_is_recorded_with_who_and_what_sent_it(client):
    c, _ = client
    r = c.post("/api/document-snapshot", json=_body(attorney_id="somebody-else"),
               headers={"X-User-ID": "atty-1", "X-Office-Platform": "Mac", "X-Pane-Build": "abc1234"})
    assert r.status_code == 200 and r.json()["data"] == {"recorded": 1, "enabled": True}
    (s,) = _snapshots()
    assert s["attorney_id"] == "atty-1"                     # the header, never the body
    assert (s["moment"], s["session_id"], s["document_id"]) == ("idle", "s-1", "doc-1")
    assert (s["office_platform"], s["pane_build"], s["backend_build"]) == (
        "Mac", "abc1234", "test-build")
    assert _versions() == 2


def test_a_store_failure_is_quiet_and_keeps_the_text_out_of_the_log(client, monkeypatch, caplog):
    """Review Focus 2. Mutation: let the exception through (a 500), or log str(e)."""
    c, _ = client

    def boom(**_kw):
        raise RuntimeError("invalid input syntax for type json: NDA with Acme Ltd")

    monkeypatch.setattr("api.routes.document_snapshot.save_snapshot", boom)
    with caplog.at_level(logging.WARNING, logger="api.routes.document_snapshot"):
        r = c.post("/api/document-snapshot", json=_body(), headers={"X-User-ID": "atty-1"})
    assert r.status_code == 200 and r.json()["data"] == {"recorded": 0, "enabled": True}
    assert "doc-1" in caplog.text and "RuntimeError" in caplog.text and "Acme" not in caplog.text


def test_switched_off_it_records_nothing_and_tells_the_pane(client):
    """Mutation: ignore the setting."""
    c, fake = client
    fake.document_capture_enabled = False
    r = c.post("/api/document-snapshot", json=_body(), headers={"X-User-ID": "atty-1"})
    assert r.json()["data"] == {"recorded": 0, "enabled": False}
    assert _snapshots() == [] and _versions() == 0


@pytest.mark.parametrize("bad", [{"moment": "hourly"}, {"document_id": ""}])
def test_a_capture_with_no_document_or_an_unknown_moment_is_refused(client, bad):
    c, _ = client
    r = c.post("/api/document-snapshot", json=_body(**bad), headers={"X-User-ID": "atty-1"})
    assert r.status_code == 422 and _snapshots() == []
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_document_store.py tests/test_document_snapshot_api.py -q`
Expected: `AttributeError: module 'memory.document_store' has no attribute 'save_snapshot'`; the API tests get 404 / fail to patch `api.routes.document_snapshot`.

- [ ] **Step 3: Store a capture**

In `memory/document_store.py`, add `from psycopg.types.json import Jsonb` to the imports, and append:

```python
TRACKED_CAP = 500
TRACKED_TEXT_CAP = 2_000
_TRACKED_FIELD_CAP = 200   # type, author, date are short; a client-controlled value still gets a bound


def cap_tracked_changes(items: list[dict]) -> list[dict]:
    """The first TRACKED_CAP changes, each one's text capped and marked `cut`,
    every string made storable (JSONB refuses a lone surrogate and a NUL)."""
    capped = []
    for item in items[:TRACKED_CAP]:
        text = str(item.get("text") or "")
        capped.append({
            "type": storable(str(item.get("type") or ""))[:_TRACKED_FIELD_CAP],
            "author": storable(str(item.get("author") or ""))[:_TRACKED_FIELD_CAP],
            "date": storable(str(item.get("date") or ""))[:_TRACKED_FIELD_CAP],
            "text": storable(text[:TRACKED_TEXT_CAP]),
            "cut": bool(item.get("cut")) or len(text) > TRACKED_TEXT_CAP,
        })
    return capped


def save_snapshot(*, attorney_id: str, session_id: str, document_id: str, moment: str,
                  current_text: str, original_text: str, tracked_changes: list[dict],
                  tracked_total: int, client: dict, backend_build: str) -> None:
    """Store both views of the document and the capture's row. RAISES — the
    route decides what a failure means (nothing, to the attorney).

    tracked_total is never trusted below what arrived; -1 (unreadable) keeps
    no list."""
    current_sha = save_version(document_id, current_text, attorney_id=attorney_id, source="pane")
    original_sha = save_version(document_id, original_text, attorney_id=attorney_id, source="pane")
    total = -1 if tracked_total < 0 else max(tracked_total, len(tracked_changes))
    items = [] if total < 0 else cap_tracked_changes(tracked_changes)
    with get_pool().connection() as conn:
        conn.execute(
            """INSERT INTO document_snapshot
               (timestamp, attorney_id, session_id, document_id, moment, current_sha256,
                original_sha256, tracked_changes, tracked_total,
                office_platform, office_version, pane_build, backend_build)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (datetime.now(timezone.utc).isoformat(), attorney_id, session_id, document_id,
             moment, current_sha, original_sha, Jsonb(items), total,
             client.get("office_platform", ""), client.get("office_version", ""),
             client.get("pane_build", ""), backend_build),
        )
```

- [ ] **Step 4: The request models**

In `api/models.py`, add `from typing import Literal` below `from __future__ import annotations`, and append:

```python
class TrackedChangeItem(BaseModel):
    """One tracked change as Word reports it (Word.TrackedChange)."""
    type: str = ""
    author: str = ""
    date: str = ""
    text: str = ""
    cut: bool = False


class DocumentSnapshot(BaseModel):
    """The pane's copy of the document at one moment (SP4, spec 2026-10-15).

    attorney_id is deliberately absent — identity comes from the auth seam, the
    same rule as FeedbackSubmission."""
    session_id: str = ""
    document_id: str = Field(..., min_length=1,
                             description="The assistant's document id; the pane never captures without one")
    moment: Literal["pane_open", "idle", "finalize"]
    current_text: str = Field("", description="getReviewedText(current): every tracked change accepted")
    original_text: str = Field("", description="getReviewedText(original): every tracked change rejected")
    tracked_changes: list[TrackedChangeItem] = Field(default_factory=list)
    tracked_total: int = Field(0, ge=-1, description="How many tracked changes Word reported "
                               "before any cap; -1 = they could not be read")
```

- [ ] **Step 5: The route**

Create `api/routes/document_snapshot.py`:

```python
"""POST /api/document-snapshot — the pane's copy of the document (SP4).

Quiet like /api/events: always 200, because a capture must never reach the
attorney — a failure records nothing and says so in `recorded`. Identity and
the client facts come from the request headers, never the body, so the SSO
cutover reaches this with no change here.
Spec: docs/superpowers/specs/2026-10-15-document-changes-design.md
"""
import logging
from dataclasses import asdict

from fastapi import APIRouter, Depends

from api.auth import resolve_user_id
from api.client_info import ClientInfo, resolve_client_info
from api.models import ApiResponse, DocumentSnapshot
from config import get_settings
from memory.document_store import save_snapshot

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


@router.post("/document-snapshot", response_model=ApiResponse)
def post_document_snapshot(
    body: DocumentSnapshot,
    user_id: str = Depends(resolve_user_id),
    client: ClientInfo = Depends(resolve_client_info),
) -> ApiResponse:
    settings = get_settings()
    if not settings.document_capture_enabled:
        return ApiResponse(status="ok", data={"recorded": 0, "enabled": False})
    try:
        save_snapshot(
            attorney_id=user_id, session_id=body.session_id, document_id=body.document_id,
            moment=body.moment, current_text=body.current_text, original_text=body.original_text,
            tracked_changes=[change.model_dump() for change in body.tracked_changes],
            tracked_total=body.tracked_total, client=asdict(client),
            backend_build=settings.build_sha,
        )
    except Exception as e:  # noqa: BLE001 — telemetry's own failure (class 4): nobody to tell
        # The type only: Postgres quotes offending input — contract text — in some messages.
        logger.warning("document snapshot not recorded (%s) — document %s, moment %s",
                       type(e).__name__, body.document_id, body.moment)
        return ApiResponse(status="ok", data={"recorded": 0, "enabled": True})
    return ApiResponse(status="ok", data={"recorded": 1, "enabled": True})
```

In `api/main.py`, add `from api.routes.document_snapshot import router as document_snapshot_router` after the `compact_router` import, and `app.include_router(document_snapshot_router)` after `app.include_router(compact_router)`.

- [ ] **Step 6: Run the tests to see them pass**

Run: `uv run pytest tests/test_document_store.py tests/test_document_snapshot_api.py -q` → all pass.
Run: `uv run pytest tests/ -q` → all pass.

- [ ] **Step 7: Commit**

```bash
git add memory/document_store.py api/models.py api/routes/document_snapshot.py api/main.py \
        tests/test_document_store.py tests/test_document_snapshot_api.py
git commit -m "feat(api): POST /api/document-snapshot stores the pane's copy of the document, quietly

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Comparing what was applied with what the document became

**Files:**
- Create: `scripts/document_changes.py`, `tests/test_document_changes.py`

**Interfaces:**
- Produces (all pure): `normalize(text) -> str`; `paragraphs(text) -> list[str]`; `edit_verdict(applied: dict, final_text: str) -> str`; `closest_paragraph_finder(text: str) -> Callable[[str], str]`; `changed_paragraphs(first: str, last: str, applied: list[dict]) -> list[dict]` (keys `mark` `+`/`-`, `text`, `label` `assistant`/`not suggested`); `build_document_changes(document_id: str, observations: list[dict], versions: dict[str, dict], applied: list[dict]) -> dict`; `format_document_changes(d: dict, *, full: bool = False) -> list[str]`; constants `KEPT`, `ALTERED`, `REVERTED`, `NO_LATER`, `VERDICTS`, `MOMENTS`, `PARAGRAPHS_SHOWN = 20`.
- Input shapes (Task 6 produces them from the database): an observation is `{"timestamp", "kind": "turn"|"pane_open"|"idle"|"finalize", "sha256", "attorney_id", "user_name", "tracked_changes": list|None, "tracked_total": int}`; `versions` maps sha256 → `{"text", "truncated"}`; an applied event is `{"timestamp", "turn_id", "applied": {"action", "target_text", "new_text", "proposed_text"?, "position"?}}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_document_changes.py`:

```python
"""scripts/document_changes.py — the verdicts and paragraph labels behind
feedback_report --changes (spec 2026-10-15, "Reading it"). Pure: no database."""
from __future__ import annotations

import time

import pytest

from scripts.document_changes import (
    ALTERED,
    KEPT,
    NO_LATER,
    REVERTED,
    build_document_changes,
    changed_paragraphs,
    edit_verdict,
    format_document_changes,
    paragraphs,
)

_REPLACE = {"action": "replace", "target_text": "the Seller", "new_text": "Acme Ltd"}
_INSERT = {"action": "insert", "target_text": "4. Term", "new_text": "4.1 Renewal is automatic."}
_DELETE = {"action": "delete", "target_text": "Non-solicitation for 24 months."}
_REDLINE = {"action": "redline", "target_text": "Cap: 1x fees\nExcluding fraud", "new_text": "Cap: 2x fees"}


@pytest.mark.parametrize("applied, final, verdict", [
    (_REPLACE, "Sold by Acme Ltd.", KEPT),
    (_REPLACE, "Sold by the Seller.", REVERTED),
    (_REPLACE, "Sold by Acme Limited.", ALTERED),
    ({**_REPLACE, "action": "replace_all", "target_text": "[__]"}, "Acme Ltd and [__]", KEPT),
    (_INSERT, "4. Term\r4.1 Renewal is automatic.", KEPT),
    (_INSERT, "4. Term\r4.1 Renewal needs notice.", ALTERED),
    (_DELETE, "Term. Fees.", KEPT),
    (_DELETE, "Non-solicitation for 24 months. Fees.", REVERTED),
    (_REDLINE, "Cap: 2x fees", KEPT),
    (_REDLINE, "Cap: 1x fees\rExcluding fraud", REVERTED),
    (_REDLINE, "Cap: 3x fees\rExcluding fraud", ALTERED),
    ({"action": "replace", "target_text": "Optional clause.", "new_text": ""}, "Fees.", KEPT),
])
def test_each_verdict(applied, final, verdict):
    """The spec's verdict table, a case per cell. Mutation: swap kept and reverted."""
    assert edit_verdict(applied, final) == verdict


def test_a_curled_quote_or_a_rewrapped_line_is_not_an_alteration():
    """Word curls straight quotes as you type. Mutation: compare without normalize()."""
    applied = {"action": "replace", "target_text": "Buyer", "new_text": 'the "Client" shall\npay'}
    assert edit_verdict(applied, "Then the “Client” shall pay within 30 days.") == KEPT


def test_paragraphs_split_on_words_carriage_returns():
    """Review Focus 3: Word separates paragraphs with \\r. Mutation: split("\\n")."""
    assert paragraphs("1. Parties\r2. Term\r\n3. Fees\n\n  \n4. Law") == [
        "1. Parties", "2. Term", "3. Fees", "4. Law"]


def test_empty_texts_have_no_paragraphs_and_change_nothing():
    """Review Focus 4."""
    assert paragraphs("") == [] and changed_paragraphs("", "", []) == []


def test_changed_paragraphs_are_labelled_side_by_side():
    """A clause typed where an applied deletion was is the attorney's, not the
    assistant's. Mutations: label everything one way; pair old with new."""
    first = "1. Parties: [__]\r2. Term: 1 year\r3. Non-solicit: 24 months"
    last = "1. Parties: Acme Ltd\r2. Term: 2 years\r4. Audit rights on request"
    applied = [{"action": "replace", "target_text": "[__]", "new_text": "Acme Ltd"},
               {"action": "delete", "target_text": "3. Non-solicit: 24 months"}]
    assert changed_paragraphs(first, last, applied) == [
        {"mark": "-", "text": "1. Parties: [__]", "label": "assistant"},
        {"mark": "-", "text": "2. Term: 1 year", "label": "not suggested"},
        {"mark": "-", "text": "3. Non-solicit: 24 months", "label": "assistant"},
        {"mark": "+", "text": "1. Parties: Acme Ltd", "label": "assistant"},
        {"mark": "+", "text": "2. Term: 2 years", "label": "not suggested"},
        {"mark": "+", "text": "4. Audit rights on request", "label": "not suggested"},
    ]


def _obs(ts, sha, kind="idle", **over):
    o = {"timestamp": ts, "kind": kind, "sha256": sha, "attorney_id": "atty-1", "user_name": "",
         "tracked_changes": None if kind == "turn" else [], "tracked_total": 0}
    o.update(over)
    return o


A, B = "a" * 64, "b" * 64
_VERSIONS = {A: {"text": "Fees: 10 days.", "truncated": False},
             B: {"text": "Fees: 30 days.", "truncated": False}}
_EDIT = {"action": "replace", "target_text": "10 days", "new_text": "30 days"}


def _applied(edit, ts="2026-10-16T09:01:00+00:00", turn_id="t-1"):
    return {"timestamp": ts, "turn_id": turn_id, "applied": edit}


def test_the_last_sighting_decides_even_when_its_text_was_seen_before():
    """A → B → A: the edit to B was undone, though A's version is the older one."""
    d = build_document_changes("doc-1", [
        _obs("2026-10-16T09:00:00+00:00", A, kind="turn"),
        _obs("2026-10-16T09:05:00+00:00", B),
        _obs("2026-10-16T09:09:00+00:00", A),
    ], _VERSIONS, [_applied(_EDIT)])
    assert [e["verdict"] for e in d["edits"]] == [REVERTED]


def test_an_edit_after_the_last_sighting_has_no_later_capture():
    """Mutation: judge against the last sighting regardless of time."""
    d = build_document_changes("doc-1", [_obs("2026-10-16T09:00:00+00:00", A, kind="turn")],
                               _VERSIONS, [_applied(_EDIT)])
    assert d["edits"][0]["verdict"] == NO_LATER


def test_sightings_without_a_stored_text_are_counted_not_compared():
    """Turns from before SP4 have a fingerprint and no text; an empty view has
    neither (Review Focus 4)."""
    d = build_document_changes("doc-1", [
        _obs("2026-10-16T08:00:00+00:00", "c" * 64, kind="turn"),
        _obs("2026-10-16T08:30:00+00:00", ""),
        _obs("2026-10-16T09:00:00+00:00", A, kind="turn"),
    ], _VERSIONS, [])
    assert d["first_stored"]["timestamp"] == "2026-10-16T09:00:00+00:00" and d["skipped"] == 2
    assert d["distinct"] == 2


def test_an_altered_edit_shows_the_closest_paragraph_now():
    versions = {
        A: {"text": "Liability cap: 1x fees.\rGoverning law: England.", "truncated": False},
        B: {"text": "Liability cap: the fees paid in the 6 months preceding the claim.\r"
                    "Governing law: England.", "truncated": False},
    }
    edit = {"action": "replace", "target_text": "1x fees",
            "new_text": "the fees paid in the 12 months preceding the claim"}
    d = build_document_changes("doc-1", [_obs("2026-10-16T09:00:00+00:00", A, kind="turn"),
                                         _obs("2026-10-16T09:05:00+00:00", B)],
                               versions, [_applied(edit, turn_id="")])
    assert d["edits"][0]["verdict"] == ALTERED
    assert d["edits"][0]["closest"] == \
        "Liability cap: the fees paid in the 6 months preceding the claim."


def test_the_block_reads_as_the_spec_shows_it():
    tracked = [{"type": "Added", "author": "Jane Smith", "text": "30"},
               {"type": "Deleted", "author": "Jane Smith", "text": "10"},
               {"type": "Added", "author": "Counterparty LLP", "text": "x"}]
    d = build_document_changes("doc-1", [
        _obs("2026-10-16T09:00:00+00:00", A, kind="turn", user_name="A. Tester"),
        _obs("2026-10-16T09:05:00+00:00", B, tracked_changes=tracked, tracked_total=3),
    ], _VERSIONS, [_applied({**_EDIT, "proposed_text": "20 days"}, turn_id="6aef53da-0000")])
    out = "\n".join(format_document_changes(d))
    assert "document doc-1  A. Tester (atty-1) · 2026-10-16 09:00 → 2026-10-16 09:05" in out
    assert ("observations 2 (1 turns · 0 pane_open · 1 idle · 0 finalize) · 2 distinct texts"
            in out)
    assert "first stored text 2026-10-16 09:00 (turn)" in out
    assert "applied edits 1: 1 kept · 0 altered · 0 reverted · 0 no later capture" in out
    assert (f'{"kept":<17}{"replace":<12}"10 days" → "30 days"   (edited before applying)'
            "   turn 6aef53da") in out
    assert ("tracked changes at the last capture: 3 — Jane Smith 2 (1 ins, 1 del) · "
            "Counterparty LLP 1 (1 ins)") in out
    assert ("paragraphs changed since the first text: 2 — 2 explained by an applied edit · "
            "0 not suggested") in out


def test_unreadable_or_absent_tracked_changes_say_so():
    turn_only = build_document_changes(
        "doc-1", [_obs("2026-10-16T09:00:00+00:00", A, kind="turn")], _VERSIONS, [])
    unreadable = build_document_changes(
        "doc-1", [_obs("2026-10-16T09:00:00+00:00", A, tracked_total=-1)], _VERSIONS, [])
    assert "tracked changes: no pane capture yet" in "\n".join(format_document_changes(turn_only))
    assert "tracked changes at the last capture: unreadable in that Word" in \
        "\n".join(format_document_changes(unreadable))


def test_excerpts_are_cut_unless_full():
    d = build_document_changes(
        "doc-1", [_obs("2026-10-16T09:00:00+00:00", A, kind="turn"),
                  _obs("2026-10-16T09:05:00+00:00", B)],
        _VERSIONS, [_applied({"action": "delete", "target_text": "x" * 100}, turn_id="")])
    short = "\n".join(format_document_changes(d))
    full = "\n".join(format_document_changes(d, full=True))
    assert "x" * 39 + "…" in short and "x" * 100 not in short and "x" * 100 in full


def test_a_long_negotiated_contract_reads_back_in_seconds():
    """Review Focus 5: 2,000 paragraphs, 100 applied edits, 60 reworded after.
    Mutation: rank every paragraph with difflib's ratio for the closest one."""
    paras = [f"{i}. The Supplier shall perform obligation number {i} with due care and skill."
             for i in range(2000)]
    last_paras = list(paras)
    edits = []
    for n, i in enumerate(range(0, 2000, 20)):
        new = f"{i}. The Supplier shall perform obligation number {i} within ten days."
        edits.append(_applied({"action": "replace", "target_text": paras[i], "new_text": new},
                              turn_id=""))
        last_paras[i] = new.replace("ten", "fifteen") if n < 60 else new
    versions = {A: {"text": "\r".join(paras), "truncated": False},
                B: {"text": "\r".join(last_paras), "truncated": False}}
    started = time.monotonic()
    d = build_document_changes("doc-1", [_obs("2026-10-16T09:00:00+00:00", A, kind="turn"),
                                         _obs("2026-10-16T09:05:00+00:00", B)], versions, edits)
    lines = format_document_changes(d)
    assert time.monotonic() - started < 5
    assert sum(e["verdict"] == ALTERED for e in d["edits"]) == 60
    assert "    … 180 more — --full lists them all" in lines
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_document_changes.py -q`
Expected: `ModuleNotFoundError: No module named 'scripts.document_changes'`.

- [ ] **Step 3: Write the module**

Create `scripts/document_changes.py`:

```python
"""Compare what the assistant applied with what the document became (SP4).

Pure functions — no database, no clock. feedback_report --changes fetches the
rows (memory/document_store.py) and prints what build_document_changes returns.
Spec: docs/superpowers/specs/2026-10-15-document-changes-design.md, "Reading it".

Verdicts are exact after normalisation (spec D8): no similarity score decides
anything. Shared words only pick the paragraph printed beside an `altered`
edit, for a person to judge.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from collections import Counter
from typing import Callable

# The pane's normalize.ts rules — curly quotes straightened, non-breaking spaces
# made plain — plus every whitespace run collapsed, so Word curling a quote or
# re-wrapping a line is not an alteration.
_STRAIGHTEN = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"', " ": " ", " ": " ",
})
_WHITESPACE = re.compile(r"\s+")

KEPT, ALTERED, REVERTED, NO_LATER = "kept", "altered", "reverted", "no later capture"
VERDICTS = (KEPT, ALTERED, REVERTED, NO_LATER)          # the order they are counted in
MOMENTS = ("pane_open", "idle", "finalize")
PARAGRAPHS_SHOWN = 20
_TRACKED_KIND = {"Added": "ins", "Deleted": "del", "Formatted": "fmt"}
_KIND_ORDER = ("ins", "del", "fmt", "other")


def normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFC", text or "").translate(_STRAIGHTEN)).strip()


def paragraphs(text: str) -> list[str]:
    """The non-empty paragraphs, normalised. splitlines() splits on Word's \\r
    as well as on \\n and \\r\\n."""
    return [p for p in (normalize(line) for line in (text or "").splitlines()) if p]


def _verdict(applied: dict, final: str) -> str:
    """edit_verdict against an already-normalised text."""
    action = applied.get("action", "")
    new = normalize(applied.get("new_text", ""))
    target = normalize(applied.get("target_text", ""))
    if action == "delete":
        if not target:
            return ALTERED
        return REVERTED if target in final else KEPT
    if action in ("replace", "replace_all") and not new:
        # A replacement with nothing: kept while the old wording stays gone.
        return REVERTED if target and target in final else KEPT
    if new and new in final:
        return KEPT
    if action in ("replace", "replace_all") and target and target in final:
        return REVERTED
    if action == "redline":
        anchors = [normalize(a) for a in applied.get("target_text", "").split("\n") if a.strip()]
        if anchors and all(a in final for a in anchors):
            return REVERTED
    return ALTERED


def edit_verdict(applied: dict, final_text: str) -> str:
    """kept / reverted / altered for one applied edit against a later text (the
    spec's verdict table). Whether there IS a later text is the caller's call."""
    return _verdict(applied, normalize(final_text))


def closest_paragraph_finder(text: str) -> Callable[[str], str]:
    """A function giving the paragraph of `text` sharing the most words with a
    needle (Jaccard over word sets) — printed beside an `altered` edit; it
    decides nothing. The word sets are built once per text, so a 2,000-paragraph
    contract with a hundred altered edits stays fast."""
    paras = paragraphs(text)
    words = [set(p.lower().split()) for p in paras]

    def closest(needle: str) -> str:
        mine = set(normalize(needle).lower().split())
        if not paras or not mine:
            return ""

        def overlap(i: int) -> float:
            union = len(mine | words[i])
            return len(mine & words[i]) / union if union else 0.0

        return paras[max(range(len(paras)), key=overlap)]

    return closest


def changed_paragraphs(first: str, last: str, applied: list[dict]) -> list[dict]:
    """Paragraphs only in the first text (-) or only in the last (+). A `+` is the
    assistant's when it holds an applied text; a `-` when it holds what an
    applied replacement, deletion or redline took out (an insert's target is
    only its anchor). Everything else is `not suggested`. Old and new are never
    paired into one line: a clause typed where an applied deletion was would
    inherit the deletion's label."""
    old, new = paragraphs(first), paragraphs(last)
    put_in = [t for t in (normalize(a.get("new_text", "")) for a in applied) if t]
    taken_out: list[str] = []
    for a in applied:
        if a.get("action") == "insert":
            continue
        target = a.get("target_text", "")
        lines = target.split("\n") if a.get("action") == "redline" else [target]
        taken_out += [t for t in (normalize(line) for line in lines) if t]

    out: list[dict] = []
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        for text in old[i1:i2]:
            out.append({"mark": "-", "text": text, "label": "assistant"
                        if any(t in text for t in taken_out) else "not suggested"})
        for text in new[j1:j2]:
            out.append({"mark": "+", "text": text, "label": "assistant"
                        if any(t in text for t in put_in) else "not suggested"})
    return out


def _by_author(changes: list[dict]) -> dict[str, Counter]:
    authors: dict[str, Counter] = {}
    for change in changes:
        kind = _TRACKED_KIND.get(change.get("type", ""), "other")
        authors.setdefault(change.get("author") or "(no author)", Counter())[kind] += 1
    return authors


def build_document_changes(document_id: str, observations: list[dict],
                           versions: dict[str, dict], applied: list[dict]) -> dict:
    """Everything --changes prints for one document.

    observations — every sighting of the text, oldest first: timestamp, kind
      ('turn' or a capture moment), sha256, attorney_id, user_name,
      tracked_changes, tracked_total (memory/document_store.observations).
    versions — sha256 → {text, truncated} (memory/document_store.versions).
    applied — Apply events with their text, oldest first: timestamp, turn_id,
      applied (memory/document_store.applied_events).
    """
    stored = [o for o in observations if o["sha256"] in versions]
    kinds = Counter(o["kind"] for o in observations)
    names = [o["user_name"] for o in observations if o.get("user_name")]
    result: dict = {
        "document_id": document_id,
        "attorneys": sorted({o["attorney_id"] for o in observations if o["attorney_id"]}),
        "name": names[-1] if names else "",
        "first": observations[0]["timestamp"] if observations else "",
        "last": observations[-1]["timestamp"] if observations else "",
        "counts": {kind: kinds.get(kind, 0) for kind in ("turn", *MOMENTS)},
        "distinct": len({o["sha256"] for o in observations if o["sha256"]}),
        "first_stored": None, "skipped": 0, "truncated": False,
        "edits": [], "tracked": None, "paragraphs": [],
    }
    final = ""
    closest: Callable[[str], str] = lambda _needle: ""  # noqa: E731
    if stored:
        first, last = stored[0], stored[-1]
        result["first_stored"] = {"timestamp": first["timestamp"], "kind": first["kind"]}
        result["skipped"] = next(i for i, o in enumerate(observations) if o is first)
        result["truncated"] = any(versions[o["sha256"]].get("truncated") for o in stored)
        final_text = versions[last["sha256"]]["text"]
        final = normalize(final_text)
        closest = closest_paragraph_finder(final_text)
        result["paragraphs"] = changed_paragraphs(
            versions[first["sha256"]]["text"], final_text, [e["applied"] for e in applied])
    for event in applied:
        a = event["applied"]
        if stored and stored[-1]["timestamp"] > event["timestamp"]:
            verdict = _verdict(a, final)
            near = closest(a.get("new_text", "")) if verdict == ALTERED else ""
        else:
            verdict, near = NO_LATER, ""
        result["edits"].append({
            "verdict": verdict, "action": a.get("action", ""),
            "target_text": a.get("target_text", ""), "new_text": a.get("new_text", ""),
            "edited": bool(a.get("proposed_text")), "turn_id": event.get("turn_id", ""),
            "closest": near,
        })
    captures = [o for o in observations if o["kind"] != "turn"]
    if captures:
        latest = captures[-1]
        changes = latest["tracked_changes"] or []
        result["tracked"] = {"total": latest["tracked_total"], "listed": len(changes),
                             "by_author": _by_author(changes)}
    return result


def _head(text: str, width: int, full: bool) -> str:
    return text if full or len(text) <= width else text[: width - 1] + "…"


def _tail(text: str, width: int, full: bool) -> str:
    return text if full or len(text) <= width else "…" + text[-(width - 1):]


def _minute(timestamp: str) -> str:
    return timestamp[:16].replace("T", " ")


def format_document_changes(d: dict, *, full: bool = False) -> list[str]:
    """One document's block, as feedback_report --changes prints it."""
    who = ", ".join(d["attorneys"]) or "—"
    if d["name"]:
        who = f"{d['name']} ({who})"
    c = d["counts"]
    lines = [
        f"document {d['document_id']}  {who} · {_minute(d['first'])} → {_minute(d['last'])}",
        f"  observations {sum(c.values())} ({c['turn']} turns · {c['pane_open']} pane_open · "
        f"{c['idle']} idle · {c['finalize']} finalize) · {d['distinct']} distinct texts",
    ]
    if d["first_stored"] is None:
        lines.append("  no stored text yet — every observation has only a fingerprint")
        return lines
    first, skipped = d["first_stored"], d["skipped"]
    note = (f" — {skipped} earlier observation{' has' if skipped == 1 else 's have'} "
            "only a fingerprint" if skipped else "")
    lines.append(f"  first stored text {_minute(first['timestamp'])} ({first['kind']}){note}")
    if d["truncated"]:
        lines.append("  ⚠ a stored text was cut at 1,000,000 characters — "
                     "edits past the cut read as altered")

    tally = Counter(e["verdict"] for e in d["edits"])
    lines.append(f"  applied edits {len(d['edits'])}: "
                 + " · ".join(f"{tally.get(v, 0)} {v}" for v in VERDICTS))
    for e in d["edits"]:
        what = f"\"{_head(e['target_text'], 40, full)}\""
        if e["action"] != "delete":
            what += f" → \"{_tail(e['new_text'], 40, full)}\""
        edited = "   (edited before applying)" if e["edited"] else ""
        turn = f"   turn {e['turn_id'][:8]}" if e["turn_id"] else ""
        lines.append(f"    {e['verdict']:<17}{e['action']:<12}{what}{edited}{turn}")
        if e["closest"]:
            lines.append(f"{'':<21}closest now: \"{_head(e['closest'], 70, full)}\"")

    t = d["tracked"]
    if t is None:
        lines.append("  tracked changes: no pane capture yet")
    elif t["total"] < 0:
        lines.append("  tracked changes at the last capture: unreadable in that Word")
    else:
        authors = sorted(t["by_author"].items(), key=lambda item: (-sum(item[1].values()), item[0]))
        parts = [f"{author} {sum(kinds.values())} ("
                 + ", ".join(f"{kinds[k]} {k}" for k in _KIND_ORDER if kinds.get(k)) + ")"
                 for author, kinds in authors]
        listed = f" (first {t['listed']} listed)" if t["listed"] < t["total"] else ""
        lines.append(f"  tracked changes at the last capture: {t['total']}{listed}"
                     + (" — " + " · ".join(parts) if parts else ""))

    p = d["paragraphs"]
    mine = sum(1 for x in p if x["label"] == "assistant")
    lines.append(f"  paragraphs changed since the first text: {len(p)} — "
                 f"{mine} explained by an applied edit · {len(p) - mine} not suggested")
    shown = p if full else p[:PARAGRAPHS_SHOWN]
    for x in shown:
        lines.append(f"    {x['mark']} \"{_head(x['text'], 70, full)}\"   {x['label']}")
    if len(shown) < len(p):
        lines.append(f"    … {len(p) - len(shown)} more — --full lists them all")
    return lines
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_document_changes.py -q` → all pass (the long-contract test well under 5 s).

- [ ] **Step 5: Commit**

```bash
git add scripts/document_changes.py tests/test_document_changes.py
git commit -m "feat(report): verdicts and changed paragraphs for what was applied vs what stayed

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `feedback_report --changes`, and captures in `--turns`

**Files:**
- Modify: `memory/document_store.py` (reads), `scripts/feedback_report.py`
- Create: `tests/test_document_report.py`

**Interfaces:**
- Consumes: Task 5's `build_document_changes`, `format_document_changes`; Task 4's tables.
- Produces: `document_store.documents_with_copies(*, since="", attorney_id="", document_id="", limit=50) -> list[str]`; `observations(document_id) -> list[dict]` (Task 5's observation shape plus `turn_id`, `session_id`); `versions(document_id) -> dict[str, dict]`; `applied_events(document_id) -> list[dict]`; `captures_for_sessions(session_ids: list[str]) -> list[dict]` (keys `timestamp`, `session_id`, `document_id`, `moment`, `current_sha256`, `tracked_total`); `feedback_report.print_changes(blocks, *, full)`, `format_capture(c: dict, previous_sha: str | None) -> str`, `print_turns(rows, limit, pane_events=(), captures=())`; CLI flags `--changes`, `--full`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_document_report.py`:

```python
"""feedback_report --changes and the capture lines in --turns (SP4): a document's
story read back from turn_log, document_version, document_snapshot and the
applied text on interaction_event (spec 2026-10-15)."""
from __future__ import annotations

import sys

from psycopg.types.json import Jsonb

import memory.document_store as ds
from memory.db import get_pool
from memory.fingerprint import fingerprint
from memory.turn_log import build_turn_row, write_turn
from scripts.feedback_report import format_capture, main


def _turn(turn_id, arrived_at, doc_text):
    return build_turn_row(
        kind="query", turn_id=turn_id, trace_id="", arrived_at=arrived_at, duration_ms=1000,
        attorney_id="atty-1", user_name="A. Tester", session_id="s-1", document_id="doc-1",
        task_type="research", request="who signs?", doc_text=doc_text, status="ok",
        outcome="ok", degradations=[], api_error="", awaiting_review=False, facts={},
        result=None, compaction=None, client={}, backend_build="dev", runtime={},
    )


def _capture(ts, moment, current_text, *, session_id="s-1", document_id="doc-1",
             tracked=(), total=None):
    """A capture at a chosen time — save_snapshot always stamps now()."""
    sha = ds.save_version(document_id, current_text, attorney_id="atty-1", source="pane")
    with get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO document_snapshot (timestamp, attorney_id, session_id, document_id, "
            "moment, current_sha256, original_sha256, tracked_changes, tracked_total) "
            "VALUES (%s, 'atty-1', %s, %s, %s, %s, '', %s, %s)",
            (ts, session_id, document_id, moment, sha, Jsonb(list(tracked)),
             len(tracked) if total is None else total),
        )


def _applied(ts, applied, *, document_id="doc-1"):
    """An Apply at a chosen time, with its text — record_events stamps now()."""
    with get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO interaction_event (timestamp, turn_id, session_id, document_id, "
            "attorney_id, surface, action, applied) "
            "VALUES (%s, 't-1', 's-1', %s, 'atty-1', 'chat', 'edit_applied', %s)",
            (ts, document_id, Jsonb(applied)),
        )


_FIRST = "MUTUAL NDA\rParties: [__]\rTerm: 1 year"
_LAST = "MUTUAL NDA\rParties: Acme Ltd\rTerm: 2 years"


def _story():
    write_turn(_turn("t-1", "2026-10-16T09:00:00+00:00", _FIRST))
    ds.save_version("doc-1", _FIRST, attorney_id="atty-1", source="turn")
    _applied("2026-10-16T09:01:00+00:00",
             {"action": "replace", "target_text": "[__]", "new_text": "Acme Ltd"})
    _capture("2026-10-16T09:05:00+00:00", "idle", _LAST, tracked=[
        {"type": "Added", "author": "Jane Smith", "date": "", "text": "Acme Ltd"},
        {"type": "Deleted", "author": "Jane Smith", "date": "", "text": "[__]"}])


def test_observations_keep_every_sighting_in_time_order():
    """A → B → A: two versions, three sightings, the last of them A.
    Mutation: order by document_version.first_seen, or read versions instead."""
    write_turn(_turn("t-1", "2026-10-16T09:00:00+00:00", "Fees: 10 days."))
    ds.save_version("doc-1", "Fees: 10 days.", attorney_id="atty-1", source="turn")
    _capture("2026-10-16T09:05:00+00:00", "idle", "Fees: 30 days.")
    _capture("2026-10-16T09:09:00+00:00", "idle", "Fees: 10 days.")
    seen = ds.observations("doc-1")
    assert [o["kind"] for o in seen] == ["turn", "idle", "idle"]
    assert seen[-1]["sha256"] == seen[0]["sha256"] == fingerprint("Fees: 10 days.")[1]
    assert len(ds.versions("doc-1")) == 2


def test_changes_tells_one_documents_story(monkeypatch, capsys):
    _story()
    monkeypatch.setattr(sys, "argv", ["feedback_report", "--changes", "--document", "doc-1"])
    main()
    out = capsys.readouterr().out
    assert "=== Document changes (1 document) ===" in out
    assert "document doc-1  A. Tester (atty-1) · 2026-10-16 09:00 → 2026-10-16 09:05" in out
    assert "applied edits 1: 1 kept · 0 altered · 0 reverted · 0 no later capture" in out
    assert "tracked changes at the last capture: 2 — Jane Smith 2 (1 ins, 1 del)" in out
    assert '+ "Term: 2 years"   not suggested' in out


def test_changes_lists_only_the_documents_in_scope(monkeypatch, capsys):
    _story()
    _capture("2026-10-16T10:00:00+00:00", "pane_open", "OTHER CONTRACT",
             document_id="doc-2", session_id="s-2")
    monkeypatch.setattr(sys, "argv", ["feedback_report", "--changes", "--document", "doc-2"])
    main()
    out = capsys.readouterr().out
    assert "document doc-2" in out and "document doc-1" not in out


def test_the_timeline_shows_each_capture_and_whether_the_text_changed(monkeypatch, capsys):
    """Mutation: compare each capture with the previous capture only, ignoring turns."""
    write_turn(_turn("t-1", "2026-10-16T09:00:00+00:00", _FIRST))
    _capture("2026-10-16T09:01:00+00:00", "idle", _FIRST)
    _capture("2026-10-16T09:02:00+00:00", "idle", _LAST, total=3)
    _capture("2026-10-16T09:03:00+00:00", "finalize", _LAST, session_id="s-other")
    monkeypatch.setattr(sys, "argv", ["feedback_report", "--turns"])
    main()
    out = capsys.readouterr().out
    assert out.index("turn: t-1") < out.index("· capture idle: text unchanged, 0 tracked changes")
    assert "· capture idle: text changed, 3 tracked changes" in out
    assert "capture finalize" not in out             # another session is not on this page
    assert "=== Turns (1) ===" in out                # captures are not counted as turns


def test_a_capture_line_says_what_it_can():
    c = {"timestamp": "2026-10-16T09:02:00+00:00", "moment": "pane_open",
         "current_sha256": "a", "tracked_total": -1}
    assert format_capture(c, None) == (
        "  2026-10-16T09:02:00  · capture pane_open: first text on this page, "
        "tracked changes unreadable")
    assert format_capture({**c, "tracked_total": 1}, "a").endswith(
        "text unchanged, 1 tracked change")
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_document_report.py -q`
Expected: `ImportError: cannot import name 'format_capture'`.

- [ ] **Step 3: The reads**

Append to `memory/document_store.py`:

```python
def documents_with_copies(*, since: str = "", attorney_id: str = "", document_id: str = "",
                          limit: int = 50) -> list[str]:
    """The documents with SP4 data in scope — at least one stored text or
    capture — the newest `limit` by last activity, oldest of them first."""
    clauses: list[str] = []
    params: list = []
    if since:
        clauses.append("ts >= %s")
        params.append(since)
    if attorney_id:
        clauses.append("attorney_id = %s")
        params.append(attorney_id)
    if document_id:
        clauses.append("document_id = %s")
        params.append(document_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT document_id FROM (
            SELECT document_id, max(ts) AS last FROM (
                SELECT document_id, attorney_id, first_seen AS ts FROM document_version
                UNION ALL
                SELECT document_id, attorney_id, timestamp AS ts FROM document_snapshot
            ) seen {where}
            GROUP BY document_id ORDER BY last DESC LIMIT %s
        ) newest ORDER BY last
    """
    with get_pool().connection() as conn:
        return [row[0] for row in conn.execute(sql, [*params, limit]).fetchall()]


def observations(document_id: str) -> list[dict]:
    """Every sighting of the document's text — its turns and its captures —
    oldest first. Sightings, not versions, give the order: a document that goes
    A → B → A has two versions and three sightings, the last of them A."""
    sql = """
        SELECT timestamp, 'turn' AS kind, doc_sha256 AS sha256, turn_id, session_id,
               attorney_id, user_name, NULL::jsonb AS tracked_changes, 0 AS tracked_total
          FROM turn_log WHERE document_id = %s AND doc_sha256 <> ''
        UNION ALL
        SELECT timestamp, moment, current_sha256, '', session_id,
               attorney_id, '', tracked_changes, tracked_total
          FROM document_snapshot WHERE document_id = %s
        ORDER BY timestamp
    """
    with get_pool().connection() as conn:
        cur = conn.execute(sql, (document_id, document_id))
        names = [column.name for column in cur.description]
        return [dict(zip(names, values)) for values in cur.fetchall()]


def versions(document_id: str) -> dict[str, dict]:
    """sha256 → the stored text of each version of the document."""
    with get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT sha256, text, chars, truncated, first_seen, source "
            "FROM document_version WHERE document_id = %s", (document_id,)).fetchall()
    return {r[0]: {"text": r[1], "chars": r[2], "truncated": r[3], "first_seen": r[4],
                   "source": r[5]} for r in rows}


def applied_events(document_id: str) -> list[dict]:
    """The document's Apply events that carry their text, oldest first."""
    with get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT timestamp, turn_id, action, applied FROM interaction_event "
            "WHERE document_id = %s AND applied IS NOT NULL ORDER BY timestamp, id",
            (document_id,)).fetchall()
    return [{"timestamp": r[0], "turn_id": r[1], "action": r[2], "applied": r[3]} for r in rows]


def captures_for_sessions(session_ids: list[str]) -> list[dict]:
    """The captures made in these pane sessions, oldest first — for --turns."""
    with get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT timestamp, session_id, document_id, moment, current_sha256, tracked_total "
            "FROM document_snapshot WHERE session_id = ANY(%s) ORDER BY timestamp, id",
            (list(session_ids),)).fetchall()
    keys = ("timestamp", "session_id", "document_id", "moment", "current_sha256", "tracked_total")
    return [dict(zip(keys, r)) for r in rows]
```

- [ ] **Step 4: The report**

In `scripts/feedback_report.py`:

1. Docstring usage block — add a line: `    uv run python -m scripts.feedback_report --changes --document <id>`.
2. Imports — add `from memory import document_store` and `from scripts.document_changes import build_document_changes, format_document_changes`.
3. `_parse_args` — add after `--activity`:

```python
    p.add_argument("--changes", action="store_true",
                   help="print, per document, what became of each applied edit and what else "
                        "changed (SP4)")
    p.add_argument("--full", action="store_true",
                   help="with --changes: whole texts and every changed paragraph, not excerpts")
```

4. Add, after `format_pane_event`:

```python
def format_capture(c: dict, previous_sha: str | None) -> str:
    """A pane capture (SP4) as one timeline line. `previous_sha` is the text last
    seen of the same document on this page; None if none was."""
    if previous_sha is None:
        change = "first text on this page"
    elif c["current_sha256"] == previous_sha:
        change = "text unchanged"
    else:
        change = "text changed"
    total = c["tracked_total"]
    tracked = ("tracked changes unreadable" if total < 0
               else f"{total} tracked change{'' if total == 1 else 's'}")
    return f"  {c['timestamp'][:19]}  · capture {c['moment']}: {change}, {tracked}"


def print_changes(blocks: list[dict], *, full: bool) -> None:
    n = len(blocks)
    print(f"=== Document changes ({n} document{'' if n == 1 else 's'}) ===")
    print("  Per document: what became of each applied edit, the tracked changes at the")
    print("  last capture, and the paragraphs changed since the first stored text.")
    print("  How to read it: docs/feedback-loop.md.")
    print()
    if not blocks:
        print("  (none yet)\n")
        return
    for d in blocks:
        print("\n".join(format_document_changes(d, full=full)))
        print()
```

5. Replace `print_turns` with this version (same header and cut logic; captures join the timeline):

```python
def print_turns(rows: list[dict], limit: int, pane_events: list[dict] = (),
                captures: list[dict] = ()) -> None:
    """`rows` is recent_turns(limit=limit + 1): the one extra row, when it exists,
    is the proof that older turns were cut off. It is the oldest written (lowest
    id), not printed — a page of exactly `limit` turns cut nothing and says so.
    `pane_events` (no turn, same sessions) and `captures` (SP4, same sessions)
    are printed between the turns by time; on a cut page only those from the
    first shown turn on. A capture says whether the text changed since the
    document was last seen on this page. The unprinted row still serves as the
    first shown turn's predecessor for change markers."""
    cut = len(rows) > limit
    before_page = None
    if cut:
        oldest = min(row["id"] for row in rows)
        before_page = next(row for row in rows if row["id"] == oldest)
        rows = [row for row in rows if row["id"] != oldest]
    print(f"=== Turns ({len(rows)}) ===")
    print("  One entry per request to /api/query, its resume or /api/compact —")
    print("  failures included — oldest first, with what the attorney did in the")
    print("  pane in between. How to read it: docs/feedback-loop.md.")
    # A cut page has lost the START of the story, and fifty rows read like the whole of it.
    if cut:
        print(f"  Only the newest {limit} turns are shown — raise --limit for more.")
    print()
    if not rows:
        print("  (none yet)\n")
        return
    start = rows[0]["timestamp"] if cut else ""
    entries = [(r["timestamp"], 1, "turn", (prev, r)) for prev, r in zip([before_page, *rows], rows)]
    entries += [(e["timestamp"], 0, "pane", e) for e in pane_events if e["timestamp"] >= start]
    entries += [(c["timestamp"], 0, "capture", c) for c in captures if c["timestamp"] >= start]
    last_seen: dict[str, str] = {}
    for _, _, kind, item in sorted(entries, key=lambda x: (x[0], x[1])):
        if kind == "turn":
            prev, row = item
            print("\n".join(format_turn(row, prev)))
            print()
            if row["doc_sha256"]:
                last_seen[row["document_id"]] = row["doc_sha256"]
        elif kind == "pane":
            print(format_pane_event(item))
        else:
            print(format_capture(item, last_seen.get(item["document_id"])))
            last_seen[item["document_id"]] = item["current_sha256"]
```

6. In `main()`, the `--turns` branch passes the captures:

```python
    if args.turns:
        rows = recent_turns(since=since, attorney_id=args.attorney,
                            document_id=args.document, limit=args.limit + 1)
        sessions = sorted({r["session_id"] for r in rows if r["session_id"]})
        events = pane_events(session_ids=sessions, without_turn=True) if sessions else []
        captures = document_store.captures_for_sessions(sessions) if sessions else []
        print_turns(rows, args.limit, events, captures)
        return
    if args.changes:
        ids = document_store.documents_with_copies(
            since=since, attorney_id=args.attorney, document_id=args.document, limit=args.limit)
        print_changes([
            build_document_changes(doc, document_store.observations(doc),
                                   document_store.versions(doc),
                                   document_store.applied_events(doc))
            for doc in ids
        ], full=args.full)
        return
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest tests/test_document_report.py tests/test_turn_report.py tests/test_activity_report.py -q` → all pass (the existing `--turns` tests still pass: `print_turns(rows, limit, events)` keeps its first three parameters).
Run: `uv run pytest tests/ -q` → all pass.

- [ ] **Step 6: Commit**

```bash
git add memory/document_store.py scripts/feedback_report.py tests/test_document_report.py
git commit -m "feat(report): feedback_report --changes, and pane captures in the --turns timeline

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Deleting a document's copies on request

**Files:**
- Modify: `memory/document_store.py`
- Create: `scripts/delete_document_data.py`, `tests/test_delete_document_data.py`

**Interfaces:**
- Consumes: Task 4's `save_snapshot`, Task 3's `record_events(..., applied)`.
- Produces: `document_store.delete_document_data(*, document_id: str = "", attorney_id: str = "", dry_run: bool = True) -> dict[str, int]` (keys `document_version`, `document_snapshot`, `interaction_event.applied`; `ValueError` unless exactly one scope); `document_store.untouched_counts(*, document_id="", attorney_id="") -> dict[str, int | None]`; `scripts.delete_document_data.main(argv: list[str] | None = None) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_delete_document_data.py`:

```python
"""scripts/delete_document_data.py (SP4, spec D9): one document's or one
attorney's copies go, everything else stays, and it says what it does not touch."""
from __future__ import annotations

import pytest

import memory.document_store as ds
from memory.db import get_pool
from memory.feedback_store import record_events
from scripts.delete_document_data import main


def _seed():
    for doc, atty in (("doc-1", "atty-1"), ("doc-2", "atty-2")):
        ds.save_snapshot(attorney_id=atty, session_id="s", document_id=doc, moment="idle",
                         current_text=f"{doc} now", original_text=f"{doc} before",
                         tracked_changes=[], tracked_total=0, client={}, backend_build="dev")
        record_events([{"document_id": doc, "action": "edit_applied",
                        "applied": {"action": "replace", "target_text": "a", "new_text": "b"}}],
                      attorney_id=atty)


def _count(sql: str, *params) -> int:
    with get_pool().connection() as conn:
        return conn.execute(sql, params).fetchone()[0]


def _left(doc: str) -> tuple[int, int, int, int]:
    """(versions, snapshots, events with applied text, events) of one document."""
    return (
        _count("SELECT count(*) FROM document_version WHERE document_id = %s", doc),
        _count("SELECT count(*) FROM document_snapshot WHERE document_id = %s", doc),
        _count("SELECT count(*) FROM interaction_event WHERE document_id = %s "
               "AND applied IS NOT NULL", doc),
        _count("SELECT count(*) FROM interaction_event WHERE document_id = %s", doc),
    )


def test_a_dry_run_counts_and_deletes_nothing():
    """Mutation: delete when dry_run is True."""
    _seed()
    assert ds.delete_document_data(document_id="doc-1") == {
        "document_version": 2, "document_snapshot": 1, "interaction_event.applied": 1}
    assert _left("doc-1") == (2, 1, 1, 1)


def test_deleting_one_document_keeps_its_events_and_every_other_document():
    """The Apply still happened — only its text goes.
    Mutations: ignore the filter; delete the event instead of clearing `applied`."""
    _seed()
    ds.delete_document_data(document_id="doc-1", dry_run=False)
    assert _left("doc-1") == (0, 0, 0, 1)
    assert _left("doc-2") == (2, 1, 1, 1)


def test_deleting_by_attorney():
    _seed()
    ds.delete_document_data(attorney_id="atty-2", dry_run=False)
    assert _left("doc-2") == (0, 0, 0, 1) and _left("doc-1") == (2, 1, 1, 1)


@pytest.mark.parametrize("scope", [{}, {"document_id": "doc-1", "attorney_id": "atty-1"}])
def test_exactly_one_scope(scope):
    with pytest.raises(ValueError):
        ds.delete_document_data(**scope)


def test_the_command_counts_by_default_and_names_what_it_leaves(capsys):
    _seed()
    main(["--document", "doc-1"])
    out = capsys.readouterr().out
    assert "Would delete (dry run — add --yes to delete) for document doc-1:" in out
    assert "Not touched" in out and "Phoenix" in out and "nightly" in out
    assert _left("doc-1") == (2, 1, 1, 1)


def test_the_command_deletes_with_yes(capsys):
    _seed()
    main(["--attorney", "atty-1", "--yes"])
    out = capsys.readouterr().out
    assert "Deleted for attorney atty-1:" in out
    assert "review_store" in out and "n/a (keyed by document only)" in out
    assert _left("doc-1") == (0, 0, 0, 1)
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_delete_document_data.py -q`
Expected: `ModuleNotFoundError: No module named 'scripts.delete_document_data'`.

- [ ] **Step 3: The store side**

Append to `memory/document_store.py`:

```python
def _scope(document_id: str, attorney_id: str) -> tuple[str, str]:
    """The one column to delete by — never built from input, only these two."""
    if bool(document_id) == bool(attorney_id):
        raise ValueError("give exactly one of document_id / attorney_id")
    return ("document_id", document_id) if document_id else ("attorney_id", attorney_id)


def delete_document_data(*, document_id: str = "", attorney_id: str = "",
                         dry_run: bool = True) -> dict[str, int]:
    """SP4's copies for one document or one attorney: counted, and deleted
    unless dry_run. `applied` is set to NULL rather than the event deleted —
    the Apply still happened; only its text goes. A version belongs to whoever
    first sent it."""
    column, value = _scope(document_id, attorney_id)
    counts_sql = {
        "document_version": f"SELECT count(*) FROM document_version WHERE {column} = %s",
        "document_snapshot": f"SELECT count(*) FROM document_snapshot WHERE {column} = %s",
        "interaction_event.applied":
            f"SELECT count(*) FROM interaction_event WHERE {column} = %s AND applied IS NOT NULL",
    }
    with get_pool().connection() as conn:
        counts = {name: conn.execute(sql, (value,)).fetchone()[0]
                  for name, sql in counts_sql.items()}
        if not dry_run:
            with conn.transaction():
                conn.execute(f"DELETE FROM document_version WHERE {column} = %s", (value,))
                conn.execute(f"DELETE FROM document_snapshot WHERE {column} = %s", (value,))
                conn.execute(f"UPDATE interaction_event SET applied = NULL "
                             f"WHERE {column} = %s AND applied IS NOT NULL", (value,))
    return counts


def untouched_counts(*, document_id: str = "", attorney_id: str = "") -> dict[str, int | None]:
    """What delete_document_data leaves for the same scope — it is not a full
    erasure. None: that table cannot be scoped this way (reviews are keyed by
    document only)."""
    column, value = _scope(document_id, attorney_id)
    out: dict[str, int | None] = {}
    with get_pool().connection() as conn:
        for table in ("review_store", "conversation_store", "conversation_summary",
                      "feedback", "turn_log"):
            if table == "review_store" and column == "attorney_id":
                out[table] = None
                continue
            out[table] = conn.execute(
                f"SELECT count(*) FROM {table} WHERE {column} = %s", (value,)).fetchone()[0]
    return out
```

- [ ] **Step 4: The command**

Create `scripts/delete_document_data.py`:

```python
#!/usr/bin/env python3
"""Delete the document copies SP4 keeps, for one document or one attorney.

    uv run python -m scripts.delete_document_data --document <id>          # counts only
    uv run python -m scripts.delete_document_data --document <id> --yes    # delete
    $DC run --rm --no-deps backend python -m scripts.delete_document_data --attorney <id> --yes

Removes the stored texts (document_version), the pane's captures
(document_snapshot) and the applied text on Apply events. It is NOT a full
erasure, and prints what it leaves: reviews, conversations, feedback, the
questions in turn_log, Phoenix traces and the nightly dumps.
Spec: docs/superpowers/specs/2026-10-15-document-changes-design.md, "Deleting it".
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory.db import init_db
from memory.document_store import delete_document_data, untouched_counts


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Delete SP4's document copies for one document or attorney.")
    scope = p.add_mutually_exclusive_group(required=True)
    scope.add_argument("--document", default="", help="one document_id")
    scope.add_argument("--attorney", default="", help="one attorney_id")
    p.add_argument("--yes", action="store_true", help="delete; without it, only count")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    init_db()
    scope = {"document_id": args.document, "attorney_id": args.attorney}
    counts = delete_document_data(**scope, dry_run=not args.yes)
    verb = "Deleted" if args.yes else "Would delete (dry run — add --yes to delete)"
    target = f"document {args.document}" if args.document else f"attorney {args.attorney}"
    print(f"{verb} for {target}:")
    print(f"  stored texts (document_version)    {counts['document_version']}")
    print(f"  pane captures (document_snapshot)  {counts['document_snapshot']}")
    print(f"  applied text on Apply events       {counts['interaction_event.applied']}")
    print()
    print("Not touched — these also hold text from this scope:")
    for table, n in untouched_counts(**scope).items():
        print(f"  {table:<22} {'n/a (keyed by document only)' if n is None else n}")
    print("  Phoenix traces         every turn's prompt, the document included")
    print("  nightly dumps          data/backups/app-db-*-nightly.sql.gz, kept up to 14 days")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest tests/test_delete_document_data.py -q` → all pass.
Run: `uv run pytest tests/ -q` → all pass.

- [ ] **Step 6: Commit**

```bash
git add memory/document_store.py scripts/delete_document_data.py tests/test_delete_document_data.py
git commit -m "feat(document-store): delete one document's or attorney's copies on request

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The pane's capture module

**Files:**
- Create: `clients/word/src/documentCapture.ts`, `clients/word/src/documentCapture.test.ts`
- Modify: `clients/word/src/word.ts` (reader), `scripts/check.sh` (`EXPECTED_PASS_COUNT` 324 → 340)

**Interfaces:**
- Consumes: `POST /api/document-snapshot` (Task 4) — reply `{"data": {"recorded", "enabled"}}`; `readDocumentId()` (`docIdentity.ts`); `requestHeaders()` (`requestHeaders.ts`).
- Produces (from `documentCapture.ts`): types `Moment`, `TrackedChange`, `DocumentReading`, `SnapshotBody`, `SnapshotReply`, `CaptureDeps`, `DocumentCapture`; constants `IDLE_MS = 30_000`, `FINALIZE_WAIT_MS = 3_000`, `POLL_MS = 120_000`, `TRACKED_CAP = 500`, `TRACKED_TEXT_CAP = 2_000`; `buildSnapshotBody(documentId, sessionId, moment, reading) -> SnapshotBody`; `createDocumentCapture(deps) -> DocumentCapture` (`capture(moment)` never throws; `noteChange()`); `withinTime(work, ms, schedule?) -> Promise<void>`; `captureBeforeFinalize(capture = installed, ms = FINALIZE_WAIT_MS, schedule?) -> Promise<void>`; `postDocumentSnapshot(body) -> Promise<SnapshotReply>`; `installDocumentCapture(sessionId: string): void`. From `word.ts`: `readDocumentForCapture(): Promise<DocumentReading>`.

- [ ] **Step 1: Write the failing test**

Create `clients/word/src/documentCapture.test.ts` (16 assertions):

```ts
// SP4 document capture. Run with: npx tsx src/documentCapture.test.ts
import {
  buildSnapshotBody,
  captureBeforeFinalize,
  createDocumentCapture,
  IDLE_MS,
  TRACKED_CAP,
  TRACKED_TEXT_CAP,
  withinTime,
  type DocumentReading,
  type SnapshotBody,
  type SnapshotReply,
} from "./documentCapture";
import { pass } from "./testAssert";

const READING: DocumentReading = {
  current: "NDA with Acme Ltd",
  original: "NDA with [__]",
  tracked: [{ type: "Added", author: "Jane Smith", date: "2026-10-16T09:00:00.000Z", text: "Acme Ltd" }],
};

/** Timers the test fires by hand. */
function fakeTimers() {
  const pending = new Map<number, () => void>();
  let next = 0;
  return {
    schedule: (fn: () => void) => {
      pending.set(++next, fn);
      return next;
    },
    cancel: (handle: unknown) => {
      pending.delete(handle as number);
    },
    fire: () => {
      const fns = [...pending.values()];
      pending.clear();
      fns.forEach((fn) => fn());
    },
    count: () => pending.size,
  };
}

function harness(opts: {
  id?: string;
  read?: () => Promise<DocumentReading>;
  reply?: () => Promise<SnapshotReply>;
} = {}) {
  const sent: SnapshotBody[] = [];
  let reads = 0;
  const timers = fakeTimers();
  const capture = createDocumentCapture({
    readDocumentId: () => opts.id ?? "d-1",
    readDocument: () => {
      reads++;
      return opts.read ? opts.read() : Promise.resolve(READING);
    },
    send: (body) => {
      sent.push(body);
      return opts.reply ? opts.reply() : Promise.resolve({ recorded: 1, enabled: true });
    },
    sessionId: "s-1",
    schedule: timers.schedule,
    cancel: timers.cancel,
  });
  return { capture, sent, reads: () => reads, timers };
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

{
  const h = harness();
  await h.capture.capture("pane_open");
  const b = h.sent[0];
  pass(
    h.sent.length === 1 && b.moment === "pane_open" && b.session_id === "s-1" && b.document_id === "d-1" &&
      b.current_text === READING.current && b.original_text === READING.original &&
      b.tracked_changes.length === 1 && b.tracked_total === 1,
    "a capture sends both views, the tracked changes, the moment, the session and the document",
  );
}

{
  const h = harness({ id: "" });
  await h.capture.capture("pane_open");
  pass(h.reads() === 0 && h.sent.length === 0, "a document the assistant has no id for is neither read nor sent");
}

{
  let reading = READING;
  const h = harness({ read: () => Promise.resolve(reading) });
  await h.capture.capture("pane_open");
  await h.capture.capture("idle");
  pass(h.sent.length === 1, "an idle capture of an unchanged document sends nothing");
  reading = { ...READING, current: "NDA with Acme Limited" };
  await h.capture.capture("idle");
  pass(h.sent.length === 2 && h.sent[1].current_text === "NDA with Acme Limited",
    "an idle capture of a changed document sends it");
  await h.capture.capture("finalize");
  pass(h.sent.length === 3 && h.sent[2].moment === "finalize", "a finalize capture sends even when nothing changed");
}

{
  let release: (r: DocumentReading) => void = () => {};
  const h = harness({ read: () => new Promise<DocumentReading>((resolve) => { release = resolve; }) });
  const first = h.capture.capture("idle");
  const second = h.capture.capture("idle");
  release(READING);
  await first;
  await second;
  pass(h.reads() === 1 && h.sent.length === 1, "a capture asked for while one is reading is skipped");
}

{
  let release: (r: DocumentReading) => void = () => {};
  let calls = 0;
  const h = harness({
    read: () =>
      ++calls === 1
        ? new Promise<DocumentReading>((resolve) => { release = resolve; })
        : Promise.resolve({ ...READING, current: "finalized" }),
  });
  const idle = h.capture.capture("idle");
  const fin = h.capture.capture("finalize");
  release(READING);
  await idle;
  await fin;
  pass(h.sent.map((b) => b.moment).join(",") === "idle,finalize",
    "Finalize waits for a capture already reading, then sends its own");
}

{
  const h = harness({ reply: () => Promise.resolve({ recorded: 0, enabled: false }) });
  await h.capture.capture("pane_open");
  await h.capture.capture("finalize");
  h.capture.noteChange();
  pass(h.sent.length === 1 && h.timers.count() === 0, "a server that says capture is off stops every later capture");
}

{
  let failing = true;
  const h = harness({
    read: () => (failing ? Promise.reject(new Error("Word busy")) : Promise.resolve(READING)),
  });
  let threw = false;
  try {
    await h.capture.capture("pane_open");
  } catch {
    threw = true;
  }
  failing = false;
  await h.capture.capture("pane_open");
  pass(!threw && h.sent.length === 1, "a failed read is swallowed and the next capture still works");
}

{
  let down = true;
  const h = harness({
    reply: () => (down ? Promise.reject(new Error("offline")) : Promise.resolve({ recorded: 1, enabled: true })),
  });
  await h.capture.capture("idle");
  down = false;
  await h.capture.capture("idle");
  pass(h.sent.length === 2, "a capture the server never got is sent again at the next pause");
}

{
  const h = harness();
  h.capture.noteChange();
  h.capture.noteChange();
  h.capture.noteChange();
  const waiting = h.timers.count();
  h.timers.fire();
  await tick();
  pass(waiting === 1 && h.sent.length === 1 && h.sent[0].moment === "idle",
    `changes within ${IDLE_MS / 1000} s of each other make one idle capture`);
}

{
  const tracked = [
    { type: "Added", author: "A", date: "", text: "x".repeat(TRACKED_TEXT_CAP + 1) },
    ...Array.from({ length: TRACKED_CAP }, () => ({ type: "Deleted", author: "B", date: "", text: "y" })),
  ];
  const b = buildSnapshotBody("d-1", "s-1", "idle", { ...READING, tracked });
  pass(
    b.tracked_changes.length === TRACKED_CAP && b.tracked_total === TRACKED_CAP + 1 &&
      b.tracked_changes[0].text.length === TRACKED_TEXT_CAP && b.tracked_changes[0].cut === true &&
      b.tracked_changes[1].cut === undefined,
    "tracked changes are capped at 500, 2,000 characters each, keeping the count from before the cap",
  );
}

{
  const h = harness({ read: () => Promise.resolve({ ...READING, tracked: null }) });
  await h.capture.capture("pane_open");
  const b = h.sent[0];
  pass(b.tracked_total === -1 && b.tracked_changes.length === 0 && b.current_text === READING.current,
    "a Word that cannot read tracked changes still sends both texts, with tracked_total -1");
}

{
  const timers = fakeTimers();
  let done = false;
  const p = withinTime(new Promise<void>(() => {}), 3000, timers.schedule).then(() => { done = true; });
  timers.fire();
  await p;
  pass(done, "withinTime gives up on work that never settles when its time is up");
  let settled = false;
  await withinTime(Promise.reject(new Error("boom")), 3000, fakeTimers().schedule).then(() => {
    settled = true;
  });
  pass(settled, "withinTime resolves, never rejects, when the work fails");
}

{
  const timers = fakeTimers();
  const h = harness({ read: () => new Promise<DocumentReading>(() => {}) });
  let through = false;
  const p = captureBeforeFinalize(h.capture, 3000, timers.schedule).then(() => { through = true; });
  timers.fire();
  await p;
  pass(through, "Finalize goes ahead when its capture hangs");
}
```

- [ ] **Step 2: Run it to see it fail**

Run: `cd clients/word && npx tsx src/documentCapture.test.ts`
Expected: fails to load — `Cannot find module './documentCapture'`.

- [ ] **Step 3: Write the module**

Create `clients/word/src/documentCapture.ts`:

```ts
// SP4: copies of the document as the attorney works on it — when the pane
// opens, after each pause in editing, and just before Finalize — so what the
// assistant applied can be compared with what the attorney kept.
// Spec: docs/superpowers/specs/2026-10-15-document-changes-design.md
//
// Quiet like recordEvent: nothing here can reach the attorney or stop what they
// are doing. Only documents the assistant has been used on are captured — the
// id is read with readDocumentId(), never created: resolveDocumentId() writes
// to the file and marks it modified.
import { readDocumentId } from "./docIdentity";
import { requestHeaders } from "./requestHeaders";
import { readDocumentForCapture } from "./word";

export type Moment = "pane_open" | "idle" | "finalize";

export interface TrackedChange {
  type: string;
  author: string;
  date: string;
  text: string;
  cut?: boolean;
}

/** tracked === null: Word could not read the tracked changes (sent as tracked_total -1). */
export interface DocumentReading {
  current: string;
  original: string;
  tracked: TrackedChange[] | null;
}

export interface SnapshotBody {
  session_id: string;
  document_id: string;
  moment: Moment;
  current_text: string;
  original_text: string;
  tracked_changes: TrackedChange[];
  tracked_total: number;
}

export interface SnapshotReply {
  recorded: number;
  enabled: boolean;
}

export const IDLE_MS = 30_000;
export const FINALIZE_WAIT_MS = 3_000;
export const POLL_MS = 120_000;
export const TRACKED_CAP = 500;
export const TRACKED_TEXT_CAP = 2_000;

type Schedule = (fn: () => void, ms: number) => unknown;
type Cancel = (handle: unknown) => void;

export interface CaptureDeps {
  readDocumentId: () => string;
  readDocument: () => Promise<DocumentReading>;
  send: (body: SnapshotBody) => Promise<SnapshotReply>;
  sessionId: string;
  schedule?: Schedule;
  cancel?: Cancel;
}

export interface DocumentCapture {
  /** Read and send the document. Never throws. */
  capture(moment: Moment): Promise<void>;
  /** The document may have changed: (re)start the idle timer. */
  noteChange(): void;
}

/** The body for one capture: tracked changes capped, their count kept from before the cap. */
export function buildSnapshotBody(
  documentId: string,
  sessionId: string,
  moment: Moment,
  reading: DocumentReading,
): SnapshotBody {
  const all = reading.tracked;
  return {
    session_id: sessionId,
    document_id: documentId,
    moment,
    current_text: reading.current,
    original_text: reading.original,
    tracked_changes: (all ?? []).slice(0, TRACKED_CAP).map((c) =>
      c.text.length > TRACKED_TEXT_CAP ? { ...c, text: c.text.slice(0, TRACKED_TEXT_CAP), cut: true } : c,
    ),
    tracked_total: all === null ? -1 : all.length,
  };
}

const contentOf = (body: SnapshotBody): string =>
  JSON.stringify([body.current_text, body.original_text, body.tracked_changes]);

export function createDocumentCapture(deps: CaptureDeps): DocumentCapture {
  const schedule: Schedule = deps.schedule ?? ((fn, ms) => setTimeout(fn, ms));
  const cancel: Cancel = deps.cancel ?? ((h) => clearTimeout(h as ReturnType<typeof setTimeout>));
  let running: Promise<void> | null = null;
  let stopped = false;
  let lastSent = "";
  let timer: unknown = undefined;

  async function run(moment: Moment): Promise<void> {
    try {
      const documentId = deps.readDocumentId();
      if (!documentId) return;
      const body = buildSnapshotBody(documentId, deps.sessionId, moment, await deps.readDocument());
      const content = contentOf(body);
      if (moment === "idle" && content === lastSent) return;
      const reply = await deps.send(body);
      lastSent = content;
      if (!reply.enabled) stopped = true;
    } catch {
      /* a capture must never reach the attorney */
    }
  }

  async function capture(moment: Moment): Promise<void> {
    if (stopped) return;
    if (running) {
      // One read at a time; Finalize alone waits, so its moment is not lost.
      if (moment !== "finalize") return;
      await running;
      if (stopped) return;
    }
    const mine = run(moment);
    running = mine;
    await mine;
    if (running === mine) running = null;
  }

  function noteChange(): void {
    if (stopped) return;
    try {
      if (timer !== undefined) cancel(timer);
      timer = schedule(() => {
        timer = undefined;
        void capture("idle");
      }, IDLE_MS);
    } catch {
      /* never let a timer break typing */
    }
  }

  return { capture, noteChange };
}

/** Resolves when `work` settles or after `ms`, whichever is first. Never rejects. */
export function withinTime(
  work: Promise<void>,
  ms: number,
  schedule: Schedule = (fn, t) => setTimeout(fn, t),
): Promise<void> {
  return new Promise<void>((resolve) => {
    work.then(resolve, resolve);
    schedule(resolve, ms);
  });
}

let installed: DocumentCapture | null = null;

/** Finalize's own capture, given at most FINALIZE_WAIT_MS: Finalize goes ahead either way. */
export function captureBeforeFinalize(
  capture: DocumentCapture | null = installed,
  ms: number = FINALIZE_WAIT_MS,
  schedule?: Schedule,
): Promise<void> {
  if (!capture) return Promise.resolve();
  let work: Promise<void>;
  try {
    work = capture.capture("finalize");
  } catch {
    return Promise.resolve();
  }
  return withinTime(work, ms, schedule);
}

export async function postDocumentSnapshot(body: SnapshotBody): Promise<SnapshotReply> {
  const res = await fetch("/api/document-snapshot", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...requestHeaders() },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`document snapshot: ${res.status}`);
  const json = (await res.json()) as { data?: { recorded?: number; enabled?: boolean } };
  return { recorded: Number(json.data?.recorded ?? 0), enabled: json.data?.enabled !== false };
}

/** Both change events (spec D3) feed the idle timer; true if either registered. */
async function listenForChanges(onChange: () => void): Promise<boolean> {
  if (typeof Word === "undefined" || typeof Office === "undefined") return false;
  const paragraphs = Word.run(async (context) => {
    // WordApi 1.5, absent from some office-js typing releases — the same narrow
    // cast App.tsx uses for the live document size.
    const doc = context.document as unknown as {
      onParagraphChanged: { add: (h: () => Promise<void>) => void };
    };
    doc.onParagraphChanged.add(async () => {
      onChange();
    });
    await context.sync();
    return true;
  }).catch(() => false);
  const selection = new Promise<boolean>((resolve) => {
    try {
      Office.context.document.addHandlerAsync(
        Office.EventType.DocumentSelectionChanged,
        () => onChange(),
        (result: Office.AsyncResult<void>) => resolve(result.status === Office.AsyncResultStatus.Succeeded),
      );
    } catch {
      resolve(false);
    }
  });
  const results = await Promise.all([paragraphs, selection]);
  return results.some(Boolean);
}

/** Once per pane load, from App: capture now, then after every pause in editing. */
export function installDocumentCapture(sessionId: string): void {
  if (installed) return;
  const capture = createDocumentCapture({
    readDocumentId,
    readDocument: readDocumentForCapture,
    send: postDocumentSnapshot,
    sessionId,
  });
  installed = capture;
  void capture.capture("pane_open");
  listenForChanges(() => capture.noteChange())
    .then((listening) => {
      // Neither event registered: a slow poll stands in, still sending only on change.
      if (!listening) {
        setInterval(() => {
          if (document.visibilityState === "visible") void capture.capture("idle");
        }, POLL_MS);
      }
    })
    .catch(() => {
      /* never let capture wiring break the pane */
    });
}
```

In `clients/word/src/word.ts`, add `import type { DocumentReading, TrackedChange } from "./documentCapture";` to the imports at the top (type-only, so no runtime cycle), and add below `readBody`:

```ts
/** SP4: both views of the document and its tracked changes, in one Word.run.
 *  The tracked changes come in their own sync: on a Word without them
 *  (WordApi 1.6) the texts still come back, with tracked = null. */
export async function readDocumentForCapture(): Promise<DocumentReading> {
  if (!isWordAvailable()) throw new Error("Word is not available");
  return Word.run(async (context) => {
    const body = context.document.body;
    const current = body.getReviewedText(Word.ChangeTrackingVersion.current);
    const original = body.getReviewedText(Word.ChangeTrackingVersion.original);
    await context.sync();
    let tracked: TrackedChange[] | null = null;
    try {
      const changes = body.getTrackedChanges();
      changes.load("items/type,items/author,items/date,items/text");
      await context.sync();
      tracked = changes.items.map((c) => ({
        type: String(c.type),
        author: c.author ?? "",
        date: isoDate(c.date),
        text: c.text ?? "",
      }));
    } catch {
      tracked = null;
    }
    return { current: current.value, original: original.value, tracked };
  });
}

function isoDate(value: unknown): string {
  try {
    const d = new Date(value as string | number | Date);
    return Number.isNaN(d.getTime()) ? "" : d.toISOString();
  } catch {
    return "";
  }
}
```

In `scripts/check.sh`, change `EXPECTED_PASS_COUNT=324` to `EXPECTED_PASS_COUNT=340`.

- [ ] **Step 4: Run it to see it pass**

Run: `cd clients/word && npx tsx src/documentCapture.test.ts` → 16 `PASS:` lines, no `FAIL:`.
Run: `cd clients/word && npx tsc --noEmit` → no errors.

- [ ] **Step 5: Commit**

```bash
git add clients/word/src/documentCapture.ts clients/word/src/documentCapture.test.ts \
        clients/word/src/word.ts scripts/check.sh
git commit -m "feat(pane): capture the document at open, after editing pauses and before Finalize

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: The pane sends the applied text

**Files:**
- Modify: `clients/word/src/feedback.ts`, `clients/word/src/components/EditProposalCard.tsx`, `clients/word/src/components/FindingCard.tsx`, `clients/word/src/feedback.test.ts`, `scripts/check.sh` (340 → 347)

**Interfaces:**
- Consumes: `/api/events` accepting `applied` (Task 3); `EditProposal` (`parseEditBlocks.ts`).
- Produces (`feedback.ts`): `interface AppliedEdit { action; target_text; new_text; proposed_text?; position? }`; `APPLIED_TEXT_CAP = 20_000`; `capApplied(a: AppliedEdit): AppliedEdit`; `appliedFromProposal(p: EditProposal, appliedText: string): AppliedEdit`; `recordEvent(turn, surface, action, extra)` with `extra.applied?: AppliedEdit`.

- [ ] **Step 1: Write the failing tests**

In `clients/word/src/feedback.test.ts`, extend the import at the top to:

```ts
import {
  APPLIED_TEXT_CAP,
  appliedFromProposal,
  buildSnapshot,
  capApplied,
  onFlagRequested,
  recordEvent,
  requestFlag,
  sendFeedback,
} from "./feedback";
```

and append at the end of the file (7 assertions):

```ts
// --- SP4: the applied text -----------------------------------------------
let lastEvent: Record<string, unknown> = {};
(globalThis as { fetch?: unknown }).fetch = (_url: string, init: { body: string }) => {
  lastEvent = JSON.parse(init.body).events[0];
  return Promise.resolve({ ok: true });
};
recordEvent(TURN, "chat", "edit_applied", {
  targetKind: "replace",
  targetRef: "the Seller",
  applied: { action: "replace", target_text: "the Seller", new_text: "Acme Ltd" },
});
pass(
  JSON.stringify(lastEvent.applied) ===
    JSON.stringify({ action: "replace", target_text: "the Seller", new_text: "Acme Ltd" }),
  "recordEvent sends the applied text with the event",
);

recordEvent(TURN, "chat", "edit_discarded");
pass(!("applied" in lastEvent), "an event with no applied text sends none");

const huge = "x".repeat(APPLIED_TEXT_CAP + 10);
const capped = capApplied({ action: "insert", target_text: huge, new_text: huge, proposed_text: huge, position: "after" });
pass(
  capped.target_text.length === APPLIED_TEXT_CAP && capped.new_text.length === APPLIED_TEXT_CAP &&
    capped.proposed_text?.length === APPLIED_TEXT_CAP && capped.position === "after",
  "each applied field is capped at 20,000 characters",
);

const proposal = { action: "replace" as const, target_text: "1x fees", new_text: "2x fees" };
pass(
  JSON.stringify(appliedFromProposal(proposal, "3x fees")) ===
    JSON.stringify({ action: "replace", target_text: "1x fees", new_text: "3x fees", proposed_text: "2x fees" }),
  "an Apply records the text the attorney applied, and the model's when they differ",
);
pass(appliedFromProposal(proposal, "2x fees").proposed_text === undefined,
  "an Apply of the model's text as proposed records no separate proposal");
pass(
  JSON.stringify(appliedFromProposal({ action: "insert", anchor_text: "4. Term", new_text: "4.1 Renewal." }, "4.1 Renewal.")) ===
    JSON.stringify({ action: "insert", target_text: "4. Term", new_text: "4.1 Renewal.", position: "after" }),
  "an insert records its anchor as the target, and where it went",
);
pass(
  JSON.stringify(appliedFromProposal({ action: "delete", target_text: "Non-solicit." }, "")) ===
    JSON.stringify({ action: "delete", target_text: "Non-solicit.", new_text: "" }),
  "a delete records what it took out",
);
```

- [ ] **Step 2: Run it to see it fail**

Run: `cd clients/word && npx tsx src/feedback.test.ts`
Expected: fails — `APPLIED_TEXT_CAP` / `capApplied` / `appliedFromProposal` are not exported.

- [ ] **Step 3: Implement**

In `clients/word/src/feedback.ts`, add `import type { EditProposal } from "./parseEditBlocks";` below the `requestHeaders` import, and add above `recordEvent`:

```ts
/** What an Apply actually put into the document (SP4): full text, each field
 *  capped at APPLIED_TEXT_CAP. proposed_text only when the attorney edited the
 *  model's wording in the card first. */
export interface AppliedEdit {
  action: string;
  target_text: string;
  new_text: string;
  proposed_text?: string;
  position?: string;
}

export const APPLIED_TEXT_CAP = 20_000;

export function capApplied(a: AppliedEdit): AppliedEdit {
  const out: AppliedEdit = {
    action: a.action,
    target_text: a.target_text.slice(0, APPLIED_TEXT_CAP),
    new_text: a.new_text.slice(0, APPLIED_TEXT_CAP),
  };
  if (a.proposed_text !== undefined) out.proposed_text = a.proposed_text.slice(0, APPLIED_TEXT_CAP);
  if (a.position !== undefined) out.position = a.position;
  return out;
}

/** The Apply payload for a chat edit card. `appliedText` is what went in — the
 *  draft box, which the attorney may have reworded. */
export function appliedFromProposal(p: EditProposal, appliedText: string): AppliedEdit {
  if (p.action === "delete") return { action: "delete", target_text: p.target_text ?? "", new_text: "" };
  const out: AppliedEdit = {
    action: p.action,
    target_text: (p.action === "insert" ? p.anchor_text : p.target_text) ?? "",
    new_text: appliedText,
  };
  if ((p.new_text ?? "") !== appliedText) out.proposed_text = p.new_text ?? "";
  if (p.action === "insert") out.position = p.position ?? "after";
  return out;
}
```

Change `recordEvent`'s `extra` parameter type to `{ targetKind?: string; targetRef?: string; detail?: string; request?: string; applied?: AppliedEdit } = {}`, and add the field to the event object it sends, after `request: …`:

```ts
            ...(extra.applied ? { applied: capApplied(extra.applied) } : {}),
```

In `clients/word/src/components/EditProposalCard.tsx`, import `appliedFromProposal` from `"../feedback"` (add it to the existing import), and the success branch of `onApply` becomes:

```ts
      recordEvent(turn, "chat", "edit_applied", {
        targetKind: proposal.action,
        targetRef: ref,
        applied: appliedFromProposal(proposal, draftText),
      });
```

In `clients/word/src/components/FindingCard.tsx`, the success branch of `onAccept` becomes:

```ts
      recordEvent(turn, "findings", "redline_applied", {
        targetRef: finding.issueId || finding.clause,
        applied: { action: "redline", target_text: anchors.join("\n"), new_text: finding.redline },
      });
```

In `scripts/check.sh`, change `EXPECTED_PASS_COUNT=340` to `EXPECTED_PASS_COUNT=347`.

- [ ] **Step 4: Run it to see it pass**

Run: `cd clients/word && npx tsx src/feedback.test.ts` → every line `PASS:` (7 new).
Run: `cd clients/word && npx tsc --noEmit` → no errors.

- [ ] **Step 5: Commit**

```bash
git add clients/word/src/feedback.ts clients/word/src/feedback.test.ts \
        clients/word/src/components/EditProposalCard.tsx clients/word/src/components/FindingCard.tsx \
        scripts/check.sh
git commit -m "feat(pane): an Apply sends the text it put in, and the model's when the attorney edited it

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Wiring the capture into the pane, and the disclosure line

**Files:**
- Modify: `clients/word/src/App.tsx`, `clients/word/src/components/FinalizeBar.tsx`

**Interfaces:**
- Consumes: `installDocumentCapture(sessionId)`, `captureBeforeFinalize()` (Task 8).

No unit test: the pane has no React harness (CLAUDE.md). This task is checked by typecheck, the build, and the owner's sideload under *Verification* below.

- [ ] **Step 1: Install the capture**

In `clients/word/src/App.tsx`, add `import { installDocumentCapture } from "./documentCapture";` to the imports, and below the SP3 `useEffect` (the one ending `}, [sessionId]);` after `recordPaneEvent("pane_opened", …)`):

```tsx
  // SP4: copies of the document as the attorney works — at open, after each
  // pause in editing, and before Finalize (documentCapture.ts). Only documents
  // the assistant has an id for; quiet, like the events above. Guarded inside,
  // so a second mount (StrictMode, HMR) installs nothing twice.
  useEffect(() => {
    installDocumentCapture(sessionId);
  }, [sessionId]);
```

- [ ] **Step 2: The disclosure line**

In `App.tsx`, replace the text of `<p className="disclosure">` with:

```tsx
        <p className="disclosure">
          During testing, the assistant records your questions, its answers and the document
          text sent with them; while this pane is open on a document you have used it on, a
          copy each time you pause editing, with its tracked changes and who made them; when
          this pane is opened, which tab you use, how long answers take, errors it shows you,
          and what you do with its suggestions — so we can measure what it gets wrong. Sending
          feedback includes the document.
        </p>
```

- [ ] **Step 3: Finalize captures first**

In `clients/word/src/components/FinalizeBar.tsx`, add `import { captureBeforeFinalize } from "../documentCapture";`, and `onFinalize` becomes:

```tsx
  const onFinalize = async () => {
    setStatus({ kind: "running" });
    // SP4: the document as it stood before accepting — at most 3 s, and
    // Finalize goes ahead whatever the capture does.
    await captureBeforeFinalize();
    const res = await finalizeDocument();
    if (res.ok) {
      setStatus({ kind: "done", count: res.value });
      recordPaneEvent("finalize_applied");
    } else {
      setStatus({ kind: "error", message: res.error });
      recordPaneEvent("finalize_failed", { detail: res.error });
    }
  };
```

- [ ] **Step 4: Typecheck and build**

Run: `cd clients/word && npx tsc --noEmit && npm run build` → no errors (`dist/` is gitignored).
Run: `git status --short clients/word/src` → only the two modified files; no `.js` emitted into `src/`.

- [ ] **Step 5: Commit**

```bash
git add clients/word/src/App.tsx clients/word/src/components/FinalizeBar.tsx
git commit -m "feat(pane): install the document capture; Finalize captures first; disclosure names the copies

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Docs, and the full gate

**Files:**
- Modify: `docs/tester-setup.md`, `docs/feedback-loop.md`, `docs/deploy-vm.md`, `docs/wiki.md`, `CLAUDE.md`

- [ ] **Step 1: The tester disclosure** — `docs/tester-setup.md`, *What the assistant records*:

Replace the bullet `- what you do with its suggestions — applied, discarded, or failed to apply;` with:

```markdown
- what you do with its suggestions — applied (with the text that was applied), discarded, or failed to apply;
```

Add after the bullet that starts `- when the task pane is opened`:

```markdown
- while the task pane is open on a document you have used the assistant on: a copy of the document when you open the pane, each time you pause editing, and when you use Finalize — with its tracked changes and the name Word shows for who made each one (yours and, in a negotiated draft, the other side's). Edits made while the pane is closed are seen only when you open it again;
```

Replace the paragraph that starts `The records of pane use (opens, tabs, waits, errors) contain no document text` with:

```markdown
The records of pane use (opens, tabs, waits, errors) contain no document text, except that an error message can quote the passage it was working on; the copies of the document, of course, do. The copies are kept for the length of the pilot — ask, and we delete them for a document or for you. All of it is used only to find what the assistant gets wrong.
```

- [ ] **Step 2: Reading it** — `docs/feedback-loop.md`: insert a new section immediately before `## From a complaint to the prompt that caused it`:

````markdown
## A document, before and after

What the attorney did to the document itself (SP4): each turn's text; a copy when
the pane opens, 30 s after every pause in editing, and just before Finalize — both
views (every tracked change accepted / every one rejected) and the tracked changes
with who made them; and the full text of every Apply.

```bash
uv run python -m scripts.feedback_report --changes --document <id>
$DC run --rm --no-deps backend python -m scripts.feedback_report --changes --days 7   # on the VM
```

One block per document:

```
document 4f1c…  A. Tester (cc91e640-…) · 2026-10-16 09:12 → 2026-10-18 14:02
  observations 9 (3 turns · 2 pane_open · 4 idle · 0 finalize) · 6 distinct texts
  first stored text 2026-10-16 09:12 (turn)
  applied edits 5: 3 kept · 1 altered · 1 reverted · 0 no later capture
    kept             replace     "This Agreement shall be governed…" → "…laws of England and Wales"   turn 6aef53da
    altered          replace     "Liability cap…" → "…12 months' fees"   (edited before applying)
                     closest now: "…the fees paid in the 6 months preceding the claim"
  tracked changes at the last capture: 14 — Jane Smith 9 (6 ins, 3 del) · Counterparty LLP 5 (5 ins)
  paragraphs changed since the first text: 11 — 4 explained by an applied edit · 7 not suggested
    + "The Supplier shall indemnify the Client against…"   not suggested
```

- **Applied edits** are judged against the last text seen after them: `kept` (the
  applied wording is there), `reverted` (gone, and the original wording is back),
  `altered` (neither — reworded or removed; `closest now` is the paragraph sharing
  the most words with it, for you to judge), `no later capture` (nothing seen
  since). Comparison ignores curly vs straight quotes and line wrapping.
  `(edited before applying)` = the attorney changed the model's wording in the card.
- **Tracked changes** are counted by the author Word shows. The assistant's edits
  carry the attorney's own name (Office.js attributes them to the current user), so
  the *other* names are the other side; the applied-edit list tells the assistant
  apart from the attorney.
- **Paragraphs changed** — `+` only in the last text, `-` only in the first (a
  reworded paragraph is one of each); `assistant` when an applied edit explains
  it. The `not suggested` lines are where to look for what the review missed.
- `--full` prints whole texts and every changed paragraph (20 by default).
- In `--turns`, each capture is a line between the turns:
  `· capture idle: text changed, 3 tracked changes`.

It cannot tell yet: reworded from removed (`altered` covers both); the other side's
second round when the file comes back with our document id (it reads as `not
suggested` — the author counts separate it); anything done while the pane was
closed and never reopened. Turns from before SP4 have a fingerprint and no text,
so a document's story starts at its first stored text. Deleting a document's
copies on request: `docs/deploy-vm.md`, *Deleting document copies*.

````

- [ ] **Step 3: The runbook** — `docs/deploy-vm.md`: insert immediately before `## Step 5 — Verify`:

````markdown
### Deleting document copies

`document_version` and `document_snapshot` hold copies of contracts as attorneys edited them, and `interaction_event.applied` the text of each Apply (SP4). They are kept for the pilot; on request, for one document or one attorney:

```bash
$DC run --rm --no-deps backend python -m scripts.delete_document_data --document <id>         # counts only
$DC run --rm --no-deps backend python -m scripts.delete_document_data --document <id> --yes   # delete
```

`--attorney <id>` does the same for everything one attorney sent. It is not a full erasure and prints what it leaves: reviews, conversations, feedback, the questions in `turn_log`, Phoenix traces, and the nightly dumps (up to 14 days).

````

- [ ] **Step 4: The wiki** — `docs/wiki.md`:

Insert as the first row of the *Shipped Since Last Update* table (directly under `|---|---|---|`):

```markdown
| **What attorneys do to the document (SP4)** | `feat/document-changes` | `turn_log` named the document only by a fingerprint, `edit_applied` kept 200 characters of the target and none of the text that went in, and attorneys send redlined files without pressing Finalize — so whether an applied edit survived, and what the attorney changed that the assistant never suggested, was invisible. Now each pane turn's text is kept (`document_version`: one row per distinct text, keyed by the SHA-256 `turn_log` already records — `memory/fingerprint.py` is the one hash); the pane captures the document when it opens, 30 s after editing pauses (`onParagraphChanged` and `DocumentSelectionChanged`; a 2-minute poll only when neither registers) and just before Finalize — both views (`getReviewedText` current and original) and the tracked changes with their authors (`document_snapshot`, `POST /api/document-snapshot`); and every Apply keeps its full text, plus the model's when the attorney edited the card first (`interaction_event.applied`). `feedback_report --changes` reads it back per document: a verdict per applied edit (`kept` / `reverted` / `altered` / `no later capture`, exact after normalisation), the tracked changes by author, and the paragraphs changed since the first text, labelled `assistant` or `not suggested`; captures also appear in `--turns`. Quiet throughout; a failed write logs ids, hash and size, never text (Postgres echoes input in some messages). `scripts.delete_document_data` removes one document's or attorney's copies on request, and the tester disclosure names the copies. Spec `docs/superpowers/specs/2026-10-15-document-changes-design.md`, plan `docs/superpowers/plans/2026-10-15-document-changes.md`. |
```

Insert in the *Follow-ups / Roadmap* table, directly after the row starting `| **Pilot tracking, next step (SP3)`:

```markdown
| **SP4 follow-ups — reading what attorneys kept** | Low (new 2026-10-15) | (1) `altered` covers both reworded and removed; split them once real pilot data shows where a similarity threshold belongs (the deferred edit-matching heuristic). (2) A counterparty's second round returned in the same file reads as `not suggested`; separate it by author once we see how often it happens. (3) Neither change event (`onParagraphChanged`, `DocumentSelectionChanged`) has been checked in Word for Windows. |
```

- [ ] **Step 5: CLAUDE.md** — no new lines (the file is at 148 of its 150):

In the *Relational stores are Postgres* bullet, replace `All seven store tables (`audit_log`, `review_store`, `conversation_store`, `conversation_summary`, `feedback`, `interaction_event`, `turn_log`)` with `All nine store tables (`audit_log`, `review_store`, `conversation_store`, `conversation_summary`, `feedback`, `interaction_event`, `turn_log`, `document_version`, `document_snapshot`)`, and `truncates every store table between tests — from a hardcoded list, so a new table must be added there.` with `truncates every store table between tests — from `STORE_TABLES`, which `tests/test_db.py` checks against the database.`

In the *Feedback capture is keyed to `turn_id`* bullet, after the sentence ending `Read it with `feedback_report --turns`.`, insert: `SP4 keeps the document itself: each pane turn's text and the pane's captures (open / 30 s after editing pauses / before Finalize, with the tracked changes and their authors) in `document_version` + `document_snapshot`, and each Apply's full text in `interaction_event.applied` — read with `--changes`, deleted on request with `scripts.delete_document_data`; a failed write logs ids, hash and size, never text.`

Run: `wc -l CLAUDE.md` → still 148.

- [ ] **Step 6: The full gate**

Run: `bash scripts/check.sh`
Expected: exit 0 — backend tests all pass (887 + this branch's new ones), `word add-in assertions: 347/347 PASS`, eval no regressions, error-code vocabulary gate, image build and `api.main` import inside it, Caddyfile validates.

- [ ] **Step 7: Commit**

```bash
git add docs/tester-setup.md docs/feedback-loop.md docs/deploy-vm.md docs/wiki.md CLAUDE.md
git commit -m "docs: what attorneys do to the document — disclosure, reading it, deleting it

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Verification — with the owner, after Task 11

Not an implementer task: Word sideloads are the owner's. The controller prepares, the owner clicks.

1. Restart the local backend (`bash scripts/start.sh` — uvicorn does not reload) and `cd clients/word && npm run dev`.
2. In Word for Mac, on a scratch NDA: ask one question (mints the document id) → apply an edit and leave it (→ `kept`); apply a second, then reword it by hand (→ `altered`); apply a third, then reject its tracked insertion (→ `reverted`); type a new clause by hand (→ `not suggested`); wait 30 s after the last keystroke; Finalize.
3. Read back: `uv run python -m scripts.feedback_report --changes --document <id>` and `--turns --days 1`. Confirm: an `idle` capture landed after typing (D3's events fire in the pane), the tracked changes show the attorney's Word name, a `finalize` capture exists, the disclosure line shows in the pane.
4. Delete the probe data: `uv run python -m scripts.delete_document_data --document <id> --yes`.

Then the rollout in the spec (*Rollout*) — each VM step only after the owner's OK for that deploy.
