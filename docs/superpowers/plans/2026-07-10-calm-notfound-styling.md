# Calm "Not Found" Message Styling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render the locator's benign "nothing to locate" message (`NO_MATCH_MESSAGE`) in a neutral gray pill instead of the red error pill, while genuine errors stay red.

**Architecture:** Tag the benign failure in the data — add an optional `notFound` flag to `Result` and a `notFound()` helper in `word.ts`; the three null-range guards return `notFound(NO_MATCH_MESSAGE)`. `FindingCard.tsx` routes a failed result to a new `notfound` `ActionState` kind when `res.notFound`, rendered via a new muted `.card-status.info` CSS class. No string-matching of the message. Frontend-only.

**Tech Stack:** TypeScript, React (Office.js task pane), CSS. Typecheck via `tsc --noEmit`; standalone `.ts` tests via `npx tsx`. Office.js/React/CSS behavior is smoke-tested (sideload), not unit-tested.

## Global Constraints

- **Frontend-only.** Touch only `clients/word/src/word.ts`, `clients/word/src/components/FindingCard.tsx`, `clients/word/src/styles.css`, and (Task 2) `docs/wiki.md`. No backend/graph/prompt/parser change — review outputs must stay byte-identical.
- **All imports at top of file** (repo hard rule); never emit `.js` into `src/` (tsconfig `noEmit:true`).
- **Tag, don't string-match.** The benign case is identified by the `notFound` flag on `Result`, never by comparing message text.
- **Only the null-range `NO_MATCH_MESSAGE` becomes `notFound`.** Word-unavailable, empty-text, no-redline, and `acceptRedline`'s completeness-guard ("Couldn't find the exact target text…") stay `fail` (red).
- **`notFound` is an OPTIONAL field** on the existing `{ ok: false }` variant, so every current `if (!res.ok)` consumer keeps compiling unchanged.
- Commands run from `clients/word/`.

---

### Task 1: Neutral pill for the benign "not found" case

**Files:**
- Modify: `clients/word/src/word.ts` (Result type + `notFound` helper + 3 call sites)
- Modify: `clients/word/src/components/FindingCard.tsx` (new `notfound` state kind + routing + render)
- Modify: `clients/word/src/styles.css` (new `.card-status.info`)

**Interfaces:**
- Consumes: existing `Result`, `ok`, `fail`, `NO_MATCH_MESSAGE` in `word.ts`; existing `.card-status` base rule and `--secondary-hover-bg` / `--muted` tokens in `styles.css`.
- Produces: `Result` fail variant now carries optional `notFound?: boolean`; `FindingCard` reads `res.notFound`.

- [ ] **Step 1: Extend `Result` and add the `notFound` helper (`word.ts`)**

Change the `Result` type (currently line 10) and add the helper next to `fail` (currently lines 12-13):

```ts
export type Result<T = void> =
  | { ok: true; value: T }
  | { ok: false; error: string; notFound?: boolean };

const ok = <T>(value: T): Result<T> => ({ ok: true, value });
const fail = (error: string): Result<never> => ({ ok: false, error });
// A benign "there is nothing in the document to locate" outcome (a finding that
// describes rather than quotes a section) — NOT a genuine failure. The card
// renders these in a neutral pill instead of the red error pill.
const notFound = (error: string): Result<never> => ({ ok: false, error, notFound: true });
```

- [ ] **Step 2: Route the three null-range guards to `notFound` (`word.ts`)**

There are exactly three `return fail(NO_MATCH_MESSAGE)` occurrences — the `if (!range)` guards in `showInDocument`, `goToClause`, and `acceptRedline`. Change each to `return notFound(NO_MATCH_MESSAGE)`:

```ts
      if (!range) return notFound(NO_MATCH_MESSAGE);
```

Leave every other `fail(...)` in the file unchanged (Word-unavailable, empty-text, no-redline, and `acceptRedline`'s completeness-guard "Couldn't find the exact target text…" message).

- [ ] **Step 3: Verify the swap (`word.ts`)**

Run: `cd clients/word && grep -n "notFound(NO_MATCH_MESSAGE)\|fail(NO_MATCH_MESSAGE)" src/word.ts`
Expected: three `notFound(NO_MATCH_MESSAGE)` lines, and **zero** `fail(NO_MATCH_MESSAGE)` lines.

- [ ] **Step 4: Add the `notfound` state kind + routing (`FindingCard.tsx`)**

Extend the `ActionState` union (currently lines 6-10) with a `notfound` kind:

```ts
type ActionState =
  | { kind: "idle" }
  | { kind: "running" }
  | { kind: "done"; message: string }
  | { kind: "error"; message: string }
  | { kind: "notfound"; message: string };
```

Then route failed results to `notfound` when `res.notFound`. In `onJump` (currently line 31):

```ts
    setJump(res.ok ? { kind: "idle" } : { kind: res.notFound ? "notfound" : "error", message: res.error });
```

In `onShow` (currently lines 40-41), change only the failure branch:

```ts
    if (res.ok) setComment({ kind: "done", message: "Commented ✓" });
    else setComment({ kind: res.notFound ? "notfound" : "error", message: res.error });
```

In `onAccept` (currently lines 52-53), change only the failure branch:

```ts
    if (res.ok) setRedline({ kind: "done", message: "Applied ✓ — see Track Changes" });
    else setRedline({ kind: res.notFound ? "notfound" : "error", message: res.error });
```

- [ ] **Step 5: Render the `notfound` kind (`FindingCard.tsx`)**

The status render block (currently lines 141-145) ends with the `error`/`done` lines. Add three `notfound` lines using the new muted class, next to their matching `error` lines:

```tsx
      {comment.kind === "done" && <div className="card-status success">{comment.message}</div>}
      {comment.kind === "error" && <div className="card-status error">{comment.message}</div>}
      {comment.kind === "notfound" && <div className="card-status info">{comment.message}</div>}
      {redline.kind === "done" && <div className="card-status success">{redline.message}</div>}
      {redline.kind === "error" && <div className="card-status error">{redline.message}</div>}
      {redline.kind === "notfound" && <div className="card-status info">{redline.message}</div>}
      {jump.kind === "error" && <div className="card-status error">{jump.message}</div>}
      {jump.kind === "notfound" && <div className="card-status info">{jump.message}</div>}
```

- [ ] **Step 6: Add the `.card-status.info` rule (`styles.css`)**

Immediately after the `.card-status.error` block (currently lines 286-289), add:

```css
.card-status.info {
  background: var(--secondary-hover-bg);
  color: var(--muted);
}
```

- [ ] **Step 7: Typecheck**

Run: `cd clients/word && npm run typecheck`
Expected: exits 0, no output. (The `notfound` union member is exhaustively handled; `res.notFound` narrows on the `{ ok: false }` branch.)

- [ ] **Step 8: Full frontend suite (no regression)**

Run: `cd clients/word && for f in src/*.test.ts; do npx tsx "$f"; done | grep -c "PASS:"; for f in src/*.test.ts; do npx tsx "$f"; done | grep -c "FAIL:"`
Expected: `154` PASS and `0` FAIL (this task adds no asserts — it's Office.js-wrapper failure classification + React render + CSS, verified by smoke, per the spec).

- [ ] **Step 9: Commit**

```bash
git add clients/word/src/word.ts clients/word/src/components/FindingCard.tsx clients/word/src/styles.css
git commit -m "feat(word): neutral pill for benign 'not found' finding results

The calm NO_MATCH_MESSAGE (a finding that describes rather than quotes a section)
was rendered in the red error pill, reading like a failure. Tag it in the data:
Result gains an optional notFound flag, the three null-range guards return
notFound() instead of fail(), and FindingCard routes those to a new 'notfound'
state rendered via a muted .card-status.info pill. Genuine errors (Word
unavailable, completeness-guard, empty text) stay red. No string-matching."
```

---

### Task 2: Update wiki (follow-up resolved)

**Files:**
- Modify: `docs/wiki.md` (mark the calm-box styling follow-up resolved / note it shipped)

**Interfaces:** none (docs only).

- [ ] **Step 1: Locate the follow-up and Shipped table**

Run: `grep -n "calm-box\|calm no-match\|red/error styling\|Shipped Since Last Update\|Last updated:" docs/wiki.md`
Read the matching follow-up row (the deferred "red styling on the now-calm no-match box" item recorded from the clause-locator-hardening branch) and the "Shipped Since Last Update" table region so edits match the file's existing format.

- [ ] **Step 2: Record the change**

Mark the calm-box styling follow-up **resolved** in whatever convention the file uses for closed follow-ups (the clause-locator row used `~~strikethrough~~` + `**DONE**`). If there's a distinct "Shipped Since Last Update" table, add a short row: benign `NO_MATCH_MESSAGE` now renders in a neutral gray `.card-status.info` pill (tagged via a new `Result.notFound` flag, not string-matching); genuine errors stay red. Frontend-only; **smoke-confirm pending** (appended after sideload). If the wiki header carries a date, bump it to 2026-07-10; the frontend assert count is unchanged (154 — no new asserts).

- [ ] **Step 3: Commit**

```bash
git add docs/wiki.md
git commit -m "docs(wiki): calm not-found message now a neutral pill; follow-up resolved"
```

---

## Self-Review

**Spec coverage:**
- `Result.notFound` + `notFound()` helper, 3 call sites (`word.ts`) → Task 1 Steps 1-3. ✅
- `notfound` `ActionState` kind + routing + render (`FindingCard.tsx`) → Task 1 Steps 4-5. ✅
- `.card-status.info` neutral pill (`styles.css`) → Task 1 Step 6. ✅
- Genuine errors / completeness-guard stay red → Task 1 Step 2 (leave other `fail` unchanged) + Global Constraints. ✅
- No unit test by design; typecheck + smoke → Task 1 Steps 7-8 + spec Testing. ✅
- Wiki follow-up resolved (CLAUDE.md ship rule) → Task 2. ✅

**Placeholder scan:** No TBD/TODO. Every code step shows the exact code; every run step shows the command and expected output. ✅

**Type consistency:** `Result` fail variant `{ ok: false; error: string; notFound?: boolean }` is defined in Step 1 and read as `res.notFound` in Steps 4; `ActionState` `notfound` kind defined in Step 4 and rendered in Step 5; `.card-status.info` defined in Step 6, referenced in Step 5. ✅

**Task independence:** Task 1 is one coherent behavioral unit (the flag is inert until FindingCard reads it, so splitting word.ts from the render would leave a no-op mid-state — folded into one task). Task 2 is docs-only.
