# What attorneys do to the document — design

- **Date:** 2026-10-15
- **Status:** Draft — awaiting review
- **Branch:** `feat/document-changes` (to be cut from `main` at `4993385`)
- **SP4 of the pilot-tracking plan.** SP1 stopped the pilot losing data (`b2d8b29`); SP2 recorded every turn the backend handled (`turn_log`, `283f1c6`); SP3 recorded what happens in the pane around the turns (`99412a3`); `fce8fa6` recorded what each turn ran on. **SP4 records what the attorney does to the document itself**, so what the assistant proposed can be compared with what the attorney kept — and what the attorney changed that the assistant never suggested.

---

## Context & motivation

### 1. An Apply is the last thing we see

`edit_applied` stores the first 200 characters of the target and nothing of the text that went in. The attorney can rewrite the draft before pressing Apply (`EditProposalCard` applies `draftText`, not the model's `new_text`), so even the applied wording is unknown. After the click, nothing: whether the edit was still in the contract an hour later, reworded, or rejected as a tracked change is invisible.

### 2. Attorneys work in tracked changes and may never press Finalize

They send the redlined file to the other side with its tracked changes. Finalize (accept all) is optional, and today it records only that it happened (`finalize_applied`). A capture that hangs on Finalize would see nothing for most documents.

### 3. The attorney's own changes are the misses

What an attorney rewrites by hand — a clause the review never flagged — is the one failure class invisible to both the developer and any eval (tester-feedback spec, 2026-08-14). It is recorded nowhere.

### 4. The text exists only where it can't be analysed

Each turn sends the full document; `turn_log` keeps only its SHA-256 (`doc_sha256`), by design. Phoenix spans hold the text, but Phoenix has no auth, its restore is unrehearsed, and spans can't be joined in SQL.

### 5. Office.js attributes the assistant's edits to the attorney

Edits made through the add-in are tracked under the current Word user — the same author as the attorney's own typing (`finalizeDocument`'s comment). The tracked-change author therefore separates *their* changes (the other side's, in a negotiated draft) from *ours*, but not the assistant from the attorney. That split has to come from the applied text we record.

---

## Goals

The data alone — without asking the attorney — must answer, per document:

1. **What happened to each applied edit?** `kept`, `reverted`, `altered`, or `no later capture`, judged against the last text seen after the apply.
2. **What did the attorney change that the assistant never suggested?** Paragraphs changed since the first stored text that contain no applied edit.
3. **Whose changes are in the file?** Tracked changes by author and type at each capture.
4. **All of it without Finalize.** Finalize adds a capture when used; nothing depends on it.

## Non-goals

- **No capture while the pane is closed**, and no auto-opening the pane with the document (`Office.AutoShowTaskpaneWithDocument` writes to the file).
- **No automatic "reworded" vs "removed" split.** That needs a fuzzy threshold calibrated on real pilot data — the deferred edit-matching heuristic. `altered` prints the closest paragraph for a reader to judge.
- **No automatic separation of a counterparty's second round.** The author counts let a reader do it by hand.
- **No formatting comparison.** Formatting-only tracked changes are listed with their type; texts are compared as text.
- **No Word comments.**
- **No full erasure across every table.** The deletion command removes SP4's data and lists what it does not touch.
- **No attorney-facing view** of the captures, no diff storage or compression, no Chainlit, no reading saved files from SharePoint/OneDrive.

---

## Decisions

### D1 — Deduplicated versions; observations give the order (approach A)

`document_version` holds each distinct text of a document once, keyed by `(document_id, sha256)`. Every place the text was seen is an **observation** — a `turn_log` row (it already carries `document_id` and `doc_sha256`) or a `document_snapshot` row — and observations, not versions, give the order: a document that goes A → B → A has two versions and three observations, the last one A. Rejected: storing the text on `turn_log` / event rows (a 130 KB MSA stored again on every turn, inside tables designed as small telemetry) and reading it out of Phoenix (no auth, not SQL-joinable).

### D2 — One hash function for turns and versions

`memory/turn_log.py::_fingerprint` moves to `memory/fingerprint.py::fingerprint(text) -> (chars, sha256)`, used by both `turn_log` and the version store, so a turn's `doc_sha256` and its version's key cannot diverge. The SHA-256 is always of the **full** text, before any cap.

### D3 — Three capture moments in the pane, plus every turn on the backend

| `moment` | When |
|---|---|
| `pane_open` | The pane mounts. Catches everything changed while it was closed. |
| `idle` | 30 s after the last change notification. Sent only if the payload differs from the last one this pane sent. |
| `finalize` | Just before `acceptAll()`, when Finalize is used. |

`pane_open` and `finalize` always send — the moment itself is information. The backend also stores each turn's document text (`source='turn'`).

**The change notification comes from two events, either of which restarts the 30 s timer:** Word's `onParagraphChanged` (WordApi 1.5 — `App.tsx` already registers it for the live document size, where it is noted as firing per keystroke) and the shared Office API's `DocumentSelectionChanged` (fires as the attorney types or moves around). Neither is recorded anywhere as verified inside our pane on Word for Mac, so the design depends on neither: whichever fires drives the capture, and the end-to-end sideload confirms an idle capture lands after typing. **Only when neither registers** does a poll every 2 minutes while `document.visibilityState === "visible"` stand in, still sending only on change. Not polling otherwise: nothing runs while nothing happens.

### D4 — A capture holds both views and the tracked changes

- the **current** view, `getReviewedText(current)` — as if every change were accepted; the same text a turn sends, so the same hash;
- the **original** view, `getReviewedText(original)` — every tracked change rejected;
- the **tracked changes**: `type`, `author`, `date`, `text` for each (`body.getTrackedChanges()`, already used by Finalize). `authorEmail` is not read.

Both texts go to `document_version` (`source='pane'`).

### D5 — Only documents the assistant has been used on

A capture reads `readDocumentId()` at the moment it runs — never `resolveDocumentId()`, which mints an id and marks the file modified (SP3). The id is minted on the first question, review, flag or condense, so a contract that is merely open with the pane is never recorded; once the attorney asks the first question, idle captures start in that same pane session. The id travels in the file, so a draft the assistant was used on by whoever sent it is captured as soon as the pane opens on it — the disclosure says so.

### D6 — Quiet everywhere

Like `interaction_event`: a capture failure never reaches the attorney, never blocks typing or Finalize, sets no `memory_degraded`, and records no degradation code (class 4 — telemetry's own failure, as `write_turn_quietly`). **Unlike `write_turn_quietly`, a failed version write logs only `document_id`, the SHA-256 and the size — never the text**, which would put a contract into journald.

### D7 — Applied text is recorded in full

`interaction_event` gains `applied JSONB`. `edit_applied` and `redline_applied` fill it with what actually went into the document, plus the model's proposal when the attorney edited it first.

### D8 — Verdicts are exact after normalisation

Comparisons use the pane's `normalize.ts` rules (NFC; curly → straight quotes; U+00A0 / U+202F → space) plus collapsing whitespace runs, so Word turning a quote curly is not an alteration. No similarity threshold anywhere in a verdict.

### D9 — Kept for the pilot; deleted on request

No automatic expiry. A deletion command removes one document's or one attorney's SP4 data, dry-run by default.

### D10 — New tables are created; the existing one gains a column by ALTER

`document_version` and `document_snapshot` are new (`CREATE TABLE IF NOT EXISTS`). `interaction_event` exists on the VM, so `applied` comes by `ALTER … ADD COLUMN IF NOT EXISTS` and gets the upgrade test SP2/SP3 tables got: rebuild it as of 2026-10-15, run `init_db()`, require the column.

---

## Design

### The pane

**New module `clients/word/src/documentCapture.ts`.** The logic takes its collaborators as parameters, so the assertion harness runs it without Word:

```ts
type Moment = "pane_open" | "idle" | "finalize";
type TrackedChange = { type: string; author: string; date: string; text: string };
// tracked === null: the tracked changes could not be read (sent as tracked_total -1)
type DocumentReading = { current: string; original: string; tracked: TrackedChange[] | null };

createDocumentCapture(deps: {
  readDocumentId: () => string;               // docIdentity.readDocumentId
  readDocument: () => Promise<DocumentReading>; // one Word.run, below
  send: (body: object) => Promise<{ recorded: number; enabled: boolean }>;
  sessionId: string;
  schedule?: (fn: () => void, ms: number) => unknown;   // the debounce timer; setTimeout by default
  cancel?: (handle: unknown) => void;                    // clearTimeout by default
}): {
  capture(moment: Moment): Promise<void>;  // never throws
  noteChange(): void;                      // (re)starts the 30 s idle timer
}
```

`capture`:

1. `readDocumentId()` → `""` means return without reading.
2. One capture at a time: a call while one is in flight returns immediately — except `finalize`, which never waits behind a capture in flight: it reads at once, so its read is issued before `acceptAll()`.
3. Read; cap the list at **500** tracked changes and each change's `text` at **2,000** chars (`cut: true` on a cut item), keeping `tracked_total` = the count before the cap. `date` is sent as ISO-8601.
4. For `idle`: compare `current + "\0" + original + "\0" + JSON(tracked)` with the last payload this pane sent; equal → stop. (A string comparison — no hashing in the pane.)
5. `send`; remember the payload; a reply with `enabled: false` stops every later capture in this pane session.
6. Any exception anywhere → swallowed.

`readDocument` (in `word.ts`, next to `readBody`): one `Word.run` loading `getReviewedText(current)` and `getReviewedText(original)`, then — in its own `sync`, inside its own `try` — `getTrackedChanges()` with `items/type, items/author, items/date, items/text`. If only the tracked changes fail to load (a Word without WordApi 1.6), the capture still sends both texts, with an empty list and `tracked_total = -1`; the report prints "tracked changes unreadable" for it.

**Wiring.**

- `App.tsx`: once, after mount, `installDocumentCapture(sessionId)` — creates the capture, calls `capture("pane_open")`, and registers both change events (D3) with a 30 s debounce → `capture("idle")`, or the 2-minute poll when neither registers.
- `FinalizeBar.tsx`: after the in-pane confirm, `await Promise.race([capture("finalize"), 3 s timeout])`, then `finalizeDocument()` as today. A throw or timeout in the capture never stops Finalize.
- `feedback.ts`: `recordEvent`'s `extra` gains `applied?: Applied`, each string capped at **20,000** chars.
- `EditProposalCard.tsx`, on `edit_applied`: `applied = { action, target_text, new_text, proposed_text, position }` — `target_text` is `target_text`, or `anchor_text` for an insert; `new_text` is the text actually applied (`draftText`); `proposed_text` is the model's `new_text`, only when it differs; `position` only for an insert.
- `FindingCard.tsx`, on `redline_applied`: `applied = { action: "redline", target_text: anchors[0], new_text: <redline> }` — `anchors[0]` is the clause text the redline replaced: the text `acceptRedline` validates its match against. The other anchors are only search fallbacks (clause names, the Issue prose); recording them would make a rejected redline read `altered` and let a clause name claim unrelated paragraphs.
- The disclosure line in `App.tsx` (below).

### The backend

**Tables** (`memory/db.py`):

```sql
CREATE TABLE IF NOT EXISTS document_version (
    document_id TEXT NOT NULL,
    sha256      TEXT NOT NULL,              -- of the FULL text
    text        TEXT NOT NULL,              -- capped at 1,000,000 chars
    chars       INTEGER NOT NULL,           -- the full text's length
    truncated   BOOLEAN NOT NULL DEFAULT FALSE,
    first_seen  TEXT NOT NULL,              -- ISO-8601 UTC, server clock
    attorney_id TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL,              -- 'turn' | 'pane'
    PRIMARY KEY (document_id, sha256)
);
CREATE TABLE IF NOT EXISTS document_snapshot (
    id              BIGSERIAL PRIMARY KEY,
    timestamp       TEXT NOT NULL,          -- server clock
    attorney_id     TEXT NOT NULL DEFAULT '',
    session_id      TEXT NOT NULL DEFAULT '',
    document_id     TEXT NOT NULL,
    moment          TEXT NOT NULL,          -- pane_open | idle | finalize
    current_sha256  TEXT NOT NULL,
    original_sha256 TEXT NOT NULL,
    tracked_changes JSONB NOT NULL DEFAULT '[]',
    tracked_total   INTEGER NOT NULL DEFAULT 0,
    office_platform TEXT NOT NULL DEFAULT '', office_version TEXT NOT NULL DEFAULT '',
    pane_build      TEXT NOT NULL DEFAULT '', backend_build  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_document_snapshot_doc ON document_snapshot (document_id, timestamp);
ALTER TABLE interaction_event ADD COLUMN IF NOT EXISTS applied JSONB;
```

Both new tables go on `tests/conftest.py`'s hardcoded TRUNCATE list.

**Store, `memory/document_store.py`:**

- `save_version(document_id, text, attorney_id, source) -> str` — `fingerprint()` on the full text, cap the stored text, `INSERT … ON CONFLICT DO NOTHING`; returns the SHA-256.
- `save_version_quietly(...)` — never raises; logs per D6.
- `save_snapshot(...)` — both versions, then the snapshot row.
- Read functions for the report: observations of a document (turns + snapshots, by timestamp), its versions by SHA, its `applied` events; and `delete_document_data(document_id="", attorney_id="", dry_run=True) -> dict[str, int]`.

**Endpoint, `POST /api/document-snapshot`** (new `api/routes/document_snapshot.py`, registered in `api/main.py`):

- Body (`api/models.py`, `DocumentSnapshot`): `session_id`, `document_id`, `moment` (one of the three; anything else → 422), `current_text`, `original_text`, `tracked_changes`, `tracked_total`.
- Attorney from `resolve_user_id`, client facts from `resolve_client_info` (SP2) plus `backend_build` — never from the body.
- Re-applies every cap server-side (the pane is not trusted to have).
- Always `200 {"recorded": 0|1, "enabled": bool}`; a store failure is `recorded: 0`. `document_capture_enabled=False` → `{"recorded": 0, "enabled": false}`, nothing written.

**Per-turn version.** `finish_turn` adds `save_version_quietly(document_id, doc_text, attorney_id, "turn")` to the same after-response background work as the `turn_log` write (and runs it synchronously on raising exits, like the row). Only `/api/query` submits with a document UUID and text store a version — Chainlit sends no UUID, a resume carries the checkpoint's resolved id instead of one the client sent, compaction carries no text — and none when `document_capture_enabled` is off.

**Events.** `InteractionEvent` gains `applied: dict | None = None`; `_insert_event` writes it, re-capping each string at 20,000 chars.

**Config.** `document_capture_enabled: bool = True` — off means no document copies: the endpoint records nothing and the turns store no text. It does not stop the applied text on Apply events: that comes with the events, which `feedback_enabled` governs. On `RECORDED` in `api/runtime_facts.py` (`test_every_setting_is_classified_exactly_once` enforces it). Caps are module constants.

### Reading it

**`feedback_report --changes`** — one block per document with SP4 data, scoped by the existing `--document` / `--attorney` / `--days` / `--since` / `--limit` (documents, oldest last observation first); `--full` prints whole paragraphs instead of ~150-char excerpts. "SP4 data" = at least one `document_version` or `document_snapshot` row; the attorney's name is found as `--activity` finds it (latest `user_name` from `turn_log` / `feedback`):

```
document 4f1c…  A. Tester · 2026-10-16 09:12 → 2026-10-18 14:02
  observations 9 (3 turns · 2 pane_open · 4 idle · 0 finalize) · 6 distinct texts
  first stored text 2026-10-16 09:12 (turn) — 2 earlier turns have only a fingerprint
  applied edits 5: 3 kept · 1 altered · 1 reverted · 0 no later capture
    kept      replace  "This Agreement shall be governed…" → "…laws of England and Wales"   turn 6aef53da
    altered   replace  "Liability cap…" → "…12 months' fees"   (edited before applying)
              closest now: "…the fees paid in the 6 months preceding the claim"
    reverted  delete   "Non-solicitation. For 24 months…"
  tracked changes at the last capture: 14 — Jane Smith 9 (6 ins, 3 del) · Counterparty LLP 5 (5 ins)
  paragraphs changed since the first text: 11 — 4 explained by an applied edit · 7 not suggested
    + "The Supplier shall indemnify the Client against…"            not suggested
    - "Either party may terminate on 90 days' notice…"               not suggested
```

**Verdicts** — against the last observation after the event's timestamp (both server clocks, ISO UTC, so a string comparison orders them); all comparisons normalised (D8):

| action | `kept` | `reverted` | `altered` |
|---|---|---|---|
| `replace`, `replace_all` | `new_text` present | `new_text` absent, `target_text` present | neither |
| `insert` | `new_text` present | — | absent |
| `delete` | `target_text` absent | `target_text` present | `target_text` not in the last stored text before the event |
| `redline` | `new_text` present | `new_text` absent, every anchor line present | otherwise |

No observation after the event → `no later capture`. `altered` prints the last text's paragraph sharing the most words with `new_text` (word-set overlap, display only — difflib's ratio against every paragraph of a 2,000-paragraph contract takes tens of seconds). `replace_all` is `kept` when at least one occurrence is present. A multi-line `replace` with as many lines as its target, differing from it on exactly one after trimming, is judged on that line pair — `simplifyMultilineReplace` applies only that line. A `delete`, or a replacement with nothing, whose target is not in the last stored text before the event reads `altered`: its absence proves nothing, since `deleteClause` removes the clause it finds even from a misquote. With no stored text before the event, the table decides.

**Changed paragraphs** — `splitlines()` of the first stored text and the last observation, empty lines dropped, normalised, aligned with `difflib.SequenceMatcher`: a paragraph only in the last text is `+`, only in the first is `-` — a reworded paragraph is one of each. A `+` is the assistant's when it contains an applied `new_text`; a `-` when it contains what an applied replacement, deletion or redline took out (its `target_text`; an insert's target is only its anchor); otherwise `not suggested`. A paragraph present in both an applied edit's old and new text is context and explains nothing. Old and new are never paired into one line: a clause typed where an applied deletion was would inherit the deletion's label. An applied edit the attorney then reworded leaves its paragraph `not suggested` — the rewording is the attorney's change, and the edit's own verdict says `altered`.

**Pure functions** in new `scripts/document_changes.py` — `normalize`, `edit_verdict`, `closest_paragraph`, `changed_paragraphs`, `build_document_changes`, `format_document_changes` — unit-tested on their own; `feedback_report` only fetches and prints.

**`--turns`** interleaves each capture in its session's timeline, one line, the way SP3's pane events are: `· capture idle: text changed, 3 tracked changes` (`text unchanged` when its current SHA equals the previous observation's).

### Deleting it

`python -m scripts.delete_document_data --document <id> | --attorney <id> [--yes]`. Without `--yes` it prints what it would delete and deletes nothing. With it: the versions, the snapshots, and `applied` set to NULL on the matching events. `--document` matches `document_id` on all three; `--attorney` matches their `attorney_id` columns — a version belongs to whoever first sent it. Either way it then prints what it does **not** touch for that scope: `review_store`, `conversation_store`, `feedback` snapshots, the `turn_log` questions, `interaction_event`'s short text excerpts (the target excerpt on an Apply, the error messages that quote it, the attorney's question), `audit_log`'s request summaries, Phoenix traces, the nightly dumps (kept up to 14 days), and the manual dumps taken before a risky deploy (`app-db-<date>-<reason>.sql.gz`, which never expire) — so nobody mistakes it for a full erasure.

### Disclosure

**The pane's header line** (`App.tsx`) becomes:

> During testing, the assistant records your questions, its answers and the document text sent with them; while this pane is open on a document the assistant has been used on — by you or by whoever sent it to you — a copy when the pane opens, each time you pause editing and before Finalize, with its tracked changes and who made them; when this pane is opened, which tab you use, how long answers take, errors it shows you, and what you do with its suggestions — so we can measure what it gets wrong. Sending feedback includes the document.

**`docs/tester-setup.md`, *What the assistant records*:**

- the suggestions bullet: "what you do with its suggestions — applied (with the text that was applied), discarded, or failed to apply;"
- a new bullet: "while the task pane is open on a document the assistant has been used on, by you or by whoever sent it to you: a copy of the document when you open the pane, each time you pause editing, and when you use Finalize — with its tracked changes and the name Word shows for who made each one (yours and, in a negotiated draft, the other side's). Edits made while the pane is closed are seen only when you open it again;"
- the closing paragraph: pane events hold no document text (except an error quoting a passage); **the document copies do**. They are kept for the length of the pilot; ask and we delete them for a document or for you.

---

## Verification

### RED first, then mutation-checked

| Guard | Test fails first because… | Mutation it must catch |
|---|---|---|
| a repeated text adds no version | saving the same text twice leaves one row | key the row on `first_seen` |
| a capped text keeps the full text's hash | a 1,000,001-char text's key equals `fingerprint(full)` | hash after capping |
| a turn's version joins its `turn_log` row | `doc_sha256` of the row equals the version's `sha256` | a second hash function |
| no document id or no text → no version | a Chainlit-shaped turn writes no version | drop the guard |
| a failed version write keeps the text out of the log | with a failing store, the log has the SHA and not the text | log the row |
| the attorney comes from the header | a body `attorney_id` is ignored | read it from the body |
| a store failure is quiet | `200 {"recorded": 0}` | let it raise |
| the off switch | `enabled: false`, nothing written | ignore the setting |
| server-side caps | 600 tracked changes → 500 stored, `tracked_total` 600; a 5,000-char change → 2,000 + `cut` | trust the pane |
| a VM database gains `applied` | `interaction_event` rebuilt as of 2026-10-15 + `init_db()` lacks it | the column in CREATE only |
| each verdict (table above) | one case per cell | swap `kept` / `reverted` |
| normalisation | a curly-quoted copy of the applied text is `kept` | compare raw |
| A → B → A | the last text is A | order by `first_seen` |
| `no later capture` | an apply after the last observation | compare against the last observation regardless of time |
| paragraph labels | an applied-edit paragraph is the assistant's, another is `not suggested` | label everything one way |
| deletion | a dry run deletes nothing; `--yes` deletes only the target's rows | ignore `--yes` / the filter |
| the setting is classified | `RECORDED` holds `document_capture_enabled` | leave it off both lists |
| pane: unchanged `idle` sends nothing | a second identical reading makes no `send` | drop the comparison |
| pane: one at a time | two concurrent calls → one `readDocument` | drop the in-flight flag |
| pane: no id, no capture | `readDocumentId` → `""`: no read, no send | read anyway |
| pane: unreadable tracked changes still send the texts | a reader whose tracked-change load throws → one `send` with both texts and `tracked_total: -1` | let the whole capture fail |
| pane: `enabled: false` stops | after it, a new reading makes no `send` | ignore the reply |
| pane: Finalize survives the capture | a throwing or never-resolving capture → `finalizeDocument` still called | await without the race |
| pane: the applied payload | `edit_applied` carries `draftText` as `new_text` and the model's text as `proposed_text` | send `proposal.new_text` |

### Other checks

- `scripts/check.sh` green; `EXPECTED_PASS_COUNT` raised by exactly the new pane assertions.
- **End to end, local sideload** — also the first sight of three Word facts nothing else can check: an idle capture lands after typing (D3's events), `getTrackedChanges()` items load `type`/`author`/`date`/`text`, and `getReviewedText(original)` returns the rejected view: ask a question on a scratch NDA; apply an edit, reword it by hand, pause → `altered`; apply another, reject its tracked insertion → `reverted`; apply a third and leave it → `kept`; type a new clause by hand → `not suggested`; Finalize → a `finalize` capture; `--changes` and `--turns` read all of it back; the disclosure line shows.
- **Windows:** unverified — no Windows machine. Both events exist there, but the spec does not claim either was tested there.

---

## Known limits

- Nothing runs while the pane is closed; edits made then are seen at the next pane open. A document never reopened with the pane ends at its last capture, and the last ≤ 30 s of editing before the pane closes can be missed.
- The document id lives in the file only after the file is saved; an unsaved new document loses it on close, and a later session on it starts unlinked (an existing property of document ids).
- A counterparty's second round returned in the same file shows as `not suggested`; the author counts separate it by hand.
- Turns from before the deploy have a fingerprint and no text; the report starts from the first stored text and says how many earlier turns it skipped.
- Finalize reads the tracked changes in a second step after the texts, so a read slower than 3 s can see them after Finalize has accepted them.
- The document id travels in the file's settings (`docIdentity.ts`), so a contract saved with Save As as the start of the next deal carries the first one's id: both files' observations interleave under one `document_id`, the verdicts and changed paragraphs compare one file with the other, and `--document` deletion removes both.

---

## Rollout

The established flow, each VM step only after the owner's OK (CLAUDE.md rule 6):

1. Push the branch to both remotes; bundle it to the VM.
2. **Dump `app-db` first** — new tables and a new column: `data/backups/app-db-<date>-pre-sp4.sql.gz`.
3. Step 4 with `BUILD_SHA`; recreates backend and Caddy. No attorney reinstalls (the pane ships in the Caddy image).
4. Verify on the VM: the tables and column exist; the owner opens the pane on a document used before → a `pane_open` snapshot; applies an edit, changes something by hand, pauses → an `idle` snapshot; `--changes --document <id>` shows the verdicts; `--turns` shows the capture lines; the new disclosure line shows in the pane.
5. **The owner tells the existing tester** what is now recorded (the owner's message — not sent by us).
6. Delete the probe data with the new deletion command (`--yes`), then the pre-deploy dump once verified (owner's OK); fast-forward `main`, push both remotes; move the VM checkout to `main`.

## Docs to update

- `docs/tester-setup.md` — the disclosure changes above.
- `docs/feedback-loop.md` — `--changes`, the verdicts and their limits, the capture lines in `--turns`.
- `docs/deploy-vm.md` — the deletion command (*Working files* / data section); `pre-sp4` dump.
- `docs/wiki.md` — Shipped row; new follow-ups: the fuzzy reworded/removed split, counterparty-round separation, the Windows check.
- `CLAUDE.md` — one clause on the `turn_log` bullet (versions + snapshots, the delete command), only if it stays ≤ 150 lines.
