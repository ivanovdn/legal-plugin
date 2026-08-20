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
  const fake = createFakeWord(c.input.paragraphs ?? []);
  fake.install();
  try {
    return await Word.run(async (context) => {
      const range = await findClauseRange(context, c.input.target ?? "");
      if (range === null) return (c.expect.matched ?? null) === null;
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

async function runParse(c: Case): Promise<boolean> {
  const { blocks } = extractEditBlocks(c.input.prose ?? "");
  if (!sameEdits(blocks, c.expect.edits)) return false;
  if (c.expect.normalized !== undefined) {
    if (!sameEdits(normalizeProposals(blocks), c.expect.normalized)) return false;
  }
  return true;
}

async function runCase(c: Case): Promise<boolean> {
  if (c.kind === "parse") return runParse(c);
  if (c.kind === "match") return runMatch(c);
  if (c.kind === "apply") return runApply(c);
  return false; // an unknown kind is a corpus error, not a pass
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

async function main(): Promise<void> {
  const baseline = loadBaseline();
  const all = loadCases();
  let clean = true;
  for (const kind of KINDS) {
    if (!(await runKind(kind, all, baseline))) clean = false;
  }
  if (!clean) process.exitCode = 1;
}

await main();
