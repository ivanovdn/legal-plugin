// Eval runner — client half.
//
// Runs `match` and `apply` cases against the Word fake, and `parse` cases
// through the frontend parser so the two parsers can be compared. Prints one
// line per kind and exits non-zero on a regression or an unexpected pass.
//
// Run with: npx tsx src/eval/runner.ts   (from clients/word/)
//
// `declare const process` rather than @types/node — same reasoning as
// testAssert.ts; node:fs and node:path are declared in nodeShim.d.ts.
import { readFileSync, readdirSync } from "node:fs";
import { join, resolve } from "node:path";

import { applyEdit, findClauseRange } from "../word";
import { extractEditBlocks, normalizeProposals, type EditProposal } from "../parseEditBlocks";
import { createFakeWord, type FakeParagraph } from "./wordFake";

declare const process: { exitCode?: number; cwd(): string };

const CASES_DIR = resolve(process.cwd(), "../../evals/cases");
const BASELINE_PATH = resolve(process.cwd(), "../../evals/baseline.json");

// Typed rather than Record<string, unknown>: under `strict`, comparing an
// `unknown` against a boolean or a string is an error, and every use below
// would need a cast. Declaring the union of all three kinds' fields once —
// each optional — keeps the handlers cast-free.
type Case = {
  id: string;
  kind: "parse" | "match" | "apply";
  why: string;
  input: {
    prose?: string;
    paragraphs?: FakeParagraph[];
    target?: string;
    proposal?: EditProposal;
  };
  expect: {
    edits?: EditProposal[];
    normalized?: EditProposal[];
    matched?: string | null;
    ok?: boolean;
    errorContains?: string;
    reviewed?: string;
    trackingModeLog?: string[];
  };
};

const loadCases = (): Case[] =>
  readdirSync(CASES_DIR)
    .filter((f) => f.endsWith(".json"))
    .sort()
    .map((f) => {
      const parsed = JSON.parse(readFileSync(join(CASES_DIR, f), "utf8")) as Partial<Case>;
      return { ...parsed, id: parsed.id ?? f.replace(/\.json$/, "") } as Case;
    });

const loadBaseline = (): Record<string, string> => {
  try {
    return JSON.parse(readFileSync(BASELINE_PATH, "utf8")) as Record<string, string>;
  } catch {
    return {};
  }
};

async function runMatch(c: Case): Promise<boolean> {
  // `matched` is the only field a `match` case can omit and still name a real
  // expectation ("no match" is a value, not an absence). A typo'd field name
  // must fail the case, not silently fall back to "expected no match" via `??`.
  if (!("matched" in c.expect)) return false;
  const fake = createFakeWord(c.input.paragraphs ?? []);
  fake.install();
  try {
    return await Word.run(async (context) => {
      const range = await findClauseRange(context, c.input.target ?? "");
      if (range === null) return c.expect.matched === null;
      range.load("text");
      await context.sync();
      return range.text === c.expect.matched;
    });
  } finally {
    fake.uninstall();
  }
}

async function runApply(c: Case): Promise<boolean> {
  if (!c.input.proposal) return false;
  const fake = createFakeWord(c.input.paragraphs ?? []);
  fake.install();
  try {
    const result = await applyEdit(c.input.proposal);
    if (result.ok !== c.expect.ok) return false;
    if (c.expect.errorContains !== undefined) {
      if (result.ok || !result.error.includes(c.expect.errorContains)) return false;
    }
    if (c.expect.reviewed !== undefined && fake.reviewedText() !== c.expect.reviewed) return false;
    if (c.expect.trackingModeLog !== undefined) {
      if (fake.trackingModeLog().join(",") !== c.expect.trackingModeLog.join(",")) return false;
    }
    return true;
  } finally {
    fake.uninstall();
  }
}

// Serialized-JSON comparison: `undefined`-valued keys are dropped by
// `JSON.stringify` on both sides, but key ORDER still matters. Verified this
// doesn't bite any of the 3 new cases (case files were written directly from
// the parser's own JSON.stringify output, so field order already matches) —
// if a future case fails only on key order, stable-sort keys here before
// comparing (a comparison fix, not a case fix; see Task 6's report).
const sameEdits = (a: unknown, b: unknown): boolean => JSON.stringify(a) === JSON.stringify(b);

/**
 * `expect.edits` is the BACKEND's raw output — `_extract_proposed_edits(prose)`,
 * asserted verbatim by evals/run_parse.py. It is NOT "pre-normalization output
 * from both parsers": `extractEditBlocks` (this file's import) bakes
 * `normalizeProposals` into its own return value internally
 * (parseEditBlocks.ts:400-406), so the frontend has no raw/pre-normalization
 * stage to compare against at all — only the backend does.
 *
 * `expect.normalized` (optional) is `extractEditBlocks(prose).blocks` — the
 * frontend's own (already-normalized) output. Defaults to `expect.edits` when
 * absent, which is correct for inputs that trigger no normalization (the 3
 * Task 1 cases: TS's extraction is a no-op there, so its output equals the
 * backend's raw output).
 *
 * This mirrors the real data flow, not a hypothetical shared "extraction"
 * stage: in production, ChatTab.tsx prefers the backend's `proposed_edits`
 * whenever non-empty and runs `normalizeProposals` on whichever list wins
 * (CLAUDE.md: "normalize at the point of use, not only inside
 * extractEditBlocks"). So the invariant that actually matters — the one whose
 * violation would silently hand an attorney one edit where they should get
 * two — is `normalizeProposals(backend_raw) === frontend_output`, asserted
 * below as check 2. Comparing raw backend output against already-normalized
 * frontend output (the previous design) compares two different pipeline
 * stages and calls the mismatch a divergence; it is not one.
 */
async function runParse(c: Case): Promise<boolean> {
  const { blocks } = extractEditBlocks(c.input.prose ?? "");
  // 1. Pin extractEditBlocks's OWN output (its already-normalized "blocks").
  const expectedOwn = c.expect.normalized ?? c.expect.edits;
  if (!sameEdits(blocks, expectedOwn)) return false;
  // 2. Cross-language agreement, in the sense that matters: normalizing the
  //    backend's raw extraction must produce exactly what the frontend's own
  //    extraction already returns. This is the actual production invariant
  //    (see docstring above) and the reason this eval kind exists.
  if (!sameEdits(normalizeProposals(c.expect.edits ?? []), blocks)) return false;
  return true;
}

async function runCase(c: Case): Promise<boolean> {
  if (c.kind === "parse") return runParse(c);
  if (c.kind === "match") return runMatch(c);
  return runApply(c); // only "apply" remains — main() rejects any other kind before this runs
}

/**
 * Run every case of one kind and print its score line.
 *
 * Scoring is per kind but the contract is identical to the Python runner's:
 * a baseline entry only lowers the bar for a case that actually ran, and a
 * baselined case that PASSES is reported as loudly as a regression.
 *
 * Returns false if this kind regressed or produced an unexpected pass.
 */
async function runKind(
  kind: Case["kind"],
  all: Case[],
  baseline: Record<string, string>,
): Promise<boolean> {
  const cases = all.filter((c) => c.kind === kind);
  const results: Array<[string, boolean]> = [];
  for (const c of cases) results.push([c.id, await runCase(c)]);

  const knownFailing = new Set(Object.keys(baseline).filter((id) => cases.some((c) => c.id === id)));
  const passed = results.filter(([, ok]) => ok).length;
  const expected = results.length - knownFailing.size;
  const regressions = results.filter(([id, ok]) => !ok && !knownFailing.has(id)).map(([id]) => id);
  const unexpectedPasses = results.filter(([id, ok]) => ok && knownFailing.has(id)).map(([id]) => id);

  for (const [id, ok] of results) {
    if (!ok) console.log(`  [${knownFailing.has(id) ? "known" : "FAIL"}] ${id}`);
  }
  for (const id of unexpectedPasses) {
    console.log(`  [now-passing] ${id} — in baseline but passed; remove the entry or check the case`);
  }
  const known = results.length - expected;
  console.log(`${kind} ${passed}/${results.length}${known ? `   (${known} known-failing)` : ""}`);
  return regressions.length === 0 && unexpectedPasses.length === 0;
}

const KINDS = ["parse", "match", "apply"] as const;
const KNOWN_KINDS = new Set<string>(KINDS);

async function main(): Promise<void> {
  const baseline = loadBaseline();
  const all = loadCases();

  // A case whose "kind" isn't recognized never matches any of the three
  // `c.kind === kind` filters below, so it would otherwise run in NO kind and
  // the corpus would silently be short one case. Catch it here, before any
  // kind runs, rather than letting it vanish.
  const unknown = all.filter((c) => !KNOWN_KINDS.has(c.kind)).map((c) => c.id);
  if (unknown.length > 0) {
    console.log(`  [corpus] unknown kind in: ${unknown.join(", ")}`);
    process.exitCode = 1;
    return;
  }

  let clean = true;
  for (const kind of KINDS) {
    if (!(await runKind(kind, all, baseline))) clean = false;
  }
  if (!clean) process.exitCode = 1;
}

await main();
