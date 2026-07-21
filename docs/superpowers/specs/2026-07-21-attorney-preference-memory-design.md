# Attorney Preference Memory (`USER.md`) — Design

> **Status:** Approved design (brainstorming) — 2026-07-21.
> **Stage:** 1 of the *self-improving agent harness* (the project north star). This spec
> builds the harness's first organ: a durable, per-attorney memory the agent **reads** and
> **suggests to**, and the attorney **owns**. Later stages (inferred capture, company-environment
> knowledge, autonomous writes) are explicitly **out of scope** — see [§10](#10-out-of-scope--the-harness-follow-up).

---

## 1. Overview

Give every attorney a durable, plain-English **preference file** — `USER.md`, one per attorney —
that the agent reads as grounding on every review/chat turn, and that shapes how the assistant
answers and rates. The attorney **owns and edits** it (directly, in a new Word "Preferences" tab).
The agent is **read-only + suggester**: when the attorney explicitly asks it to remember something,
it proposes a one-line preference the attorney adds with one click.

This is deliberately the *simple* design: because the **human is the only writer**, a raw markdown
file is safe (no concurrent agent writes, no LLM-regeneration drift). `USER.md` is on-brand — the
project already treats markdown as the contract (`CLAUDE.md`, every `SKILL.md`, the generated
playbook).

**Why this first:** it delivers immediate value (personalized reviews) and lays the substrate the
harness grows from, while keeping the door open for autonomous/inferred learning later.

---

## 2. Goals

1. **Store** a per-attorney `USER.md`, keyed by `attorney_id`, server-side.
2. **Serve** it to the Word add-in via `GET`/`PUT` endpoints (the "cabinet").
3. **Edit** it in a new **Preferences tab** (view / edit / save).
4. **Ground** on it — inject `USER.md` into both the chat prompt and the contract-review prompt so
   preferences actually influence output. *(This is the payoff; storage without injection is inert.)*
5. **Suggest** — on an explicit "remember that…", the agent proposes a preference line the attorney
   adds with one click.

## 3. Non-goals (Stage 1)

- **No inferred/autonomous capture.** The agent never writes `USER.md` itself and never infers
  preferences from patterns. It only suggests when *explicitly asked*, and the human commits. →
  [§10](#10-out-of-scope--the-harness-follow-up).
- **No company/firm-wide memory.** Per-attorney only.
- **No structured/semantic substrate.** No SQLite table, no Qdrant `memory` collection this stage.
- **No per-entry provenance/confirmation metadata.** Everything in `USER.md` is human-approved by
  construction (the attorney typed or clicked it), so no "who wrote this / confirmed?" fields are
  needed yet. (The harness *will* need them — that's part of why it graduates off the flat file.)
- **Preferences never override firm policy.** See the ceiling constraint in [§4](#4-global-constraints).

---

## 4. Global constraints

- **`SKILL.md`/playbook is still the ceiling.** Attorney preferences layer *emphasis and working
  style* on top of the playbook; they must **never** relax or override a playbook risk rating or
  firm legal policy. Enforced two ways: (a) a `_PREFERENCES_DIRECTIVE` that says exactly this, and
  (b) **placement** — the preference system message is injected **early**, never last, so the
  playbook, MSA directive, and No-Signature gate retain final authority (they are the
  most-recent-instruction the model reads). Same category as the existing `_OUTPUT_CONSTRAINTS`.
- **Identity via the existing seam.** `attorney_id` = `state["user_id"]`, resolved by
  `resolve_user_id` (`api/auth.py`) from the `X-User-ID` header today, the O365 `oid` when
  `sso_enabled`. No new identity mechanism.
- **Path safety.** `attorney_id` reaches the filesystem — sanitize to `[A-Za-z0-9_-]` before it
  becomes a path segment; reject anything else (no traversal).
- **Best-effort grounding.** A preference read failure must **never** break the turn — log and
  return empty, exactly like `_build_chat_grounding` already does.
- **Prompt change is an allowed one.** The single `CHAT_SYSTEM_PROMPT` addition (define the
  `preference` output block) is a *universal output-contract* rule, the same kind as the existing
  edit-JSON format instruction — not model-specific coaching. Consistent with
  `[[feedback-fix-in-code-not-prompt]]`. All *parsing* stays in code.
- **Config-gated.** `preferences_enabled` (default `True`); off → endpoints inert, no injection.
- **Backend restart required** after backend changes (`uvicorn` no auto-reload).

---

## 5. Architecture & components

Layering mirrors the existing durable stores (`memory/` = storage, `skills/grounding.py` = the
single shared assembly point that keeps the two surfaces from drifting):

```
memory/preferences.py         (NEW)  raw file IO — load/save USER.md, sanitize id
skills/grounding.py           (MOD)  load_attorney_preferences_block() — read + truncate + wrap in directive
api/routes/preferences.py     (NEW)  GET/PUT /api/preferences  (Depends(resolve_user_id))
api/main.py                   (MOD)  register preferences_router
config.py                     (MOD)  preferences_enabled / preferences_dir / preferences_max_chars
graph/state.py                (MOD)  + proposed_preferences: list[str]
skills/legal_research.py      (MOD)  inject prefs block (chat) + parse ```preference blocks
skills/contract_review/…      (MOD)  inject prefs block (review)  — never last
graph/nodes/output_formatter  (MOD)  surface report["proposed_preferences"]
clients/word/src/preferences.ts        (NEW)  getPreferences / savePreferences
clients/word/src/components/PreferencesTab.tsx (NEW)  the cabinet: view/edit/save
clients/word/src/components/Tabs.tsx   (MOD)  + "preferences" tab
clients/word/src/App.tsx               (MOD)  mount PreferencesTab (display-toggle, state lifted)
clients/word/src/components/ChatTab.tsx(MOD)  render preference-suggestion card → one-click Add
clients/word/src/api.ts                (MOD)  report type += proposed_preferences?: string[]
```

### 5.1 Storage — `memory/preferences.py`

Pure file IO, `dir`-per-call, mirroring `review_store.py` / `conversation_store.py`.

- Layout: `<preferences_dir>/<safe_attorney_id>/USER.md` (default `preferences_dir = "data/attorneys"`).
- `load_preferences(base_dir, attorney_id) -> str` — file contents, or `""` if absent. Never raises
  on a missing file.
- `save_preferences(base_dir, attorney_id, markdown) -> None` — creates the dir, writes UTF-8,
  **replaces** the whole file (last-write-wins). Enforces a hard size cap (reject > ~20 000 chars).
- `_safe_attorney_id(attorney_id) -> str` — allow only `[A-Za-z0-9_-]`; raise `ValueError` otherwise
  (path-traversal guard). `attorney_id` is a UUID / `oid` in practice.

### 5.2 Assembly — `skills/grounding.py`

- `load_attorney_preferences_block(attorney_id, max_chars) -> str` — calls
  `memory.preferences.load_preferences`, truncates to `max_chars` (truncation-marked), wraps the raw
  markdown in `_PREFERENCES_DIRECTIVE`. Returns `""` when disabled, empty, or on **any** error
  (logged). This is the *single* formatter both surfaces call — no duplicate directive text.

`_PREFERENCES_DIRECTIVE` (wording, structural/ceiling-safe):

> "The following are this attorney's standing working preferences. Apply them to *emphasis, tone,
> and what to surface* — but they do **not** override the playbook, firm policy, or any risk rating
> in this review. When a preference conflicts with the playbook, the playbook wins."

### 5.3 Endpoints — `api/routes/preferences.py`

Registered in `api/main.py` alongside the existing three routers. Both use
`Depends(resolve_user_id)` so the SSO seam applies for free.

- `GET /api/preferences` → `{status:"ok", data:{markdown: str}}` for the caller's `attorney_id`.
- `PUT /api/preferences` body `{markdown: str}` → writes, returns `{status:"ok"}`; `413` if over cap.
- `preferences_enabled=False` → `GET` returns empty markdown, `PUT` returns a disabled error.
- Anonymous callers (Chainlit, no header) pool under `"anonymous"` — acceptable Stage 1 (the Word
  client always sends a stable per-install id); documented, not solved here.

### 5.4 Injection

- **Chat** (`skills/legal_research.py::_run_doc_chat`, current seam ~L781–792): a new
  `_load_preferences_block(state)` (thin call to `grounding.load_attorney_preferences_block` with
  `state["user_id"]` + `config.preferences_max_chars`) is appended to `system_messages` **right after
  `CHAT_SYSTEM_PROMPT`** (early → subordinate to playbook/review, per [§4](#4-global-constraints)).
- **Review** (`skills/contract_review/contract_review.py`, current seam ~L180–186): the same block is
  inserted **after the playbook, before `_OUTPUT_CONSTRAINTS`** — never last; `_MSA_COMPARISON_DIRECTIVE`
  remains the final system message.

### 5.5 Suggestion flow (explicit only)

- **Prompt contract** — `CHAT_SYSTEM_PROMPT` gains one rule: *when the user explicitly asks you to
  remember a standing preference, in addition to answering, emit a fenced* ` ```preference ` *block
  containing the one-line preference (one per line). Do not emit it otherwise, and never propose a
  preference that contradicts the playbook.*
- **Parse in code** — `_extract_proposed_preferences(prose) -> list[str]` pulls the lines inside
  ` ```preference ` fences (plain text — deliberately **not** JSON, to avoid the edit-block parsing
  fragility). Non-fatal: no block → `[]`, prose still shown.
- **Plumbing** — `_run_doc_chat` surfaces the list; `legal_research` sets a new
  `state["proposed_preferences"]`; `output_formatter` packs `report["proposed_preferences"]`;
  `api.ts` types it; `ChatTab` renders a card per suggestion:
  `💡 Remember this preference? "…"  [Add]`. **Add** appends the line to the current `USER.md` and
  `PUT`s it (a human-authorized write — the human clicked). Ignore = nothing persists.

---

## 6. Data flow

**Read (every turn):** turn arrives → `resolve_user_id` → `state["user_id"]` → injection helper
reads `USER.md` → wrapped block added early in system messages → model applies preferences (bounded
by the playbook) → answer/review.

**Write (attorney-driven):** attorney edits in the Preferences tab → `PUT /api/preferences` → file
replaced. Or: agent emits a `preference` block → card in Chat → **Add** → append + `PUT`.

Single attorney = inherently sequential writes; last-write-wins on `PUT` is acceptable this stage.

---

## 7. Error handling

| Failure | Behavior |
|---|---|
| `USER.md` absent | `load_preferences` → `""`; turn proceeds ungrounded-on-prefs |
| Preference read throws | `load_attorney_preferences_block` logs, returns `""`; **turn never breaks** |
| Bad `attorney_id` (traversal) | `_safe_attorney_id` raises → endpoint `400`; injection treats as empty |
| Oversized `PUT` | `413`, file unchanged |
| `preferences_enabled=False` | endpoints inert, no injection |
| No ` ```preference ` block | `_extract_proposed_preferences` → `[]`; prose answer unaffected |

Preference failure does **not** set `memory_degraded` — it's a soft enhancement, not a core store,
and shouldn't raise the amber banner meant for Redis/review-write loss. (Documented choice.)

---

## 8. Testing

**Backend (offline, `tmp_path`):**
- `memory/preferences.py`: save→load round-trip; missing file → `""`; dir auto-create; two
  `attorney_id`s isolated; `_safe_attorney_id` rejects `../`, slashes, empty; oversize rejected.
- `skills/grounding.py`: `load_attorney_preferences_block` wraps in the directive; truncates at
  `max_chars`; returns `""` on empty and on a forced read error; `""` when disabled.
- `_extract_proposed_preferences`: single line, multiple lines, no block → `[]`, fence with prose
  around it.
- Chat injection: prefs system message present (and **not last**) when `USER.md` exists; absent when
  empty/disabled.
- Review injection: prefs present, positioned after playbook and **before** `_OUTPUT_CONSTRAINTS`;
  `_MSA_COMPARISON_DIRECTIVE` still last when an MSA is attached.
- `api/routes/preferences.py` (TestClient, `X-User-ID` header): `PUT` then `GET` returns it; a
  second `attorney_id` gets its own; disabled flag; `413` on oversize.

**Test hazard:** any test exercising the real `memory_writer` on a `research` turn must still
monkeypatch the conversation-store writes (existing rule) — the preference path adds no new live-DB
write to `memory_writer`, but keep the pattern intact.

**Frontend:** unit coverage for the block extractor; the tab + suggestion card verified by **Word
sideload smoke** (per `CLAUDE.md`, `tsc --noEmit` is not enough) — load/edit/save a `USER.md`, then
confirm a "remember that…" chat turn shows a card whose **Add** persists (visible on reload).

---

## 9. Risks & limitations (accepted this stage)

- **Prompt bloat / doc crowding.** Preferences are *grounding*, and `_cap_chat_context` truncates
  only the *document*, never grounding — a huge `USER.md` could crowd the doc out. Mitigated by the
  `preferences_max_chars` injection cap + the hard write cap. Small files in practice.
- **Preference vs playbook conflict.** Bounded by the directive + early placement (playbook wins).
  A model could still over-weight a preference; the ceiling is enforced structurally, not provably.
- **SSO cutover re-keys the path.** When `sso_enabled` flips, `attorney_id` changes from the
  localStorage UUID to the `oid`, so `USER.md` moves — same one-time migration already noted for the
  conversation store. Documented, not handled here.
- **Anonymous pooling** (Chainlit) — all anonymous callers share one `USER.md`. Acceptable; Word is
  always keyed.
- **Last-write-wins** across two devices for the same attorney — acceptable single-user stage.

---

## 10. Out of scope — the harness follow-up

Recorded as the project's **headline follow-up** (`docs/wiki.md`) and north star
(`[[project-self-improving-harness]]`). Stage 1 is built so these plug in without redesign:

- **Inferred capture** — the agent detects valuable patterns across sessions and *proposes* (then
  *writes*) memory autonomously: the actual self-improving loop.
- **Company-environment knowledge** — broaden from per-attorney prefs to firm-wide/environment
  memory.
- **Structured + semantic substrate** — autonomous, growing, multi-writer, cross-document memory
  outgrows a flat file: SQLite rows (safe append + provenance/confirmed metadata) and the empty
  Qdrant `memory` collection (semantic recall of *which* learnings are relevant to *this* clause).
  `USER.md` can remain the human-facing view on top.
- **Agent-write safety + confirmation policy** — append-only/reflection-step writes; whether
  autonomous learnings apply immediately or require human confirmation.

The seam that keeps this cheap: Stage 1 routes all reads through
`grounding.load_attorney_preferences_block`, so a future structured backend swaps behind one
function — the injection sites don't change.
