# Context Counter and Attorney-Triggered Compaction — Design

**Status:** ready to plan
**Date:** 2026-08-25
**Branch:** `feat/context-compaction`
**Predecessor:** [2026-08-21-context-budget-design.md](2026-08-21-context-budget-design.md) — slices 3 and 4 of its Follow-ups, joined

## Why

The predecessor made truncation *visible*. This makes it *avoidable*.

`_cap_chat_context` enforces the chat budget by shortening only the document —
a tail cut, so liability, indemnity, termination, governing law and the
signature blocks go first. The budget was raised to 150,000 chars, which fits a
real contract today. But measured against the actual grounded case, there is
almost no slack:

| scenario | fixed (cannot be summarised) | history's share |
|---|---|---|
| **MSA, grounded** | doc 84,859 + playbook 38,587 + sys/review 8,000 = **131,446** | **18,554 chars — 12%** |
| MSA, ungrounded | 87,859 | 62,141 — 41% |
| NDA, grounded | 49,104 | 100,896 — 67% |

`131,446 + 18,554 = 150,000` exactly. A grounded MSA chat sits precisely at the
edge: once history passes roughly twelve messages, every further message of
conversation is paid for **in contract text**.

### The principle this yields

> **Compaction exists to protect the contract, not to make things fit.**
> History is compressible. The contract is not. So never cut the document while
> compressible history remains.

This inverts the obvious framing. "Summarise to free space" recovers very
little space — history is only 12% of the budget in the case that matters, and
the document (57%) and playbook (26%) can never be summarised: one is the
source of truth, the other is the ceiling. But those same 18,554 characters are
the difference between the model reading the signature blocks and not.

There is a second, independent motive. History is a **prefill tax paid every
turn, forever**: at the measured 1,397 tok/s, a 20-message window is ~6.1k
tokens ≈ **4.4 s of prefill on every single turn**, growing linearly and without
bound. Compaction is the only lever that bounds it.

## Non-goals

- **Automatic compaction.** Deliberately deferred (see Follow-ups). The trigger
  logic is identical; only who pulls it changes, so this is a config flag later,
  not a rewrite.
- **Re-summarising summaries.** Bounded instead by a capped *injection* window
  (see "Bounding accumulation"), which is cheaper and matches an existing
  pattern. Re-summarising remains available later as an explicit action, never a
  silent one.
- **Compacting the document, playbook, MSA, or prior review.** None is ever
  summarised.
- **Predicting a turn's cost before it is asked.** Not possible —
  `_needs_grounding` keys off the question's wording, so the same contract costs
  49k or 131k depending on what is asked. The counter reports measured history,
  never a forecast.
- **Retrieval within the document.** Rejected in the predecessor and still
  rejected: it trades a visible truncation for an invisible one.

## The counter

A one-line summary in the shared header (`App.tsx`, above `<Tabs>`) so both tabs
see it:

```
context  131k / 150k tokens · history 12%          ▸
```

Expanded, the breakdown that makes the action decidable:

```
document      84,859   57%    ← never compacted
playbook      38,587   26%    ← never compacted
prior review   5,000    3%
history       18,554   12%    ← what "condense" reclaims
system         3,000    2%
```

**The breakdown must be backend-fed.** The client cannot compute it: it knows
the document text from `readBody()` and nothing else — not the playbook size,
not whether grounding attached, not the prior-review block, not the injected
history. All of that is assembled in `_run_doc_chat`. So a
`report.context_breakdown` object joins the existing `report.tokens` and
`report.context_truncated` on every turn.

Two honesty constraints, both load-bearing:

- The figures are **the last turn's real measured values**, labelled as such.
  Not a prediction — grounding is question-dependent and unknowable in advance.
- The **document** line updates live between turns from `readBody()`
  (`onParagraphChanged`, WordApi 1.5, debounced ~2 s), because it is the one
  component the client can watch change. Every other line holds its last
  measured value until the next turn.

Sizes are shown in **tokens**, converted from chars at `est_chars_per_token`
(4.89, measured). The predecessor's self-calibrating refinement
(`charsPerToken = charsSent / tokens.input`, EWMA-smoothed, persisted) applies
to the live document estimate only; the backend-fed lines are already exact.

## The trigger

The line turns amber and a **Condense earlier turns** action appears when
**both** conditions hold:

```
projected total > compaction_warn_pct of budget    AND    compressible history exists
```

The second condition is what stops the control crying wolf: offering the button
when nothing is older than the verbatim window offers a no-op. The first fires
at 90% rather than 100% so the attorney gets the choice *before* the document
starts being cut, not after.

New config, all `@lru_cache`'d in `get_settings` (⇒ a `start.sh` restart):

| field | default | meaning |
|---|---|---|
| `compaction_enabled` | `True` | master switch |
| `compaction_keep_recent_messages` | `6` | messages kept verbatim (three turns) |
| `compaction_warn_pct` | `90` | budget share at which the action appears |
| `compaction_max_quotes` | `24` | cap per segment (~400–500 tokens) |
| `compaction_max_injected_segments` | `3` | segments injected; the store retains all |

> **Amended during implementation (2026-08-25).** Two of these defaults did not survive
> contact with real data, and the reasons are worth keeping. `compaction_keep_recent_messages`
> was not merely mistuned at `6` — it was the wrong *unit*: a message COUNT guarding a
> character budget, so a conversation holding exactly six messages had nothing condensable
> while the document was being truncated. It is now a **floor of `2`**, with everything older
> eligible. `compaction_max_quotes` became a **ceiling** rather than a size: the real cap is
> derived from the characters the caller needs freed, then trimmed oldest-quote-first to that
> target, with a new `compaction_min_quotes = 4` as the point below which a summary is not worth
> having. A third rule was superseded outright — see "The validation gate" below.

### Bounding accumulation

Segments must not be allowed to grow without bound, and the arithmetic is
tighter than it first appears. On the grounded-MSA case history's **entire**
allowance is 18,554 chars = **3,794 tokens**. Uncapped, five accumulated
segments at ~800 tokens would be **4,000 tokens — more than the space history
had in the first place**, at which point compaction stops helping and the
summaries themselves start pushing the document toward truncation. That is
precisely the outcome this feature exists to prevent.

Two bounds, together:

- **`compaction_max_quotes = 24`** holds a segment to roughly 400–500 tokens.
- **`compaction_max_injected_segments = 3`** caps what reaches the prompt at
  ~1,500 tokens ≈ 7,300 chars — about 40% of history's MSA allowance, in
  exchange for roughly sixty turns of raw conversation.

Older segments stay in the store, auditable, simply outside the injection
window. **This is the same pattern `conversation_max_messages` already uses:
the store retains everything, the injected window is capped.** Consistency here
is deliberate — it is one idea applied at two levels, not two mechanisms.

## What a summary contains

**Extractive, both sides.** Each entry is a short verbatim quote carrying the
`conversation_store` row id it came from:

```
--- EARLIER IN THIS CONVERSATION (turns 1–14, condensed) ---
This is recalled discussion, not a current finding. The attached document,
the prior review and the playbook above take precedence over anything here.

[#412 attorney] "use Suzy Quatro for all signature blocks"
[#419 assistant, said earlier] "the cap is Green under the playbook"
[#437 attorney] "we'll accept 12 months"
--- END ---
```

### Why extractive rather than narrative

The fabrication risk in summarisation comes from **abstraction**, not from which
side spoke. A paraphrased legal conclusion drifts; a quoted one cannot. Keeping
both sides costs nothing in safety once the format is quotes — and it preserves
recall of the assistant's earlier reasoning, which an abstractive
attorney-only summary would lose.

Assistant lines are marked **"said earlier"** so a position the document has
since outgrown reads as history rather than as a live legal judgment. This is
the same hazard `_reconcile_review_with_doc` already handles for recalled
reviews.

Summaries are built from `_sanitize_history`-cleaned text, so fenced
` ```json ` / ` ```preference ` blocks can never be quoted into a summary and
become few-shot examples — the `2ae99ecc` failure, where replayed raw output
outranked the system prompt.

## The validation gate

Because the format is quotes with row ids, fabrication stops being merely
*detectable* and becomes **rejectable**. This is the single most important
element of the design.

> Before a segment is written, every quote is checked against the row it cites.
> The cited id must fall inside the segment's range, and the quoted text must
> actually appear in that row after light normalisation (whitespace collapse,
> curly→straight quotes). **Any failing quote invalidates the entire segment.**
> Retry once; on a second failure write nothing and report the failure.

A deterministic, zero-LLM guard on an LLM's output. It is only possible because
the format is extractive — and it is the reason the extractive choice is not
merely a preference. Narrative summaries would leave fabrication undetectable
by construction.

> **Amended during implementation (2026-08-25).** Two rules in the quoted block above
> were superseded. **"Any failing quote invalidates the entire segment"** was written
> against *invention* and predates the complete-sentence check that landed later; a
> verbatim quote trimmed mid-sentence is a formatting failure, not a fabrication.
> Measured against the production model on real rows, per-quote failure runs ~9%, at
> which all-or-nothing rejects roughly 90% of segments — compaction would effectively
> never run. A failing quote is now **dropped** and the count reported, with rejection
> reserved for a segment in which *nothing* verifies. The per-line guarantee is
> untouched: `_quote_failure` is shared by the strict validator and the partitioner so
> the two cannot diverge, and nothing unverified reaches the prompt. Separately,
> **"whitespace collapse"** turned out to be actively wrong: collapsing newlines welds a
> markdown reply into one enormous "sentence" in which every bullet is unquotable, which
> alone rejected every segment generated from a real conversation. Line breaks are
> preserved; a break may begin a sentence but never end one.

## Storage

Append-only, mirroring how `conversation_store` already works:

```sql
conversation_summary (
  id BIGSERIAL PRIMARY KEY,
  timestamp TEXT NOT NULL,
  document_id TEXT NOT NULL,
  attorney_id TEXT NOT NULL,
  from_id BIGINT NOT NULL,      -- conversation_store range, inclusive
  to_id   BIGINT NOT NULL,
  content TEXT NOT NULL
)
```

Indexed on `(document_id, attorney_id, id)`, matching `idx_conv`.

**Raw rows are never deleted.** A summary is a *view*, never the record — the
same principle behind stripping fenced blocks on read rather than write. Any
segment can be audited against its range, or discarded, without touching
history.

Segments are **append-only and never re-summarised**: each covers a fixed
`from_id`→`to_id`, so a fabrication cannot propagate into a later generation and
every block stays auditable against exactly the rows it came from. A rolling
summary would, by turn 100, have re-summarised itself several times, leaving an
error indistinguishable from fact and implicitly "confirmed" at each pass.

⚠️ **`tests/conftest.py` truncates a HARDCODED table list.** This table must be
added there or it leaks state between tests and greens a lie.

## Trigger mechanics and injection

**`POST /api/compact`** — a distinct endpoint, not a flag on `/api/query`.
Compaction produces no answer, carries its own latency, and must not sit on the
turn path. It takes `document_id`, resolves the attorney through the existing
`resolve_user_id` seam (never a request-body id), and returns the new breakdown
plus what was condensed.

Reading, in `_load_prior_conversation`: **summary segments oldest-first, then
verbatim rows after the last `to_id`**. Injection order in `_run_doc_chat` is
otherwise unchanged — the summary block joins at the **bottom** of the system
messages, after playbook / MSA / prior review, so recalled discussion never
outranks live grounding. The block's own header says so.

## Failure behaviour

**Loud, never best-effort.** The attorney clicked and was told it happened, so a
silent failure is a lie — the same split as `save_review` (loud) versus
`append_turn` (quiet). A failed compaction changes nothing: no segment row, no
deleted turns, and the pane reports it.

A compaction failure must never break a subsequent chat turn: if
`conversation_summary` cannot be read, `_load_prior_conversation` degrades to
raw rows and flags `memory_degraded`, exactly as it does today for the
conversation store.

## Testing

Deterministic and zero-LLM, which most of this is:

1. **The validation gate** — highest value. A summary quoting a row outside its
   range, or misquoting a row inside it, must be rejected and nothing written.
   Mutation-proved: delete the check, confirm the test fails.
2. **Segment selection** — which rows a compaction covers; ranges never overlap
   or gap; the verbatim window is respected; a second compaction starts after
   the previous `to_id`.
3. **Breakdown arithmetic and the threshold** — including "no compressible
   history ⇒ no action offered", so the control cannot cry wolf.
4. **Injection order** — summaries land below grounding, verbatim rows after
   summaries, oldest-first.
5. **Round trip** — compact, then assert the next turn's assembled context is
   smaller and the document is no longer truncated. This is the test that pins
   the feature's actual purpose.
6. **Loudness** — a failed compaction surfaces in the response and writes no row.

Not automatable, stated so it is not mistaken for covered:

- **Whether the summaries are useful.** Needs an attorney reading one. This is
  the thing to watch in a pilot, and no test substitutes for it.
- **Rendering in the Word pane.** Per the add-in rule, `tsc --noEmit` is not
  sufficient; needs a sideload smoke test.

## Risks

| risk | mitigation |
|---|---|
| A summary asserts something the attorney never said | The validation gate rejects any quote that does not match its cited row. Residual: a *correctly quoted* line can still mislead out of context — mitigated by "said earlier" labelling and by ranking the block below live grounding. |
| Compaction loses nuance an attorney needed | Raw rows are never deleted; a segment can be discarded and the conversation re-read in full. |
| `compaction_keep_recent_messages = 6` is wrong | A guess, flagged as tunable. Too low and recent nuance gets quoted instead of read; too high and compaction barely helps on the MSA case where only 18,554 chars exist to reclaim. |
| The 90% threshold nags on routine chats | The "compressible history exists" condition should prevent it — NDA history has 67% of the budget to grow into. Watch in pilot; the value is config. |
| Summarisation latency surprises the attorney | It is attorney-triggered and one-off, amortised over every later turn. The pane must show progress; at the measured 51.4 tok/s a ~500-token segment is ~10 s of generation, plus prefill of the range being summarised. |
| Conversation older than the injection window silently drops out | `compaction_max_injected_segments = 3` bounds the prompt but means discussion beyond roughly sixty turns is no longer represented at all. It remains in the store and is auditable, but the model will not see it. This is a real information loss, chosen over the alternative — letting summaries grow until they starve the contract. If a pilot shows attorneys reaching back that far, Follow-up 2 (re-summarising) is the answer, not a larger window. |
| `document_id` churn splits a conversation | Pre-existing (the unsaved-document identity bug, still open). Compaction inherits it: segments key off the same id as the rows. Not made worse here, but worth closing before a pilot leans on long conversations. |

## Follow-ups

| # | Item | Notes |
|---|---|---|
| 1 | **Automatic compaction** ("visible but automatic") | Same trigger logic, fired without a click, with the pane showing "N earlier turns condensed" and the summary readable. A config flag over this design, not a rewrite. |
| 2 | **Re-summarising summaries** | Accumulation is bounded here by `compaction_max_injected_segments`, so older segments simply fall out of the prompt. Re-summarising would let them stay *represented* rather than dropped. Only worth it if a pilot shows attorneys reaching back past ~60 turns. Explicit action, never silent. |
| 3 | **Self-calibrating chars/token for the live document estimate** | `charsPerToken = charsSent / tokens.input`, seeded 4.89, EWMA-smoothed, persisted. Auto-adapts if the backend model changes. |
| 4 | **Compaction in the review path** | Reviews carry no conversational history today, so there is nothing to compact. Revisit only if that changes. |
