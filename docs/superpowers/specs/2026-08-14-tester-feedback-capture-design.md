# Tester feedback capture — design

> Date: 2026-08-14 · Branch: `feat/tester-feedback-capture` · Status: design, approved for planning

## Why

The legal team is about to test the add-in on real contracts (VM deployment Bucket B),
and the project has **no feedback surface at all** — no route, no table, no component.
Today an attorney's complaint reaches the developer verbally, gets reconstructed from
memory, and is chased down by pasting a trace id into a Claude Code session by hand.

That is workable for one developer sitting next to one attorney. It does not survive a
multi-tester pilot, and it produces nothing durable: every incident is re-derived from
scratch and nothing accumulates.

The sharper problem is measurement. **Six roadmap items are blocked on rates nobody has:**

| Item | Blocked on |
|---|---|
| Chat: spurious edit proposals on factual questions | "a rate question, not a design question" |
| Chat edit path fabricates facts — provenance guard | "until the eval measures the false-positive rate" |
| `chat_history` bleed — residual scope | "the rate is still unknown" |
| Playbook-adherence violations — improvised findings | "score improvisation rate as an eval metric" |
| Deterministic placeholder safety-net — Layer 2 | "build when Layer 1's measured recall proves insufficient" |
| Chat: relaxing preferences vs the ceiling | "needs a dedicated eval" |

Complaints alone can never produce a rate — they are a numerator with no denominator.
But the denominator is already being generated and discarded: every Apply, Discard, and
apply-failure in the pane is an event with a verdict attached, and none of it is
recorded. This spec captures both halves.

The secondary payoff is that a captured complaint carrying a full input snapshot **is**
an eval fixture. The same artifact serves triage now and the harness later, so this work
is a prerequisite for Phase A rather than a detour around it.

## Non-goals

Explicitly **not** in this work:

- **Any path by which feedback changes agent behavior.** This is a capture layer. The
  self-improving harness consumes feedback eventually; it does not start there, and
  nothing in this spec writes to `USER.md`, the playbook, or any prompt.
- **The eval harness itself.** This produces the raw material. Promoting a feedback row
  to a runnable fixture is Phase A's job.
- **Category chips, severity levels, star ratings.** The taxonomy is unknown. Guessing it
  before reading thirty real items would bake in the guess.
- **An admin UI.** A read script is the whole reporting story.
- **Cross-attorney visibility.** An attorney sees their own feedback land and nothing else.
- **Langfuse score push.** Langfuse has a scores API that would pin feedback directly to
  the trace, and it is the wrong choice here: Langfuse does not deploy to the VM (Phoenix
  does). Postgres is the only store present on both.
- **Prompt or model changes of any kind.** Nothing in this spec alters what the agent says.
- **Editing or deleting feedback after submission.**

## Context: what exists to build on

Three Postgres stores already share one pattern — a table in `memory/db.py`'s
`_STATEMENTS`, a module in `memory/`, creation via `init_db()` at `api/main.py` lifespan,
truncation between tests in `tests/conftest.py`. A fourth store is a well-worn path.

The codebase also has an established **loud/quiet write convention** that this spec
inherits rather than invents:

| Store | Failure behavior | Rationale |
|---|---|---|
| `review_store.save_review` | **Loud** — propagates | The user believes their review was saved |
| `conversation_store.append_turn` | **Quiet** — try/except, logs | Telemetry must never fail a turn |

Feedback follows `save_review`. Events follow `append_turn`.

### The gap: a turn is not addressable

```
api/routes/query.py:164     "trace_id": session_id,
```

`state["trace_id"]` holds the **session** id, not a trace id — and is written once and
read nowhere (verified by repo-wide grep: `graph/state.py:50` declares it,
`api/routes/query.py:164` writes it, no reader). Meanwhile one `session_id` covers every
turn in **both** tabs, since `clients/word/src/App.tsx` mints a single id per pane
lifetime.

So today there is no identifier that resolves a piece of feedback to one prompt. Feedback
captured without fixing this would point at a session containing a dozen turns.

The genuine OpenTelemetry trace id is available and is the 32-hex format already used for
every trace lookup in this project (`0f63a143…`, `5d058b78…`, `85533de4…`). Exposing it
closes the loop between a complaint and the trace that produced it.

---

## Slice 1 — the join key

**`observability/spans.py`** gains one helper, consistent with the module's contract that
tracing must never break a turn:

```python
def current_trace_id() -> str:
    """32-hex OTel trace id for the active span, or "" when no provider is
    configured / the span is not recording. Best-effort; never raises."""
```

**`api/routes/query.py`** — `submit_query` and `resume_query` each mint a fresh
`turn_id` (uuid4) and read `current_trace_id()`:

- both are returned in the response payload (`_payload_from_result` gains the two fields,
  so the interrupt, legacy and normal branches all carry them);
- both are stamped on the root span via `set_trace_attributes(metadata=...)`, so a trace
  can be found by `turn_id` in whichever backend is deployed;
- `state["trace_id"]` is repointed at the real trace id. The field is currently unread, so
  this is a correction with no consumer to break.

`turn_id` is minted per HTTP request — a resume is a distinct turn from the submit that
interrupted, and both are separately flaggable.

**`api/models.py`** — the **response** needs no change: `ApiResponse` is a generic envelope
with an untyped `data` dict, and the typed contract lives on the client. Slice 3 does add
two **request** models (`FeedbackSubmission`, `InteractionEventBatch`), following
`PreferencesUpdate`.

**`clients/word/src/api.ts`** — `QueryResponse.data` gains `turn_id?: string` and
`trace_id?: string`.

**Client retention.** Both ids must survive to the moment a card is flagged:

- `ChatMessage` (in `ChatTab.tsx`) gains `turnId` / `traceId`, set when the reply is
  appended. Chat messages already carry per-message state (`rawResponse`,
  `proposedEdits`), so this follows the existing shape.
- `FindingsTab` holds the review's `turnId` / `traceId` in the lifted state alongside
  `findingsResult`, and passes them down to each `FindingCard`.

## Slice 2 — the stores

Two tables in `memory/db.py`'s `_STATEMENTS`:

```sql
CREATE TABLE IF NOT EXISTS feedback (
    id BIGSERIAL PRIMARY KEY,
    timestamp TEXT NOT NULL,
    turn_id TEXT NOT NULL DEFAULT '',
    trace_id TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    document_id TEXT NOT NULL DEFAULT '',
    attorney_id TEXT NOT NULL,
    user_name TEXT NOT NULL DEFAULT '',
    surface TEXT NOT NULL,              -- 'findings' | 'chat' | 'general'
    target_kind TEXT NOT NULL DEFAULT '',   -- 'finding' | 'edit' | 'reply' | ''
    target_ref TEXT NOT NULL DEFAULT '',    -- issue id / clause / target_text excerpt
    comment TEXT NOT NULL,
    snapshot JSONB
);
CREATE INDEX IF NOT EXISTS idx_feedback_turn ON feedback (turn_id);

CREATE TABLE IF NOT EXISTS interaction_event (
    id BIGSERIAL PRIMARY KEY,
    timestamp TEXT NOT NULL,
    turn_id TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    document_id TEXT NOT NULL DEFAULT '',
    attorney_id TEXT NOT NULL,
    surface TEXT NOT NULL,
    action TEXT NOT NULL,
    target_kind TEXT NOT NULL DEFAULT '',
    target_ref TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT ''     -- error text on failures; the count on per-turn counters
);
CREATE INDEX IF NOT EXISTS idx_event_turn ON interaction_event (turn_id, action);
```

**Why two tables and not one with a `kind` column.** The volume ratio is roughly 100:1;
`snapshot` would be NULL on virtually every row; the two are queried in entirely different
ways (events get `GROUP BY … COUNT`, feedback gets read as prose); and they have different
lifetimes — events are prunable telemetry, a feedback row is a human artifact that is never
deleted. Since querying is the entire point of the exercise, the schema optimises for it.

**`memory/feedback_store.py`** — alongside the other three stores:

```python
def save_feedback(...) -> None:      # LOUD: exceptions propagate
def record_event(...) -> None:       # QUIET: try/except, logs, never raises
def recent_feedback(limit: int = 50) -> list[dict]
def event_counts() -> list[dict]     # GROUP BY surface, action
```

The loud/quiet split lives **in the store**, so both the route and any future caller
inherit it without re-deciding.

## Slice 3 — the API

**`api/routes/feedback.py`**, shaped after `api/routes/preferences.py`:

| Endpoint | Behavior |
|---|---|
| `POST /api/feedback` | Attorney identity from `resolve_user_id` / `resolve_user_name`. A store failure returns **500** — the attorney must learn their feedback did not send. |
| `POST /api/events` | Accepts a **batch** (list) so a burst of interactions costs one request. Always returns 200; a store failure is logged, never surfaced. |

Both gated on `config.feedback_enabled`; disabled → `POST /api/feedback` returns 403
(consistent with `PUT /api/preferences`), `POST /api/events` returns 200 and discards.

Request bodies are two new models in `api/models.py` (`FeedbackSubmission`,
`InteractionEventBatch`), following `PreferencesUpdate`.

`attorney_id` is never accepted from the request body — it comes from the identity seam
only, so the SSO cutover reaches feedback for free.

**When `feedback_enabled=False`** the client is not told in advance (there is no config
endpoint, and adding one for this is not worth it). The `⚑` stays visible and a submission
surfaces the 403 as "feedback is currently disabled" in the panel. Events already fail
silently by design, so a disabled backend is invisible on that path — which is correct.

## Slice 4 — the window

**`clients/word/src/components/FeedbackPanel.tsx`** — an in-pane panel, **not**
`Office.context.ui.displayDialogAsync`. A dialog is a separate window with its own origin
and message-passing, and this codebase already records that the Mac webview is unreliable
for that class of thing (`window.confirm` in `FinalizeBar`). `FinalizeBar`'s in-pane
confirm is the established pattern.

Contents: a textarea, Send, Cancel, and a line naming exactly what will be attached —
the attorney must never send something they did not know they were sending.

**Two entry points:**

1. **`⚑` on a card** — `FindingCard`, `EditProposalCard`, and each assistant message in
   `ChatTab`. Opens pre-attached to that item and its turn.
2. **One button in the pane header** — opens attached to the current document and the most
   recent turn, with no specific item. This exists for the **misses**: "you didn't flag the
   assignment clause" has no card to hang off, and a miss is the one failure class that is
   invisible to both the developer and any harness.

**One disclosure line** in the pane stating that interactions with the assistant's
suggestions are recorded. Silent is not the same as hidden, and a legal team is the worst
possible audience to discover undisclosed logging.

## Slice 5 — the event log

Instrumentation on click handlers that already exist. No new UI, no attorney behavior
change.

| Source | Actions |
|---|---|
| `EditProposalCard` | `edit_applied`, `edit_discarded`, `edit_failed` (+ error in `detail`), `edit_jump_notfound` |
| `FindingCard` | `redline_applied`, `redline_failed`, `finding_commented`, `finding_jump_notfound` |
| `PreferenceSuggestionCard` | `preference_added` |
| `ChatTab` (per turn, on reply receipt) | `edits_proposed`, `preferences_suggested` — each with a count in `detail` |
| `FindingsTab` (per review) | `findings_rendered` with a count |

**The per-turn counters are the denominators.** Without `edits_proposed` you can only see
cards the attorney acted on, and *ignored* is a different signal from *discarded*.
`PreferenceSuggestionCard` has an Add button and **no dismiss**, so an ignored suggestion
is only visible as `preferences_suggested` with no matching `preference_added` — the
counter is the sole source of that signal, not a redundancy.

The per-turn events fire in the `send()` / submit handler, not during render, so React
StrictMode double-rendering cannot double-count them.

**`clients/word/src/feedback.ts`** — `sendFeedback()` (propagates errors to the panel) and
`recordEvent()` (fire-and-forget, swallows everything including network failure). A pane
with no backend must keep working exactly as it does today.

**The sleeper value is the failure events.** `edit_failed` carries the error string, which
makes it a direct field measurement of `body.search` matching — the single most expensive
problem in this project's history, spanning `findClauseRange`, `searchCandidates`, the 85%
completeness guard, wildcard escaping, tab-segment reduction and multi-line collapse. Every
one of those was built against anecdotes. There is currently **zero** data on how often the
matcher misses in real use. `finding_jump_notfound` does the same for anchor quality.

## Slice 6 — reading it back

**`scripts/feedback_report.py`** — recent comments with their trace ids and targets, then
event counts grouped by surface and action. No UI, no endpoint.

This is not optional polish. Feedback that nobody reads is worse than no feedback, because
it costs the attorney something and returns nothing.

---

## What gets captured

**On a flagged item** (rare — expect dozens over a pilot), the client sends a snapshot
sufficient to replay the case on a different model later:

```json
{
  "document_text": "…",
  "assistant_output": "…",
  "request": "…",
  "contract_type_detected": "nda",
  "target": { "…the finding row or edit proposal…" },
  "memory_degraded": false
}
```

**On an event** (frequent — expect thousands), ids and counters only. No document text,
no model output.

### On storing contract text

The snapshot puts client contract text in Postgres. This was an explicit decision, taken
on the grounds that it is not a new category of exposure: `review_store` already holds
full review markdown that quotes contract text heavily, and `conversation_store` holds
chat content — same database, same host, same tenancy, same access path. The alternative
(excerpt only) forecloses the High-priority multi-model evaluation item, because a case
that cannot be re-run cannot be compared across models.

Capped by `config.feedback_snapshot_max_chars`, truncation-marked, following the
`msa_max_chars` / `chat_context_max_chars` convention.

## Config

```python
feedback_enabled: bool = True              # False = routes disabled, no client capture
feedback_snapshot_max_chars: int = 200000  # per-snapshot cap, truncation-marked
```

Both land in `get_settings()`, which is `@lru_cache`'d — **a change to either requires a
`bash scripts/start.sh` restart.**

## Testing

**Backend**

- `feedback_store` round-trip for both tables; `init_db()` creates both.
- `save_feedback` **raises** on a store failure; `record_event` **does not** — asserted by
  injecting a failing pool, not by inspecting the source.
- `POST /api/feedback` takes `attorney_id` from the identity seam and **ignores** any
  `attorney_id` in the body.
- A store failure yields 500 on `/api/feedback` and 200 on `/api/events`.
- `feedback_enabled=False` → 403 and 200 respectively.
- Snapshot over the cap is truncated and marked.
- `current_trace_id()` returns `""` with no provider configured and a 32-hex string with one.
- `/api/query` payload carries `turn_id` and `trace_id`; two calls yield different `turn_id`s.

**Frontend** (`clients/word/src/feedback.test.ts`, run by `scripts/check.sh`)

- `recordEvent` swallows a rejected fetch and never throws.
- `sendFeedback` propagates a non-ok response so the panel can show it.

### The trap

`tests/conftest.py:37` truncates a **hardcoded** three-table list:

```python
"TRUNCATE audit_log, review_store, conversation_store RESTART IDENTITY"
```

Both new tables must be added. Missing this does not fail — it leaks state between tests
and produces a green suite that is lying, which is the exact failure mode that produced
three vacuous tests on 2026-08-13. **Verify by mutation:** break a store write and confirm
the relevant test actually goes red.

## Risks

| Risk | Mitigation |
|---|---|
| **Nobody uses the window.** Expert users under time pressure do not fill in forms. | The event log carries the measurement load and needs no attorney cooperation at all. The window is upside, not the foundation. |
| **Free text is unstructured and needs reading.** | Deliberate. Thirty real items are the input to designing the taxonomy; chips designed today would encode a guess. Volume over a pilot is small enough to read by hand. |
| **The disclosure line makes testers self-conscious.** | Accepted. Undisclosed logging in a legal tool is not a trade worth making. |
| **Snapshot size on very large contracts.** The 129KB MSA already on record would produce a ~130KB row. | Capped and truncation-marked. Dozens of such rows are single-digit MB. |
| **`current_trace_id()` returns `""` when tracing is off** (a valid configuration — the helper no-ops with no provider). | `turn_id` is minted independently of tracing and is always present. The trace id is an accelerator for lookup, never the join key. |
| **Event volume grows unbounded**, like `conversation_store` before it. | Same posture as that store: acknowledged, deferred, and listed as a follow-up rather than solved speculatively. |
| **Feedback is mistaken for a learning loop** by testers who expect the agent to improve because they flagged something. | The panel says what it does — the feedback reaches the developer. It does not promise the agent will change. |

## Follow-ups (deliberately out of scope)

- **Promote a feedback row to an eval fixture.** The snapshot is designed to make this a
  data transformation rather than a re-derivation. Phase A.
- **Derive the taxonomy** from the first ~30 items and add category chips as a v2.
- **Retention/pruning for `interaction_event`.** Bundles with the existing
  `conversation_store` retention follow-up.
- **The attorney's own tracked changes as ground truth.** Their redline is the
  highest-fidelity signal available and they produce it anyway, but a diff against an
  unknown baseline cannot distinguish "rejected our clause" from "the client negotiated
  it" — it answers *what changed*, not *what we got wrong*. Worth revisiting once the
  cheaper signals are in place.
- **Surface aggregate rates in the pane** (e.g. a developer-only diagnostics view). Needs
  data first.
