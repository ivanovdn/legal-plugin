# Chat memory & grounding — design

> **Status:** approved design, ready for an implementation plan.
> **Scope:** one consolidated design, implemented in four phases (0→3), each its own commit/PR.
> **Source ideas:** `docs/00_PLAN.md` + `docs/01_reliability_floor.md` … `docs/04_context_cap.md`,
> grounded against the code audit in `docs/context_and_memory_audit.md`.

## Problem

The agent has two surfaces with **asymmetric knowledge**:

- The **Findings tab** (`contract_review`) loads the firm playbook and, for SOWs, auto-attaches the
  governing MSA. Its output is then discarded down to a 300-char stub.
- The **Chat tab** (`legal_research._run_doc_chat`) — the surface where redlines are actually
  applied — loads **neither** the playbook nor the MSA, and remembers **nothing** of a prior review.

This asymmetry is the root cause of five problems (consolidated from the audit + the `00`–`04` docs):

| # | Problem | Root cause |
|---|---------|-----------|
| A | After a review, chat can't recall findings | Assistant replies trimmed to 300 chars in `chat_history`; the full review is discarded |
| B | Memory doesn't survive pane close / bleeds across docs | Session key = pane lifetime (`App.tsx` per-mount UUID), not the document |
| C | "Does this SOW conflict with the MSA?" fails in chat | Playbook + MSA attached on the Findings path only, never on the chat path |
| D | Chat context can silently overflow | Whole document re-sent every turn, uncapped (the review path caps the MSA at 24k chars; chat has no cap) |
| E | Memory silently vanishes intermittently | If Redis is down, the checkpointer returns `None` → every turn becomes stateless, with no signal |

## Guiding principle

Build the dumb version, **measure** with the token telemetry already in place (`observability/tracing.py`
`ollama_usage`), and add cleverness only when measurement shows the simple thing failing. Several
"obvious" optimizations (structured-JSON findings, selective injection, clause segmentation) are
deliberately deferred for this reason.

## Architecture

The core move is to remove the asymmetry at its source: build **shared grounding** and a **shared
persisted-review store**, and have *both* surfaces use them. Patching each surface independently
would let the asymmetry reappear.

Two storage roles, deliberately kept separate (they have opposite requirements):

1. **Checkpointer — live conversation state — stays Redis.** The LangGraph thread / rolling working
   state. Fast, ephemeral. Problem E is about *this* store; the fix is observability (Phase 0), not
   replacing it.
2. **Review store — persisted findings — SQLite.** Durable legal-record data keyed to a document,
   surviving sessions / pane closes / restarts. SQLite is **already in the codebase** (`memory/audit.py`
   via `memory_writer`, `aiosqlite` dependency), so this adds no new storage tech and follows an
   existing pattern. It is also the natural home for the later FTS cross-matter precedent layer.

### New / changed components

1. **`memory/review_store.py`** (new) — SQLite-backed persisted reviews, mirroring `memory/audit.py`.
   Stores the **full markdown review**, **list-shaped**: one row per `(document_id, session_id)` with
   `timestamp` and `contract_type`. Schema is FTS-ready for the later precedent layer.
   - `save_review(document_id, session_id, markdown, contract_type) -> None` — **write failure is loud**
     (raises / surfaces; never a silent no-op).
   - `load_latest_review(document_id) -> dict | None`
   - `load_history(document_id) -> list[dict]`

2. **`memory/document_id.py`** (new) — `resolve_document_id(text) -> str`: SHA-256 of a **normalized
   preamble region** (NFC + lowercased + whitespace-collapsed first ~800 chars — title + parties),
   **not** the full body (which changes on every redline and would orphan the stored review). One
   function, so the later Office.js-custom-property upgrade is a one-function swap.

3. **`skills/grounding.py`** (new) — extracts the grounding logic currently **inline** in
   `skills/contract_review/contract_review.py` into one reusable builder used by both surfaces:
   `detect_contract_type(text)`, `load_playbook_bundle(contract_type)`, `attach_parent_msa(text,
   client_id)`. `contract_review` is **refactored to call it** (one source of truth — the asymmetry
   cannot reappear). No backwards-compat shim: call sites change directly.

4. **`graph/state.py`** — `LegalAgentState` gains `document_id: str` and `memory_degraded: bool`.
   `output_formatter` adds `memory_degraded` to `report`.

5. **Node wiring:**
   - **`intake`** resolves `document_id` from the uploaded text (it runs first and already sets
     `filters` — natural home).
   - **`memory_writer`** (already the terminal SQLite-writing node) also persists the review when
     `task_type == "contract_review"` and a review is present — loud on failure.
   - **`legal_research._run_doc_chat`** loads the latest review + builds grounding, then assembles the
     cache-ordered prompt.

### Data flow

```
Review turn:  intake(resolve document_id) → … → contract_review(grounding via shared helper)
              → llm_caller(markdown review) → output_formatter → history_appender
              → memory_writer(persist review md, keyed to document_id, LOUD on fail) → END

Chat turn:    intake(resolve document_id) → … → legal_research._run_doc_chat:
                 load_latest_review(document_id)        [degrade-with-warning on fail]
                 build_grounding(playbook + MSA)        [degrade-with-warning on fail]
                 assemble (stable-first, question-last):
                   [system: CHAT_SYSTEM_PROMPT] [system: playbook] [system: MSA note + MSA text]
                   [system: prior review findings] [*chat_history] [user: document + question]
              → ChatOllama → … → memory_writer(nothing to persist) → END
```

**Prompt ordering is load-bearing.** Putting the *stable* grounding (playbook + MSA + findings)
first and the *changing* question last lets Ollama's prefix cache reuse the expensive grounding
prefix across turns — the ~12k-token playbook prefill is paid once, not per turn. Today the chat
path puts the question first, which defeats the cache; the reorder is behavior-preserving.

## Phases

Build order per `00_PLAN`. One spec, four commits/PRs. Phase 0 is first because Phases 1–2 write
*more* state, so a silent-stateless failure does more damage as memory becomes central.

### Phase 0 — Reliability floor (problem E)

Make degraded storage **observable**, not silent. Fail loud, not necessarily fail hard.

- **Reads** (checkpointer unreachable, or `load_latest_review` fails) → the turn **proceeds**, sets
  `state["memory_degraded"] = True`, logs at **error** level; the response carries the flag and
  `ChatTab` renders a small "memory unavailable this turn" banner. Don't block the lawyer mid-task.
- **Writes** (a persisted-review write fails) → **loud**: `memory_writer` runs last (after
  `output_formatter` builds `report`), so it surfaces a failure by mutating the already-built report
  — e.g. `report["review_persist_error"] = <message>` — which the API returns and `FindingsTab`
  renders. Never a silent no-op — the user must not believe a review saved when it didn't.

**Done when:** pulling Redis out mid-session produces a visible/logged signal, not silent
statelessness; a failed persisted-review write is never silent. Out of scope: Redis HA / failover /
clustering — this is observability of the failure, not eliminating it.

### Phase 1 — Persist + inject the review (problems A + B)

- On the review turn, `memory_writer` calls `save_review(document_id, session_id, markdown,
  contract_type)`. **Append** a new session per review (list-shaped). Legal rarely wants a prior
  analysis silently discarded; the list shape means switching to overwrite-latest later is no
  migration.
- On the chat turn, `_run_doc_chat` calls `load_latest_review(document_id)` and injects the **latest
  review in full**, replacing the 300-char trim for review content. Older sessions → one-line
  summaries **deferred** until re-review exists (not needed yet).
- `document_id` = preamble hash (§Architecture). Survives pane close and does not bleed across
  documents → solves B. Documented interim limitation: editing the title/parties block orphans the
  stored review until the Office.js id lands.
- **Findings representation: persist the markdown we already produce.** Do **not** change the review
  to emit structured JSON — that collides with the markdown-table output format (`SKILL.md` is the
  ceiling), the Word `parser.ts` + `deriveBlockers`, and the multi-LLM eval, and is listed
  out-of-scope in CLAUDE.md. The `headline`/`detail` split is forward-prep only and not load-bearing
  while we inject everything in full. Structured findings become a separate, later initiative built
  on a *measured* need (FTS precedent layer, or context overflow forcing selective injection).

**Done when:** a review persists in full, structured by `(document_id, session)`; reopening the pane
on the same document still surfaces the prior review in chat; "expand on the IP risk you flagged"
answers from the stored review without re-deriving; the 300-char trim no longer governs what chat
knows about the review.

### Phase 2 — Playbook + MSA on chat (problem C)

- `_run_doc_chat` calls the shared `build_grounding` (detect type → load bundle → attach parent MSA
  for SOWs), and assembles the prompt **stable-grounding-first, question-last** (§Data flow) so the
  grounding prefix is cached.
- Chat keeps `CHAT_SYSTEM_PROMPT`; it does **not** adopt the review path's `_OUTPUT_CONSTRAINTS`
  (that governs markdown-table formatting, irrelevant to chat). A light, **model-neutral,
  playbook-grounded** MSA note — adapted from the existing `_MSA_COMPARISON_DIRECTIVE` ("the MSA
  governs; ground answers in it; don't invent MSA terms") — is added so SOW-vs-MSA questions work.
  **SKILL.md stays the ceiling**: the note is structural, not a legal position.
- Mirror the MSA immediately (not playbook-only) — the MSA is the concrete payload behind the
  headline complaint. Reuse the review path's MSA cap (Phase 3).

**Done when:** "does this SOW conflict with the MSA?" in chat answers from the actual MSA; chat
redlining reflects firm playbook fallbacks, not generic contract norms.

### Phase 3 — Context cap guard (problem D)

Lands with/after Phase 2 — Phase 2 is what makes document + findings + playbook + MSA compete for one
fixed local-model window.

- A crude **assembled-context budget**, `chat_context_max_chars` (config; promote the review path's
  `_MSA_MAX_CHARS` module constant to config too for consistency). Set the default to fit the
  deployed model's context window with headroom for the answer — illustrative starting value
  `chat_context_max_chars = 120_000` (~30k tokens), to be tuned against `ollama_usage`. If the
  assembled context exceeds the budget, **truncate the document portion** until within budget —
  preserve the playbook + MSA + findings grounding (the higher-value context we just added). Mark the
  truncation and log it.
- Keep it crude: the goal is removing the sharp edge (silent overflow → hard failure), not clever
  compression. **No** clause segmentation / retrieval-narrowing / per-clause addressing yet — that is
  the eventual endgame, built on a *measured* signal (token telemetry shows the document's share
  leaving no room for grounding), not now.

**Done when:** a large document on chat can't silently overflow the window once MSA + playbook +
findings are present; the cap behavior is observable (logged/surfaced) and consistent with the
review path's MSA cap.

## Confirmed decisions

1. **Findings representation:** persist the existing markdown review; defer structured JSON. (Phase 1.)
2. **Persistence target:** checkpointer stays Redis (live state); persisted reviews go to SQLite
   (durable, queryable, FTS-ready). SQLite is already in use, so no migration debt.
3. **Re-review of a changed document:** **append** a new session (list-shaped), not overwrite.
4. **Chat grounding attach:** always attach playbook + MSA, with the prompt reordered
   stable-grounding-first / question-last for prefix-cache reuse.
5. **Cap behavior:** truncate the **document** first, never the grounding.
6. **`document_id` quality:** interim preamble hash now; Office.js custom-document-property id later
   (one-function swap). Confirm whether the Word integration already exposes a document identifier
   before finalizing the interim — if a real id exists, skip the hash.
7. **Fail-loud split:** degrade-with-warning on reads; loud on persisted-review writes.

## Error handling

- All grounding / review **reads** wrapped in try/except → set `memory_degraded`, log at error,
  proceed. Review **writes** → loud (surfaced, never swallowed). Tracing stays best-effort.
- All imports at top of file (hard rule). No backwards-compat shims — change call sites directly.
- Backend requires a restart to load changes (`uvicorn` does not hot-reload Python).
- Prompt additions stay model-neutral so the multi-LLM eval isn't skewed.

## Testing (TDD)

- `resolve_document_id`: stable across body redlines, differs across documents, survives
  whitespace/quote normalization.
- `review_store`: save→load-latest; append builds history; load returns `None` when absent; **write
  failure raises/surfaces (loud)**.
- `skills/grounding.py`: type detection + bundle load + MSA attach — the **existing `contract_review`
  tests must stay green** through the refactor (proves behavior-preserving).
- chat injection: stored review injected; grounding assembled **before** the question; store failure
  sets `memory_degraded` and still answers.
- cap: truncates the **document**, never the grounding; truncation marked + logged.
- Full suite green; then **live sideload smoke in Word** (per CLAUDE.md — `tsc --noEmit` alone is not
  enough).

## Out of scope (deferred — measured-need triggers, not guesses)

- Structured-JSON review output / selective ("expand on demand") finding injection — build when the
  review stops fitting alongside the document (token telemetry signal).
- Clause segmentation / retrieval-narrowing on the chat path — the endgame for context budget;
  build when telemetry shows the document crowding out grounding.
- FTS cross-matter precedent recall — lands naturally on the SQLite review store as a query, later.
- Office.js custom-document-property `document_id` — a one-function swap of `resolve_document_id`.
- Redis HA / failover.

## File structure

- **Create:** `memory/review_store.py`, `memory/document_id.py`, `skills/grounding.py`,
  `tests/test_review_store.py`, `tests/test_document_id.py`, `tests/test_grounding.py`.
- **Modify:** `graph/state.py` (state fields), `graph/nodes/intake.py` (resolve id),
  `graph/nodes/memory_writer.py` (persist review), `graph/nodes/output_formatter.py`
  (`memory_degraded`), `skills/contract_review/contract_review.py` (use `grounding.py`),
  `skills/legal_research.py` (`_run_doc_chat`: load review + grounding + reorder + cap),
  `config.py` (`chat_context_max_chars`, MSA cap), `clients/word/src/components/ChatTab.tsx`
  (degraded banner), `clients/word/src/api.ts` (surface `memory_degraded`).
- **Tests:** extend `tests/test_skills.py` (chat injection, grounding refactor green) and the
  observability/contract-review suites as needed.
