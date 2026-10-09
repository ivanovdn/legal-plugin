# Per-turn record (`turn_log`) — design

- **Date:** 2026-10-09
- **Status:** Draft — awaiting review
- **Branch:** `feat/turn-log` (cut from `main` at `14c902e`)
- **SP2 of the pilot-tracking plan** from the 2026-10-08 day-1 audit. SP1 stopped the loss and mislabelling of what the pilot records (`b2d8b29`). **SP2 is A + B:** a complete durable record per turn (A), and which Word and which build sent it (B). SP3 is C + D (pane lifecycle events, Caddy access log). E waits for SP2/SP3 data.

---

## Context & motivation

### 1. Reconstructing one turn took three tables and a 40,000-character prompt

Reconstructing the first tester's first day (trace `9f239597`, 2026-10-08) meant joining `audit_log` to `interaction_event` by `session_id` and timestamp — `audit_log` has no `turn_id` — and then reading the doc-chat span's 40,000-character `input.value` in Phoenix to learn which playbook the turn used. Phoenix was the only place that said what grounded the answer, how long the model took to load, or how many tokens it read. Until SP1 it lost all of that on every container recreate.

### 2. `audit_log` cannot see the turns support needs most

`audit_log` is written by `memory_writer`, the graph's last node ([graph/graph.py](../../../graph/graph.py): `output_formatter → history_appender → memory_writer → END`). Three kinds of request never reach it:

- **a turn whose graph raised** (`graph_invoke_failed`, `stateless_fallback_failed`, `resume_failed`, `resume_state_load_failed`) — exactly the turns a tester complains about;
- **a contract review paused for sign-off** — the interrupted submit writes no row ([memory_writer.py](../../../graph/nodes/memory_writer.py));
- **every compaction run**, manual or automatic.

It also holds none of: `turn_id`, `trace_id`, `document_id`, outcome, reason codes, grounding, model timings, token counts.

### 3. Model timings live only on spans

`set_gen_attributes` writes Ollama's load / prompt-eval / eval timings and token counts as span attributes. The main call's token counts also reach `report.tokens`; the timings reach nothing durable. The 5.9 s model reload in the first pilot turn — 45% of it — was visible only in Phoenix.

### 4. Nothing records which Word or which build sent a request

The day-1 audit found the pane serving `8536201` (2026-08-27) while the backend ran `474f1bd`: two compaction fixes were missing from production and nothing reported the mismatch. Nor could the audit say which Word the tester used. Neither image knows its commit: `.dockerignore` drops `.git/` from the backend build context, and the pane is built from `./clients/word` alone.

---

## Goals

- **One durable row per accepted request** to `POST /api/query`, `POST /api/query/{session_id}/resume` and `POST /api/compact`, failed ones included.
- From that row — joined by `turn_id` to `interaction_event`, `feedback` or `audit_log` when needed — answer for any turn: **who, when, which document; did it work; what grounded it; where the time went; what sent it.**
- **The row and the trace cannot disagree:** the row's `outcome` is the very value stamped as `app.outcome`.
- **Recording never slows or breaks a turn,** and the row is identical with tracing off.
- **Phoenix becomes the drill-down** (full prompt, span tree), not the system of record.

## Non-goals

- **Document and answer text.** The row keeps a fingerprint of the document (D2). Answers already live in `conversation_store` / `review_store`; the full prompt stays in Phoenix.
- **Requests rejected before any work:** FastAPI's 422, SSO's 401/503 once SSO is on, compaction's 403 (disabled) and 400 (no `document_id`). SP3's Caddy access log will see them.
- **SP3's ground:** pane lifecycle events (pane opened, tab viewed, finalize, client errors), the wait the attorney measured client-side, the Caddy access log, and platform/build on `feedback` / `interaction_event` rows. The new headers arrive with those requests from now on; SP3 decides whether to store them.
- **E's ground:** which model digest Spark had loaded, 👍/👎 per answer, document provenance.
- **Backfill** of turns before the deploy; alerting; dashboards; any new pane UI; client IP addresses.

---

## Decisions

### D1 — A new table, written by the request handler

Chosen by the owner on 2026-10-09 over two alternatives:

- *Widen `audit_log`:* one table, but still blind to crashed turns, paused reviews and compaction (§2); its row is written before the outcome is final; the headers would have to travel through graph state.
- *Copy from Phoenix nightly:* almost no app code, but only as complete as tracing, up to a day late, another cron job on the VM — and it contradicts the trace-coverage rule that a turn's record must not depend on whether anyone was watching ([observability/spans.py](../../../observability/spans.py), `_accumulate`).

`audit_log` keeps its job as the legal audit trail and gains only `turn_id`.

### D2 — A fingerprint of the document, not its text

`doc_chars` and `doc_sha256` of the text the request carried. Two turns with equal hashes saw the same text; a hash that changes between a review and a chat turn means the attorney edited the document in between. The text stays where it already is: Phoenix (persistent since SP1) and the snapshot on flagged feedback. Owner's decision, 2026-10-09, accepting the trade-off: if Phoenix's volume is lost, unflagged turns can no longer be replayed.

### D3 — Every accepted request writes exactly one row, at every exit

Each handler funnels every exit through one function that stamps `app.outcome` and builds the row from that same value — the raising exits included.

### D4 — Quiet, and after the response

The row is written by a FastAPI background task, after the response has gone out, so the attorney waits for nothing and an app-db outage adds no second 3 s wait to an already-degraded turn. Building the row is guarded as well as writing it. A failure of either logs at ERROR — the whole row, when it was built — and changes nothing else. Same class as `interaction_event` (telemetry must never break a turn) and **no reason code**: class 4, telemetry's own failures, deliberately has none ([degradations.py](../../../observability/degradations.py)). Exits that raise skip background tasks, so they write synchronously before raising.

### D5 — Collected with tracing off

The facts collector is fed before any `is_recording()` check, exactly like the reason-code accumulator, so the row is the same whether or not a TracerProvider exists.

### D6 — Build id = the deployed commit, passed in by the deploy command

`BUILD_SHA=$(git rev-parse --short HEAD)` on the deploy command line; compose hands it to both image builds as a build argument. A build without it says `unknown`, visibly, on every row; local runs say `dev`.

### D7 — Columns are cheap to change

The owner expects to add or drop columns after auditing real rows. Every column change follows the store's existing `ADD COLUMN IF NOT EXISTS` pattern in `init_db()`: a one-line change, no migration tool.

---

## Design

### The table — `turn_log`

Created by `memory.db.init_db()` at backend startup, like the six existing store tables.

```sql
CREATE TABLE IF NOT EXISTS turn_log (
    id BIGSERIAL PRIMARY KEY,
    -- which turn
    timestamp TEXT NOT NULL,                  -- ISO-8601 UTC when the request ARRIVED (the other tables stamp write time)
    turn_id TEXT NOT NULL,                    -- unique; joins interaction_event, feedback, audit_log, span metadata
    trace_id TEXT NOT NULL DEFAULT '',        -- '' when tracing is off
    kind TEXT NOT NULL,                       -- 'query' | 'resume' | 'compact'
    session_id TEXT NOT NULL DEFAULT '',      -- '' on compact
    document_id TEXT NOT NULL DEFAULT '',
    attorney_id TEXT NOT NULL,
    user_name TEXT NOT NULL DEFAULT '',
    -- what was asked
    task_type TEXT NOT NULL DEFAULT '',       -- what ran; what was requested if the graph never got that far; '' on compact
    request TEXT NOT NULL DEFAULT '',         -- the attorney's words: the question (query), the notes (resume), '' (compact); first 2,000 chars
    doc_chars INTEGER NOT NULL DEFAULT 0,
    doc_sha256 TEXT NOT NULL DEFAULT '',      -- hex SHA-256 of the UTF-8 text; '' when there was none
    -- did it work
    status TEXT NOT NULL,                     -- 'ok' | 'error': what the API returned (compaction's HTTP errors -> 'error')
    outcome TEXT NOT NULL,                    -- 'ok' | 'degraded' | 'failed': the value stamped as app.outcome
    degradations JSONB NOT NULL DEFAULT '[]', -- reason codes in the order recorded (= app.degradations)
    error TEXT NOT NULL DEFAULT '',           -- the first mark_failed message, else the API's error text; first 2,000 chars
    awaiting_review BOOLEAN NOT NULL DEFAULT FALSE,
    -- what grounded it
    contract_type TEXT NOT NULL DEFAULT '',   -- the playbook attached; '' = answered without one
    contract_type_defaulted BOOLEAN NOT NULL DEFAULT FALSE,  -- nothing matched; NDA assumed
    msa_title TEXT NOT NULL DEFAULT '',       -- the governing MSA attached; '' = none
    preferences_chars INTEGER NOT NULL DEFAULT 0,            -- size of the USER.md block injected; 0 = none
    doc_kept_pct INTEGER,                     -- NULL = no truncation; else % kept (chat: we cut the document; review: % that fit before the model drops the rest)
    context JSONB,                            -- chat: report.context_breakdown as the pane received it; NULL otherwise
    -- where the time went
    duration_ms INTEGER NOT NULL DEFAULT 0,   -- handler entry -> row built
    llm_calls INTEGER NOT NULL DEFAULT 0,
    llm_load_ms INTEGER NOT NULL DEFAULT 0,   -- this and the next four: sums over llm_detail
    llm_prompt_ms INTEGER NOT NULL DEFAULT 0,
    llm_gen_ms INTEGER NOT NULL DEFAULT 0,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    llm_detail JSONB NOT NULL DEFAULT '[]',   -- one object per model call, in order
    -- what came back
    response_chars INTEGER NOT NULL DEFAULT 0,
    edits_proposed INTEGER NOT NULL DEFAULT 0,
    preferences_proposed INTEGER NOT NULL DEFAULT 0,
    compaction JSONB,                         -- compact: compact_conversation's result dict; NULL otherwise
    -- what sent it
    office_platform TEXT NOT NULL DEFAULT '', -- Office's diagnostics: 'Mac' | 'PC' | 'OfficeOnline' | ...
    office_version TEXT NOT NULL DEFAULT '',
    pane_build TEXT NOT NULL DEFAULT '',      -- '' = no header: a pane from before SP2, or Chainlit
    backend_build TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT ''       -- first 300 chars
)
```

Indexes: `UNIQUE (turn_id)`, `(attorney_id, id)`, `(document_id, id)`.

Also `ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS turn_id TEXT NOT NULL DEFAULT ''` and an index on it; older rows keep `''`. Nothing in production reads `audit_log`, and the tests that `SELECT *` from it index columns from the front, while `ADD COLUMN` appends at the end.

Size: a few KB per row; `llm_detail` and `context` dominate. Rows are kept for the whole pilot, and the nightly dump covers them.

**`llm_detail` entries** are `{"name", "model", "tokens_in", "tokens_out", "load_ms", "prompt_ms", "gen_ms", "total_ms"}`, with `null` wherever Ollama reported no value. A ReAct run (`traced_agent_invoke`) is one entry holding its run's sums, as on its span. A stateless re-run after a mid-invoke Redis failure appends its own calls after those of the attempt that failed: the row counts everything the request spent.

### Collecting during the turn — `observability/spans.py`

One more per-request collector next to the reason-code list: a dict in a `ContextVar`, opened by the outermost `@traced` span and reset when it closes, exactly like `_root_degradations`. It reaches graph nodes the same way that list already does in production — codes recorded inside nodes reach `app.outcome` on the root.

| Fed by | What | Rule |
|---|---|---|
| `set_gen_attributes(...)` | appends an `llm_detail` entry | completed calls only (`output`, `usage` or `timings` given); before the `is_recording()` guard |
| `mark_failed(reason, exc=, detail=)` | `error` = `"{reason}: {ExcClass}: {exc}"`, or `"{reason}: {detail}"` without `exc` | the first failure wins; before the guard |
| `note_turn(**facts)` (new) | grounding facts | the last write wins; a no-op outside a request |

`turn_facts()` returns a copy, like `degradations()`. The handler reads both before its root span closes.

**Grounding producers:**

- `skills/legal_research/context.py::_build_chat_grounding` — `contract_type` and `contract_type_defaulted` once the bundle has loaded (it currently discards `detect_contract_type`'s second value); `msa_title` once the MSA block is built.
- `skills/contract_review/contract_review.py` — the same three, at the same two points.
- `skills/grounding.py::preferences_block_for_state` — `preferences_chars`, the length of the block it returns; one site covers both paths.

**Call names:** `llm_caller`, `planner` and `intent_router` call `set_gen_attributes` without a `name`. They start passing their span's existing name, so nothing changes in Phoenix: `set_gen_attributes(name=…)` renames the span to the name it already has.

### Building and writing the row — `memory/turn_log.py` (new)

- **`build_turn_row(...)` is pure:** no I/O, no settings, no clock. It takes the request's facts (kind, ids, arrival time, duration, question, document text, client facts, backend build), what the client got (status, error text, `awaiting_review`), the outcome and reason codes, the collector's snapshot, and the graph's final state or compaction result — `None` when nothing came back. It derives the fingerprint, the caps and the sums over `llm_detail`. `document_id` and `task_type` come from the final state when there is one (after `intake` has resolved the preamble-hash fallback), else from the request. `response_chars`, `edits_proposed`, `preferences_proposed`, `doc_kept_pct` and `context` come from the final state.
- **`write_turn(row)`:** one `INSERT` through the shared pool, JSONB via `psycopg.types.json.Jsonb`, as `feedback_store` does.
- **`write_turn_quietly(row)`:** `write_turn` inside a `try`. On any exception it logs `TURN LOG WRITE FAILED (…) — row recorded here instead: {json}` at ERROR. Never raises.
- **`recent_turns(*, since, attorney_id, document_id, limit)`**, for the report: the rows, plus per `turn_id` the counts of `edit_applied`, `edit_failed`, `edit_discarded`, `redline_applied` and `redline_failed` from `interaction_event`, and of `feedback` rows.

### The handlers — `api/routes/query.py`, `api/routes/compact.py`

- **At entry,** each handler records `arrived_at` (ISO UTC) and a monotonic start time. Each gains `background_tasks: BackgroundTasks` and `client: ClientInfo = Depends(resolve_client_info)`. `post_compact` also gains `user_name` for its row, and mints a `turn_id`, stamped on its span metadata as `submit_query` does; it is not returned, since the pane has no use for it yet.
- **Every exit goes through one `_finish(...)` per handler:**
  1. compute `outcome` once and call `set_outcome(outcome)`;
  2. read `degradations()` and `turn_facts()` — still inside the root span;
  3. call `build_turn_row(...)` with that same `outcome`;
  4. `background_tasks.add_task(write_turn_quietly, row)`;
  5. return the response.

  Steps 2–4 sit in one `try`: a bug there logs at ERROR with the `turn_id`, and the response goes out unchanged. The raising exits of `post_compact` call `write_turn_quietly(row)` synchronously, then raise.
- **The exits:**
  - `submit_query`: ok (paused for sign-off included), stateless fallback ok, stateless fallback failed, graph failed;
  - `resume_query`: state load failed; session expired (status `error`, outcome as derived — the TTL working as designed, mirroring the span); ok; failed;
  - `post_compact`: store read raised, result error, refused, ok.
- **Resume reads no identity** (only `submit_query` does — CLAUDE.md), so a resume row takes `attorney_id`, `user_name`, `task_type` and the document fingerprint from the checkpointed state that `get_state` already loads.
- **`turn_id` enters the graph state** (`LegalAgentState.turn_id`): seeded in `initial_state`, and re-stamped by resume's `Command(update=…)` next to `turn_started_at` (an undeclared key would be dropped). `memory_writer` passes it to `write_audit_log`.

Tests that call these handlers as plain functions (`tests/test_turn_outcome.py`) pass a `BackgroundTasks()` and a `ClientInfo()`, and run the tasks before asserting on the row.

### What sent it — pane, headers, builds

**Pane — new `clients/word/src/requestHeaders.ts`:**

- `requestHeaders()` = `userHeaders()` + `clientHeaders()`. The six `userHeaders()` call sites (`api.ts` ×2, `feedback.ts` ×2, `preferences.ts` ×2) switch to it; no shim (CLAUDE.md rule 5).
- `clientHeaders()` sends `X-Office-Platform` and `X-Office-Version` from `Office.context.diagnostics.platform` / `.version`, read inside a `try`: no `Office` global, or a throw, gives `""`. It also sends `X-Pane-Build` = `paneBuild()`.
- `paneBuild()` = `typeof __PANE_BUILD__ === "string" ? __PANE_BUILD__ : "dev"`. `__PANE_BUILD__` is a Vite `define`, declared in a `src/*.d.ts`: `process.env.PANE_BUILD`, else `"dev"` under `vite serve`, else `"unknown"`. The `typeof` guard is what lets the same module run under `tsx` (unit tests, the eval runner), where no `define` happens.

**Backend — new `api/client_info.py`:**

- `ClientInfo`, a frozen dataclass whose fields default to `""`, and `resolve_client_info(...)`: the three headers (first 64 chars each) and `User-Agent` (first 300 chars).
- `config.build_sha: str = "dev"`, a declared settings field read from env `BUILD_SHA` — a bare `os.environ` read would crash on an `.env` key (CLAUDE.md).
- The same four facts go on the root span's metadata (`office.platform`, `office.version`, `pane.build`, `backend.build`), so Phoenix shows them too.

**Builds:**

- **`Dockerfile` (backend):** `ARG BUILD_SHA=unknown` and `ENV BUILD_SHA=$BUILD_SHA` as the last layers, after `COPY . .`, so the dependency layers stay cached.
- **`clients/word/Dockerfile`:** `ARG BUILD_SHA=unknown` and `ENV PANE_BUILD=$BUILD_SHA` after `COPY . .`, before `RUN npm run build`. `npm ci` stays cached; the bundle is rebuilt whenever the commit changes, about 30 s per deploy. Accepted cost: the bundle now differs on every deploy, so the 2026-10-08 "byte-identical pane" check gives way to checking the served bundle's build id.
- **`docker-compose.remote.yml`:** both `build:` entries gain `args: {BUILD_SHA: "${BUILD_SHA:-unknown}"}`.
- **`docs/deploy-vm.md` Step 4:** the deploy command sets `BUILD_SHA=$(git rev-parse --short HEAD)`.

**What the pair tells you:** one deploy stamps both images with the same commit, so `pane_build <> backend_build` is an attorney whose Word still runs an older pane — left open through a deploy, or cached. `pane_build = ''` is a pane from before SP2, or Chainlit.

### Reading it

- **`uv run python -m scripts.feedback_report --turns [--days N] [--attorney ID] [--document ID]`** prints the filtered turns as a timeline instead of the feedback summary. Per turn: time, kind and task, outcome with its codes, duration split into load / prompt / generation, contract type + MSA + kept %, edits proposed → applied / failed / discarded, flags, platform and version, and a `stale pane` marker when the two builds differ; the question and the `turn_id` / `trace_id` underneath. On the VM: `$DC run --rm --no-deps backend python -m scripts.feedback_report --turns --days 1`.
- **`docs/feedback-loop.md`** gains ready SQL: stale panes; the slowest turns by model reload; SOW turns without an MSA; failed turns with their errors; turns per platform and version.

---

## Verification

### RED first, then mutation-checked

Each is written failing first; then each guard is broken on purpose to watch its test fail.

1. **Collected with tracing off.** Under a non-recording span, a completed `set_gen_attributes` call and a `mark_failed` both reach `turn_facts()`. Mutation: move the append after the `is_recording()` guard.
2. **Every exit writes exactly one row,** parametrised over the twelve exits above. Mutation: drop the row from any one exit.
3. **Row and trace agree.** For one request, the row's `outcome` and `degradations` equal the root span's `app.outcome` and `app.degradations`. Mutation: derive the row's outcome separately.
4. **A crashed graph still leaves a row:** `graph.invoke` raises → a row with `outcome='failed'`, `status='error'` and the error text.
5. **Recording failures change nothing.** `write_turn` raising, and `build_turn_row` raising, each leave the response identical, with an ERROR line carrying the `turn_id`. Mutation: remove either guard.
6. **`audit_log.turn_id` matches** the response's `turn_id` for the same request, on submit and on resume.

### Other tests

- **`build_turn_row`,** one test per shape: ok chat, ok review, degraded (truncated), crashed, paused, and compaction ok / refused / failed. Plus the fingerprint, the caps and the sums over `llm_detail`.
- **One run through the real graph** with the model faked: a SOW doc-chat turn whose answer promises an edit gives `contract_type='sow'`, the MSA title, and both `doc_chat` and `doc_chat_json_retry` in `llm_detail`. This also proves the collector crosses into graph nodes in the suite, not only in production.
- **`resolve_client_info`:** headers present, absent, oversize.
- **`recent_turns` and the `--turns` printer** on seeded rows.
- **`tests/conftest.py`:** its hardcoded `TRUNCATE` list gains `turn_log` (CLAUDE.md: a missing table leaks state and greens a lie).
- **Pane:** `requestHeaders.test.ts` covers Office present, `Office` undefined, and diagnostics throwing; `paneBuild()` under `tsx` is `dev`. `scripts/check.sh`'s `EXPECTED_PASS_COUNT` rises by the new assertions.
- **Gate:** `bash scripts/check.sh` green. Its image step also checks that an image built without `BUILD_SHA` reports `unknown`.
- **Sideload in Word for Mac** (CLAUDE.md: `tsc` is not enough): one chat turn writes a row with `office_platform='Mac'`, the Word version and `pane_build='dev'`.
- **Code review** of the branch before merge.

---

## Rollout

Every VM step waits for the owner's OK and the owner's choice of who runs it (CLAUDE.md rule 6).

1. Merge `feat/turn-log`; push both remotes.
2. **Dump app-db** to `data/backups/app-db-<date>-pre-sp2.sql.gz` (deploy-vm.md, *Working files*). This is the first deploy that changes the database schema.
3. **Deploy** with Step 4's command, which now sets `BUILD_SHA`. Backend startup creates `turn_log` and adds `audit_log.turn_id`.
4. **Verify:**
   - `$DC exec -T backend env | grep BUILD_SHA` shows the deployed commit;
   - the served bundle carries it: fetch the asset that `/taskpane.html` references and grep for the sha;
   - a `curl` probe turn as `vm-smoke` writes a complete row: `backend_build` = the commit, `pane_build = ''`, timings filled, grounding columns matching what the probe asked, and an `audit_log` row with the same `turn_id`;
   - the owner asks one question from Word: `office_platform`, `office_version`, and `pane_build` = the commit;
   - existing row counts are unchanged; the probe rows are deleted.
5. **Attorneys do nothing.** Their panes pick up the new bundle the next time they open (`taskpane.html` is served `no-store`). Until then their rows show `pane_build = ''`.

**Rollback:** redeploy the previous commit. The new table and column are inert to it.

## Docs to update

- `docs/feedback-loop.md` — `--turns`, the SQL, and a refreshed *What this cannot answer yet*.
- `docs/deploy-vm.md` — the Step 4 command, the post-deploy checks, the pre-SP2 dump.
- `CLAUDE.md` — one Backend line. It sits at its 150-line cap, so consolidate rather than append.
- `docs/wiki.md` — a Shipped entry; SP3 in the follow-ups.

## After the first audit

The owner expects to change columns once real rows have been read (2026-10-09). Candidates already visible: a per-reason `detail` beside `degradations`; `risk_level` copied from `audit_log`; the client-measured wait (SP3); the model digest (E).
