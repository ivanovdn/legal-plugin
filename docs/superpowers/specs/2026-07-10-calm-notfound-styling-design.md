# Calm "not found" message styling — design

> **Status:** approved design, ready for an implementation plan.
> **Scope:** one frontend-only UI fix in the Word add-in. **No backend, graph, prompt,
> parser, or LLM change** — zero risk to review quality.
> **Source:** the `docs/wiki.md` follow-up + ledger note from the clause-locator-hardening
> smoke test (2026-07-10) — the now-calm `NO_MATCH_MESSAGE` still rendered in the red
> error pill and read like a failure.

## Problem

The locator's benign "nothing to locate" message (`NO_MATCH_MESSAGE` in
[`word.ts`](../../../clients/word/src/word.ts)) is returned as a `fail(...)`, and
[`FindingCard.tsx`](../../../clients/word/src/components/FindingCard.tsx) renders every
failed jump/comment/redline result through `<div className="card-status error">` — the
**red** pill (`.card-status.error`, red-bg / red-fg). So a finding that merely *describes*
a section rather than quoting it (nothing wrong, nothing to locate) looks identical to a
genuine failure like "Word is not available." The copy was softened in the prior branch;
the styling was not.

## Guiding principle

Distinguish the **benign "not found"** case from a **genuine error** in the data — not by
string-matching the message text (fragile coupling). Only the benign case restyles to a
neutral pill; real errors stay red. Frontend-only, additive.

---

## 1. `word.ts` — tag the benign case

- Extend the failure variant of `Result` to carry an optional flag:
  `export type Result<T = void> = { ok: true; value: T } | { ok: false; error: string; notFound?: boolean };`
- Add a `notFound` helper beside `fail`:
  `const notFound = (error: string): Result<never> => ({ ok: false, error, notFound: true });`
- Change the **null-range** guard in the two **navigation** entry points from
  `return fail(NO_MATCH_MESSAGE)` to `return notFound(NO_MATCH_MESSAGE)`:
  `showInDocument` and `goToClause`.
  > **Correction (smoke-driven, 2026-07-13).** The original design also routed
  > `acceptRedline`'s null-range through `notFound`. Smoke testing showed that was
  > wrong: `acceptRedline` is a **mutation** — a null-range means the requested
  > edit *did not apply*, which the user must act on. That is a genuine **red**
  > error, not the benign navigation case. So `acceptRedline`'s null-range stays a
  > `fail(...)` with an apply-appropriate message ("Couldn't find the target text
  > in the document — there's nothing to replace here."), and only the two
  > navigation paths use `notFound`. (Consequently `FindingCard`'s `onAccept` never
  > sees `notFound` and always renders a redline failure red; only `onJump`/`onShow`
  > read the flag.)
- **Everything else stays `fail` (red):** `isWordAvailable()` failure, empty-text /
  no-redline guards, and `acceptRedline`'s **completeness-guard** message
  ("Couldn't find the exact target text… rephrase or quote the exact wording") — these
  are genuine, action-requiring failures, not the calm case.

## 2. `FindingCard.tsx` — render the calm case softly

- Add an `ActionState` kind: `{ kind: "notfound"; message: string }`.
- In `onJump`, `onShow`, and `onAccept`, a failed result routes to `notfound` when
  `res.notFound`, else `error` as today. For example:
  `setJump(res.ok ? { kind: "idle" } : { kind: res.notFound ? "notfound" : "error", message: res.error });`
  (`onJump` currently resets to `idle` on success — that is unchanged; only the failure
  branch splits.)
- Add render lines for the `notfound` kind alongside the existing `error` lines, using the
  new muted class: `<div className="card-status info">{…message}</div>`. Genuine errors keep
  `<div className="card-status error">` (red). This applies to all three actions
  (comment, redline, jump) for consistency.

## 3. `styles.css` — the neutral pill

- New rule `.card-status.info` — same pill geometry as the base `.card-status`
  (inherits `font-size`/`padding`/`border-radius`), with
  `background: var(--secondary-hover-bg);` (#f3f5f8, a subtle gray already in `:root`) and
  `color: var(--muted);` (#5c6772). No red.

---

## Edge cases

- **A genuine error on the same action** (e.g. Word closed mid-session) → still `fail` →
  red `card-status error`. The two states are mutually exclusive per action.
- **`acceptRedline` completeness-guard** (found a too-short partial match) → stays `fail`
  (red) — it asks the user to rephrase, so the alarm is appropriate.
- **Older `Result` consumers** — `notFound` is an *optional* field on the existing
  `{ ok: false }` shape, so every current `if (!res.ok)` site keeps compiling and behaving
  as before; only `FindingCard` reads the new flag.

## Non-goals / Out of scope

- Changing the message text (already done in the prior branch).
- Suppressing the affordance for label-only findings, or the recurring-placeholder
  disambiguation follow-up — separate, deferred.
- Restyling genuine errors or the completeness-guard warning.

## Testing

- **`tsc --noEmit`** clean (`npm run typecheck`).
- **No unit test:** the `notFound` helper is a trivial object shape; the meaningful behavior
  is Office.js-wrapper failure classification + React render + CSS — the same
  smoke-tested posture as the original calm-message change.
- **Smoke test (required before merge):** sideload in Word for Mac —
  (a) a label-only / bundled-block finding that can't be located shows the calm message in a
  **neutral gray** pill, not red;
  (b) a genuine error (e.g. open the pane outside Word, or trigger a real failure) still shows
  the **red** error pill.

## Risks / rollback

- **Risk: near-zero.** Additive optional field + one new CSS class + a render branch. No
  behavior change beyond which pill color a benign not-found uses. Review outputs
  byte-identical (no graph/prompt/model surface touched).
- **Rollback:** revert the frontend commit; nothing depends on the `notFound` flag except
  `FindingCard`.
