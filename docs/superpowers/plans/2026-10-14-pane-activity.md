# Pane Activity and Caddy Logs (SP3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record what attorneys do in the Word pane between turns (opens, tabs, Finalize, errors, real waits) and every request reaching the VM (Caddy access + TLS-error logs), and read it back with `feedback_report --activity` and a richer `--turns`.

**Architecture:** Pane events are new `action`s under `surface="pane"` on the existing `interaction_event` table, sent through the existing fire-and-forget `recordEvent`. `interaction_event` and `feedback` gain four client columns (platform, version, pane build, backend build) via `ALTER … ADD COLUMN IF NOT EXISTS`, stamped from the headers SP2 already sends. Caddy writes two JSON-lines files into `data/caddy-logs/`, read with `jq`.

**Tech Stack:** Python 3.12 / FastAPI / psycopg3 / Postgres (testcontainers in tests); React + TypeScript Word add-in (hand-rolled `npx tsx` assertion scripts); Caddy 2.11.4.

**Spec:** `docs/superpowers/specs/2026-10-14-pane-activity-design.md` (commit `433dcd8`)

## Global Constraints

- Python 3.12 via `uv`; **all imports at the top of the file** (CLAUDE.md rule 1).
- `interaction_event` writes stay **QUIET** (never raise); `feedback` writes stay **LOUD** (raise). Unchanged by this plan.
- New columns are added **only** by `ALTER TABLE … ADD COLUMN IF NOT EXISTS … TEXT NOT NULL DEFAULT ''` placed after the table's CREATE — never only inside the CREATE (the VM's tables already exist).
- The four client columns, exact names: `office_platform`, `office_version`, `pane_build`, `backend_build`. `backend_build` = the backend's `get_settings().build_sha` at write time.
- Pane events: `surface` is exactly `"pane"`. Actions, exactly: `pane_opened`, `tab_viewed`, `finalize_applied`, `finalize_failed`, `client_error`, `turn_waited`. `target_ref` values: tabs `findings` | `chat` | `preferences`; errors `chat` | `review` | `compact` | `preferences` | `window`; waits `chat` | `review`.
- Pane events are sent only through `recordEvent` (`clients/word/src/feedback.ts`): void, never awaited, never throws. `detail` ≤ 500 chars, `target_ref` ≤ 200 (its existing caps).
- Opening the pane must **never create** a document id (that writes Office settings and marks the document modified). Pane events read the id with `readDocumentId()` (read-only).
- Caddy files: `/var/log/caddy/access.log` and `/var/log/caddy/tls-errors.log`, each `roll_size 10MiB`, `roll_keep 10`, `roll_keep_for 2160h`, `mode 0644`, `format json`; the TLS log is `level DEBUG` + `include http.stdlib`. Host folder `./data/caddy-logs`. Pinned image `caddy:2.11.4@sha256:844f60b64e4724a5aa8245e019dace0d3f199f7433ce6c57676cb30a920dbad9` (from `clients/word/Dockerfile`).
- `scripts/check.sh` `EXPECTED_PASS_COUNT`: **299 → 311** (10 in the new `paneEvents.test.ts`, 2 added to `docIdentity.test.ts`).
- No VM action of any kind in any task (CLAUDE.md rule 6). Tests need Docker running.
- Every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Opening a never-saved or never-reviewed document must not dirty it.** A pane open that minted a document id would make Word ask "save changes?" on a file the attorney only looked at. → Task 2 pins `readDocumentId()` as read-only; Task 3 uses only it.
2. **An error raised while reporting an error must not loop.** → Task 2's re-entrancy test.
3. **A day from a pre-SP3 pane** (turns, no pane events) must print `opens 0` and `—` builds, not crash or hide the day. → Task 4 test.
4. **Office appends a query string to the pane URL** (`/taskpane.html?_host_Info=…`), so the pane-load recipe must match by prefix. → Task 6 verifies the recipe against such a line.
5. **Rotated Caddy files are gzip-compressed** (Caddy's default), so a recipe reading only `access.log` silently drops history. → Task 6 recipe reads `zcat -f data/caddy-logs/access*.log*` and verifies it on a plain file.

---

### Task 1: Client facts on `interaction_event` and `feedback`

**Files:**
- Modify: `memory/db.py` (after the `interaction_event` `request` ALTER at ~line 124, and after `idx_feedback_turn` at ~line 98)
- Modify: `memory/feedback_store.py` (`save_feedback`, `_insert_event`, `record_event`, `record_events`)
- Modify: `api/routes/feedback.py`
- Modify: `api/models.py` (`InteractionEvent` docstring)
- Test: `tests/test_feedback_store.py`, `tests/test_feedback_api.py`

**Interfaces:**
- Consumes: `api.client_info.ClientInfo` / `resolve_client_info` (SP2); `config.get_settings().build_sha`.
- Produces: `save_feedback(..., snapshot, client: dict | None = None, backend_build: str = "") -> int`; `record_events(events, *, attorney_id, client: dict | None = None, backend_build: str = "") -> int`; `record_event(..., request="", client: dict | None = None, backend_build: str = "") -> None`. `client` is `dataclasses.asdict(ClientInfo)`-shaped (keys `office_platform`, `office_version`, `pane_build`; extra keys ignored). Columns `office_platform`, `office_version`, `pane_build`, `backend_build` on both tables.

- [ ] **Step 1: Write the failing store tests** — append to `tests/test_feedback_store.py`, and add `from memory.db import get_pool, init_db` to its imports at the top:

```python
_CLIENT = {"office_platform": "PC", "office_version": "16.0.18129", "pane_build": "p1"}
_CLIENT_COLUMNS = {"office_platform", "office_version", "pane_build", "backend_build"}

# Each table's columns as deployed on the VM before SP3 (2026-10-14). Frozen:
# never edit — they stand for every database created before this change.
_EVENT_COLUMNS_2026_10_14 = (
    "timestamp", "turn_id", "session_id", "document_id", "attorney_id", "surface",
    "action", "target_kind", "target_ref", "detail", "request",
)
_FEEDBACK_COLUMNS_2026_10_14 = (
    "timestamp", "turn_id", "trace_id", "session_id", "document_id", "attorney_id",
    "user_name", "surface", "target_kind", "target_ref", "comment", "snapshot",
)


def _client_columns(table: str) -> list[tuple]:
    with get_pool().connection() as conn:
        return conn.execute(
            f"SELECT office_platform, office_version, pane_build, backend_build "
            f"FROM {table} ORDER BY id"
        ).fetchall()


def test_record_events_stamps_what_sent_it_and_the_backend_build():
    fs.record_events([{"surface": "pane", "action": "pane_opened"}],
                     attorney_id="atty-1", client=_CLIENT, backend_build="b1")
    assert _client_columns("interaction_event") == [("PC", "16.0.18129", "p1", "b1")]


def test_save_feedback_stamps_what_sent_it_and_the_backend_build():
    _save(client=_CLIENT, backend_build="b1")
    assert _client_columns("feedback") == [("PC", "16.0.18129", "p1", "b1")]


def test_a_writer_without_client_facts_records_them_empty():
    """Chainlit and pre-SP2 panes send no client headers: unknown, not an error."""
    fs.record_events([{"action": "edit_applied"}], attorney_id="atty-1")
    _save()
    assert _client_columns("interaction_event") == [("", "", "", "")]
    assert _client_columns("feedback") == [("", "", "", "")]


@pytest.mark.parametrize("table, shipped", [
    ("interaction_event", _EVENT_COLUMNS_2026_10_14),
    ("feedback", _FEEDBACK_COLUMNS_2026_10_14),
])
def test_a_database_from_before_sp3_gains_the_client_columns(table, shipped):
    """The VM's tables exist, so CREATE TABLE IF NOT EXISTS never runs there
    again: a column added only inside the CREATE fails every write in
    production, quietly for events. Rebuild the table as it shipped, start up
    as the VM does, and require the four new columns.

    Mutation: move one client column into the CREATE and delete its ALTER."""
    with get_pool().connection() as conn:
        conn.execute(f"DROP TABLE {table}")
        conn.execute(
            f"CREATE TABLE {table} (id BIGSERIAL PRIMARY KEY, "
            + ", ".join(f"{c} TEXT" for c in shipped) + ")"
        )
    try:
        init_db()
        with get_pool().connection() as conn:
            got = {r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = %s AND column_name <> 'id'", (table,)
            ).fetchall()}
        assert got == set(shipped) | _CLIENT_COLUMNS
    finally:
        # Every other test needs the real column types back.
        with get_pool().connection() as conn:
            conn.execute(f"DROP TABLE {table}")
        init_db()
```

- [ ] **Step 2: Write the failing API tests** — in `tests/test_feedback_api.py`, add `from memory.db import get_pool` to the imports, add `build_sha="test-build"` to the fixture's `SimpleNamespace(...)`, and append:

```python
_CLIENT_HEADERS = {"X-User-ID": "atty-1", "X-Office-Platform": "Mac",
                   "X-Office-Version": "16.113", "X-Pane-Build": "abc1234"}


def _client_columns(table: str) -> list[tuple]:
    with get_pool().connection() as conn:
        return conn.execute(
            f"SELECT office_platform, office_version, pane_build, backend_build "
            f"FROM {table} ORDER BY id"
        ).fetchall()


def test_events_carry_what_sent_them(client):
    c, _ = client
    c.post("/api/events", json={"events": [{"surface": "pane", "action": "pane_opened"}]},
           headers=_CLIENT_HEADERS)
    assert _client_columns("interaction_event") == [("Mac", "16.113", "abc1234", "test-build")]


def test_feedback_carries_what_sent_it(client):
    c, _ = client
    c.post("/api/feedback", json=_body(), headers=_CLIENT_HEADERS)
    assert _client_columns("feedback") == [("Mac", "16.113", "abc1234", "test-build")]
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/test_feedback_store.py tests/test_feedback_api.py -q`
Expected: the new tests FAIL — `TypeError: record_events() got an unexpected keyword argument 'client'`, `psycopg.errors.UndefinedColumn: column "office_platform" does not exist`, and the upgrade test's set comparison missing the four columns.

- [ ] **Step 4: Add the columns** — in `memory/db.py`, directly after `"CREATE INDEX IF NOT EXISTS idx_feedback_turn ON feedback (turn_id)",` insert:

```python
    # What sent the flag (SP3): the Word host and pane build from the request
    # headers, and the backend's own build — a flag from a stale pane is then a
    # property of its row. ALTER, not the CREATE above: the VM's table exists.
    "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS office_platform TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS office_version TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS pane_build TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS backend_build TEXT NOT NULL DEFAULT ''",
```

and directly after `"ALTER TABLE interaction_event ADD COLUMN IF NOT EXISTS request TEXT NOT NULL DEFAULT ''",` insert:

```python
    # What sent the event (SP3) — same four facts and the same reason as on
    # feedback above. Pane lifecycle events (surface='pane') need them most: a
    # pane_opened without its pane_build cannot show a stale pane.
    "ALTER TABLE interaction_event ADD COLUMN IF NOT EXISTS office_platform TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE interaction_event ADD COLUMN IF NOT EXISTS office_version TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE interaction_event ADD COLUMN IF NOT EXISTS pane_build TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE interaction_event ADD COLUMN IF NOT EXISTS backend_build TEXT NOT NULL DEFAULT ''",
```

- [ ] **Step 5: Write them in the store** — in `memory/feedback_store.py`, add after `COUNTER_ACTIONS`:

```python
def _client_values(client: dict | None) -> tuple[str, str, str]:
    """The three client facts from a ClientInfo-shaped dict; missing → ""."""
    c = client or {}
    return c.get("office_platform", ""), c.get("office_version", ""), c.get("pane_build", "")
```

Replace `save_feedback`'s signature tail and INSERT:

```python
    comment: str,
    snapshot: dict | None,
    client: dict | None = None,
    backend_build: str = "",
) -> int:
    """Insert one feedback row and return its id. Raises on any failure."""
    with get_pool().connection() as conn:
        cur = conn.execute(
            """INSERT INTO feedback
               (timestamp, turn_id, trace_id, session_id, document_id, attorney_id,
                user_name, surface, target_kind, target_ref, comment, snapshot,
                office_platform, office_version, pane_build, backend_build)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (
                datetime.now(timezone.utc).isoformat(),
                turn_id, trace_id, session_id, document_id, attorney_id,
                user_name, surface, target_kind, target_ref, comment,
                Jsonb(snapshot) if snapshot is not None else None,
                *_client_values(client), backend_build,
            ),
        )
```

Replace `_insert_event` with:

```python
def _insert_event(
    *,
    turn_id: str,
    session_id: str,
    document_id: str,
    attorney_id: str,
    surface: str,
    action: str,
    target_kind: str,
    target_ref: str,
    detail: str,
    request: str,
    client: dict | None,
    backend_build: str,
) -> None:
    """Raw insert — RAISES. The quiet policy lives in the two callers below, so
    record_events can count what actually landed instead of what it attempted."""
    with get_pool().connection() as conn:
        conn.execute(
            """INSERT INTO interaction_event
               (timestamp, turn_id, session_id, document_id, attorney_id,
                surface, action, target_kind, target_ref, detail, request,
                office_platform, office_version, pane_build, backend_build)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                datetime.now(timezone.utc).isoformat(),
                turn_id, session_id, document_id, attorney_id,
                surface, action, target_kind, target_ref, detail, request,
                *_client_values(client), backend_build,
            ),
        )
```

In `record_event`, add `client: dict | None = None,` and `backend_build: str = "",` after `request: str = "",`, and pass `client=client, backend_build=backend_build,` to `_insert_event`. Replace `record_events` with:

```python
def record_events(
    events: list[dict], *, attorney_id: str,
    client: dict | None = None, backend_build: str = "",
) -> int:
    """Insert a batch, returning how many actually landed. NEVER raises.
    `client` / `backend_build` describe the request that carried the batch, so
    they are the same on every row of it."""
    written = 0
    for event in events:
        try:
            _insert_event(
                turn_id=event.get("turn_id", ""),
                session_id=event.get("session_id", ""),
                document_id=event.get("document_id", ""),
                attorney_id=attorney_id,
                surface=event.get("surface", ""),
                action=event.get("action", ""),
                target_kind=event.get("target_kind", ""),
                target_ref=event.get("target_ref", ""),
                detail=event.get("detail", ""),
                request=event.get("request", ""),
                client=client,
                backend_build=backend_build,
            )
            written += 1
        except Exception as e:
            logger.warning("interaction_event batch row failed: %s", e)
    return written
```

- [ ] **Step 6: Read them in the route** — in `api/routes/feedback.py`, add to the imports `from dataclasses import asdict` and `from api.client_info import ClientInfo, resolve_client_info`. Add `client: ClientInfo = Depends(resolve_client_info),` as the last parameter of both `post_feedback` and `post_events`. In `post_feedback`, add to the `save_feedback(...)` call:

```python
        client=asdict(client),
        backend_build=settings.build_sha,
```

Replace `post_events`'s body with:

```python
    settings = get_settings()
    if not settings.feedback_enabled:
        return ApiResponse(status="ok", data={"recorded": 0})
    written = record_events(
        [e.model_dump() for e in body.events], attorney_id=user_id,
        client=asdict(client), backend_build=settings.build_sha,
    )
    return ApiResponse(status="ok", data={"recorded": written})
```

In `api/models.py`, replace `InteractionEvent`'s docstring `"""One recorded interaction with an assistant suggestion."""` with:

```python
    """One thing the attorney did in the pane: an interaction with an assistant
    suggestion, or (surface="pane") a pane lifecycle event — pane_opened,
    tab_viewed, finalize_applied / finalize_failed, client_error, turn_waited.
    What sent it (Word host, pane build) comes from the request headers, not
    from here."""
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_feedback_store.py tests/test_feedback_api.py tests/test_turn_report.py -q`
Expected: all PASS.

- [ ] **Step 8: Mutation-check the upgrade test** — temporarily move `office_platform TEXT NOT NULL DEFAULT ''` into the `interaction_event` CREATE and delete its ALTER; run `uv run pytest tests/test_feedback_store.py -q -k before_sp3` → expect the `interaction_event` case to FAIL naming `office_platform`. Restore with `git checkout memory/db.py` and re-apply Step 4, or undo by hand; confirm `git diff memory/db.py` shows only Step 4's lines.

- [ ] **Step 9: Run the full backend suite**

Run: `uv run pytest tests/ -q`
Expected: all PASS.

- [ ] **Step 10: Commit**

```bash
git add memory/db.py memory/feedback_store.py api/routes/feedback.py api/models.py tests/test_feedback_store.py tests/test_feedback_api.py
git commit -m "feat(feedback): events and flags record which Word and which builds sent them

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Pane event module and a read-only document id

**Files:**
- Create: `clients/word/src/paneEvents.ts`
- Create: `clients/word/src/paneEvents.test.ts`
- Modify: `clients/word/src/docIdentity.ts` (add `readDocumentId`)
- Modify: `clients/word/src/docIdentity.test.ts` (2 assertions at the end)
- Modify: `scripts/check.sh` (`EXPECTED_PASS_COUNT=299` → `311`)

**Interfaces:**
- Consumes: `recordEvent(turn: TurnRef, surface: string, action: string, extra?: { targetKind?; targetRef?; detail?; request? }): void` from `clients/word/src/feedback.ts`.
- Produces: `readDocumentId(): string`; `setPaneSession(sessionId: string): void`; `recordPaneEvent(action: string, extra?: { turnId?: string; targetRef?: string; detail?: string }): void`; `reportClientError(where: string, error: unknown, turnId?: string): void`; `installErrorHandlers(target: EventTarget): void`; `startWait(now?: () => number): () => number`.

- [ ] **Step 1: Write the failing docIdentity assertions** — append to the END of `clients/word/src/docIdentity.test.ts`, and add `readDocumentId` to its existing import from `"./docIdentity"`:

```ts
// --- readDocumentId: read-only, never mints ----------------------------------
// The pane reads the id when it opens. resolveDocumentId() would CREATE one on a
// document that has none, writing Office settings — which marks the file
// modified, so Word asks "save changes?" about a document the attorney only
// looked at. readDocumentId must never write.
let settingsWrites = 0;
const settingsWith = (value: string | null) => ({
  context: { document: { settings: {
    get: (k: string) => (k === "legalTriageDocId" ? value : null),
    set: () => { settingsWrites++; },
    saveAsync: () => { settingsWrites++; },
  } } },
});
(globalThis as { Office?: unknown }).Office = settingsWith("doc-existing");
pass(readDocumentId() === "doc-existing", "readDocumentId returns the stored id");
(globalThis as { Office?: unknown }).Office = settingsWith(null);
pass(readDocumentId() === "" && settingsWrites === 0,
  "readDocumentId on a document with no id returns empty and writes nothing");
```

- [ ] **Step 2: Write the failing pane test** — create `clients/word/src/paneEvents.test.ts`:

```ts
// Pane lifecycle events (SP3). Run with: npx tsx src/paneEvents.test.ts
import {
  installErrorHandlers,
  recordPaneEvent,
  reportClientError,
  setPaneSession,
  startWait,
} from "./paneEvents";
import { pass } from "./testAssert";

(globalThis as { localStorage?: unknown }).localStorage = { getItem: () => null, setItem: () => {} };
(globalThis as { Office?: unknown }).Office = {
  context: { document: { settings: { get: () => "d-1", set: () => {}, saveAsync: () => {} } } },
};

type Sent = Record<string, string>;
let sent: Sent[] = [];
let onSend: () => void = () => {};
(globalThis as { fetch?: unknown }).fetch = (_url: string, init: { body: string }) => {
  sent.push(JSON.parse(init.body).events[0]);
  onSend();
  return Promise.resolve({ ok: true });
};
const last = (): Sent => sent[sent.length - 1];

setPaneSession("s-1");

recordPaneEvent("pane_opened", { detail: "saved" });
pass(
  last().surface === "pane" && last().action === "pane_opened" && last().detail === "saved" &&
    last().session_id === "s-1" && last().document_id === "d-1" && last().turn_id === "",
  "recordPaneEvent sends surface pane with the pane's session and document, and no turn",
);

recordPaneEvent("tab_viewed", { targetRef: "chat" });
pass(last().action === "tab_viewed" && last().target_ref === "chat",
  "tab_viewed carries the tab in target_ref");

recordPaneEvent("turn_waited", { turnId: "t-9", targetRef: "review", detail: "96100" });
pass(last().turn_id === "t-9" && last().target_ref === "review" && last().detail === "96100",
  "turn_waited carries its turn, its kind and the milliseconds");

reportClientError("chat", new Error("Backend returned 502 Bad Gateway"), "t-3");
pass(
  last().action === "client_error" && last().target_ref === "chat" &&
    last().detail === "Backend returned 502 Bad Gateway" && last().turn_id === "t-3",
  "reportClientError sends the message, where it happened and the turn",
);

reportClientError("preferences", "plain string");
pass(last().detail === "plain string" && last().turn_id === "",
  "a non-Error is sent as its string, with no turn");

// Uncaught errors and rejections, on a stand-in for window.
const win = new EventTarget();
installErrorHandlers(win);
installErrorHandlers(win); // a second install must not double every report
sent = [];
win.dispatchEvent(Object.assign(new Event("error"), { error: new Error("boom"), message: "boom" }));
pass(
  sent.length === 1 && last().action === "client_error" && last().target_ref === "window" &&
    last().detail === "boom",
  "an uncaught error is reported once, from window, with its message",
);

sent = [];
win.dispatchEvent(Object.assign(new Event("unhandledrejection"), { reason: new Error("lost promise") }));
pass(sent.length === 1 && last().detail === "lost promise",
  "an unhandled rejection is reported with its reason");

// A report that itself raises an error event must not loop. Without the guard
// each send dispatches another error (capped at 5 so a broken guard fails fast
// instead of hanging the gate).
sent = [];
let depth = 0;
onSend = () => {
  if (depth++ < 5) {
    win.dispatchEvent(Object.assign(new Event("error"), { error: new Error("again") }));
  }
};
win.dispatchEvent(Object.assign(new Event("error"), { error: new Error("first") }));
onSend = () => {};
pass(sent.length === 1 && sent[0].detail === "first",
  "an error raised while reporting an error is dropped, not re-reported");

let clock = 1000;
const elapsed = startWait(() => clock);
clock = 1250.6;
pass(elapsed() === 251, "startWait gives whole milliseconds since it started");
clock = 900;
pass(elapsed() === 0, "a clock that steps backwards never gives a negative wait");
```

- [ ] **Step 3: Run them to verify they fail**

Run: `cd clients/word && npx tsx src/paneEvents.test.ts; npx tsx src/docIdentity.test.ts | tail -3`
Expected: `paneEvents.test.ts` fails to load (`Cannot find module './paneEvents'`); `docIdentity.test.ts` errors with `readDocumentId is not a function` (or `does not provide an export named 'readDocumentId'`).

- [ ] **Step 4: Add `readDocumentId`** — in `clients/word/src/docIdentity.ts`, directly above `resolveDocumentId`:

```ts
/** The document's stored id, or "" — READ-ONLY. Never creates one: writing
 *  Office settings marks the document modified, so a pane that merely opened
 *  would make Word ask to save a file the attorney never changed. */
export function readDocumentId(): string {
  try {
    const existing = Office.context.document.settings.get(SETTINGS_KEY);
    return typeof existing === "string" ? existing : "";
  } catch {
    return "";
  }
}
```

- [ ] **Step 5: Create the module** — `clients/word/src/paneEvents.ts`:

```ts
// Pane lifecycle events (SP3): what the attorney does in the pane between
// turns, the errors it shows them, and how long they really waited — sent as
// interaction_event rows with surface "pane". Everything goes through
// recordEvent, so none of it can surface in the pane or break what the
// attorney is doing. Spec: docs/superpowers/specs/2026-10-14-pane-activity-design.md
import { recordEvent } from "./feedback";
import { readDocumentId } from "./docIdentity";

let paneSessionId = "";
let installedOn: EventTarget | null = null;
let reporting = false;

/** The pane's session (App mints it once per pane load); every pane event carries it. */
export function setPaneSession(sessionId: string): void {
  paneSessionId = sessionId;
}

/** Send one pane event. The document id is read at send time and never created. */
export function recordPaneEvent(
  action: string,
  extra: { turnId?: string; targetRef?: string; detail?: string } = {},
): void {
  recordEvent(
    { turnId: extra.turnId ?? "", traceId: "", sessionId: paneSessionId, documentId: readDocumentId() },
    "pane",
    action,
    { targetRef: extra.targetRef, detail: extra.detail },
  );
}

/** An error the pane showed the attorney — or an uncaught one, where = "window". */
export function reportClientError(where: string, error: unknown, turnId = ""): void {
  // If reporting raises another error event synchronously, drop it rather than loop.
  if (reporting) return;
  reporting = true;
  try {
    const detail = error instanceof Error ? error.message : String(error);
    recordPaneEvent("client_error", { turnId, targetRef: where, detail });
  } finally {
    reporting = false;
  }
}

/** Uncaught errors and unhandled rejections → client_error. Once per target. */
export function installErrorHandlers(target: EventTarget): void {
  if (installedOn === target) return;
  installedOn = target;
  target.addEventListener("error", (e) => {
    const ev = e as ErrorEvent;
    reportClientError("window", ev.error ?? ev.message ?? "unknown error");
  });
  target.addEventListener("unhandledrejection", (e) => {
    reportClientError("window", (e as PromiseRejectionEvent).reason ?? "unhandled rejection");
  });
}

/** Start a wait; the returned function gives the whole milliseconds since. */
export function startWait(now: () => number = () => Date.now()): () => number {
  const started = now();
  return () => Math.max(0, Math.round(now() - started));
}
```

- [ ] **Step 6: Raise the gate's count** — in `scripts/check.sh`, change `EXPECTED_PASS_COUNT=299` to `EXPECTED_PASS_COUNT=311`.

- [ ] **Step 7: Run to verify they pass**

Run: `cd clients/word && npx tsx src/paneEvents.test.ts | grep -c '^PASS' && npx tsx src/docIdentity.test.ts | grep -c '^FAIL'; npx tsc --noEmit`
Expected: `10`, then `0` FAILs, then no tsc output.

- [ ] **Step 8: Mutation-check the guard** — temporarily delete the line `if (reporting) return;` in `paneEvents.ts`; run `npx tsx src/paneEvents.test.ts | grep FAIL` → expect `FAIL: an error raised while reporting an error is dropped, not re-reported`. Restore the line.

- [ ] **Step 9: Commit**

```bash
git add clients/word/src/paneEvents.ts clients/word/src/paneEvents.test.ts clients/word/src/docIdentity.ts clients/word/src/docIdentity.test.ts scripts/check.sh
git commit -m "feat(pane): a pane event module, and a document id read that never writes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Wire the pane events into the pane

**Files:**
- Modify: `clients/word/src/App.tsx`
- Modify: `clients/word/src/components/ChatTab.tsx` (`send`)
- Modify: `clients/word/src/components/FindingsTab.tsx` (`onReview`)
- Modify: `clients/word/src/components/PreferencesTab.tsx` (load + save catches)
- Modify: `clients/word/src/components/ContextMeter.tsx` (`runCompaction` catch)
- Modify: `clients/word/src/components/FinalizeBar.tsx` (`onFinalize`)

**Interfaces:**
- Consumes (Task 2): `setPaneSession`, `recordPaneEvent`, `reportClientError`, `installErrorHandlers`, `startWait`.
- Produces: nothing new for later tasks — the rows they read (`surface="pane"`, actions per Global Constraints).

There is no React test harness in this repo (CLAUDE.md), so this task's check is `tsc`, the existing assertions, and the owner's sideload in Task 7.

- [ ] **Step 1: App — opened, tabs, uncaught errors, the disclosure** — in `clients/word/src/App.tsx` add the import:

```tsx
import { installErrorHandlers, recordPaneEvent, setPaneSession } from "./paneEvents";
```

Directly after the `const [tab, setTab] = useState<TabKey>("findings");` line insert:

```tsx
  // SP3: the pane's own lifecycle. One pane_opened per load — saved or not, read
  // WITHOUT minting a document id (that would mark the document modified).
  useEffect(() => {
    setPaneSession(sessionId);
    installErrorHandlers(window);
    isDocumentUnsaved()
      .then((u) => (u ? "unsaved" : "saved"))
      .catch(() => "")
      .then((saved) => recordPaneEvent("pane_opened", { detail: saved }));
  }, [sessionId]);

  const changeTab = (next: TabKey) => {
    if (next !== tab) recordPaneEvent("tab_viewed", { targetRef: next });
    setTab(next);
  };
```

Change `<Tabs active={tab} onChange={setTab} />` to `<Tabs active={tab} onChange={changeTab} />`.

Replace the disclosure paragraph's text:

```tsx
        <p className="disclosure">
          During testing, the assistant records when this pane is opened, which tab you
          use, how long answers take, errors it shows you, and what you do with its
          suggestions — so we can measure what it gets wrong.
        </p>
```

- [ ] **Step 2: Chat — wait and errors** — in `clients/word/src/components/ChatTab.tsx` add `import { recordPaneEvent, reportClientError, startWait } from "../paneEvents";`. In `send`, replace from `setBusy(true);` through the start of the `const turn: TurnRef = {` line with:

```tsx
    setBusy(true);
    // SP3: the wait as the attorney feels it — from Send, before Word is read.
    const elapsed = startWait();
    let sent = false;
    let turnId = "";
    try {
      const docText = await readBody();
      sent = true;
      const res = await chatQuery(question, docText, sessionId);
      turnId = res.data?.turn_id ?? "";
      if (res.status === "error") {
        const message = (res.errors ?? ["unknown error"])[0];
        setError(message);
        reportClientError("chat", message, turnId);
        setMemoryDegraded(false);
        setTruncated(null);
        return;
      }
      const turn: TurnRef = {
```

and replace the closing `catch`/`finally` of `send`:

```tsx
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      reportClientError("chat", e, turnId);
    } finally {
      if (sent) recordPaneEvent("turn_waited", { turnId, targetRef: "chat", detail: String(elapsed()) });
      setBusy(false);
    }
  };
```

- [ ] **Step 3: Review — wait and errors** — in `clients/word/src/components/FindingsTab.tsx` add `import { recordPaneEvent, reportClientError, startWait } from "../paneEvents";`. Replace `onReview`'s opening through `const res = await submitReview(text, sessionId);` and its error check with:

```tsx
  const onReview = async () => {
    setPersistError(null);
    // SP3: the wait as the attorney feels it — from pressing Review, before Word is read.
    const elapsed = startWait();
    let sent = false;
    let turnId = "";
    const fail = (message: string) => {
      setStatus({ kind: "error", message });
      reportClientError("review", message, turnId);
    };
    try {
      setStatus({ kind: "reading" });
      const text = await readBody();
      if (!text || text.trim().length < 20) {
        fail("Document is empty or too short to review.");
        return;
      }
      setStatus({ kind: "reviewing", charCount: text.length });
      sent = true;
      const res = await submitReview(text, sessionId);
      turnId = res.data?.turn_id ?? "";
      if (res.status === "error") {
        fail((res.errors ?? ["unknown error"])[0]);
        return;
      }
```

Replace `setStatus({ kind: "error", message: "Backend returned no review text." });` with `fail("Backend returned no review text.");`. Replace the closing `catch` of `onReview`:

```tsx
    } catch (e) {
      fail(e instanceof Error ? e.message : String(e));
    } finally {
      if (sent) recordPaneEvent("turn_waited", { turnId, targetRef: "review", detail: String(elapsed()) });
    }
  };
```

- [ ] **Step 4: Preferences and compaction errors** — in `clients/word/src/components/PreferencesTab.tsx` add `import { reportClientError } from "../paneEvents";` and, in BOTH `catch (e)` blocks (the load in the `useEffect` and `save`), add after the `setError(...)` line:

```tsx
        reportClientError("preferences", e);
```

In `clients/word/src/components/ContextMeter.tsx` add `import { reportClientError } from "../paneEvents";` and, as the first line inside `runCompaction`'s `catch (e) {`, add:

```tsx
      reportClientError("compact", e);
```

- [ ] **Step 5: Finalize** — in `clients/word/src/components/FinalizeBar.tsx` add `import { recordPaneEvent } from "../paneEvents";` and replace `onFinalize`'s two result lines with:

```tsx
    if (res.ok) {
      setStatus({ kind: "done", count: res.value });
      recordPaneEvent("finalize_applied");
    } else {
      setStatus({ kind: "error", message: res.error });
      recordPaneEvent("finalize_failed", { detail: res.error });
    }
```

- [ ] **Step 6: Typecheck and run every pane assertion**

Run: `cd clients/word && npx tsc --noEmit && for f in src/*.test.ts; do npx tsx "$f"; done | grep -c '^PASS: '`
Expected: no tsc output; `311`.

- [ ] **Step 7: Confirm no call site mints a document id on open** — run `grep -n "resolveDocumentId" clients/word/src/App.tsx clients/word/src/paneEvents.ts`. Expected: no line in `paneEvents.ts`; `App.tsx`'s existing uses only inside `onFlagRequested` (unchanged).

- [ ] **Step 8: Commit**

```bash
git add clients/word/src/App.tsx clients/word/src/components/ChatTab.tsx clients/word/src/components/FindingsTab.tsx clients/word/src/components/PreferencesTab.tsx clients/word/src/components/ContextMeter.tsx clients/word/src/components/FinalizeBar.tsx
git commit -m "feat(pane): record pane opens, tabs, Finalize, errors shown and the real wait

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `feedback_report --activity`

**Files:**
- Modify: `memory/feedback_store.py` (add `pane_events`)
- Modify: `memory/turn_log.py` (add `turn_summaries`)
- Modify: `scripts/feedback_report.py` (`--activity`, `build_activity`, `format_activity`, `print_activity`)
- Test: `tests/test_activity_report.py` (new)

**Interfaces:**
- Consumes (Task 1): the four client columns on `interaction_event`.
- Produces: `pane_events(*, since="", document_id="", attorney_id="", session_ids: list[str] | None = None, without_turn: bool = False) -> list[dict]` with keys `timestamp, attorney_id, session_id, turn_id, action, target_ref, detail, pane_build, backend_build`, oldest first; `turn_summaries(*, since="", attorney_id="", document_id="") -> list[dict]` with keys `timestamp, attorney_id, user_name, task_type, duration_ms` (kind='query' only), oldest first; `build_activity(turns, events) -> list[dict]`; `format_activity(row) -> list[str]`; `_secs(ms: float) -> str` (existing, widened to float).

- [ ] **Step 1: Write the failing tests** — create `tests/test_activity_report.py`:

```python
"""feedback_report --activity: one line per attorney per day — pane use beside the turns."""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from memory import feedback_store as fs
from memory.turn_log import build_turn_row, turn_summaries, write_turn
from scripts.feedback_report import build_activity, format_activity, main


def _turn(turn_id, **overrides):
    args = dict(
        kind="query", turn_id=turn_id, trace_id="", arrived_at="2026-10-15T09:46:56+00:00",
        duration_ms=7200, attorney_id="atty-1", user_name="A. Tester", session_id="s-1",
        document_id="doc-1", task_type="research", request="Who signs?",
        doc_text="NDA", status="ok", outcome="ok", degradations=[],
        api_error="", awaiting_review=False, facts={}, result=None, compaction=None,
        client={"office_platform": "Mac", "office_version": "16.89",
                "pane_build": "abc1234", "user_agent": "ua"},
        backend_build="abc1234",
    )
    args.update(overrides)
    return build_turn_row(**args)


def _t(ts, task_type="research", duration_ms=7200, attorney_id="atty-1", user_name="A. Tester"):
    return {"timestamp": ts, "attorney_id": attorney_id, "user_name": user_name,
            "task_type": task_type, "duration_ms": duration_ms}


def _e(ts, action, attorney_id="atty-1", target_ref="", detail="", pane_build="p1", backend_build="p1"):
    return {"timestamp": ts, "attorney_id": attorney_id, "session_id": "s-1", "turn_id": "",
            "action": action, "target_ref": target_ref, "detail": detail,
            "pane_build": pane_build, "backend_build": backend_build}


def test_a_day_with_opens_and_no_questions_still_shows():
    """The blind spot this exists for: the pane opened, nothing asked.
    Mutation: count opens from the turns."""
    (day,) = build_activity([], [_e("2026-10-15T09:36:00+00:00", "pane_opened"),
                                 _e("2026-10-15T14:02:00+00:00", "pane_opened")])
    assert (day["day"], day["opens"], day["chat"], day["reviews"]) == ("2026-10-15", 2, 0, 0)
    assert "opens 2 · chat 0 · reviews 0" in format_activity(day)[0]


def test_turns_are_counted_by_kind():
    (day,) = build_activity(
        [_t("2026-10-15T10:00:00+00:00"), _t("2026-10-15T10:05:00+00:00"),
         _t("2026-10-15T10:09:00+00:00", task_type="contract_review")],
        [])
    assert (day["chat"], day["reviews"]) == (2, 1)


def test_medians_keep_chat_and_review_apart():
    """Mutation: pool chat and review samples into one list."""
    turns = [_t("2026-10-15T10:00:00+00:00", duration_ms=500),
             _t("2026-10-15T10:01:00+00:00", duration_ms=700),
             _t("2026-10-15T10:02:00+00:00", task_type="contract_review", duration_ms=88000)]
    events = [_e("2026-10-15T10:00:09+00:00", "turn_waited", target_ref="chat", detail="1000"),
              _e("2026-10-15T10:01:09+00:00", "turn_waited", target_ref="chat", detail="3000"),
              _e("2026-10-15T10:01:30+00:00", "turn_waited", target_ref="chat", detail="9000"),
              _e("2026-10-15T10:03:40+00:00", "turn_waited", target_ref="review", detail="96100"),
              _e("2026-10-15T10:04:00+00:00", "turn_waited", target_ref="chat", detail="oops")]
    (day,) = build_activity(turns, events)
    line = format_activity(day)[1]
    assert "waited in Word: chat 3.0s · review 96.1s" in line
    assert "server: chat 0.6s · review 88.0s" in line


def test_attorneys_and_days_are_separate_entries_in_order():
    rows = build_activity(
        [_t("2026-10-16T09:00:00+00:00", attorney_id="atty-2", user_name="B. Two")],
        [_e("2026-10-15T09:00:00+00:00", "pane_opened"),
         _e("2026-10-16T08:00:00+00:00", "pane_opened")])
    assert [(r["day"], r["attorney_id"]) for r in rows] == [
        ("2026-10-15", "atty-1"), ("2026-10-16", "atty-1"), ("2026-10-16", "atty-2")]


def test_errors_and_finalize_are_counted():
    (day,) = build_activity([], [_e("2026-10-15T09:00:00+00:00", "client_error", target_ref="chat"),
                                 _e("2026-10-15T09:10:00+00:00", "finalize_applied"),
                                 _e("2026-10-15T09:11:00+00:00", "finalize_failed")])
    assert (day["errors"], day["finalize"]) == (1, 1)


def test_the_build_line_marks_a_stale_pane_only_when_both_builds_differ():
    (stale,) = build_activity([], [_e("2026-10-15T09:00:00+00:00", "pane_opened",
                                      pane_build="old1", backend_build="new2")])
    assert format_activity(stale)[2].endswith("pane old1 · backend new2  ⚠ stale pane")
    (same,) = build_activity([], [_e("2026-10-15T09:00:00+00:00", "pane_opened")])
    assert "stale" not in format_activity(same)[2]


def test_a_day_from_a_pre_sp3_pane_shows_its_turns_and_no_opens():
    """Review Focus #3: turns but no pane events — opens 0, builds unknown, no crash."""
    (day,) = build_activity([_t("2026-10-15T10:00:00+00:00")], [])
    lines = format_activity(day)
    assert "opens 0 · chat 1" in lines[0]
    assert "waited in Word: chat — · review —" in lines[1]
    assert lines[2].strip() == "pane — · backend —"


def test_the_name_comes_from_the_turns_and_falls_back_to_the_id():
    named, unnamed = build_activity(
        [_t("2026-10-15T10:00:00+00:00")],
        [_e("2026-10-15T09:00:00+00:00", "pane_opened", attorney_id="atty-zzz")])
    assert "A. Tester (atty-1)" in format_activity(named)[0]
    assert "  atty-zzz   opens 1" in format_activity(unnamed)[0]


def test_turn_summaries_keeps_only_questions():
    """A resume continues a review already counted; a compaction is not a question."""
    write_turn(_turn("q-1"))
    write_turn(_turn("r-1", kind="resume", task_type="contract_review"))
    write_turn(_turn("c-1", kind="compact", task_type=""))
    assert [t["task_type"] for t in turn_summaries()] == ["research"]


def test_pane_events_returns_only_the_pane_surface_oldest_first():
    fs.record_events([{"surface": "chat", "action": "edit_applied"},
                      {"surface": "pane", "action": "pane_opened", "detail": "saved"},
                      {"surface": "pane", "action": "tab_viewed", "target_ref": "chat"}],
                     attorney_id="atty-1")
    assert [e["action"] for e in fs.pane_events()] == ["pane_opened", "tab_viewed"]


def test_the_cli_reads_both_tables(monkeypatch, capsys):
    now = datetime.now(timezone.utc).isoformat()
    write_turn(_turn("q-1", arrived_at=now, duration_ms=7200))
    fs.record_events([{"surface": "pane", "action": "pane_opened", "session_id": "s-1"},
                      {"surface": "pane", "action": "turn_waited", "turn_id": "q-1",
                       "target_ref": "chat", "detail": "9800"}],
                     attorney_id="atty-1",
                     client={"office_platform": "Mac", "pane_build": "abc1234"},
                     backend_build="abc1234")
    monkeypatch.setattr(sys, "argv", ["feedback_report", "--activity"])
    main()
    out = capsys.readouterr().out
    assert "=== Activity (1 attorney-days) ===" in out
    assert "A. Tester (atty-1)   opens 1 · chat 1 · reviews 0" in out
    assert "waited in Word: chat 9.8s" in out and "server: chat 7.2s" in out
    assert "pane abc1234 · backend abc1234" in out
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_activity_report.py -q`
Expected: collection ERROR — `ImportError: cannot import name 'turn_summaries' from 'memory.turn_log'` (and `build_activity`).

- [ ] **Step 3: Add the two reads** — in `memory/feedback_store.py`, append:

```python
def pane_events(
    *, since: str = "", document_id: str = "", attorney_id: str = "",
    session_ids: list[str] | None = None, without_turn: bool = False,
) -> list[dict]:
    """The pane's own lifecycle events (surface='pane'), oldest first: opens,
    tabs, Finalize, errors shown, and turn_waited. `session_ids` narrows to the
    sessions a --turns page shows; `without_turn` keeps only the events that
    belong to no turn.

    Keys: timestamp, attorney_id, session_id, turn_id, action, target_ref,
    detail, pane_build, backend_build.
    """
    where, params = _filters(since, document_id, attorney_id)
    clauses = ["surface = 'pane'"] + ([where] if where else [])
    if session_ids is not None:
        clauses.append("session_id = ANY(%s)")
        params.append(list(session_ids))
    if without_turn:
        clauses.append("turn_id = ''")
    with get_pool().connection() as conn:
        cur = conn.execute(
            f"""SELECT timestamp, attorney_id, session_id, turn_id, action, target_ref,
                       detail, pane_build, backend_build
                FROM interaction_event WHERE {' AND '.join(clauses)}
                ORDER BY timestamp, id""",
            params,
        )
        rows = cur.fetchall()
    keys = ("timestamp", "attorney_id", "session_id", "turn_id", "action", "target_ref",
            "detail", "pane_build", "backend_build")
    return [dict(zip(keys, r)) for r in rows]
```

In `memory/turn_log.py`, append:

```python
def turn_summaries(
    *, since: str = "", attorney_id: str = "", document_id: str = "",
) -> list[dict[str, Any]]:
    """Every question in scope, oldest first — the counts and server times that
    feedback_report --activity sets beside the pane's own events. kind='query'
    only: a resume continues a review already counted, a compaction asks nothing."""
    where, params = _filters(since, document_id, attorney_id)
    sql = f"""
        SELECT timestamp, attorney_id, user_name, task_type, duration_ms
        FROM turn_log WHERE kind = 'query' {"AND " + where if where else ""}
        ORDER BY timestamp, id
    """
    with get_pool().connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    keys = ("timestamp", "attorney_id", "user_name", "task_type", "duration_ms")
    return [dict(zip(keys, r)) for r in rows]
```

- [ ] **Step 4: Add the report** — in `scripts/feedback_report.py`:

Add `from statistics import median` to the stdlib imports; extend the `memory.feedback_store` import with `pane_events`; change `from memory.turn_log import recent_turns` to `from memory.turn_log import recent_turns, turn_summaries`. Add to the module docstring's usage block: `    uv run python -m scripts.feedback_report --activity --days 7`. Add to `_parse_args` after `--turns`:

```python
    p.add_argument("--activity", action="store_true",
                   help="print one line per attorney per day: pane use beside the turns")
```

Change `def _secs(ms: int) -> str:` to `def _secs(ms: float) -> str:` and add after it:

```python
# turn_log task types as --activity names them; anything else (compaction) is not counted.
_TASK_KIND = {"research": "chat", "contract_review": "review"}


def _median_secs(values: list[int]) -> str:
    return _secs(median(values)) if values else "—"


def build_activity(turns: list[dict], events: list[dict]) -> list[dict]:
    """One entry per (UTC day, attorney), oldest day first: pane use from the
    pane's events beside the questions the backend served. A day with pane
    opens and no turns is exactly the one the turn tables cannot show.
    `events` must be oldest first, so the last build seen is the day's latest."""
    names = {t["attorney_id"]: t["user_name"] for t in turns if t["user_name"]}
    days: dict[tuple[str, str], dict] = {}

    def entry(timestamp: str, attorney_id: str) -> dict:
        key = (timestamp[:10], attorney_id)
        if key not in days:
            days[key] = {
                "day": key[0], "attorney_id": attorney_id, "name": names.get(attorney_id, ""),
                "opens": 0, "chat": 0, "reviews": 0, "finalize": 0, "errors": 0,
                "wait": {"chat": [], "review": []}, "server": {"chat": [], "review": []},
                "pane_build": "", "backend_build": "",
            }
        return days[key]

    for t in turns:
        kind = _TASK_KIND.get(t["task_type"])
        if kind is None:
            continue
        d = entry(t["timestamp"], t["attorney_id"])
        d["chat" if kind == "chat" else "reviews"] += 1
        d["server"][kind].append(t["duration_ms"])
    for e in events:
        d = entry(e["timestamp"], e["attorney_id"])
        if e["action"] == "pane_opened":
            d["opens"] += 1
        elif e["action"] == "finalize_applied":
            d["finalize"] += 1
        elif e["action"] == "client_error":
            d["errors"] += 1
        elif (e["action"] == "turn_waited" and e["target_ref"] in d["wait"]
              and e["detail"].isdigit()):
            d["wait"][e["target_ref"]].append(int(e["detail"]))
        if e["pane_build"] or e["backend_build"]:
            d["pane_build"], d["backend_build"] = e["pane_build"], e["backend_build"]
    return [days[k] for k in sorted(days)]


def format_activity(d: dict) -> list[str]:
    """One attorney-day: counts, medians (Word wait beside server time), builds."""
    who = f"{d['name']} ({d['attorney_id']})" if d["name"] else d["attorney_id"]
    stale = ("  ⚠ stale pane" if d["pane_build"] and d["backend_build"]
             and d["pane_build"] != d["backend_build"] else "")
    return [
        f"  {d['day']}  {who}   opens {d['opens']} · chat {d['chat']} · reviews {d['reviews']}"
        f" · finalize {d['finalize']} · errors {d['errors']}",
        f"    waited in Word: chat {_median_secs(d['wait']['chat'])} · review "
        f"{_median_secs(d['wait']['review'])}   server: chat {_median_secs(d['server']['chat'])}"
        f" · review {_median_secs(d['server']['review'])}   (medians)",
        f"    pane {d['pane_build'] or '—'} · backend {d['backend_build'] or '—'}{stale}",
    ]


def print_activity(rows: list[dict]) -> None:
    print(f"=== Activity ({len(rows)} attorney-days) ===")
    print("  One entry per attorney per UTC day: pane opens and what was done in the")
    print("  pane, beside the questions the backend served. How to read it: docs/feedback-loop.md.")
    print()
    if not rows:
        print("  (none yet)\n")
        return
    for d in rows:
        print("\n".join(format_activity(d)))
        print()
```

In `main()`, directly before `if args.turns:`, insert:

```python
    if args.activity:
        print_activity(build_activity(
            turn_summaries(since=since, attorney_id=args.attorney, document_id=args.document),
            pane_events(**f),
        ))
        return
```

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest tests/test_activity_report.py tests/test_turn_report.py -q`
Expected: all PASS.

- [ ] **Step 6: Mutation-check** — temporarily change `if e["action"] == "pane_opened":` to `if False:` and add `d["opens"] += 1` under the `d["chat" if …] += 1` line; run `uv run pytest tests/test_activity_report.py -q -k no_questions` → expect FAIL. Restore. Then temporarily replace `d["wait"][e["target_ref"]].append(...)` with `d["wait"]["chat"].append(int(e["detail"]))` → `-k medians` expect FAIL. Restore; `git diff` shows only the intended code.

- [ ] **Step 7: Commit**

```bash
git add memory/feedback_store.py memory/turn_log.py scripts/feedback_report.py tests/test_activity_report.py
git commit -m "feat(report): --activity — pane opens, errors and real waits per attorney per day

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `--turns` shows the wait and the pane events between turns

**Files:**
- Modify: `memory/turn_log.py` (`recent_turns` adds `waited_ms`)
- Modify: `scripts/feedback_report.py` (`format_turn`, `format_pane_event`, `print_turns`, `main`, the per-item section)
- Test: `tests/test_turn_report.py`

**Interfaces:**
- Consumes (Task 4): `pane_events(session_ids=..., without_turn=True)`.
- Produces: `recent_turns` rows gain `waited_ms: int | None`; `format_pane_event(e: dict) -> str`; `print_turns(rows, limit, pane_events: list[dict] = ())`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_turn_report.py`, adding `from memory.db import get_pool` and `from scripts.feedback_report import format_pane_event` (extend the existing import) at the top:

```python
def _pane_row(ts, action, *, session_id="s-1", turn_id="", target_ref="", detail=""):
    """A pane event at a chosen time — record_events always stamps now()."""
    with get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO interaction_event (timestamp, turn_id, session_id, document_id, "
            "attorney_id, surface, action, target_ref, detail) "
            "VALUES (%s, %s, %s, 'doc-1', 'atty-1', 'pane', %s, %s, %s)",
            (ts, turn_id, session_id, action, target_ref, detail),
        )


def test_each_turn_shows_the_wait_its_own_turn_waited_recorded():
    """Mutation: join turn_waited on session_id instead of turn_id."""
    write_turn(_turn("t-1", arrived_at="2026-10-09T10:00:00+00:00"))
    write_turn(_turn("t-2", arrived_at="2026-10-09T10:05:00+00:00"))
    _pane_row("2026-10-09T10:00:20+00:00", "turn_waited", turn_id="t-1",
              target_ref="chat", detail="9800")
    _pane_row("2026-10-09T10:05:20+00:00", "turn_waited", turn_id="t-2",
              target_ref="chat", detail="not-a-number")
    first, second = recent_turns()
    assert (first["waited_ms"], second["waited_ms"]) == (9800, None)
    assert "13.1s total (waited 9.8s in Word)" in _text(first)
    assert "waited" not in _text(second)


def test_the_timeline_puts_this_sessions_pane_events_between_its_turns(monkeypatch, capsys):
    write_turn(_turn("t-1", arrived_at="2026-10-09T10:00:00+00:00"))
    write_turn(_turn("t-2", arrived_at="2026-10-09T10:05:00+00:00"))
    _pane_row("2026-10-09T09:59:00+00:00", "pane_opened", detail="saved")
    _pane_row("2026-10-09T10:02:00+00:00", "tab_viewed", target_ref="preferences")
    _pane_row("2026-10-09T10:03:00+00:00", "pane_opened", session_id="s-other", detail="saved")
    _pane_row("2026-10-09T10:00:20+00:00", "turn_waited", turn_id="t-1",
              target_ref="chat", detail="9800")
    monkeypatch.setattr(sys, "argv", ["feedback_report", "--turns"])
    main()
    out = capsys.readouterr().out
    opened = out.index("· pane opened (saved)")
    first = out.index("turn: t-1")
    tab = out.index("· tab: preferences")
    second = out.index("turn: t-2")
    assert opened < first < tab < second
    assert out.count("pane opened") == 1          # the other session's open is not here
    assert "turn_waited" not in out               # a wait shows on its turn, not as a line
    assert "=== Turns (2) ===" in out             # pane lines are not counted as turns


def test_each_pane_event_reads_as_one_line():
    e = {"timestamp": "2026-10-09T10:02:00+00:00", "target_ref": "", "detail": ""}
    assert format_pane_event({**e, "action": "pane_opened", "detail": "unsaved"}) == \
        "  2026-10-09T10:02:00  · pane opened (unsaved)"
    assert format_pane_event({**e, "action": "pane_opened"}).endswith("(save state unknown)")
    assert format_pane_event({**e, "action": "finalize_failed", "detail": "x" * 300}) == \
        "  2026-10-09T10:02:00  · finalize failed: " + "x" * 120
    assert format_pane_event({**e, "action": "client_error", "target_ref": "compact",
                              "detail": "Backend returned 502"}).endswith(
        "· error in compact: Backend returned 502")


def test_the_feedback_summary_leaves_pane_activity_to_activity(monkeypatch, capsys):
    record_event(turn_id="", session_id="s-1", document_id="doc-1", attorney_id="atty-1",
                 surface="pane", action="pane_opened")
    record_event(turn_id="t-1", session_id="s-1", document_id="doc-1", attorney_id="atty-1",
                 surface="chat", action="edit_applied")
    monkeypatch.setattr(sys, "argv", ["feedback_report"])
    main()
    out = capsys.readouterr().out
    assert "=== Per-item actions (1) ===" in out
    assert "pane_opened" not in out and "--activity" in out
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_turn_report.py -q`
Expected: collection ERROR — `ImportError: cannot import name 'format_pane_event' from 'scripts.feedback_report'`.

- [ ] **Step 3: Add `waited_ms`** — in `memory/turn_log.py::recent_turns`, inside the inner `SELECT t.*,` list, after the `AS flags` line, add a comma to it and then:

```sql
                (SELECT max(e.detail::bigint) FROM interaction_event e
                  WHERE e.turn_id = t.turn_id AND e.action = 'turn_waited'
                    AND e.detail ~ '^[0-9]+$') AS waited_ms
```

- [ ] **Step 4: Print it, and interleave the pane** — in `scripts/feedback_report.py::format_turn`, replace the timing `lines.append(` with:

```python
    waited = f" (waited {_secs(t['waited_ms'])} in Word)" if t["waited_ms"] is not None else ""
    lines.append(
        f"    {_secs(t['duration_ms'])} total{waited} · {t['llm_calls']} model call(s): "
        f"load {_secs(t['llm_load_ms'])} · prompt {_secs(t['llm_prompt_ms'])} · "
        f"gen {_secs(t['llm_gen_ms'])} · {t['tokens_in']}→{t['tokens_out']} tokens"
    )
```

Add after `format_turn`:

```python
def format_pane_event(e: dict) -> str:
    """A pane event that belongs to no turn, as one timeline line."""
    what = {
        "pane_opened": f"pane opened ({e['detail'] or 'save state unknown'})",
        "tab_viewed": f"tab: {e['target_ref']}",
        "finalize_applied": "finalize applied",
        "finalize_failed": f"finalize failed: {e['detail'][:120]}",
        "client_error": f"error in {e['target_ref'] or 'pane'}: {e['detail'][:120]}",
    }.get(e["action"], e["action"])
    return f"  {e['timestamp'][:19]}  · {what}"
```

Replace `print_turns` with:

```python
def print_turns(rows: list[dict], limit: int, pane_events: list[dict] = ()) -> None:
    """`rows` is recent_turns(limit=limit + 1): the one extra row, when it exists,
    is the proof that older turns were cut off. It is the oldest written (lowest
    id), not printed — a page of exactly `limit` turns cut nothing and says so.
    `pane_events` (no turn, same sessions) are printed between the turns by time;
    on a cut page only those from the first shown turn on."""
    cut = len(rows) > limit
    if cut:
        oldest = min(row["id"] for row in rows)
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
    entries = [(r["timestamp"], True, format_turn(r)) for r in rows] + [
        (e["timestamp"], False, [format_pane_event(e)])
        for e in pane_events if e["timestamp"] >= start
    ]
    for _, is_turn, lines in sorted(entries, key=lambda x: (x[0], x[1])):
        print("\n".join(lines))
        if is_turn:
            print()
```

In `main()`, replace the `if args.turns:` block with:

```python
    if args.turns:
        rows = recent_turns(since=since, attorney_id=args.attorney,
                            document_id=args.document, limit=args.limit + 1)
        sessions = sorted({r["session_id"] for r in rows if r["session_id"]})
        events = pane_events(session_ids=sessions, without_turn=True) if sessions else []
        print_turns(rows, args.limit, events)
        return
```

In the per-item section, replace the `item_counts = ...` line with:

```python
    item_counts = [c for c in event_counts(**f)
                   if c["action"] not in COUNTER_ACTIONS and c["surface"] != "pane"]
```

and replace the line `print("  denominators, and the two must not be divided across sections.\n")` with:

```python
    print("  denominators, and the two must not be divided across sections.")
    print("  Pane activity (opens, tabs, errors, waits) is under --activity, not here.\n")
```

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest tests/test_turn_report.py tests/test_activity_report.py -q`
Expected: all PASS. (If an existing `print_turns` test asserts the old two header lines verbatim, update only its expected header text to the new wording — the behaviour under test is unchanged.)

- [ ] **Step 6: Mutation-check** — temporarily change `WHERE e.turn_id = t.turn_id` to `WHERE e.session_id = t.session_id`; run `-k own_turn_waited` → expect FAIL. Restore.

- [ ] **Step 7: Run the full backend suite**

Run: `uv run pytest tests/ -q`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add memory/turn_log.py scripts/feedback_report.py tests/test_turn_report.py
git commit -m "feat(report): --turns shows the wait in Word and the pane events between turns

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Caddy access and TLS-error logs

**Files:**
- Modify: `Caddyfile`
- Modify: `docker-compose.remote.yml` (service `caddy`, `volumes`)
- Modify: `scripts/check.sh` (a Caddyfile validation step)
- Modify: `docs/deploy-vm.md` (*Logs* recipe; *Working files* table row; Step 5 TLS check)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `data/caddy-logs/access.log`, `data/caddy-logs/tls-errors.log` on the host (JSON lines).

- [ ] **Step 1: Add a failing gate step** — in `scripts/check.sh`, insert directly before the final `echo "==> all checks passed"`:

```bash
# The VM's caddy reads ./Caddyfile at start; a directive it rejects is a pane
# that does not load for anyone. Validate with the image the pane is built on.
echo "==> Caddyfile validates under the pinned Caddy (clients/word/Dockerfile)"
caddy_image="$(grep -m1 -o 'caddy:[^ ]*' clients/word/Dockerfile)"
caddy_log="$(mktemp)"
if ! docker run --rm -v "$REPO_ROOT/Caddyfile:/etc/caddy/Caddyfile:ro" "$caddy_image" \
     caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >"$caddy_log" 2>&1; then
  cat "$caddy_log" >&2; rm -f "$caddy_log"
  echo "FAIL: the Caddyfile does not validate under $caddy_image — the VM's caddy would not start." >&2
  exit 1
fi
rm -f "$caddy_log"
```

Prove it can fail: temporarily add a line `	bogus_directive` inside the site block of `Caddyfile`, run only the new step's `docker run … caddy validate …` command → expect a non-zero exit. Remove the line.

- [ ] **Step 2: Add the logs** — `Caddyfile`: insert at the very top of the file (before `{$ADDIN_ORIGIN_HOST:localhost} {`):

```caddyfile
# Global options (SP3, docs/superpowers/specs/2026-10-14-pane-activity-design.md).
# A machine that rejects our certificate leaves a trace ONLY at DEBUG, from the
# http.stdlib logger: "TLS handshake error from <ip>: … unknown certificate
# authority". This log keeps exactly that logger, so the DEBUG noise of every
# other module stays out. Files are 0644 so the deploy user reads them without
# sudo (data/caddy-logs/ on the host); rolled files are gzip-compressed.
{
	log tls_errors {
		output file /var/log/caddy/tls-errors.log {
			roll_size 10MiB
			roll_keep 10
			roll_keep_for 2160h
			mode 0644
		}
		format json
		level DEBUG
		include http.stdlib
	}
}

```

and inside the site block, directly after `	encode gzip`, insert:

```caddyfile

	# Every request to the site — pane loads, API calls, the ones turned away.
	# Authorization and Cookie values are logged as REDACTED (Caddy's default).
	log {
		output file /var/log/caddy/access.log {
			roll_size 10MiB
			roll_keep 10
			roll_keep_for 2160h
			mode 0644
		}
		format json
	}
```

`docker-compose.remote.yml`, service `caddy`, `volumes:` — add after `- caddy_config:/config`:

```yaml
      # Access + TLS-error logs (Caddyfile), readable by the deploy user without
      # sudo. data/ is outside git and outside the backend image.
      - ./data/caddy-logs:/var/log/caddy
```

- [ ] **Step 3: Verify the step now passes**

Run: `bash scripts/check.sh 2>&1 | tail -3`
Expected: `==> Caddyfile validates under the pinned Caddy (clients/word/Dockerfile)` then `==> all checks passed`.

- [ ] **Step 4: Verify the logs locally** (scratch folder, removed after):

```bash
S=$(mktemp -d); mkdir -p "$S/logs"
IMG=$(grep -m1 -o 'caddy:[^ ]*' clients/word/Dockerfile)
docker run -d --name sp3-caddy -p 18443:443 -e ADDIN_ORIGIN_HOST=localhost \
  -v "$PWD/Caddyfile:/etc/caddy/Caddyfile:ro" -v "$S/logs:/var/log/caddy" "$IMG"
sleep 3
curl -s -o /dev/null https://localhost:18443/taskpane.html; echo "untrusted curl exit $?"   # 60
curl -sk -o /dev/null "https://localhost:18443/taskpane.html?_host_Info=Word\$Mac\$16.01\$en-US"
curl -sk -o /dev/null -H "X-User-ID: atty-1" -H "X-User-Name: A. Tester" \
  -H "Authorization: Bearer SECRET" https://localhost:18443/api/preferences
sleep 1; ls -l "$S/logs"
grep -c SECRET "$S/logs/access.log"
zcat -f "$S"/logs/access*.log* | jq -r 'select(.request.uri | startswith("/taskpane.html")) | [(.ts | todate | .[0:10]), .request.client_ip, (.request.headers["User-Agent"][0] // "")] | @tsv' | sort | uniq -c
zcat -f "$S"/logs/access*.log* | jq -r 'select(.request.uri | startswith("/api/")) | [(.ts | todate | .[0:10]), (.request.headers["X-User-Id"][0] // "-"), (.request.headers["X-User-Name"][0] // "")] | @tsv' | sort | uniq -c
zcat -f "$S"/logs/access*.log* | jq -r 'select(.status >= 400) | [(.ts | todate), .status, .request.method, .request.uri, .request.client_ip] | @tsv'
zcat -f "$S"/logs/tls-errors*.log* | jq -r '[(.ts | todate | .[0:10]), (.msg | capture("from (?<ip>[^ ]+):[0-9]+:").ip // "?"), (.msg | sub(".*: "; ""))] | @tsv' | sort | uniq -c
docker rm -f sp3-caddy; rm -r "$S"
```

Expected: `untrusted curl exit 60`; both files `-rw-r--r--`; `0` for SECRET; one pane-load line counting **2** (the bare URL and the `?_host_Info=` one); one API line `atty-1	A. Tester`; the status ≥ 400 list shows the `/api/preferences` request (502 — no backend in this test); one TLS line ending `unknown certificate authority`. If any recipe returns nothing, fix the recipe here before Step 5 uses it.

- [ ] **Step 5: Document it** — `docs/deploy-vm.md`:

In the *Working files* table, add a row after `data/backups/`:

```markdown
| `data/caddy-logs/` | Written by Caddy, not by hand: `access.log` (every request to the site) and `tls-errors.log` (handshakes our certificate failed). JSON lines, `0644`. | Rolled by Caddy at 10 MB, 10 kept, 90 days at most; rolled files are gzipped. |
```

At the end of the *Logs* section, add:

````markdown
**Caddy's own files** (since SP3): `data/caddy-logs/access.log` has one JSON line per request to the site — pane loads, API calls, and requests turned away — and `tls-errors.log` keeps only the handshakes our certificate failed, which Caddy reports at DEBUG and nowhere else. A TLS failure carries an IP and no identity: it fails before any request exists. Both are readable without `sudo`. Rolled files are gzipped, so read them with `zcat -f`:

```bash
cd ~/legal-plugin
# pane loads per UTC day, by IP and user agent (Office appends ?_host_Info=…)
zcat -f data/caddy-logs/access*.log* | jq -r 'select(.request.uri | startswith("/taskpane.html")) | [(.ts | todate | .[0:10]), .request.client_ip, (.request.headers["User-Agent"][0] // "")] | @tsv' | sort | uniq -c
# API requests per UTC day per attorney
zcat -f data/caddy-logs/access*.log* | jq -r 'select(.request.uri | startswith("/api/")) | [(.ts | todate | .[0:10]), (.request.headers["X-User-Id"][0] // "-"), (.request.headers["X-User-Name"][0] // "")] | @tsv' | sort | uniq -c
# every response that was not a success
zcat -f data/caddy-logs/access*.log* | jq -r 'select(.status >= 400) | [(.ts | todate), .status, .request.method, .request.uri, .request.client_ip] | @tsv'
# handshakes our certificate failed, per UTC day and IP
zcat -f data/caddy-logs/tls-errors*.log* | jq -r '[(.ts | todate | .[0:10]), (.msg | capture("from (?<ip>[^ ]+):[0-9]+:").ip // "?"), (.msg | sub(".*: "; ""))] | @tsv' | sort | uniq -c
```
````

In *Step 5 — Verify*, after the `turn_log` row check, add:

````markdown
**Confirm Caddy's files are written and readable without sudo**, and that a rejected certificate is recorded — run the `curl` from a machine that does NOT trust the VM's CA (e.g. a Mac without `caddy-root-ca.crt`):

```bash
ls -l data/caddy-logs/                     # access.log and tls-errors.log, -rw-r--r--
curl -s -o /dev/null https://<hostname>/   # from the untrusting machine: exit 60
tail -1 data/caddy-logs/tls-errors.log     # "... TLS handshake error from <ip>: remote error: tls: unknown certificate authority"
```
````

- [ ] **Step 6: Commit**

```bash
git add Caddyfile docker-compose.remote.yml scripts/check.sh docs/deploy-vm.md
git commit -m "feat(caddy): access and TLS-error logs as JSON files readable without sudo

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Docs, the full gate, and the owner's sideload

**Files:**
- Modify: `docs/feedback-loop.md`, `docs/tester-setup.md`, `docs/wiki.md`, `CLAUDE.md`

**Interfaces:**
- Consumes: everything above. Produces: nothing for code.

- [ ] **Step 1: `docs/feedback-loop.md`** — add a new section directly before `## From a complaint to the prompt that caused it`:

````markdown
## Pane use, day by day

```bash
uv run python -m scripts.feedback_report --activity --days 7
```

One entry per attorney per UTC day:

```
  2026-10-15  A. Tester (cc91e640-…)   opens 3 · chat 2 · reviews 1 · finalize 0 · errors 1
    waited in Word: chat 9.8s · review 96.1s   server: chat 7.2s · review 88.0s   (medians)
    pane 1a2b3c4 · backend 1a2b3c4
```

- **opens** counts `pane_opened` — a day with opens and `chat 0 · reviews 0` is someone who looked and asked nothing, the case no turn table can show.
- **waited in Word** is measured in the pane from pressing Send / Review to the result on screen; **server** is `turn_log.duration_ms`. The gap is reading the document out of Word, the network and Caddy.
- Opens, waits, tabs and errors exist only from panes built after SP3 (2026-10-14); a day from an older pane shows its turns with `opens 0` and builds `—`.
- `--document` narrows by document id, and a pane opened on a document that never got one (never reviewed or chatted) has none — leave `--document` off when counting opens.

The pane's events, all `interaction_event` rows with `surface = 'pane'`:

| `action` | When | `target_ref` | `detail` |
|---|---|---|---|
| `pane_opened` | each pane load | — | `saved` / `unsaved` / `""` |
| `tab_viewed` | each tab switch | `findings` / `chat` / `preferences` | — |
| `finalize_applied` / `finalize_failed` | after the in-pane confirm | — | the error, on failure |
| `client_error` | an error the pane showed, or an uncaught one | `chat` / `review` / `compact` / `preferences` / `window` | the message |
| `turn_waited` | a chat answer or review on screen, or failed | `chat` / `review` | milliseconds |

`--turns` now shows each turn's `(waited X in Word)` and prints the session's turn-less pane events (opened, tab, Finalize, errors) between the turns. Requests that never reached the backend are in Caddy's files — [deploy-vm.md](deploy-vm.md), *Logs*.
````

- [ ] **Step 2: `docs/tester-setup.md`** — insert directly before `## If something goes wrong`:

```markdown
## What the assistant records

While we test, the assistant records:

- the questions you ask and the reviews you run, and its answers;
- what you do with its suggestions — applied, discarded, or failed to apply;
- **when the task pane is opened, which tab you use, how long answers take, and any error it shows you.**

None of this records the text of your document, except that an error message can quote the passage it was working on. It is used only to find what the assistant gets wrong.

---

```

- [ ] **Step 3: `docs/wiki.md`** — in *Shipped Since Last Update*, add a row directly above the `**Per-turn record — \`turn_log\`**` row:

```markdown
| **Pane activity + Caddy logs (SP3)** | `feat/pane-activity` | The 2026-10-14 audit could not say whether the only tester had opened the pane since his one question. The pane now records `pane_opened`, `tab_viewed`, `finalize_applied`/`finalize_failed`, `client_error` and `turn_waited` (the wait measured in Word) as `interaction_event` rows with `surface='pane'`, reading the document id without ever minting one. `interaction_event` and `feedback` carry `office_platform`/`office_version`/`pane_build`/`backend_build`. Caddy writes `data/caddy-logs/access.log` and `tls-errors.log` (handshakes our certificate failed — DEBUG-only in Caddy), JSON, `0644`, no sudo. `feedback_report --activity` gives one line per attorney per day; `--turns` shows the Word wait and the pane events between turns. Spec `docs/superpowers/specs/2026-10-14-pane-activity-design.md`. |
```

and replace the cell `Not started.` at the start of the SP3 follow-up row's third column with `**Shipped** (see Shipped: Pane activity + Caddy logs). Kept for context:`.

- [ ] **Step 4: `CLAUDE.md`** — in the *Feedback capture* bullet, after the sentence ending `Read it with \`feedback_report --turns\`.`, add: ` Pane use between turns (opens, tabs, Finalize, errors, the wait measured in Word) is \`interaction_event\` with \`surface='pane'\`, read with \`--activity\`; the pane reads the document id with \`readDocumentId()\`, never \`resolveDocumentId()\`, which would mint one and dirty the file. Requests that never reach the backend are in Caddy's \`data/caddy-logs/\`.` Then run `wc -l CLAUDE.md` — expected ≤ 150.

- [ ] **Step 5: Full gate**

Run: `bash scripts/check.sh`
Expected: `==> word add-in assertions: 311/311 PASS`, the Caddyfile step passes, `==> all checks passed`.

- [ ] **Step 6: Commit the docs**

```bash
git add docs/feedback-loop.md docs/tester-setup.md docs/wiki.md CLAUDE.md
git commit -m "docs: pane activity — reading it, what testers are told, the runbook

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 7: Owner's sideload (Word for Mac)** — the controller starts `bash scripts/start.sh` and `cd clients/word && npm run dev`, and asks the owner to: quit Word (⌘Q), open a scratch copy of a contract, open **Legal Triage (Dev)**, switch to Chat then Preferences then Chat, ask one question, then Finalize on that scratch copy. Then read:

```bash
docker exec legal-plugin-app-db-1 psql -U legal -d legal -c \
  "SELECT timestamp, action, target_ref, detail, turn_id <> '' AS has_turn, office_platform, pane_build, backend_build FROM interaction_event WHERE surface = 'pane' ORDER BY id"
uv run python -m scripts.feedback_report --activity --days 1
uv run python -m scripts.feedback_report --turns --days 1
```

Expected: `pane_opened`, `tab_viewed` ×3, `turn_waited` (has_turn t, `chat`, ms), `finalize_applied`, each with `Mac`, `dev`, `dev`; `--activity` shows `opens 1 · chat 1`; `--turns` shows `(waited … in Word)` and the tab lines between. Also confirm Word did NOT mark a freshly opened, never-reviewed document as modified (open a new blank document, open the pane, close the document: Word must not ask to save).
````
