# Automatic Compaction — Design

**Date:** 2026-08-26
**Branch:** `feat/auto-compaction`
**Builds on:** [2026-08-25-context-compaction-design.md](2026-08-25-context-compaction-design.md)
(shipped `cc7b46d`), which added `POST /api/compact`, the validated-quote
segment format, and the pane's context counter.

## Why

Compaction works and is measurably correct, but it only runs when an attorney
notices an amber line in a collapsed header and presses a button. That is the
wrong thing to ask of the person whose attention we are trying to protect. The
whole point of the counter is that the budget is a thing they should not have
to manage; leaving the remedy behind a click contradicts it.

Worse, the click arrives too late to help. `can_compact` turns on at 90% of
budget, and the turn that *reveals* the amber line has already been assembled
and answered. So the attorney reads "you are at 95%", presses Condense, and the
benefit lands on the turn after next. Firing the same call automatically, as
soon as the turn that measured the pressure returns, moves the benefit forward
by one turn and removes the decision entirely.

This is the roadmap's "visible but automatic" row. It is a trigger change over
the shipped design, not a rewrite: no new endpoint, no change to the validation
gate, no change to what a segment contains.

### The principle this yields

> Compaction is plumbing. The attorney should see that it happened and be able
> to stop it, but should never have to decide *when*.

## Non-goals

- **Server-side firing.** Compaction inside `_run_doc_chat` would add the
  measured 10–30s summarisation to the attorney's own answer latency, and
  `compact_conversation` deliberately cannot compute its own target — it has
  neither the document nor the grounding, which is why `reclaim_chars` is a
  parameter. Firing stays client-side, after the turn.
- **Reading the condensed summary.** The roadmap row pairs "automatic" with
  "the summary readable". That needs a new GET route (segments are never
  returned to the client today) plus a viewer component, and it is a coherent
  piece of work on its own. Deferred; the follow-up row stays.
- **Persisting the disarm.** See Failure behaviour.
- **Changing the gate, the segment format, or `compact_conversation`.** Not one
  line of `skills/legal_research/compaction.py` changes.

## Where the decision lives

**On the backend, in the breakdown.** `build_context_breakdown` already
computes `can_compact` and ships it to the pane; automatic firing gets a
sibling field computed the same way:

```python
"auto_compact": bool(
    can_compact
    and settings.compaction_auto
    and compressible_messages >= settings.compaction_auto_min_messages
),
```

The client reads the field and obeys it. It holds no policy of its own.

Two reasons this is not merely tidier. First, the pane is served centrally out
of the caddy image (`docker-compose.remote.yml`), so a client-side flag would
mean an image rebuild and redeploy to change the behaviour, where a backend
field makes it a `start.sh` restart. (An earlier draft of this section said a
client flag would cost a rebuild *per machine* — that was wrong, and predates
the pane moving into the image; testers hand-sideload the manifest, but it
points at one central bundle.) Second, `compressible_messages` is backend truth
the client cannot derive — it is a store query — so the threshold has to be
evaluated where that number is known.

## Config

| field | default | meaning |
|---|---|---|
| `compaction_auto` | `True` | fire without a click |
| `compaction_auto_min_messages` | `6` | how much un-condensed history must accumulate first |

Both are `@lru_cache`'d in `get_settings`, so changing either needs a
`bash scripts/start.sh` restart — the same constraint every other
`compaction_*` field carries.

`compaction_auto` defaults ON. The flag exists so a pilot can switch the
behaviour off after seeing it, not so someone has to switch it on to see it.

### Why the floor is not `> 0`

`compaction_auto_min_messages` is the one genuinely new piece of policy, and it
exists to stop a churn loop that the manual button never had.

After a successful compaction, `compressible_message_count` drops to
approximately zero — every row up to `latest_to_id` is condensed, and only the
`compaction_keep_recent_messages` floor remains. Each subsequent turn appends
exactly two rows. So with a bare `> 0` threshold, and a document large enough to
hold the counter in the amber band, `auto_compact` would be true again on the
very next turn, against two short messages.

That call is not merely low-value, it is *predictably wasted*: a segment
carries a 261-char header plus ~20 chars of `[#id speaker]` per quote line, so
condensing two short messages produces a segment larger than its source, and
the net-benefit guard shipped in `cc7b46d` declines it. The result would be a
10–30s LLM call on every turn, against a shared Ollama, that is guaranteed to
write nothing.

Six messages is three turns of accumulation. It is a round number chosen to be
comfortably past the two-message-per-turn growth rate, not a measured optimum,
and it is config so a pilot can move it.

The manual button keeps the existing `can_compact` threshold. An attorney who
deliberately presses Condense on a short history gets what they asked for,
including the honest "would not save space" refusal. The floor governs only
what we do *without being asked*.

## Client behaviour

`ContextMeter` already contains the whole flow — resolve the document id,
compute `reclaimTarget` from the breakdown it was shown, call
`compactConversation`, render the outcome. Automatic firing reuses it exactly:

1. `condense()` becomes `runCompaction(auto: boolean)`. The body is otherwise
   unchanged apart from how the outcome is worded, with one exception found in
   review: it no longer clears `note` at its head. Doing so destroyed a
   completed run's notice within one frame whenever a newer breakdown was
   already pending — the `busy → false` commit that carries the finished note
   also re-runs the effect, and the next run's `setNote(null)` fired
   synchronously. A note is now replaced only when a new one arrives, and
   cleared on the error path so a stale success notice cannot sit above a
   fresh failure.
2. An effect fires `runCompaction(true)` when the displayed breakdown's
   `auto_compact` is true, no run is in flight, and auto has not been disarmed.
3. The manual button remains, unchanged, gated on `can_compact` as today.

### What the effect is keyed on, and why it is not the obvious thing

The effect must fire **once per turn**. The obvious key — the breakdown object
`ContextMeter` renders — is wrong, and quietly so.

Today `App.tsx` passes `withLiveDocument(breakdown, liveDocChars)` inline in
JSX. That call allocates a **new object on every render**, so an effect keyed on
it would fire on every render of the pane, not once per turn: several
unrequested LLM calls per turn, racing each other past the `busy` guard.

The object that genuinely changes once per turn is the **raw** breakdown in
`App.tsx` state, set by `onBreakdown`. So the live-document adjustment moves
into `ContextMeter`, which takes `breakdown`, `liveDocChars` and `docTruncated`
as props and derives what it displays:

```tsx
const shown = breakdown && liveDocChars !== null && !docTruncated
  ? withLiveDocument(breakdown, liveDocChars)
  : breakdown;
```

The effect keys on `breakdown` (raw, stable per turn) and *decides* on
`shown.auto_compact` (freshest pressure estimate). Once per turn, judged on the
best available numbers.

This also puts the counter's display logic in the counter's component and
removes a ternary from `App.tsx`'s JSX, which is where it had drifted to.

**Visible, per the roadmap row.** While an automatic run is in flight the meter
replaces the button with `Condensing earlier turns automatically… (10–30 s)`.
On completion it renders the note the manual path already produces, prefixed so
it is unambiguous that nobody clicked. The attorney can keep chatting: a
compaction committing mid-turn can only duplicate context, never lose it — see
Concurrency.

## Failure behaviour

An automatic run that throws, or returns an `error`, sets a `disarmed` flag and
auto does not fire again for the life of the pane. The error renders once.

This matters because the manual path's error handling assumes a human just
pressed a button and is owed an answer. An automatic path with the same
handling against a down Ollama would produce a red error on every turn, and a
persistent error nobody chose to trigger trains attorneys to ignore the pane.

Disarm is **per-pane-session and deliberately not persisted**. A transient
Ollama blip should not permanently disable the feature, and there is nowhere
honest to persist it to — it is a client-side judgement about a transient
condition, not a fact about the document. Reopening the pane re-arms it.

A `reason` response — nothing to condense, or the net-benefit refusal — renders
the same quiet note the manual path shows. It is not a failure, but it **does**
disarm automatic firing.

> **Amended during implementation (2026-08-26).** This section originally read
> "a `reason` response is not a failure and does not disarm." That was wrong, and
> it contradicted this design's own governing principle — the one
> `compaction_auto_min_messages` exists to serve: never spend an unrequested LLM
> call that is guaranteed to write nothing. `compact_conversation` generates the
> segment *before* it checks net benefit (`compaction.py:545` vs `:600`), so a
> refusal costs a full 10–30s call. Nothing in the backend remembers that the
> previous turn was refused, and `auto_compact` is computed purely from pressure
> and message count, so a stable refusal condition would fire an unrequested,
> guaranteed-fruitless call on **every turn** — the "cries wolf" failure this
> design argues against two paragraphs above, arriving through a different door.
>
> Disarming on the *first* refusal rather than the second: while `auto_compact`
> is true the only reachable `reason` **is** the net-benefit refusal, because
> automatic firing requires at least `compaction_auto_min_messages` compressible
> messages and so "nothing earlier to condense yet" cannot occur. The attorney
> keeps the manual button, which still reports the refusal honestly, and the
> whole design errs toward not firing unasked.

## Concurrency

Automatic firing makes an existing race more likely, so it is worth stating
what it can and cannot do. `_load_prior_conversation` reads in this order:

1. `boundary = latest_to_id(...)`
2. `load_segments(..., compaction_max_injected_segments)`
3. `load_recent(..., after_id=boundary)`

A compaction committing between any two of those steps produces at worst
**duplication** — rows replayed verbatim that a newly written segment also
quotes — and never a gap. A gap would require `boundary` to exceed what the
loaded segments cover, and it cannot: `latest_to_id` is `MAX(to_id)`, and the
newest segment is by construction inside the most-recent-N injection window.

Duplication costs budget for one turn and is self-correcting on the next. That
is an acceptable price for not blocking the attorney's chat, so no locking,
transaction, or send-blocking is introduced.

## The one fix this drags in

`withLiveDocument` recomputes `can_compact` from a live `readBody()` length,
because editing the document genuinely changes whether the budget is under
pressure. It must recompute `auto_compact` on the same basis. Left alone, a
document edit could strand the two in disagreement — auto firing while the
button believes there is nothing to do, or the reverse.

The recompute mirrors the backend's composition, using the flag state already
present on the breakdown rather than re-deriving policy:

```ts
can_compact: b.compressible_messages > 0 && pct >= b.warn_pct,
auto_compact: b.auto_compact && pct >= b.warn_pct,
```

`auto_compact` can only be narrowed here, never widened. The backend already
folded `compaction_auto` and the message floor into the field; the client knows
neither, so a live document edit may switch it off (pressure relieved) but must
never switch it on.

## Testing

**Assertable:**

- `auto_compact` is false when `compaction_auto` is off, false below
  `compaction_auto_min_messages`, false whenever `can_compact` is false, and
  true when all three hold. (`tests/test_context_breakdown.py`)
- The floor is independent of the button: a message count between 1 and
  `compaction_auto_min_messages - 1` over the warn line yields
  `can_compact: true, auto_compact: false`. This is the anti-churn guarantee
  and is the single most important assertion in the slice.
- `withLiveDocument` keeps the two fields consistent as the document grows and
  shrinks, and never widens `auto_compact`. (`contextGauge.test.ts`)

**Not assertable, and stated rather than papered over:** the effect itself. The
add-in's assertions are plain `tsx` scripts with no React test harness, so
"fires once per turn, not twice", "does not fire while busy", and "stops after
a failure" have no automated home. They get sideload verification on the VM,
which is the point of the branch.

## Risks

| risk | mitigation |
|---|---|
| An LLM call the attorney did not ask for, on a shared Ollama, during a demo | `compaction_auto=False` in the VM's `.env` turns it off with a restart; the manual button is unaffected |
| The floor is a guess | It is config, and it only has to beat the two-rows-per-turn growth rate to prevent the churn loop it exists to prevent |
| Effect fires more than once per turn | Keyed on the **raw** backend breakdown, which `setBreakdown` replaces exactly once per turn — not on the live-adjusted object, which is reallocated on every render. Also guarded by `busy`. This was a defect in the first draft of this design and is the reason the live-document adjustment moves into the component. |
| Duplicated context for a turn if the attorney sends mid-run | Accepted, bounded, self-correcting; blocking the chat would be worse |
| An attorney is surprised that history was condensed without asking | The in-flight line and the completion note are both rendered; nothing is deleted, and the raw rows remain |

## Follow-ups

1. **Read the condensed summary** (carried over, unchanged): a GET route plus a
   viewer, so an attorney can see exactly which quotes stood in for which
   turns. Automatic firing makes this more valuable, not less.
2. **Measure whether the floor is right.** `interaction_event` already records
   pane actions; an auto-fire counter alongside the reclaim figures would say
   whether 6 is too eager or too lazy, rather than leaving it a guess.
