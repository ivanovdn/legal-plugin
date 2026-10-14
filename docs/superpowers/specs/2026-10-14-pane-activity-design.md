# Pane activity and the Caddy logs — design

- **Date:** 2026-10-14
- **Status:** Draft — awaiting review
- **Branch:** `feat/pane-activity` (cut from `main` at `e67adfb`)
- **SP3 of the pilot-tracking plan** (wiki follow-up "Pilot tracking, next step (SP3)"). SP1 stopped the pilot losing data (`b2d8b29`); SP2 recorded every turn the backend handled (`turn_log`, `283f1c6`). **SP3 records what happens around the turns:** what the attorney does in the pane between them (C), and requests that never become a turn at all (D). It also settles SP2's open question: the client facts go on `feedback` and `interaction_event` too.

---

## Context & motivation

### 1. The 2026-10-14 audit could not say whether the tester used the pane

The only real attorney (`cc91e640…`) asked one question on 2026-10-08 and nothing since. Whether he opened the pane again — read a review, looked at the Preferences tab, gave up on a certificate warning — is invisible. Every table we have is written by a turn or by a click on a suggestion. A session with no question leaves no row anywhere, and the backend's own request log for past days is in journald, which needs `sudo` with the owner's password.

### 2. The wait an attorney feels is not the wait we record

`turn_log.duration_ms` starts when the request reaches the backend and ends when the handler returns. Reading the document out of Word (`getReviewedText`), the network, Caddy, and rendering the answer are all outside it. On a long contract the first of those alone can be seconds.

### 3. Errors the attorney sees are not recorded

When a chat, review, compaction or preferences call fails, the pane shows the attorney a red line and nothing is written. A request that the backend answered with an error has a `turn_log` row; a request that never got an answer (network, Caddy, a crash in the pane's own code) has nothing.

### 4. People who never got in are invisible

An attorney whose Mac does not trust our certificate never loads the pane, so no JavaScript runs and no API call is made. Caddy writes no access log at all today. Tested on the pinned `caddy:2.11.4`: a client that rejects the certificate leaves a line **only at DEBUG level**, from the `http.stdlib` logger —
`http: TLS handshake error from <ip>: remote error: tls: unknown certificate authority`. At INFO it leaves nothing.

### 5. Feedback and events don't say which Word or which build sent them

SP2 sends `X-Office-Platform`, `X-Office-Version` and `X-Pane-Build` on every request and stores them on `turn_log` only. A flag or an Apply from a stale pane cannot be told apart from one from a current pane.

---

## Goals

The data alone — without asking the attorney — must answer:

1. **Did they open the pane?** Pane opens per attorney per day, including days with no questions.
2. **What did they do in Word?** Tabs viewed, Finalize used, in session order between turns.
3. **What went wrong, and how long did they really wait?** Every error the pane showed, and the wait measured in Word from pressing Send/Review to the result on screen.
4. **Who couldn't get in?** Requests turned away before any turn, and TLS handshakes our certificate failed — by IP, since there is no request to carry an identity.

## Non-goals

- **No outbox** retrying events the pane failed to send. The pane and the API sit behind the same Caddy: when one is unreachable, mostly so is the other, and the Caddy logs cover it.
- **No "Finalize clicked then cancelled"**, no time-per-tab, no mouse or scroll tracking.
- **No import of the Caddy logs into Postgres.** They are read with `jq`.
- **No validation of action names** on the backend. Today's `/api/events` takes any string; the docs list the vocabulary.
- **Identity for TLS failures.** A failed handshake carries no headers; an IP is the most it can say.

---

## Decisions

### D1 — Pane events ride on `interaction_event` (approach A)

The pane already sends events to `/api/events`, written QUIET (telemetry never breaks what the attorney does). Pane lifecycle events are the same row shape — attorney, session, document, surface, action, detail — so they are new `action`s under a new `surface="pane"`, not a new table and endpoint. Existing readers filter by `action`, so their counts do not move. The table's description widens from "interaction with an assistant suggestion" to "something the attorney did in the pane".

### D2 — The client facts are stored on `interaction_event` and `feedback` (settles SP2's open question)

`office_platform`, `office_version`, `pane_build` from the request headers (SP2's `resolve_client_info`), **plus `backend_build`** — the backend's own `BUILD_SHA` at write time. With both builds on the row, "this pane was stale" is a property of the row, not a join against whatever the backend ran that day. Same four columns on both tables.

### D3 — Columns are added with `ALTER … ADD COLUMN IF NOT EXISTS`

Both tables exist on the VM, so `CREATE TABLE IF NOT EXISTS` never runs there again (SP2's D7). Each table gets the upgrade test `turn_log` got in `e2797ff`: rebuild it with its columns as of 2026-10-14, run `init_db()`, require the new columns.

### D4 — Two Caddy log files, JSON lines, readable without sudo

`data/caddy-logs/` on the VM, mounted into Caddy at `/var/log/caddy`:

- `access.log` — one line per request to the site, every status.
- `tls-errors.log` — only the `http.stdlib` logger, at DEBUG.

Both `mode 0644`, rolled at 10 MiB, 10 kept, 90 days at most (≤ ~220 MB together). Caddy's runtime log stays in journald at INFO, as today. All of this was verified on the pinned `caddy:2.11.4` on 2026-10-14: `mode 0644` produced `-rw-r--r--` files; the `include http.stdlib` log held only the handshake-error line; `Authorization` and `Cookie` values were written as `REDACTED`.

### D5 — Wait time is measured in the pane and stored as an event

`turn_waited`, `detail` = milliseconds, linked to the turn by `turn_id`. Not a `turn_log` column: `turn_log` is written by the backend after its response, before the pane has finished rendering it.

---

## Design

### The pane events

All sent by the existing `recordEvent` path (fire-and-forget, never throws), with `surface="pane"` unless noted.

| `action` | Fires | `turn_id` | Other fields |
|---|---|---|---|
| `pane_opened` | Once per pane load, when `App` mounts (after `Office.onReady`) | `""` | `session_id` = the pane's session; `document_id` = `resolveDocumentId()`; `detail` = `saved` \| `unsaved` (the existing `isDocumentUnsaved()` probe; `""` if the probe fails) |
| `tab_viewed` | Each tab change by the attorney; the initial tab is implied by `pane_opened` | `""` | `target_ref` = `findings` \| `chat` \| `preferences` |
| `finalize_applied` | After the in-pane confirm, when `finalizeDocument()` succeeds | `""` | — |
| `finalize_failed` | Same, when it fails | `""` | `detail` = the error text |
| `client_error` | Whenever the pane shows the attorney an error: chat send, review, compaction, preferences load/save — plus uncaught `error` / `unhandledrejection` in the pane | the turn's when one exists, else `""` | `target_ref` = `chat` \| `review` \| `compact` \| `preferences` \| `window`; `detail` = the message |
| `turn_waited` | When a chat answer or review is on screen, or has failed | the turn's (`""` if the request never returned one) | `target_ref` = `chat` \| `review`; `detail` = whole milliseconds from pressing Send / Review (before `readBody()`) to the result on screen |

`detail` and `target_ref` keep their existing caps (500 / 200 chars, `feedback.ts`). Error text can quote the contract (`Couldn't find "…"`) — `edit_failed` already stores that class of text, so no new kind of data is added.

**Pane module.** New `clients/word/src/paneEvents.ts`:

- `recordPaneEvent(action: string, extra?: { turnId?; sessionId?; documentId?; targetRef?; detail? })` — builds a `TurnRef` and calls `recordEvent(turn, "pane", action, …)`.
- `installErrorHandlers(sessionId: string)` — `window` `error` and `unhandledrejection` → `client_error` with `target_ref="window"`. Installed once, from `App`. **Guarded against loops:** a failure inside the reporting itself is swallowed, never re-reported.
- `startWait(): () => number` — returns a function giving the elapsed whole milliseconds.

Call sites: `App.tsx` (`pane_opened`, `tab_viewed`, the handlers), `FinalizeBar.tsx`, the `catch` blocks in `ChatTab.tsx` / `FindingsTab.tsx` / `ContextMeter.tsx`, the preferences load/save in `PreferencesTab.tsx`, and `turn_waited` in `ChatTab.send` / `FindingsTab`'s review.

### The backend

- `api/routes/feedback.py`: `post_events` and `post_feedback` take `client: ClientInfo = Depends(resolve_client_info)` (SP2, `api/client_info.py`) and pass it down.
- `memory/feedback_store.py`: `_insert_event`, `record_events` and `save_feedback` take `client: ClientInfo` and write `office_platform`, `office_version`, `pane_build`, and `backend_build = get_settings().build_sha`. `record_event` (no callers today) takes it too, defaulting to an empty `ClientInfo`.
- `memory/db.py`: per table, after its CREATE —
  `ALTER TABLE interaction_event ADD COLUMN IF NOT EXISTS office_platform TEXT NOT NULL DEFAULT ''` and the same for `office_version`, `pane_build`, `backend_build`; the same four for `feedback`. Rows written before the deploy read `''`.
- `api/models.py`: `InteractionEvent`'s description widens to cover pane events. No new request fields — the client facts come from headers.

### The Caddy logs

`Caddyfile` — a global options block and a `log` in the site block:

```caddyfile
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

{$ADDIN_ORIGIN_HOST:localhost} {
	log {
		output file /var/log/caddy/access.log {
			roll_size 10MiB
			roll_keep 10
			roll_keep_for 2160h
			mode 0644
		}
		format json
	}
	…existing site config unchanged…
}
```

`docker-compose.remote.yml`, service `caddy`: add `- ./data/caddy-logs:/var/log/caddy`. Docker creates the folder on first start; the files are `0644` and the folders on the path are world-readable (`data/` is root's, `755`), so the deploy user reads them with no `sudo` and no `install -d`. `data/` is outside git and outside the backend image.

**Known limit.** The access log covers requests that reach the site block. A request to the bare IP or another host name matches no site and is not logged — acceptable; attorneys use the configured host name.

### Reading it

**`feedback_report --activity`** (new) — one line per attorney per day, oldest first, scoped by the existing `--since` / `--days` / `--attorney` / `--document`:

```
2026-10-15  A. Tester (cc91e640)   opens 3 · chat 2 · reviews 1 · finalize 0 · errors 1
            waited in Word: chat 9.8s · review 96.1s   server: chat 7.2s · review 88.0s   (medians)
            pane 1a2b3c4 · backend 1a2b3c4
```

- Counts: `pane_opened`; `turn_log` rows by `task_type` (`research` → chat, `contract_review` → review); `finalize_applied`; `client_error`.
- Medians in Python over `turn_waited` (by `target_ref`) and `turn_log.duration_ms` (by task type). A median with no samples prints `—`.
- Build line: the most recent `pane_build` / `backend_build` among that day's `pane` events, marked `⚠ stale pane` when they differ and both are non-empty (the SP2 rule).
- A day with `pane_opened` and no turns prints `chat 0 · reviews 0` — the blind spot this exists for.
- Attorney name: the latest `user_name` from `turn_log` / `feedback` for that id, else just the id.

**`feedback_report --turns`** (extended):

- each turn entry gains `waited 9.8s in Word` when a `turn_waited` event carries its `turn_id`;
- the session timeline interleaves the `pane` events that carry no `turn_id` (`pane_opened`, `tab_viewed`, `finalize_*`, `client_error`) between turns, by timestamp, one short line each. The `--limit` / cut-off notice still counts turns, not these lines.

**The Caddy files** — a recipe in `docs/deploy-vm.md` (*Logs*), `jq` one-liners run from the checkout:

- pane loads per day by IP and user agent (`GET /taskpane.html`);
- API requests per day per `X-User-Id`;
- every response with status ≥ 400;
- TLS failures per IP per day from `tls-errors.log`.

---

## Verification

### RED first, then mutation-checked

| Guard | Test fails first because… | Mutation it must catch |
|---|---|---|
| events carry the client facts | `/api/events` with the three headers writes them, and `backend_build` = `build_sha` | drop `client` from `_insert_event`'s INSERT |
| feedback carries the client facts | same, through `/api/feedback` | same, in `save_feedback` |
| a VM database gains the columns | each table rebuilt as of 2026-10-14 + `init_db()` lacks them | put a column in the CREATE only |
| `--activity` counts a no-question day | opens 1, chat 0, reviews 0 printed for a day with only `pane_opened` | count opens from `turn_log` |
| `--activity` medians | a known set of waits gives a known median per kind | mix chat and review samples |
| `--turns` shows the wait | `waited` appears only on the turn whose `turn_id` matches | join on `session_id` instead |
| pane: every action's payload | `paneEvents.test.ts` with a fake `fetch` asserts `surface`, `action`, `turn_id`, `target_ref`, `detail` per action | send `surface="chat"` |
| pane: an error while reporting is not re-reported | a throwing `fetch` inside the `error` handler produces no second send | remove the guard |

### Other checks

- `scripts/check.sh` green; `EXPECTED_PASS_COUNT` raised by exactly the new pane assertions.
- **Local sideload (Word for Mac):** open the pane, switch to Chat and Preferences, ask one question, Finalize a scratch document — then read the `pane` rows, their four build/platform columns, and `--activity` / `--turns`.
- **Caddy, locally:** the compose dry run (`LOG_DRIVER=json-file`) writes both files under `data/caddy-logs/`, `0644`; an untrusted `curl` adds a line to `tls-errors.log`; `Authorization` logs as `REDACTED`.

---

## Rollout

The established flow, each VM step only after the owner's OK (CLAUDE.md rule 6):

1. Push the branch to both remotes; bundle it to the VM.
2. **Dump `app-db` first** — this adds columns: `data/backups/app-db-<date>-pre-sp3.sql.gz`.
3. Step 4 with `BUILD_SHA`. Recreates backend and Caddy; Caddy picks up the new Caddyfile and mount.
4. Verify on the VM:
   - `ls -l data/caddy-logs/` as the deploy user, no sudo: both files, `-rw-r--r--`;
   - `curl` from the Mac **without** trusting the certificate → a line in `tls-errors.log`;
   - the `jq` recipe returns rows;
   - the owner opens the pane in Word, switches a tab, asks one question → `pane_opened`, `tab_viewed`, `turn_waited` rows with `Mac` / the build on them; `--activity` shows the day.
5. Delete the probe rows and the pre-deploy dump once verified; fast-forward `main`, push both remotes; move the VM checkout to `main`.

## Docs to update

- `docs/feedback-loop.md` — the `pane` event list, `--activity`, the wait line in `--turns`.
- `docs/deploy-vm.md` — `data/caddy-logs/` in *Working files* / *Logs*, the `jq` recipe, the TLS-failure check.
- `docs/tester-setup.md` — a short *What the assistant records* section: the questions they ask, the edits they apply or discard, and **when the pane is opened, which tab is used, how long answers take, and errors shown**. These events carry no document text, except that an error message can quote the passage it failed on.
- `docs/wiki.md` — Shipped row; close the SP3 follow-up row.
- `CLAUDE.md` — extend the `turn_log` bullet by one clause (pane events + Caddy files), only if it stays ≤ 150 lines.
