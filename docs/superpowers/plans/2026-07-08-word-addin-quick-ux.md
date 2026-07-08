# Word Add-in Quick UX Wins Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add click-to-jump clause navigation and a findings filter/sort bar to the Word add-in — two frontend-only quick wins.

**Architecture:** A new pure `goToClause()` in `word.ts` selects a clause without mutating the doc (unlike `showInDocument`, which also inserts a comment); finding titles and chat edit-cards call it. A new pure `applyFindingFilters()` filters/sorts the parsed findings; `FindingsTab` renders a small filter bar over it. No backend, graph, prompt, or LLM change.

**Tech Stack:** React 18 + TypeScript + Vite (Office.js task pane). Tests are standalone `.ts` files run with `npx tsx`; typecheck is `tsc --noEmit`.

## Global Constraints

- **Frontend-only.** No change to `api/`, `graph/`, `skills/`, or any prompt/LLM behavior. Review outputs must be byte-identical.
- **All work is under `clients/word/`.** Run npm scripts from `clients/word/`; run tests from `clients/word/src/` (or with the `src/` path prefix).
- **Typecheck command:** `cd clients/word && npm run typecheck` (this is `tsc --noEmit`).
- **Unit-test command:** `cd clients/word && npx tsx src/<file>.test.ts` — a test file self-reports `PASS:`/`FAIL:` lines via a local `const pass = (cond, label) => console.log(cond ? \`PASS: ${label}\` : \`FAIL: ${label}\`);`. Verify **zero `FAIL:` lines**.
- **NEVER let `tsc` emit `.js` into `clients/word/src/`.** tsconfig is `noEmit:true`; only ever run `tsc --noEmit`. A stray `.js` shadows the `.tsx` source (Vite resolves `.js` first).
- **Navigation is non-mutating.** `goToClause` selects only — no `insertComment`, no completeness guard (partial-prefix matches are fine for navigation; only the *mutating* `acceptRedline` guards at `MATCH_COMPLETENESS_THRESHOLD`).
- **Follow existing patterns:** reuse `word.ts`'s in-module `toAnchors` / `findClauseRangeFromAnchors` / `isWordAvailable` / `ok` / `fail` / `Result`; reuse the `.badge` color classes and the Office accent `#0078d4` already in `styles.css`.
- **Branch:** do this work on `feat/word-addin-quick-ux` (repo convention `feat/*`), not on `main`.
- **Commits:** conventional-commit style; end every commit message with the trailer `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- **A React component / Office.js change cannot be unit-tested here** (no jsdom/React runner exists, by design). Those tasks gate on typecheck; behavior is verified in the Task 6 sideload smoke test. Do **not** add a React test framework (out of scope).

---

## File Structure

- **Create** `clients/word/src/findingFilters.ts` — pure `applyFindingFilters()` + `FindingFilters` type. One responsibility: filter/sort a `Finding[]`.
- **Create** `clients/word/src/findingFilters.test.ts` — unit tests for the above.
- **Modify** `clients/word/src/parser.ts` — export the existing `RISK_ORDER` (one word) so the filter module reuses it (DRY).
- **Modify** `clients/word/src/word.ts` — add `goToClause()` (select-only navigation).
- **Modify** `clients/word/src/components/FindingCard.tsx` — clickable title → `goToClause`; rename the comment button.
- **Modify** `clients/word/src/components/FindingsTab.tsx` — filter state + `FilterBar` + apply filters + reset-on-new-review + "showing X of Y".
- **Modify** `clients/word/src/components/EditProposalCard.tsx` — a "Go to" affordance calling `goToClause`.
- **Modify** `clients/word/src/styles.css` — clickable-title affordance + filter-bar styles.
- **Modify** `docs/wiki.md` — "Shipped Since Last Update" + follow-ups (per repo rule: shipping a feature updates the wiki).

---

## Task 1: `applyFindingFilters` pure function + tests

**Files:**
- Modify: `clients/word/src/parser.ts:98` (export `RISK_ORDER`)
- Create: `clients/word/src/findingFilters.ts`
- Test: `clients/word/src/findingFilters.test.ts`

**Interfaces:**
- Consumes: `Finding`, `Risk` (types) and `RISK_ORDER` (value) from `./parser`.
- Produces:
  - `interface FindingFilters { severities: Set<Risk>; owner: string; sortBy: "severity" | "clause" }`
  - `function applyFindingFilters(findings: Finding[], filters: FindingFilters): Finding[]`
  - `const ALL_RISKS: Risk[]` (canonical severity order, exported for the UI)

- [ ] **Step 1: Export `RISK_ORDER` from `parser.ts`**

In `clients/word/src/parser.ts`, change line 98 from:

```ts
const RISK_ORDER: Record<Risk, number> = { RED: 0, MISSING_CONTEXT: 1, YELLOW: 2, GREEN: 3 };
```

to:

```ts
export const RISK_ORDER: Record<Risk, number> = { RED: 0, MISSING_CONTEXT: 1, YELLOW: 2, GREEN: 3 };
```

- [ ] **Step 2: Write the failing test**

Create `clients/word/src/findingFilters.test.ts`:

```ts
// Pure-helper checks for findingFilters.ts. Run with: npx tsx src/findingFilters.test.ts
import { applyFindingFilters, ALL_RISKS, type FindingFilters } from "./findingFilters";
import type { Finding, Risk } from "./parser";

const pass = (cond: boolean, label: string) =>
  console.log(cond ? `PASS: ${label}` : `FAIL: ${label}`);

const makeFinding = (over: Partial<Finding>): Finding => ({
  issueId: "",
  clause: "Clause",
  risk: "GREEN",
  issue: "",
  currentText: "",
  anchors: [],
  hasQuotedText: false,
  redline: "",
  rationale: "",
  requiredAction: "",
  owner: "",
  externalComment: "",
  ...over,
});

const findings: Finding[] = [
  makeFinding({ clause: "Term", risk: "RED", owner: "CLCO" }),
  makeFinding({ clause: "Indemnity", risk: "YELLOW", owner: "Legal owner" }),
  makeFinding({ clause: "Definitions", risk: "MISSING_CONTEXT", owner: "" }),
  makeFinding({ clause: "Governing law", risk: "GREEN", owner: "Legal owner" }),
];

const all: FindingFilters = { severities: new Set(ALL_RISKS), owner: "all", sortBy: "severity" };

// severity subset
const redsOnly = applyFindingFilters(findings, { ...all, severities: new Set<Risk>(["RED"]) });
pass(redsOnly.length === 1 && redsOnly[0].clause === "Term", "severity subset keeps only RED");

// blockers-only (RED + MISSING_CONTEXT)
const blockers = applyFindingFilters(findings, { ...all, severities: new Set<Risk>(["RED", "MISSING_CONTEXT"]) });
pass(blockers.length === 2, "blockers-only keeps RED + MISSING_CONTEXT");

// empty severity set → empty result
const none = applyFindingFilters(findings, { ...all, severities: new Set<Risk>() });
pass(none.length === 0, "empty severity set yields no findings");

// owner filter (specific)
const legal = applyFindingFilters(findings, { ...all, owner: "Legal owner" });
pass(legal.length === 2 && legal.every((f) => f.owner === "Legal owner"), "owner filter keeps matching owner");

// owner filter (Unassigned = empty owner)
const unassigned = applyFindingFilters(findings, { ...all, owner: "Unassigned" });
pass(unassigned.length === 1 && unassigned[0].clause === "Definitions", "owner 'Unassigned' matches empty owner");

// owner 'all' keeps everything
pass(applyFindingFilters(findings, all).length === 4, "owner 'all' keeps every finding");

// sort by severity: RED, MISSING_CONTEXT, YELLOW, GREEN
const bySeverity = applyFindingFilters(findings, all).map((f) => f.risk);
pass(
  JSON.stringify(bySeverity) === JSON.stringify(["RED", "MISSING_CONTEXT", "YELLOW", "GREEN"]),
  "sort by severity orders RED<MISSING<YELLOW<GREEN",
);

// sort by clause name A–Z
const byClause = applyFindingFilters(findings, { ...all, sortBy: "clause" }).map((f) => f.clause);
pass(
  JSON.stringify(byClause) === JSON.stringify(["Definitions", "Governing law", "Indemnity", "Term"]),
  "sort by clause is alphabetical",
);

// purity: input array not mutated
const before = findings.map((f) => f.clause);
applyFindingFilters(findings, { ...all, sortBy: "clause" });
pass(JSON.stringify(findings.map((f) => f.clause)) === JSON.stringify(before), "does not mutate input array");
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd clients/word && npx tsx src/findingFilters.test.ts`
Expected: FAIL — `Cannot find module './findingFilters'` (the module doesn't exist yet).

- [ ] **Step 4: Write the minimal implementation**

Create `clients/word/src/findingFilters.ts`:

```ts
// Pure filter/sort logic for the Findings tab. Frontend-only, no Office.js.
import { RISK_ORDER, type Finding, type Risk } from "./parser";

/** Canonical severity order (matches RISK_ORDER); used to seed the UI chips. */
export const ALL_RISKS: Risk[] = ["RED", "MISSING_CONTEXT", "YELLOW", "GREEN"];

export interface FindingFilters {
  /** Only findings whose risk is in this set are kept. */
  severities: Set<Risk>;
  /** "all" | an owner name | "Unassigned" (matches findings with a blank owner). */
  owner: string;
  sortBy: "severity" | "clause";
}

/** Normalize a finding's owner for grouping/filtering. */
export function ownerKey(f: Finding): string {
  return f.owner.trim() || "Unassigned";
}

export function applyFindingFilters(findings: Finding[], filters: FindingFilters): Finding[] {
  const filtered = findings.filter((f) => {
    if (!filters.severities.has(f.risk)) return false;
    if (filters.owner !== "all" && ownerKey(f) !== filters.owner) return false;
    return true;
  });
  // Copy before sort — never mutate the caller's array.
  return [...filtered].sort((a, b) =>
    filters.sortBy === "clause"
      ? a.clause.localeCompare(b.clause)
      : RISK_ORDER[a.risk] - RISK_ORDER[b.risk],
  );
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd clients/word && npx tsx src/findingFilters.test.ts`
Expected: 9 `PASS:` lines, **zero `FAIL:`**.

- [ ] **Step 6: Typecheck**

Run: `cd clients/word && npm run typecheck`
Expected: no output, exit 0.

- [ ] **Step 7: Commit**

```bash
git add clients/word/src/parser.ts clients/word/src/findingFilters.ts clients/word/src/findingFilters.test.ts
git commit -m "feat(word): pure applyFindingFilters (severity/owner/sort) + tests

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `goToClause()` — select-only navigation in `word.ts`

**Files:**
- Modify: `clients/word/src/word.ts` (add after `showInDocument`, ~line 369)

**Interfaces:**
- Consumes (in-module, already defined in `word.ts`): `isWordAvailable`, `toAnchors`, `findClauseRangeFromAnchors`, `ok`, `fail`, `Result`.
- Produces: `function goToClause(target: string | string[]): Promise<Result<string>>` (exported).

- [ ] **Step 1: Add `goToClause` to `word.ts`**

Insert immediately **after** the `showInDocument` function (after its closing `}` near line 369) in `clients/word/src/word.ts`:

```ts
/**
 * Scroll Word to the clause matching `target` and SELECT it — nothing else.
 * Non-mutating navigation, unlike showInDocument (which also inserts a Word
 * comment). Partial-prefix matches are acceptable here: navigation never
 * writes, so there is no completeness guard (only acceptRedline guards, because
 * it mutates). Reuses the same anchor-fallback range finder.
 */
export async function goToClause(target: string | string[]): Promise<Result<string>> {
  if (!isWordAvailable()) return fail("Word is not available (open the add-in inside Word).");
  const anchors = toAnchors(target).filter((s) => s.trim());
  if (anchors.length === 0) return fail("Empty clause text — nothing to locate.");
  try {
    return await Word.run(async (context) => {
      const range = await findClauseRangeFromAnchors(context, anchors);
      if (!range) return fail("Couldn't locate this clause in the document.");
      range.select();
      range.load("text");
      await context.sync();
      return ok(range.text);
    });
  } catch (e) {
    return fail(e instanceof Error ? e.message : String(e));
  }
}
```

- [ ] **Step 2: Typecheck**

Run: `cd clients/word && npm run typecheck`
Expected: no output, exit 0. (If `toAnchors`/`findClauseRangeFromAnchors` names differ, grep `word.ts` for the actual helper `showInDocument` calls and match them — they are the same two helpers used at `showInDocument`.)

- [ ] **Step 3: Commit**

```bash
git add clients/word/src/word.ts
git commit -m "feat(word): goToClause — select clause without mutating the doc

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: FindingCard — clickable title + rename the comment button

**Files:**
- Modify: `clients/word/src/components/FindingCard.tsx`
- Modify: `clients/word/src/styles.css`

**Interfaces:**
- Consumes: `goToClause` (Task 2), the existing `ActionState` union, `finding.anchors`/`finding.currentText`.

- [ ] **Step 1: Import `goToClause`**

In `clients/word/src/components/FindingCard.tsx` line 4, change:

```ts
import { acceptRedline, showInDocument } from "../word";
```

to:

```ts
import { acceptRedline, goToClause, showInDocument } from "../word";
```

- [ ] **Step 2: Add jump state + handler**

After the existing `const [redline, setRedline] = useState<ActionState>({ kind: "idle" });` (line 23), add:

```ts
  const [jump, setJump] = useState<ActionState>({ kind: "idle" });

  const onJump = async () => {
    if (jump.kind === "running") return;
    setJump({ kind: "running" });
    const res = await goToClause(anchors);
    // Success is silent — the Word selection is the feedback. Only surface errors.
    setJump(res.ok ? { kind: "idle" } : { kind: "error", message: res.error });
  };
```

- [ ] **Step 3: Make the title clickable**

Replace the title line (line 52):

```tsx
        <div className="card-title">{finding.clause}</div>
```

with:

```tsx
        <div
          className="card-title card-title-clickable"
          role="button"
          tabIndex={0}
          title="Go to this clause in the document"
          onClick={onJump}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              onJump();
            }
          }}
        >
          {finding.clause}
        </div>
```

- [ ] **Step 4: Rename the comment button**

Replace the button block (lines 93–95):

```tsx
            <button className="secondary" onClick={onShow} disabled={comment.kind === "running"}>
              {comment.kind === "running" ? "Locating…" : "Show in document"}
            </button>
```

with:

```tsx
            <button className="secondary" onClick={onShow} disabled={comment.kind === "running"}>
              {comment.kind === "running" ? "Commenting…" : "Comment in doc"}
            </button>
```

- [ ] **Step 5: Render jump errors**

After the `redline.kind === "error"` status line (line 121), add:

```tsx
      {jump.kind === "error" && <div className="card-status error">{jump.message}</div>}
```

- [ ] **Step 6: Add title-affordance CSS**

Append to `clients/word/src/styles.css`:

```css
/* Clickable finding title → go to clause (non-mutating navigation) */
.card-title-clickable {
  cursor: pointer;
}
.card-title-clickable:hover {
  color: #0078d4;
  text-decoration: underline;
}
.card-title-clickable:focus-visible {
  outline: 2px solid #0078d4;
  outline-offset: 2px;
  border-radius: 2px;
}
```

- [ ] **Step 7: Typecheck**

Run: `cd clients/word && npm run typecheck`
Expected: no output, exit 0.

- [ ] **Step 8: Commit**

```bash
git add clients/word/src/components/FindingCard.tsx clients/word/src/styles.css
git commit -m "feat(word): click finding title to jump; comment becomes opt-in

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: FindingsTab — filter/sort bar

**Files:**
- Modify: `clients/word/src/components/FindingsTab.tsx`
- Modify: `clients/word/src/styles.css`

**Interfaces:**
- Consumes: `applyFindingFilters`, `FindingFilters`, `ALL_RISKS`, `ownerKey` (Task 1); `Risk` from `../parser`.

- [ ] **Step 1: Add imports to `FindingsTab.tsx`**

At the top of `clients/word/src/components/FindingsTab.tsx`, update the React import (line 1) and add the filter imports:

```ts
import { useEffect, useState } from "react";
```

Add after the existing `../parser` import block (after line 9):

```ts
import type { Risk } from "../parser";
import { applyFindingFilters, ALL_RISKS, ownerKey, type FindingFilters } from "../findingFilters";
```

- [ ] **Step 2: Add a `FilterBar` component**

Add this component just above `function Results(...)` (line 199) in `FindingsTab.tsx`:

```tsx
const RISK_LABEL: Record<Risk, string> = {
  RED: "RED",
  MISSING_CONTEXT: "MISSING",
  YELLOW: "YELLOW",
  GREEN: "GREEN",
};

function FilterBar({
  filters,
  setFilters,
  owners,
  shown,
  total,
}: {
  filters: FindingFilters;
  setFilters: React.Dispatch<React.SetStateAction<FindingFilters>>;
  owners: string[];
  shown: number;
  total: number;
}) {
  const toggleSeverity = (r: Risk) =>
    setFilters((f) => {
      const severities = new Set(f.severities);
      severities.has(r) ? severities.delete(r) : severities.add(r);
      return { ...f, severities };
    });

  return (
    <div className="filter-bar">
      {ALL_RISKS.map((r) => (
        <button
          key={r}
          className={`filter-chip ${filters.severities.has(r) ? "active" : ""}`}
          onClick={() => toggleSeverity(r)}
        >
          {RISK_LABEL[r]}
        </button>
      ))}
      <button
        className="filter-chip"
        onClick={() => setFilters((f) => ({ ...f, severities: new Set<Risk>(["RED", "MISSING_CONTEXT"]) }))}
      >
        Blockers only
      </button>
      <button
        className="filter-chip"
        onClick={() => setFilters((f) => ({ ...f, severities: new Set<Risk>(ALL_RISKS) }))}
      >
        All
      </button>
      {owners.length > 0 && (
        <select
          value={filters.owner}
          onChange={(e) => setFilters((f) => ({ ...f, owner: e.target.value }))}
        >
          <option value="all">All owners</option>
          {owners.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      )}
      <select
        value={filters.sortBy}
        onChange={(e) => setFilters((f) => ({ ...f, sortBy: e.target.value as FindingFilters["sortBy"] }))}
      >
        <option value="severity">Sort: severity</option>
        <option value="clause">Sort: clause name</option>
      </select>
      <span className="filter-count">
        showing {shown} of {total}
      </span>
    </div>
  );
}
```

- [ ] **Step 3: Wire filter state into `Results`**

Replace the body of `function Results({ result }: { result: ReviewSummary })` (lines 199–248) with:

```tsx
function Results({ result }: { result: ReviewSummary }) {
  const { findings, blockers, businessQuestions, gate, counts, header } = result;

  const [filters, setFilters] = useState<FindingFilters>({
    severities: new Set<Risk>(ALL_RISKS),
    owner: "all",
    sortBy: "severity",
  });

  // Reset filters whenever a new review arrives, so a stale filter can't hide
  // fresh findings. `result` is a new object on every parse.
  useEffect(() => {
    setFilters({ severities: new Set<Risk>(ALL_RISKS), owner: "all", sortBy: "severity" });
  }, [result]);

  const owners = Array.from(new Set(findings.map(ownerKey))).sort();
  const visible = applyFindingFilters(findings, filters);

  return (
    <>
      <GateBanner gate={gate} />

      <div className="summary">
        {header.contractType && (
          <span>
            <strong>Type:</strong> {header.contractType}
          </span>
        )}
        {header.trinetixRole && (
          <span>
            <strong>Role:</strong> {header.trinetixRole}
          </span>
        )}
        {header.counterparty && (
          <span>
            <strong>Counterparty:</strong> {header.counterparty}
          </span>
        )}
        {header.overallStatus && (
          <span>
            <strong>Status:</strong> {header.overallStatus}
          </span>
        )}
        <span className="badge red">{counts.red} RED</span>
        <span className="badge yellow">{counts.yellow} YELLOW</span>
        <span className="badge green">{counts.green} GREEN</span>
        {counts.missingContext > 0 && (
          <span className="badge missing_context">{counts.missingContext} MISSING</span>
        )}
      </div>

      <BlockerList blockers={blockers} />

      {findings.length === 0 && (
        <div className="status">No clause findings parsed from the response.</div>
      )}

      {findings.length > 0 && (
        <FilterBar
          filters={filters}
          setFilters={setFilters}
          owners={owners}
          shown={visible.length}
          total={findings.length}
        />
      )}

      {findings.length > 0 && visible.length === 0 && (
        <div className="status">No findings match the current filters.</div>
      )}

      <div className="findings">
        {visible.map((f, i) => (
          <FindingCard key={`${f.issueId}-${f.clause}-${i}`} finding={f} />
        ))}
      </div>

      <BusinessQuestions questions={businessQuestions} />
    </>
  );
}
```

- [ ] **Step 4: Add filter-bar CSS**

Append to `clients/word/src/styles.css`:

```css
/* Findings filter / sort bar */
.filter-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin: 8px 0;
  font-size: 12px;
}
.filter-chip {
  border: 1px solid #c8c6c4;
  background: #fff;
  border-radius: 12px;
  padding: 2px 10px;
  cursor: pointer;
  color: #605e5c;
}
.filter-chip.active {
  border-color: #0078d4;
  background: #eff6fc;
  color: #0078d4;
  font-weight: 600;
}
.filter-bar select {
  font-size: 12px;
  padding: 2px 4px;
}
.filter-count {
  margin-left: auto;
  color: #605e5c;
}
```

- [ ] **Step 5: Typecheck**

Run: `cd clients/word && npm run typecheck`
Expected: no output, exit 0.

- [ ] **Step 6: Commit**

```bash
git add clients/word/src/components/FindingsTab.tsx clients/word/src/styles.css
git commit -m "feat(word): findings filter/sort bar (severity/owner/sort + count)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: EditProposalCard — "Go to" the edit target

**Files:**
- Modify: `clients/word/src/components/EditProposalCard.tsx`

**Interfaces:**
- Consumes: `goToClause` (Task 2); `proposal.target_text` / `proposal.anchor_text` / `proposal.action` (existing `EditProposal` fields).

- [ ] **Step 1: Import `goToClause`**

In `clients/word/src/components/EditProposalCard.tsx` line 3, change:

```ts
import { applyEdit } from "../word";
```

to:

```ts
import { applyEdit, goToClause } from "../word";
```

- [ ] **Step 2: Add jump state + handler + target**

After `const [status, setStatus] = useState<Status>({ kind: "idle" });` (line 29), add:

```ts
  const [jumpError, setJumpError] = useState<string | null>(null);

  // For inserts, the doc location is the anchor; for replace/replace_all/delete
  // it's the target text.
  const jumpTarget = proposal.action === "insert" ? proposal.anchor_text : proposal.target_text;

  const onJump = async () => {
    if (!jumpTarget) return;
    setJumpError(null);
    const res = await goToClause(jumpTarget);
    if (!res.ok) setJumpError(res.error);
  };
```

- [ ] **Step 3: Add the "Go to" button**

In the `card-actions` row, add a "Go to" button before the Discard button. Change (lines 121–129):

```tsx
        {status.kind !== "applied" && (
          <button
            className="secondary"
            onClick={onDiscard}
            disabled={status.kind === "running"}
          >
            Discard
          </button>
        )}
```

to:

```tsx
        {jumpTarget && (
          <button className="secondary" onClick={onJump} disabled={status.kind === "running"}>
            Go to
          </button>
        )}
        {status.kind !== "applied" && (
          <button
            className="secondary"
            onClick={onDiscard}
            disabled={status.kind === "running"}
          >
            Discard
          </button>
        )}
```

- [ ] **Step 4: Render jump errors**

After the `status.kind === "error"` status line (line 143), add:

```tsx
      {jumpError && <div className="card-status error">{jumpError}</div>}
```

- [ ] **Step 5: Typecheck**

Run: `cd clients/word && npm run typecheck`
Expected: no output, exit 0.

- [ ] **Step 6: Commit**

```bash
git add clients/word/src/components/EditProposalCard.tsx
git commit -m "feat(word): 'Go to' on chat edit cards navigates to the target

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Full verification, sideload smoke test, and docs

**Files:**
- Modify: `docs/wiki.md`

- [ ] **Step 1: Full typecheck + all unit tests**

```bash
cd clients/word && npm run typecheck
npx tsx src/findingFilters.test.ts
npx tsx src/parser.test.ts
npx tsx src/word.test.ts
npx tsx src/parseEditBlocks.test.ts
npx tsx src/normalize.test.ts
```
Expected: typecheck exit 0; **zero `FAIL:` lines** across all test files (Task-1 export of `RISK_ORDER` must not have broken `parser.test.ts`).

- [ ] **Step 2: Sideload smoke test in Word for Mac**

Run the dev server (`cd clients/word && npm run dev`), open the sideloaded add-in in Word for Mac against a real contract, run a review, and verify:
  - [ ] Clicking a finding **title** scrolls to + selects the clause, and **no Word comment is added**.
  - [ ] The **"Comment in doc"** button still inserts the comment ("Commented ✓").
  - [ ] Severity chips toggle; **Blockers only** shows RED+MISSING; **All** restores; owner dropdown filters; both sort orders work.
  - [ ] **"showing X of Y"** count is correct, including 0 when all severities are off ("No findings match the current filters.").
  - [ ] Filters **reset** after a Re-review.
  - [ ] In the **Chat tab**, a proposed edit card's **"Go to"** selects the target text.
  - [ ] A finding whose clause can't be located shows an inline error (does not crash the list).

- [ ] **Step 3: Update `docs/wiki.md`**

Add a row to the "Shipped Since Last Update" table:

```markdown
| **Word add-in: click-to-jump navigation + findings filter/sort** | `feat/word-addin-quick-ux` | Frontend-only "Bucket A" UX wins. New `goToClause()` (`word.ts`) selects a clause **without mutating** the doc; clicking a finding **title** jumps to it (the old "Show in document" is renamed **"Comment in doc"** — the audit-trail comment is now opt-in), and chat edit cards get a **"Go to"**. New pure `applyFindingFilters()` (`findingFilters.ts`, unit-tested) powers a **filter/sort bar** in `FindingsTab` (severity chips, Blockers-only, owner dropdown, sort by severity/clause, "showing X of Y", reset-on-re-review). No backend/graph/prompt/LLM change; summary chips / gate banner / blockers card unaffected. tsc clean; +9 frontend asserts; live-smoke-tested in Word for Mac. |
```

Then update the top-of-file "Last updated" line's date and test count, and remove/annotate the covered follow-ups (the `#1`/`#8` items are now shipped).

- [ ] **Step 4: Commit**

```bash
git add docs/wiki.md
git commit -m "docs: Word add-in click-to-jump + findings filters shipped (wiki)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-review notes (author)

- **Spec coverage:** Feature 1 click-to-jump → Tasks 2 (word.ts), 3 (FindingCard title + rename), 5 (chat edit cards). Feature 2 filter/sort → Tasks 1 (pure fn + tests), 4 (FilterBar + wiring). Non-mutating navigation, partial-match tolerance, reset-on-new-review, "showing X of Y", unaffected summary/gate/blockers/business-questions, unit tests + smoke test, wiki update — all covered.
- **Type consistency:** `applyFindingFilters(findings, filters)`, `FindingFilters { severities: Set<Risk>; owner: string; sortBy }`, `ALL_RISKS`, `ownerKey` are defined in Task 1 and consumed with the same names/shapes in Task 4; `goToClause(target: string | string[])` defined in Task 2 and consumed with matching signatures in Tasks 3 and 5.
- **Non-goals honored:** no clause-*type* filter (no field), no bulk actions, no backend/LLM change, no new test framework.
