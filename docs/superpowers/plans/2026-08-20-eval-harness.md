# Eval Harness (Tier 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic, zero-LLM eval harness that covers the two things this repo's test suite does not — the Office.js matcher (which has no automated coverage at all) and whether the backend and frontend edit parsers agree on the same string.

**Architecture:** A JSON case corpus under `evals/cases/` is data, not code. Two runners consume it: a Python script for the backend parser, and a `tsx` script for the frontend parser, the matcher and the apply path. The matcher runs against a hand-written fake of Office.js whose semantics encode `CLAUDE.md`'s documented Word gotchas. Scoring is a score-plus-baseline, never all-pass, so a case promoted from a real tester complaint can live in the repo before it is fixed.

**Tech Stack:** Python 3.12 (stdlib only — `json`, `pathlib`, `argparse`), TypeScript 5.6 run via `npx tsx`, no new dependencies in either language.

**Spec:** `docs/superpowers/specs/2026-08-19-eval-harness-design.md` — read it before starting. It carries the reasoning this plan only executes.

## Global Constraints

- **Branch:** `feat/eval-harness`. The spec is already committed there (`74bb350`). Never implement on `main`.
- **All imports at the top of the file.** No lazy imports inside functions. (Repo rule 1.)
- **No backwards-compat shims.** Change call sites instead. (Repo rule 5.)
- **Exactly one change to shipped application code in this whole plan:** adding the `export` keyword to `findClauseRange` in `clients/word/src/word.ts`. No signature change, no logic change, nowhere else. If a task seems to need another, stop and raise it.
- **Zero LLM calls.** Every task in this plan runs offline and deterministically.
- **Do not restate existing coverage.** `tests/test_skills.py` (21 edit-parsing tests) and `clients/word/src/parseEditBlocks.test.ts` (462 lines) already pin extraction on both sides. A `parse` case exists only to compare the two parsers against each other, or to pin the TS-only normalization stage.
- **The Word fake must NOT be written by reading `word.ts`.** Write it from `CLAUDE.md`'s Word gotchas and the Office.js documentation. Writing it from the caller encodes the caller's assumptions and the eval then confirms itself.
- **`clients/word/tsconfig.json` is strict:** `strict`, `noUnusedLocals`, `noUnusedParameters`, and `"types": ["office-js", "vite/client"]`. No `@types/node`; follow the existing `declare const process` pattern in `clients/word/src/testAssert.ts`.
- **Never let `tsc` emit `.js` into `clients/word/src/`.** Vite resolves `.js` before `.ts` and a stray compiled file silently shadows the source.
- **`scripts/check.sh` has `EXPECTED_PASS_COUNT=221`.** Any task that adds a `*.test.ts` assertion must update that number in the same commit, or the gate fails.
- **Run `bash scripts/check.sh` before the final commit of every task.**

### Deliberate refinements to the spec

Two, both narrowing:

1. **One export, not two.** The spec anticipated exporting `searchCandidates` and `tailCandidates`. It also ruled out asserting candidate order (it would ossify the heuristics). With order unasserted, neither helper needs exporting — only `findClauseRange`. Smaller blast radius; take it.
2. **Eval sources live in `clients/word/src/eval/`, not flat in `src/`.** Three files (runner, fake, ambient shim) are a unit with one responsibility. `scripts/check.sh`'s `src/*.test.ts` glob is non-recursive, so nothing in the subdirectory is swept into the 221-assertion gate by accident.

### One thing the spec oversold

The spec claims `tsc --noEmit` checks the fake against Microsoft's Office.js types "for free". That is only partly true. Implementing `Word.RequestContext` structurally would mean implementing hundreds of members, so the fake is installed on `globalThis` and cast at the boundary. What the compiler *does* still check is that the fake's enum values are read from the real `Word.*` enums. State this limitation in the fake's header comment rather than repeating the spec's stronger claim.

---

### Task 1: Case format, Python runner, scoring

**Files:**
- Create: `evals/__init__.py`
- Create: `evals/run_parse.py`
- Create: `evals/cases/parse-stacked-objects.json`
- Create: `evals/cases/parse-array-in-one-fence.json`
- Create: `evals/cases/parse-tolerant-raw-newline.json`
- Create: `evals/baseline.json`
- Test: `tests/test_eval_runner.py`

**Interfaces:**
- Consumes: `skills.legal_research.edit_parsing._extract_proposed_edits(prose: str) -> list[dict]` (already exists).
- Produces:
  - `evals.run_parse.load_cases(cases_dir: Path) -> list[dict]` — every `*.json` in the directory, sorted by filename, each parsed and given its filename-derived `id` if absent.
  - `evals.run_parse.load_baseline(path: Path) -> dict[str, str]` — case id → reason. Missing file returns `{}`.
  - `evals.run_parse.score(results: list[tuple[str, bool]], baseline: dict[str, str]) -> dict` — returns `{"passed": int, "total": int, "expected": int, "regressions": list[str], "unexpected_passes": list[str]}` where `expected = total - len(baseline entries present in results)`.
  - `evals.run_parse.run_case(case: dict) -> bool`

**The case format** (all three kinds; later tasks add the other two):

```json
{
  "id": "parse-stacked-objects",
  "kind": "parse",
  "why": "Local LLMs emit stacked top-level objects in one fence. Reimplemented twice (_iter_json_values / iterJsonValues), so the two parsers can drift. Traces cea50c6b, f15f8a9b.",
  "input": { "prose": "..." },
  "expect": { "edits": [ ... ], "normalized": [ ... ] }
}
```

`expect.edits` is asserted by **both** runners. `expect.normalized` is optional and TS-only.

- [ ] **Step 1: Write the failing test**

Create `tests/test_eval_runner.py`:

```python
"""Unit tests for the eval runner's own logic.

The corpus itself is data and is not unit-tested — these cover only the parts
of the runner that could be wrong in a way no case would reveal: loading,
baseline diffing, and the score comparison that gates the push.
"""
import json
from pathlib import Path

from evals.run_parse import load_baseline, load_cases, score


def test_load_cases_reads_every_json_sorted(tmp_path: Path):
    (tmp_path / "b.json").write_text(json.dumps({"id": "b", "kind": "parse"}))
    (tmp_path / "a.json").write_text(json.dumps({"id": "a", "kind": "parse"}))
    assert [c["id"] for c in load_cases(tmp_path)] == ["a", "b"]


def test_load_cases_defaults_id_to_filename(tmp_path: Path):
    (tmp_path / "parse-thing.json").write_text(json.dumps({"kind": "parse"}))
    assert load_cases(tmp_path)[0]["id"] == "parse-thing"


def test_load_baseline_missing_file_is_empty(tmp_path: Path):
    assert load_baseline(tmp_path / "nope.json") == {}


def test_score_counts_a_known_failure_as_expected():
    s = score([("a", True), ("b", False)], {"b": "known"})
    assert s["passed"] == 1 and s["expected"] == 1
    assert s["regressions"] == []


def test_score_reports_a_regression():
    s = score([("a", False)], {})
    assert s["regressions"] == ["a"]
    assert s["passed"] < s["expected"]


def test_score_reports_an_unexpected_pass():
    """A baselined case that starts passing needs a human as loudly as a regression."""
    s = score([("a", True)], {"a": "known"})
    assert s["unexpected_passes"] == ["a"]


def test_score_ignores_baseline_entries_for_absent_cases():
    """A stale baseline id must not silently lower the bar for the cases that ran."""
    s = score([("a", True)], {"gone": "case was deleted"})
    assert s["expected"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_eval_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'evals'`

- [ ] **Step 3: Write minimal implementation**

Create `evals/__init__.py` (empty file).

Create `evals/run_parse.py`:

```python
"""Eval runner — backend half.

Runs every `parse` case through the BACKEND parser and compares against
`expect.edits`. `expect.normalized` is ignored here: it names a stage that
exists only in the client (`normalizeProposals`), so the TS runner owns it.

There is no skip list. Every parse case is in scope for this runner, so a case
cannot quietly stop being checked on one side.

Deliberately plain: no pytest, no fixtures, no Docker, because it has to run
identically from scripts/eval.sh and from a developer's shell. Its own logic is
unit-tested in tests/test_eval_runner.py.
"""
import argparse
import json
import sys
from pathlib import Path

from skills.legal_research.edit_parsing import _extract_proposed_edits

REPO_ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = REPO_ROOT / "evals" / "cases"
BASELINE_PATH = REPO_ROOT / "evals" / "baseline.json"


def load_cases(cases_dir: Path) -> list[dict]:
    cases = []
    for path in sorted(cases_dir.glob("*.json")):
        case = json.loads(path.read_text())
        case.setdefault("id", path.stem)
        cases.append(case)
    return cases


def load_baseline(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def run_case(case: dict) -> bool:
    prose = case["input"]["prose"]
    return _extract_proposed_edits(prose) == case["expect"]["edits"]


def score(results: list[tuple[str, bool]], baseline: dict[str, str]) -> dict:
    ran = {case_id for case_id, _ in results}
    # Only baseline entries whose case actually ran may lower the bar. A stale
    # id for a deleted case would otherwise silently forgive a live failure.
    known_failing = {cid for cid in baseline if cid in ran}
    passed = sum(1 for _, ok in results if ok)
    return {
        "passed": passed,
        "total": len(results),
        "expected": len(results) - len(known_failing),
        "regressions": [cid for cid, ok in results if not ok and cid not in known_failing],
        "unexpected_passes": [cid for cid, ok in results if ok and cid in known_failing],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run backend-side eval cases.")
    parser.add_argument("--cases", type=Path, default=CASES_DIR)
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    args = parser.parse_args()

    cases = [c for c in load_cases(args.cases) if c.get("kind") == "parse"]
    baseline = load_baseline(args.baseline)
    results = [(c["id"], run_case(c)) for c in cases]
    s = score(results, baseline)

    for case_id, ok in results:
        if not ok:
            marker = "known" if case_id in baseline else "FAIL"
            print(f"  [{marker}] {case_id}")
    for case_id in s["unexpected_passes"]:
        print(f"  [now-passing] {case_id} — in baseline but passed; remove the entry or check the case")

    known = s["total"] - s["expected"]
    suffix = f"   ({known} known-failing)" if known else ""
    print(f"parse-py {s['passed']}/{s['total']}{suffix}")
    return 0 if s["passed"] >= s["expected"] and not s["unexpected_passes"] else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_eval_runner.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Write the three no-normalization parse cases**

Create `evals/cases/parse-stacked-objects.json`:

```json
{
  "kind": "parse",
  "why": "A fenced block can hold stacked top-level objects, not just one object or an array. Parsed by _iter_json_values (py) and iterJsonValues (ts) — written twice, so they can drift. Traces cea50c6b, f15f8a9b.",
  "input": {
    "prose": "I'll update both places.\n\n```json\n{\"action\": \"replace\", \"target_text\": \"Governing Law. This Agreement shall be governed by the laws of Delaware.\", \"new_text\": \"Governing Law. This Agreement shall be governed by the laws of England and Wales.\"}\n{\"action\": \"replace\", \"target_text\": \"the courts of Delaware\", \"new_text\": \"the courts of England and Wales\"}\n```\n"
  },
  "expect": {
    "edits": [
      {"action": "replace", "target_text": "Governing Law. This Agreement shall be governed by the laws of Delaware.", "new_text": "Governing Law. This Agreement shall be governed by the laws of England and Wales."},
      {"action": "replace", "target_text": "the courts of Delaware", "new_text": "the courts of England and Wales"}
    ]
  }
}
```

Create `evals/cases/parse-array-in-one-fence.json`:

```json
{
  "kind": "parse",
  "why": "Local LLMs consolidate multi-location requests into an array inside one fence. Both parsers accept it independently, so agreement is unproven.",
  "input": {
    "prose": "Here are the two changes:\n\n```json\n[{\"action\": \"replace\", \"target_text\": \"thirty (30) days\", \"new_text\": \"sixty (60) days\"}, {\"action\": \"delete\", \"target_text\": \"This Agreement shall renew automatically.\"}]\n```\n"
  },
  "expect": {
    "edits": [
      {"action": "replace", "target_text": "thirty (30) days", "new_text": "sixty (60) days"},
      {"action": "delete", "target_text": "This Agreement shall renew automatically."}
    ]
  }
}
```

Create `evals/cases/parse-tolerant-raw-newline.json`:

```json
{
  "kind": "parse",
  "why": "When the LLM line-wraps a long string value, the raw newline makes json.loads/JSON.parse throw. Recovered by _tolerant_json_loads (py) and tolerantParse (ts) — written twice, so they can drift.",
  "input": {
    "prose": "```json\n{\"action\": \"replace\", \"target_text\": \"Confidential Information means any information\ndisclosed by either party.\", \"new_text\": \"Confidential Information means any non-public information disclosed by either party.\"}\n```\n"
  },
  "expect": {
    "edits": [
      {"action": "replace", "target_text": "Confidential Information means any information\ndisclosed by either party.", "new_text": "Confidential Information means any non-public information disclosed by either party."}
    ]
  }
}
```

Create `evals/baseline.json`:

```json
{}
```

- [ ] **Step 6: Run the runner against the real corpus**

Run: `uv run python -m evals.run_parse`
Expected: `parse-py 3/3`, exit 0.

**If any case fails**, the `expect.edits` was written wrong — the backend parser's behavior on these shapes is already pinned by `tests/test_skills.py` and is not in question. Read the actual output, correct the `expect` block, and re-run. Do **not** put it in the baseline; a case whose expectation was simply mistyped is not a known failure.

- [ ] **Step 7: Prove the runner can fail**

Temporarily change `expect.edits[0].new_text` in `parse-array-in-one-fence.json` to `"WRONG"`.
Run: `uv run python -m evals.run_parse`
Expected: prints `  [FAIL] parse-array-in-one-fence`, prints `parse-py 2/3`, exits **1**.
Revert the edit and re-run to confirm `3/3` and exit 0.

- [ ] **Step 8: Commit**

```bash
git add evals/ tests/test_eval_runner.py
git commit -m "feat(evals): case format, backend runner, score-plus-baseline"
```

---

### Task 2: The Word fake — document model and `body.search`

**Files:**
- Create: `clients/word/src/eval/wordFake.ts`
- Create: `clients/word/src/eval/nodeShim.d.ts`
- Test: `clients/word/src/wordFake.test.ts`
- Modify: `scripts/check.sh` (raise `EXPECTED_PASS_COUNT`)

**Interfaces:**
- Produces:
  - `type FakeParagraph = string | { raw: string; reviewed: string }` — a bare string means raw and reviewed are identical.
  - `createFakeWord(paragraphs: FakeParagraph[]): FakeWord`
  - `interface FakeWord { install(): void; uninstall(): void; rawText(): string; reviewedText(): string; trackingModeLog(): string[] }`
  - `install()` assigns the fake namespace to `globalThis.Word`; `uninstall()` deletes it. `word.ts` gates on `typeof Word !== "undefined"`, so nothing in it runs until `install()` is called.

**Why a `.d.ts` shim:** the TS runner (Task 4) must read a directory of JSON cases, which needs `node:fs`. `tsconfig.json` pins `"types": ["office-js", "vite/client"]` and adding `@types/node` would change typing across every browser and Office.js source for no benefit. Declaring only the two functions used mirrors the existing `declare const process` in `testAssert.ts`. Create it here so Task 4 does not have to.

**The eight semantic rules** — each is a documented `CLAUDE.md` gotcha. Implement all eight in this task; Task 3 adds ranges and mutation.

1. A query over **255 chars throws**. Model Word's real 255, not `word.ts`'s conservative `SEARCH_MAX_LEN = 200`, so the 55-char gap stays visible.
2. **A match never crosses a paragraph break** — a needle containing `\n` or `\r` returns no match, and searching is per-paragraph.
3. **`[](){}<>?*` behave as wildcards even at `matchWildcards: false`** (Word for Mac) — a literal needle containing one returns no match.
4. **`matchWildcards: true` with backslash-escaped metacharacters matches them literally.**
5. `matchCase: false` by default.
6. `matchWholeWord` requires non-word characters at both boundaries.
7. **Search runs against RAW text, including tracked-change deletions** — never the reviewed text.
8. **A literal `\t` in a needle never matches**, even where the document renders one.

- [ ] **Step 1: Write the failing test**

Create `clients/word/src/wordFake.test.ts`. It lives at `src/` (not `src/eval/`) so `scripts/check.sh`'s non-recursive `src/*.test.ts` glob picks it up — the fake's rules are our own code and belong in the normal gate.

```ts
// The Word fake's eight semantic rules, each a documented CLAUDE.md gotcha.
// Run with: npx tsx src/wordFake.test.ts
//
// These are unit tests of OUR fake, not of word.ts. If one fails, either the
// fake is wrong or a gotcha we believed about Word is wrong — both need a human.
import { createFakeWord } from "./eval/wordFake";
import { pass } from "./testAssert";

const DOC = ["1. GOVERNING LAW", "Signed by: [__]", "Title: Chief Executive", "entitled to notice"];

const search = (
  paragraphs: string[],
  query: string,
  opts: { matchCase?: boolean; matchWildcards?: boolean; matchWholeWord?: boolean } = {},
): string[] => {
  const fake = createFakeWord(paragraphs);
  fake.install();
  try {
    return fake.searchSync(query, {
      matchCase: opts.matchCase ?? false,
      matchWildcards: opts.matchWildcards ?? false,
      matchWholeWord: opts.matchWholeWord ?? false,
    });
  } finally {
    fake.uninstall();
  }
};

// --- Rule 1: over 255 chars throws (Word's real limit, not word.ts's 200) ---
let threw = false;
try {
  search(DOC, "x".repeat(256));
} catch {
  threw = true;
}
pass(threw, "rule1: a 256-char query throws");
// The paragraph is exactly the needle: `occurrences` advances by one character,
// so a needle of 255 y's inside a paragraph of 300 y's would match 46 times.
pass(search(["y".repeat(255)], "y".repeat(255)).length === 1, "rule1: 255 chars is allowed");
pass(search(["z".repeat(210)], "z".repeat(210)).length === 1, "rule1: 201-255 is allowed by Word even though word.ts filters at 200");

// --- Rule 2: never crosses a paragraph break ---
pass(search(DOC, "1. GOVERNING LAW\nSigned by:").length === 0, "rule2: a needle with \\n never matches");
pass(search(DOC, "GOVERNING LAW").length === 1, "rule2: within one paragraph matches");

// --- Rule 3: bracket metacharacters miss in literal mode ---
pass(search(DOC, "Signed by: [__]").length === 0, "rule3: literal mode misses a bracketed blank");
pass(search(DOC, "Signed by:").length === 1, "rule3: the clean leading run still matches");

// --- Rule 4: escaped metacharacters match literally in wildcard mode ---
pass(
  search(DOC, "Signed by: \\[__\\]", { matchWildcards: true }).length === 1,
  "rule4: escaped brackets match in wildcard mode",
);

// --- Rule 5: case-insensitive by default ---
pass(search(DOC, "governing law").length === 1, "rule5: matchCase false is insensitive");
pass(search(DOC, "governing law", { matchCase: true }).length === 0, "rule5: matchCase true is sensitive");

// --- Rule 6: whole-word bounds ---
pass(search(DOC, "title", { matchWholeWord: true }).length === 1, "rule6: whole word matches a standalone word");
pass(search(DOC, "title", { matchWholeWord: false }).length === 2, "rule6: substring mode also matches inside 'entitled'");
pass(search(DOC, "entitle", { matchWholeWord: true }).length === 0, "rule6: whole word rejects a prefix of a longer word");

// --- Rule 7: searches RAW text, including tracked deletions ---
const tracked = createFakeWord([{ raw: "Signed by: [__]Suzy Quatro", reviewed: "Signed by: Suzy Quatro" }]);
tracked.install();
pass(
  tracked.searchSync("\\[__\\]", { matchCase: false, matchWildcards: true, matchWholeWord: false }).length === 1,
  "rule7: a tracked deletion is still findable in the raw text",
);
pass(tracked.reviewedText() === "Signed by: Suzy Quatro", "rule7: reviewed text drops the deletion");
pass(tracked.rawText().includes("[__]"), "rule7: raw text keeps the deletion");
tracked.uninstall();

// --- Rule 8: a literal tab never matches ---
pass(
  search(["Signed by: [__]\tSigned by: Boris"], "Signed by: [__]\tSigned by: Boris").length === 0,
  "rule8: a needle containing a tab never matches",
);
pass(search(["Signed by: Ann\tSigned by: Boris"], "Signed by: Boris").length === 1, "rule8: a tab-free segment still matches");
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd clients/word && npx tsx src/wordFake.test.ts`
Expected: FAIL — cannot resolve `./eval/wordFake`.

- [ ] **Step 3: Write the ambient shim**

Create `clients/word/src/eval/nodeShim.d.ts`:

```ts
// Minimal ambient declarations for the two Node APIs the eval runner needs.
//
// tsconfig pins "types": ["office-js", "vite/client"]. Pulling in @types/node
// would change typing across every browser and Office.js source in this package
// for no benefit, so — exactly as testAssert.ts does with `declare const
// process` — we declare only what is used.
declare module "node:fs" {
  export function readdirSync(path: string): string[];
  export function readFileSync(path: string, encoding: "utf8"): string;
}

declare module "node:path" {
  export function join(...parts: string[]): string;
  export function resolve(...parts: string[]): string;
}
```

- [ ] **Step 4: Write the fake's document model and search**

Create `clients/word/src/eval/wordFake.ts`:

```ts
// A fake of the slice of Office.js the matcher touches.
//
// WRITTEN FROM CLAUDE.md's Word gotchas and the Office.js documentation —
// deliberately NOT from word.ts. Writing it from the caller would encode the
// caller's assumptions and the eval would then confirm itself.
//
// FIDELITY LIMIT: this does not implement Word.RequestContext structurally
// (that would be hundreds of members), so it is installed on globalThis and
// cast at the boundary. The compiler therefore does NOT verify shape
// conformance against @types/office-js. What it does verify is that the enum
// values below are read from the real Word.* enums, so enum drift is caught.
//
// BLIND SPOTS, stated rather than discovered later: revision rendering,
// expandTo across tables, list renumbering, content controls, and wildcard
// MATCHING (only escaped-literal wildcard queries are supported — see
// searchSync). A case asserts which characters, never how they look.

export type FakeParagraph = string | { raw: string; reviewed: string };

export type SearchOptions = {
  matchCase: boolean;
  matchWildcards: boolean;
  matchWholeWord: boolean;
};

// Word treats these as wildcards even with matchWildcards:false on Mac.
const WILDCARD_META = /[[\]{}()<>?*]/;
const WORD_CHAR = /[A-Za-z0-9_]/;

type Para = { raw: string; reviewed: string };

const toPara = (p: FakeParagraph): Para =>
  typeof p === "string" ? { raw: p, reviewed: p } : { raw: p.raw, reviewed: p.reviewed };

const isWholeWordAt = (haystack: string, index: number, length: number): boolean => {
  const before = index > 0 ? haystack[index - 1] : "";
  const after = index + length < haystack.length ? haystack[index + length] : "";
  return !WORD_CHAR.test(before) && !WORD_CHAR.test(after);
};

/** Every occurrence of `needle` in `haystack`, honouring case and whole-word. */
const occurrences = (
  haystack: string,
  needle: string,
  matchCase: boolean,
  matchWholeWord: boolean,
): number[] => {
  const hay = matchCase ? haystack : haystack.toLowerCase();
  const nee = matchCase ? needle : needle.toLowerCase();
  const hits: number[] = [];
  let from = 0;
  for (;;) {
    const at = hay.indexOf(nee, from);
    if (at === -1) return hits;
    if (!matchWholeWord || isWholeWordAt(haystack, at, needle.length)) hits.push(at);
    from = at + 1;
  }
};

export function createFakeWord(paragraphs: FakeParagraph[]) {
  const paras: Para[] = paragraphs.map(toPara);

  /**
   * The eight rules. Returns the matching substrings, per paragraph, in
   * document order.
   */
  const searchSync = (query: string, opts: SearchOptions): string[] => {
    // Rule 1 — Word's real limit is 255; word.ts filters at a conservative 200.
    if (query.length > 255) {
      throw new Error("SearchStringInvalidOrTooLong");
    }
    // Rule 2 — a search can never cross a paragraph mark.
    if (/[\n\r]/.test(query)) return [];
    // Rule 8 — a literal tab never matches, even where Word renders one.
    if (query.includes("\t")) return [];

    let needle = query;
    if (opts.matchWildcards) {
      // Rule 4 — only fully-escaped metacharacters are supported. An unescaped
      // metacharacter means real wildcard matching, which is a stated blind
      // spot: return no match rather than pretend.
      const unescaped = needle.replace(/\\(.)/g, "$1");
      const hadUnescapedMeta = WILDCARD_META.test(needle.replace(/\\./g, ""));
      if (hadUnescapedMeta) return [];
      needle = unescaped;
    } else if (WILDCARD_META.test(needle)) {
      // Rule 3 — Word for Mac mis-reads these as wildcards even in literal
      // mode, so the needle silently misses.
      return [];
    }
    if (!needle) return [];

    const found: string[] = [];
    for (const para of paras) {
      // Rule 7 — always the RAW text, tracked deletions included.
      for (const at of occurrences(para.raw, needle, opts.matchCase, opts.matchWholeWord)) {
        found.push(para.raw.slice(at, at + needle.length));
      }
    }
    return found;
  };

  return {
    searchSync,
    rawText: () => paras.map((p) => p.raw).join("\r"),
    reviewedText: () => paras.map((p) => p.reviewed).join("\r"),
    install: () => {
      /* Task 3 installs the globalThis.Word namespace here. */
    },
    uninstall: () => {
      /* Task 3 */
    },
  };
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd clients/word && npx tsx src/wordFake.test.ts`
Expected: 18 `PASS:` lines, exit 0.

- [ ] **Step 6: Prove every rule is load-bearing (mutation)**

For each of the eight rules, break it in `wordFake.ts`, confirm a **named** assertion flips to `FAIL`, then revert. Record the case that flipped:

| Rule | Break it by | An assertion that must fail |
|---|---|---|
| 1 | change `> 255` to `> 100000` | `rule1: a 256-char query throws` |
| 2 | delete the `/[\n\r]/` guard | `rule2: a needle with \n never matches` |
| 3 | delete the `else if (WILDCARD_META…)` branch | `rule3: literal mode misses a bracketed blank` |
| 4 | return `[]` unconditionally when `matchWildcards` | `rule4: escaped brackets match in wildcard mode` |
| 5 | always lowercase both sides | `rule5: matchCase true is sensitive` |
| 6 | ignore `matchWholeWord` in `occurrences` | `rule6: whole word rejects a prefix of a longer word` |
| 7 | search `para.reviewed` instead of `para.raw` | `rule7: a tracked deletion is still findable in the raw text` |
| 8 | delete the `\t` guard | `rule8: a needle containing a tab never matches` |

**A rule whose removal flips nothing is a finding, not a pass.** It means the rule has no case. Add one before continuing.

- [ ] **Step 7: Raise the assertion count and run the full gate**

`clients/word/src/wordFake.test.ts` adds **18** assertions, taking the total from 221 to 239.

**Derive the number, do not trust it.** Run the file and count:

```bash
cd clients/word && npx tsx src/wordFake.test.ts | grep -c '^PASS: '
```

Set `EXPECTED_PASS_COUNT` in `scripts/check.sh` to `221 + that count`. If it is not 239, the test file was transcribed with an assertion missing or added — find out which before moving on.

Run: `bash scripts/check.sh`
Expected: `all checks passed`, with `239/239 PASS`.

- [ ] **Step 8: Commit**

```bash
git add clients/word/src/eval/ clients/word/src/wordFake.test.ts scripts/check.sh
git commit -m "feat(evals): Word fake — document model and body.search semantics"
```

---

### Task 3: The Word fake — ranges, sync, and tracked mutation

**Files:**
- Modify: `clients/word/src/eval/wordFake.ts`
- Modify: `clients/word/src/wordFake.test.ts`
- Modify: `scripts/check.sh` (raise `EXPECTED_PASS_COUNT`)

**Interfaces:**
- Consumes: `createFakeWord` from Task 2.
- Produces, on the object returned by `createFakeWord`:
  - `install()` — sets `globalThis.Word` to a namespace with `run`, `ChangeTrackingMode`, `RangeLocation`, `InsertLocation`.
  - `uninstall()` — deletes it.
  - `trackingModeLog(): string[]` — every value assigned to `changeTrackingMode`, in order. This is how the try/finally restore is asserted without simulating revision rendering.

**Two fidelity behaviors that catch real bugs, both deliberate:**

- **Lazy loading.** `search(...)` returns a collection whose `items` stays `[]` until `context.sync()` runs the queued load, and `range.text` throws `PropertyNotLoaded` until `range.load("text")` has been synced. This is how real Office.js behaves, and it catches a forgotten `sync()` — a mistake this codebase has made before.
- **Tracked replacement.** With `changeTrackingMode === trackAll`, replacing a span leaves the old text in **raw** and removes it from **reviewed**. That is precisely the `5f188799` gotcha, and modelling it is what lets a `match` case prove the matcher still sees struck text.

Queued mutations are applied at `sync()` in **descending start offset**, so a snapshot of ranges taken before any mutation stays valid — mirroring the guarantee `replaceAll` relies on.

- [ ] **Step 1: Write the failing test**

Append to `clients/word/src/wordFake.test.ts`:

```ts
// --- Ranges, lazy loading, and tracked mutation ---
const withFake = async <T>(paras: FakeParagraph[], fn: (f: ReturnType<typeof createFakeWord>) => Promise<T>): Promise<T> => {
  const fake = createFakeWord(paras);
  fake.install();
  try {
    return await fn(fake);
  } finally {
    fake.uninstall();
  }
};

// items is empty until sync
await withFake(["Governing Law applies here"], async () => {
  await Word.run(async (context) => {
    const results = context.document.body.search("Governing Law", {
      matchCase: false, matchWildcards: false, matchWholeWord: false,
    });
    results.load("items");
    pass(results.items.length === 0, "lazy: items empty before sync");
    await context.sync();
    pass(results.items.length === 1, "lazy: items populated after sync");
  });
});

// text throws until loaded
await withFake(["Governing Law applies here"], async () => {
  await Word.run(async (context) => {
    const results = context.document.body.search("Governing Law", {
      matchCase: false, matchWildcards: false, matchWholeWord: false,
    });
    results.load("items");
    await context.sync();
    let threwOnText = false;
    try {
      void results.items[0].text;
    } catch {
      threwOnText = true;
    }
    pass(threwOnText, "lazy: range.text throws before load");
    results.items[0].load("text");
    await context.sync();
    pass(results.items[0].text === "Governing Law", "lazy: range.text readable after load");
  });
});

// expandTo spans head start -> tail end, by match boundary not paragraph
await withFake(["ALPHA middle text OMEGA tail"], async (fake) => {
  await Word.run(async (context) => {
    const body = context.document.body;
    const heads = body.search("ALPHA", { matchCase: false, matchWildcards: false, matchWholeWord: false });
    const tails = body.search("OMEGA", { matchCase: false, matchWildcards: false, matchWholeWord: false });
    heads.load("items");
    tails.load("items");
    await context.sync();
    const span = heads.items[0]
      .getRange(Word.RangeLocation.start)
      .expandTo(tails.items[0].getRange(Word.RangeLocation.end));
    span.load("text");
    await context.sync();
    pass(span.text === "ALPHA middle text OMEGA", "range: expandTo spans head start to tail end");
    pass(fake.rawText() === "ALPHA middle text OMEGA tail", "range: expandTo does not mutate");
  });
});

// tracked replacement: raw keeps the deletion, reviewed does not
await withFake(["Signed by: Boris"], async (fake) => {
  await Word.run(async (context) => {
    const doc = context.document;
    const results = doc.body.search("Boris", { matchCase: false, matchWildcards: false, matchWholeWord: false });
    results.load("items");
    await context.sync();
    doc.changeTrackingMode = Word.ChangeTrackingMode.trackAll;
    results.items[0].insertText("Suzy", Word.InsertLocation.replace);
    await context.sync();
    doc.changeTrackingMode = Word.ChangeTrackingMode.off;
    await context.sync();
  });
  pass(fake.reviewedText() === "Signed by: Suzy", "tracked: reviewed shows the replacement");
  pass(fake.rawText() === "Signed by: BorisSuzy", "tracked: raw keeps the struck original");
  pass(
    fake.trackingModeLog().join(",") === "TrackAll,Off",
    "tracked: every changeTrackingMode assignment is logged in order",
  );
});

// untracked replacement leaves no struck text
await withFake(["Signed by: Boris"], async (fake) => {
  await Word.run(async (context) => {
    const results = context.document.body.search("Boris", { matchCase: false, matchWildcards: false, matchWholeWord: false });
    results.load("items");
    await context.sync();
    results.items[0].insertText("Suzy", Word.InsertLocation.replace);
    await context.sync();
  });
  pass(fake.rawText() === "Signed by: Suzy", "untracked: raw has no struck original");
});

// a snapshot of ranges survives replacing earlier ones
await withFake(["fee here and fee there"], async (fake) => {
  await Word.run(async (context) => {
    const results = context.document.body.search("fee", { matchCase: false, matchWildcards: false, matchWholeWord: false });
    results.load("items");
    await context.sync();
    pass(results.items.length === 2, "snapshot: both matches collected upfront");
    for (const m of results.items) m.insertText("charge", Word.InsertLocation.replace);
    await context.sync();
  });
  pass(fake.rawText() === "charge here and charge there", "snapshot: both replacements land correctly");
});
```

Add `FakeParagraph` to the existing import at the top of the file:

```ts
import { createFakeWord, type FakeParagraph } from "./eval/wordFake";
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd clients/word && npx tsx src/wordFake.test.ts`
Expected: FAIL — `Word.run is not a function` (install is still a no-op).

- [ ] **Step 3: Implement ranges, sync and mutation**

Replace the `return { ... }` block at the end of `createFakeWord` in `clients/word/src/eval/wordFake.ts` with the following, and add the supporting types above it:

```ts
  // Enum values are read from the REAL Word.* enums, so drift is caught by tsc.
  // Their exact strings do not matter to behavior — the fake both writes and
  // reads them — but sourcing them here is the one genuine compiler check.
  const ChangeTrackingMode = {
    off: "Off",
    trackAll: "TrackAll",
    trackMineOnly: "TrackMineOnly",
  } as const;
  const RangeLocation = { start: "Start", end: "End", whole: "Whole" } as const;
  const InsertLocation = { replace: "Replace" } as const;

  type Mutation = { start: number; end: number; text: string; tracked: boolean };

  let trackingMode: string = ChangeTrackingMode.off;
  const modeLog: string[] = [];
  const pendingLoads: Array<() => void> = [];
  const pendingMutations: Mutation[] = [];

  // Offsets are into the RAW document, paragraphs joined by "\r".
  const paraOffsets = (): number[] => {
    const offs: number[] = [];
    let at = 0;
    for (const p of paras) {
      offs.push(at);
      at += p.raw.length + 1; // +1 for the paragraph mark
    }
    return offs;
  };

  const rawTextOf = (): string => paras.map((p) => p.raw).join("\r");
  const rawSlice = (start: number, end: number): string => rawTextOf().slice(start, end);

  type FakeRange = {
    start: number;
    end: number;
    _text?: string;
    load(prop: string): void;
    readonly text: string;
    getRange(loc: string): FakeRange;
    expandTo(other: FakeRange): FakeRange;
    insertText(text: string, loc: string): void;
  };

  const makeRange = (start: number, end: number): FakeRange => {
    const range: FakeRange = {
      start,
      end,
      load(prop: string) {
        if (prop === "text") {
          pendingLoads.push(() => {
            range._text = rawSlice(range.start, range.end);
          });
        }
      },
      get text(): string {
        if (range._text === undefined) {
          throw new Error("PropertyNotLoaded: call load('text') then context.sync()");
        }
        return range._text;
      },
      getRange(loc: string) {
        return loc === RangeLocation.end ? makeRange(range.end, range.end) : makeRange(range.start, range.start);
      },
      expandTo(other: FakeRange) {
        return makeRange(Math.min(range.start, other.start), Math.max(range.end, other.end));
      },
      insertText(text: string, loc: string) {
        if (loc !== InsertLocation.replace) {
          throw new Error(`fake supports only InsertLocation.replace, got ${loc}`);
        }
        pendingMutations.push({
          start: range.start,
          end: range.end,
          text,
          tracked: trackingMode === ChangeTrackingMode.trackAll,
        });
      },
    };
    return range;
  };

  /** Locate the paragraph containing a raw document offset. */
  const paraAt = (offset: number): { index: number; local: number } => {
    const offs = paraOffsets();
    for (let i = offs.length - 1; i >= 0; i--) {
      if (offset >= offs[i]) return { index: i, local: offset - offs[i] };
    }
    return { index: 0, local: offset };
  };

  const applyMutation = (m: Mutation): void => {
    const { index, local } = paraAt(m.start);
    const para = paras[index];
    const length = m.end - m.start;
    const original = para.raw.slice(local, local + length);
    // Tracked: the original stays in RAW (struck) and is dropped from REVIEWED.
    // Untracked: it is gone from both.
    para.raw = para.raw.slice(0, local) + (m.tracked ? original : "") + m.text + para.raw.slice(local + length);
    const reviewedAt = para.reviewed.indexOf(original);
    if (reviewedAt !== -1) {
      para.reviewed =
        para.reviewed.slice(0, reviewedAt) + m.text + para.reviewed.slice(reviewedAt + original.length);
    }
  };

  const sync = async (): Promise<void> => {
    // Descending start offset, so a snapshot of ranges taken before any
    // mutation stays valid — the guarantee replaceAll depends on.
    pendingMutations.sort((a, b) => b.start - a.start);
    for (const m of pendingMutations) applyMutation(m);
    pendingMutations.length = 0;
    for (const load of pendingLoads) load();
    pendingLoads.length = 0;
  };

  const makeCollection = (query: string, opts: SearchOptions) => {
    const collection = {
      items: [] as FakeRange[],
      load(_prop: string) {
        pendingLoads.push(() => {
          collection.items = searchRanges(query, opts);
        });
      },
    };
    return collection;
  };

  /** searchSync, but returning ranges in raw-document coordinates. */
  const searchRanges = (query: string, opts: SearchOptions): FakeRange[] => {
    const hits = searchSync(query, opts);
    if (hits.length === 0) return [];
    const raw = rawTextOf();
    const ranges: FakeRange[] = [];
    let from = 0;
    for (const hit of hits) {
      const at = opts.matchCase
        ? raw.indexOf(hit, from)
        : raw.toLowerCase().indexOf(hit.toLowerCase(), from);
      if (at === -1) continue;
      ranges.push(makeRange(at, at + hit.length));
      from = at + 1;
    }
    return ranges;
  };

  const document = {
    get changeTrackingMode(): string {
      return trackingMode;
    },
    set changeTrackingMode(value: string) {
      trackingMode = value;
      modeLog.push(value);
    },
    load(_prop: string) {
      /* changeTrackingMode is readable immediately in the fake */
    },
    body: {
      search: (query: string, opts: SearchOptions) => makeCollection(query, opts),
    },
  };

  const namespace = {
    run: async <T>(cb: (context: { document: typeof document; sync: () => Promise<void> }) => Promise<T>): Promise<T> =>
      cb({ document, sync }),
    ChangeTrackingMode,
    RangeLocation,
    InsertLocation,
  };

  return {
    searchSync,
    rawText: rawTextOf,
    reviewedText: () => paras.map((p) => p.reviewed).join("\r"),
    trackingModeLog: () => [...modeLog],
    install: () => {
      (globalThis as Record<string, unknown>).Word = namespace;
    },
    uninstall: () => {
      delete (globalThis as Record<string, unknown>).Word;
    },
  };
```

Note: `paras` must become mutable for `applyMutation`. Change its declaration from `const paras: Para[] = ...` to keep the array `const` but the elements mutable — the code above mutates `para.raw` / `para.reviewed` in place, which is already allowed. No change needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd clients/word && npx tsx src/wordFake.test.ts`
Expected: 18 + 12 = 30 `PASS:` lines, exit 0.

If `noUnusedParameters` rejects `_prop`, confirm the leading underscore is present — the compiler exempts underscore-prefixed parameters.

- [ ] **Step 5: Typecheck**

Run: `cd clients/word && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 6: Prove the two fidelity behaviors are load-bearing (mutation)**

| Break it by | An assertion that must fail |
|---|---|
| populate `collection.items` immediately in `load()` instead of queueing | `lazy: items empty before sync` |
| return `""` from the `text` getter instead of throwing | `lazy: range.text throws before load` |
| always drop the original from raw (ignore `m.tracked`) | `tracked: raw keeps the struck original` |
| sort mutations ascending instead of descending | `snapshot: both replacements land correctly` |

Revert each after confirming.

- [ ] **Step 7: Raise the assertion count and run the full gate**

This task adds **12** more assertions, taking the total from 239 to 251. Derive it the same way:

```bash
cd clients/word && npx tsx src/wordFake.test.ts | grep -c '^PASS: '
```

That count is now the whole file's total (30). Set `EXPECTED_PASS_COUNT` to `221 + 30 = 251`.

Run: `bash scripts/check.sh`
Expected: `all checks passed`, `251/251 PASS`.

- [ ] **Step 8: Commit**

```bash
git add clients/word/src/eval/wordFake.ts clients/word/src/wordFake.test.ts scripts/check.sh
git commit -m "feat(evals): Word fake — ranges, lazy loading, tracked mutation"
```

---

### Task 4: TS runner and the seven `match` cases

**Files:**
- Create: `clients/word/src/eval/runner.ts`
- Create: `evals/cases/match-bracketed-blank-wildcard-retry.json`
- Create: `evals/cases/match-progressive-prefix-shortening.json`
- Create: `evals/cases/match-head-tail-span-boundaries.json`
- Create: `evals/cases/match-short-needle-no-expansion.json`
- Create: `evals/cases/match-tracked-deletion-in-raw.json`
- Create: `evals/cases/match-tab-two-column-signature.json`
- Create: `evals/cases/match-single-word-whole-word.json`
- Modify: `clients/word/src/word.ts` (add `export` to `findClauseRange`, line ~304)

**Interfaces:**
- Consumes: `createFakeWord` (Tasks 2-3); `findClauseRange(context, currentText): Promise<Word.Range | null>` from `word.ts`.
- Produces: `clients/word/src/eval/runner.ts` as an executable script — `npx tsx src/eval/runner.ts` prints one line per kind plus failures, and exits non-zero on a regression or an unexpected pass.

**Case shape for `kind: "match"`:**

```json
{
  "kind": "match",
  "why": "...",
  "input": { "paragraphs": [...], "target": "..." },
  "expect": { "matched": "the exact substring" }
}
```

`expect.matched` of `null` asserts the matcher finds nothing — a documented limitation, pinned so it cannot regress silently in either direction.

- [ ] **Step 1: Export `findClauseRange`**

In `clients/word/src/word.ts`, change:

```ts
async function findClauseRange(
```

to:

```ts
export async function findClauseRange(
```

This is the **only** change to shipped application code in this plan. Nothing else in the file moves.

- [ ] **Step 2: Write the seven match cases**

`evals/cases/match-bracketed-blank-wildcard-retry.json`:

```json
{
  "kind": "match",
  "why": "Word for Mac treats [ ] as wildcards even at matchWildcards:false, so a labelled blank silently misses in literal mode. The escaped wildcard retry in searchFirst is the only thing that finds it. CLAUDE.md wildcard-escape gotcha.",
  "input": {
    "paragraphs": ["1. PARTIES", "Signed by: [__]", "Title: Chief Executive Officer"],
    "target": "Signed by: [__]"
  },
  "expect": { "matched": "Signed by: [__]" }
}
```

`evals/cases/match-progressive-prefix-shortening.json`:

```json
{
  "kind": "match",
  "why": "body.search matches the RAW doc while the needle is normalized; when they differ mid-phrase the full quote misses even though the text is present. searchCandidates falls back to progressively shorter word-aligned prefixes. CLAUDE.md body.search-vs-normalized gotcha.",
  "input": {
    "paragraphs": [
      "7. GOVERNING LAW",
      "This Agreement and any dispute arising out of it shall be governed by and construed in accordance with the laws of England and Wales, and the parties submit to the exclusive jurisdiction of the courts of England and Wales."
    ],
    "target": "This Agreement and any dispute arising out of it shall be governed by and construed in accordance with the laws of England and Wales, and the parties irrevocably submit to the exclusive jurisdiction of the courts of England and Wales."
  },
  "expect": { "matched": "This Agreement and any dispute arising out of it shall be governed by and construed in accordance with the laws of England and Wales, and the parties submit to the exclusive jurisdiction of the courts of England and Wales." }
}
```

`evals/cases/match-head-tail-span-boundaries.json`:

```json
{
  "kind": "match",
  "why": "For a long quote the span must run from the HEAD match's start to the TAIL match's end — match boundaries, never whole paragraphs, or a fragment rewrite over-replaces. CLAUDE.md findClauseRange head+tail gotcha.",
  "input": {
    "paragraphs": [
      "Preamble text that must not be absorbed. ALPHA the confidentiality obligations set out in this clause shall survive termination of this Agreement for a period of five years from the effective date and shall bind each receiving party OMEGA trailing text that must not be absorbed either."
    ],
    "target": "ALPHA the confidentiality obligations set out in this clause shall survive termination of this Agreement for a period of five years from the effective date and shall bind each receiving party OMEGA"
  },
  "expect": { "matched": "ALPHA the confidentiality obligations set out in this clause shall survive termination of this Agreement for a period of five years from the effective date and shall bind each receiving party OMEGA" }
}
```

`evals/cases/match-short-needle-no-expansion.json`:

```json
{
  "kind": "match",
  "why": "For needles under 200 chars findClauseRange returns the head match and must NOT expand to a separately-found tail — expansion would absorb everything up to the next occurrence of that tail word, which is often a recurring heading.",
  "input": {
    "paragraphs": ["Termination for convenience", "Either party may terminate on notice.", "Termination for cause"],
    "target": "Termination for convenience"
  },
  "expect": { "matched": "Termination for convenience" }
}
```

`evals/cases/match-tracked-deletion-in-raw.json`:

```json
{
  "kind": "match",
  "why": "body.search matches the RAW document including tracked-change deletions, which is why readBody uses getReviewedText('current') for the LLM but the matcher still sees struck text. Trace 5f188799.",
  "input": {
    "paragraphs": [
      { "raw": "Signed by: [__]Suzy Quatro", "reviewed": "Signed by: Suzy Quatro" }
    ],
    "target": "Signed by: [__]"
  },
  "expect": { "matched": "Signed by: [__]" }
}
```

`evals/cases/match-tab-two-column-signature.json`:

```json
{
  "kind": "match",
  "why": "body.search ignores raw tab characters, so a two-column signature block target spanning the tab cannot match. A documented limitation — pinned so it cannot change silently in either direction. CLAUDE.md table-tab gotcha.",
  "input": {
    "paragraphs": ["Signed by: [__]\tSigned by: Boris Bukengolts"],
    "target": "Signed by: [__]\tSigned by: Boris Bukengolts"
  },
  "expect": { "matched": null }
}
```

`evals/cases/match-single-word-whole-word.json`:

```json
{
  "kind": "match",
  "why": "Single-word clause-name anchors search whole-word-only so 'Title' cannot match inside 'entitled'. shouldMatchWholeWord in word.ts.",
  "input": {
    "paragraphs": ["Each party is entitled to notice.", "Title: Chief Executive Officer"],
    "target": "Title"
  },
  "expect": { "matched": "Title" }
}
```

- [ ] **Step 3: Write the runner (failing — no implementation yet)**

Create `clients/word/src/eval/runner.ts`:

```ts
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

import { findClauseRange } from "../word";
import type { EditProposal } from "../parseEditBlocks";
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

async function runCase(c: Case): Promise<boolean> {
  if (c.kind === "match") return runMatch(c);
  return true; // other kinds land in later tasks
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

// Kinds are added here as later tasks implement them: Task 5 adds "apply",
// Task 6 adds "parse".
const KINDS = ["match"] as const;

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
```

- [ ] **Step 4: Run the runner to verify it fails**

Run: `cd clients/word && npx tsx src/eval/runner.ts`
Expected: FAIL — `findClauseRange` is not exported (if Step 1 was skipped), or one or more `[FAIL]` lines from cases whose expectation does not yet hold.

- [ ] **Step 5: Triage every failure honestly**

For each `[FAIL]`, decide which of two things it is, and never guess:

- **The case's expectation is wrong** (a typo, a paragraph that does not contain what the target quotes). Fix the case JSON. Do not baseline it.
- **The matcher genuinely does not do this.** This is a real finding — the first automated coverage this code has ever had. Add it to `evals/baseline.json` with a one-line reason and, where one exists, the roadmap row. Do **not** fix `word.ts` in this plan; that is a separate branch with its own review.

Print the matched text when debugging by temporarily adding `console.log(range.text)` in `runMatch`.

- [ ] **Step 6: Run to verify it passes**

Run: `cd clients/word && npx tsx src/eval/runner.ts`
Expected: `match 7/7`, or `match N/7   (M known-failing)` with every gap justified in `baseline.json`. Exit 0 either way.

- [ ] **Step 7: Typecheck and run the full gate**

Run: `cd clients/word && npx tsc --noEmit && cd ../.. && bash scripts/check.sh`
Expected: no type errors; `all checks passed`. `EXPECTED_PASS_COUNT` is unchanged — the runner is not a `*.test.ts`.

- [ ] **Step 8: Commit**

```bash
git add clients/word/src/eval/runner.ts clients/word/src/word.ts evals/cases/ evals/baseline.json
git commit -m "feat(evals): TS runner and seven matcher cases against the fake"
```

---

### Task 5: The five `apply` cases

**Files:**
- Modify: `clients/word/src/eval/runner.ts`
- Create: `evals/cases/apply-ambiguous-blank-replace.json`
- Create: `evals/cases/apply-ambiguous-blank-replace-all.json`
- Create: `evals/cases/apply-completeness-guard-refuses-prefix.json`
- Create: `evals/cases/apply-replace-all-snapshot-two-matches.json`
- Create: `evals/cases/apply-simplify-multiline-one-line-differs.json`

**Interfaces:**
- Consumes: `applyEdit(proposal: EditProposal): Promise<Result<void>>` from `word.ts` — already exported, no change needed. `replaceAll`, `acceptRedline`, `deleteClause` and `insertNear` are all reached through it.
- Produces: `runApply(c: Case): Promise<boolean>` inside `runner.ts`.

**Case shape for `kind: "apply"`:**

```json
{
  "kind": "apply",
  "why": "...",
  "input": { "paragraphs": [...], "proposal": { "action": "replace", "target_text": "...", "new_text": "..." } },
  "expect": {
    "ok": false,
    "errorContains": "a distinctive substring of the refusal",
    "reviewed": "the document's reviewed text afterwards",
    "trackingModeLog": ["TrackAll", "Off"]
  }
}
```

`errorContains`, `reviewed` and `trackingModeLog` are each optional and asserted only when present. `reviewed` is what the document looks like with changes accepted — asserting that rather than raw keeps the case readable and does not depend on how the fake represents struck text.

- [ ] **Step 1: Write the five apply cases**

`evals/cases/apply-ambiguous-blank-replace.json`:

```json
{
  "kind": "apply",
  "why": "A label-less blank names no field, and plain replace takes the FIRST match — in an NDA that is the signature block's 'Signed by: [__]', so a company name lands where a person's name belongs. Trace cc81804f.",
  "input": {
    "paragraphs": ["Signed by: [__]", "Title: [__]", "for and on behalf of [__]"],
    "proposal": { "action": "replace", "target_text": "[__]", "new_text": "Blizzard Corp" }
  },
  "expect": {
    "ok": false,
    "errorContains": "usually the wrong field",
    "reviewed": "Signed by: [__]\rTitle: [__]\rfor and on behalf of [__]"
  }
}
```

`evals/cases/apply-ambiguous-blank-replace-all.json`:

```json
{
  "kind": "apply",
  "why": "The same blank, the other action: replace_all would put one value in every field (name, title, entity). Both actions must be refused, with an action-specific message. Trace cc81804f.",
  "input": {
    "paragraphs": ["Signed by: [__]", "Title: [__]", "for and on behalf of [__]"],
    "proposal": { "action": "replace_all", "target_text": "[__]", "new_text": "Blizzard Corp" }
  },
  "expect": {
    "ok": false,
    "errorContains": "every field",
    "reviewed": "Signed by: [__]\rTitle: [__]\rfor and on behalf of [__]"
  }
}
```

`evals/cases/apply-completeness-guard-refuses-prefix.json`:

```json
{
  "kind": "apply",
  "why": "searchCandidates falls back to shorter prefixes. Without the 85% completeness guard, acceptRedline would inject the full long new_text into a short prefix match — a silently-wrong tracked change. CLAUDE.md completeness-threshold gotcha.",
  "input": {
    "paragraphs": [
      "Limitation of Liability. Neither party shall be liable for indirect damages."
    ],
    "proposal": {
      "action": "replace",
      "target_text": "Limitation of Liability. Neither party shall be liable for indirect, incidental, special, consequential or punitive damages arising out of or relating to this Agreement, however caused and under any theory of liability.",
      "new_text": "Limitation of Liability. Neither party shall be liable for indirect damages, and each party's aggregate liability shall not exceed the fees paid in the preceding twelve months."
    }
  },
  "expect": {
    "ok": false,
    "errorContains": "Couldn't find the exact target text",
    "reviewed": "Limitation of Liability. Neither party shall be liable for indirect damages."
  }
}
```

`evals/cases/apply-replace-all-snapshot-two-matches.json`:

```json
{
  "kind": "apply",
  "why": "replaceAll must snapshot every match BEFORE mutating: Track Changes leaves the original visible to body.search, so any re-search inside the same Word.run keeps finding the same spot. Also pins the try/finally restore of changeTrackingMode. CLAUDE.md struck-text re-find and Track-Changes-restore gotchas.",
  "input": {
    "paragraphs": ["The Service Fee is due monthly.", "Any late Service Fee accrues interest."],
    "proposal": { "action": "replace_all", "target_text": "Service Fee", "new_text": "Subscription Fee" }
  },
  "expect": {
    "ok": true,
    "reviewed": "The Subscription Fee is due monthly.\rAny late Subscription Fee accrues interest.",
    "trackingModeLog": ["TrackAll", "Off"]
  }
}
```

`evals/cases/apply-simplify-multiline-one-line-differs.json`:

```json
{
  "kind": "apply",
  "why": "When target_text and new_text are both multi-line and differ on exactly one line, simplifyMultilineReplace collapses to a single-line replace — body.search cannot span paragraph breaks and head+tail expansion would absorb the intervening line. CLAUDE.md multi-line collapse gotcha.",
  "input": {
    "paragraphs": ["Signed by: Boris Bukengolts", "Title: Chief Executive Officer"],
    "proposal": {
      "action": "replace",
      "target_text": "Signed by: Boris Bukengolts\nTitle: Chief Executive Officer",
      "new_text": "Signed by: Suzy Quatro\nTitle: Chief Executive Officer"
    }
  },
  "expect": {
    "ok": true,
    "reviewed": "Signed by: Suzy Quatro\rTitle: Chief Executive Officer"
  }
}
```

- [ ] **Step 2: Extend the runner (failing — apply not handled yet)**

In `clients/word/src/eval/runner.ts`, add the import and the handler, and widen `main`'s filter.

Widen the existing `word.ts` import (the `EditProposal` type import is already there from Task 4):

```ts
import { applyEdit, findClauseRange } from "../word";
```

Add above `runCase`:

```ts
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
```

Change `runCase`:

```ts
async function runCase(c: Case): Promise<boolean> {
  if (c.kind === "match") return runMatch(c);
  if (c.kind === "apply") return runApply(c);
  return true; // parse lands in Task 6
}
```

Register the kind — this is the whole change to `main`, which already loops:

```ts
const KINDS = ["match", "apply"] as const;
```

- [ ] **Step 3: Run the runner to verify apply cases fail**

Run: `cd clients/word && npx tsx src/eval/runner.ts`
Expected: `match 7/7` (or the baselined figure from Task 4) and one or more `[FAIL]` lines under `apply`.

- [ ] **Step 4: Triage every failure honestly**

Same rule as Task 4 Step 5. A wrong expectation gets fixed in the case; a genuine gap in `word.ts` goes in `evals/baseline.json` with a reason and is **not** fixed here.

Two specifically worth thinking about before baselining:

- `apply-replace-all-snapshot-two-matches` expects `trackingModeLog` to be exactly `["TrackAll", "Off"]`. The fake logs every assignment, and `replaceAll` reads `originalMode` first, so the restore writes back the mode the document started in. If the log has three entries, read `replaceAll` before concluding the fake is wrong.
- `apply-simplify-multiline-one-line-differs` depends on `simplifyMultilineReplace` collapsing before `acceptRedline` runs. If it fails with a not-found error, the collapse did not happen — check `applyEdit` calls it on the `replace` path.

- [ ] **Step 5: Run to verify it passes**

Run: `cd clients/word && npx tsx src/eval/runner.ts`
Expected: `apply 5/5`, or `apply N/5   (M known-failing)` with each gap justified. Exit 0.

- [ ] **Step 6: Typecheck and run the full gate**

Run: `cd clients/word && npx tsc --noEmit && cd ../.. && bash scripts/check.sh`
Expected: no type errors; `all checks passed`.

- [ ] **Step 7: Commit**

```bash
git add clients/word/src/eval/runner.ts evals/cases/ evals/baseline.json
git commit -m "feat(evals): five apply cases — blank guards, completeness, snapshot, collapse"
```

---

### Task 6: Parser agreement and the three normalization cases

**Files:**
- Modify: `clients/word/src/eval/runner.ts`
- Create: `evals/cases/parse-multiline-blank-fill.json`
- Create: `evals/cases/parse-filled-block-rewrite.json`
- Create: `evals/cases/parse-tab-bundled-target.json`

**Interfaces:**
- Consumes: `extractEditBlocks(prose: string): { blocks: EditProposal[] }` and `normalizeProposals(blocks: EditProposal[]): EditProposal[]` from `clients/word/src/parseEditBlocks.ts` — both already exported.
- Produces: `runParse(c: Case): Promise<boolean>` inside `runner.ts`, asserting `expect.edits` against the frontend parser's raw output and, when present, `expect.normalized` against the normalized list.

**Why these three carry both fields:** the parser correctly extracts one multi-line `replace`; it is the *normalizer* that must turn it into per-line edits. A case with only a final expectation could not say which of the two stages regressed.

**How agreement is enforced:** both runners assert the same `expect.edits`. `scripts/eval.sh` (Task 7) runs both. If the backend extracts a different list from the frontend, one of the two runs reports `[FAIL]` on that case id. There is no opt-out flag — no legitimate divergence is known, so a divergence is a finding.

- [ ] **Step 1: Confirm `extractEditBlocks`'s return shape**

Run: `cd clients/word && grep -n "export function extractEditBlocks" -A 6 src/parseEditBlocks.ts`
Read the return type and use it exactly in Step 3. Do not assume it returns a bare array.

- [ ] **Step 2: Write the three normalization cases**

`evals/cases/parse-multiline-blank-fill.json`:

```json
{
  "kind": "parse",
  "why": "The LLM collapses a whole signature block into ONE replace across several 'Label: value' lines. body.search cannot cross paragraph breaks, so only the first line matches and the 85% guard rejects it. splitMultilineFieldEdits must split it per line, and a labelled blank becomes replace_all because blanks recur across blocks. Traces 02e41ead, ce45b899.",
  "input": {
    "prose": "I'll fill both fields.\n\n```json\n{\"action\": \"replace\", \"target_text\": \"Signed by: [__]\\nTitle: [__]\", \"new_text\": \"Signed by: Suzy Quatro\\nTitle: Chief Executive Officer\"}\n```\n"
  },
  "expect": {
    "edits": [
      {"action": "replace", "target_text": "Signed by: [__]\nTitle: [__]", "new_text": "Signed by: Suzy Quatro\nTitle: Chief Executive Officer"}
    ],
    "normalized": [
      {"action": "replace_all", "target_text": "Signed by: [__]", "new_text": "Signed by: Suzy Quatro"},
      {"action": "replace_all", "target_text": "Title: [__]", "new_text": "Title: Chief Executive Officer"}
    ]
  }
}
```

`evals/cases/parse-filled-block-rewrite.json`:

```json
{
  "kind": "parse",
  "why": "Same collapse, but the target lines hold real values rather than blanks — so each changed line becomes a plain replace (one occurrence), not replace_all. Trace 32deb028.",
  "input": {
    "prose": "```json\n{\"action\": \"replace\", \"target_text\": \"Signed by: Boris Bukengolts\\nTitle: Chief Executive Officer\", \"new_text\": \"Signed by: Suzy Quatro\\nTitle: Chief Executive Officer\"}\n```\n"
  },
  "expect": {
    "edits": [
      {"action": "replace", "target_text": "Signed by: Boris Bukengolts\nTitle: Chief Executive Officer", "new_text": "Signed by: Suzy Quatro\nTitle: Chief Executive Officer"}
    ],
    "normalized": [
      {"action": "replace", "target_text": "Signed by: Boris Bukengolts", "new_text": "Signed by: Suzy Quatro"}
    ]
  }
}
```

`evals/cases/parse-tab-bundled-target.json`:

```json
{
  "kind": "parse",
  "why": "The LLM prepends a two-column neighbour with a tab. body.search cannot reach across a tab, so the bundled target fails. reduceTabSegment keeps only the segment that changed. Trace 9e5b804c.",
  "input": {
    "prose": "```json\n{\"action\": \"replace\", \"target_text\": \"............\\tSigned by: [__]\", \"new_text\": \"............\\tSigned by: Suzy Quatro\"}\n```\n"
  },
  "expect": {
    "edits": [
      {"action": "replace", "target_text": "............\tSigned by: [__]", "new_text": "............\tSigned by: Suzy Quatro"}
    ],
    "normalized": [
      {"action": "replace_all", "target_text": "Signed by: [__]", "new_text": "Signed by: Suzy Quatro"}
    ]
  }
}
```

- [ ] **Step 3: Extend the runner**

Add the import to `clients/word/src/eval/runner.ts`:

```ts
import { extractEditBlocks, normalizeProposals } from "../parseEditBlocks";
```

Add the handler (adjust the destructuring to whatever Step 1 found):

```ts
/** Compare ignoring key order and undefined-valued optional fields. */
const sameEdits = (a: unknown, b: unknown): boolean => JSON.stringify(a) === JSON.stringify(b);

async function runParse(c: Case): Promise<boolean> {
  const { blocks } = extractEditBlocks(c.input.prose ?? "");
  if (!sameEdits(blocks, c.expect.edits)) return false;
  if (c.expect.normalized !== undefined) {
    if (!sameEdits(normalizeProposals(blocks), c.expect.normalized)) return false;
  }
  return true;
}
```

Add the parse branch to `runCase` (it replaces the `return true;` fallthrough):

```ts
async function runCase(c: Case): Promise<boolean> {
  if (c.kind === "parse") return runParse(c);
  if (c.kind === "match") return runMatch(c);
  if (c.kind === "apply") return runApply(c);
  return false; // an unknown kind is a corpus error, not a pass
}
```

And register the kind:

```ts
const KINDS = ["parse", "match", "apply"] as const;
```

- [ ] **Step 4: Run the runner to verify the parse cases fail**

Run: `cd clients/word && npx tsx src/eval/runner.ts`
Expected: one or more `[FAIL]` lines under `parse`.

`sameEdits` compares serialized JSON, so a field the parser sets to `undefined` or an extra optional key (`rationale`, `warning`) will fail the comparison. Print both sides while triaging:

```ts
console.log(JSON.stringify(blocks), "\n", JSON.stringify(c.expect.edits));
```

Fix the **case's** `expect` to match what the parser actually produces — the parser's behavior on these shapes is already pinned by `parseEditBlocks.test.ts` and is not in question here.

- [ ] **Step 5: Run to verify it passes**

Run: `cd clients/word && npx tsx src/eval/runner.ts`
Expected: `parse 6/6`, `match 7/7`, `apply 5/5` — or the baselined figures. Exit 0.

- [ ] **Step 6: Verify the backend runner still passes all six**

Run: `uv run python -m evals.run_parse`
Expected: `parse-py 6/6`.

**If the three new cases fail here**, that is the agreement check firing — the two parsers disagree on `expect.edits`. That is a genuine finding and the first time it has ever been visible. Record it in `evals/baseline.json` with both observed outputs in the reason, and raise it. Do **not** relax the case to make it pass.

- [ ] **Step 7: Prove the agreement check works (mutation)**

Temporarily edit `parse-multiline-blank-fill.json` so `expect.edits[0].new_text` differs from what both parsers produce.
Run both runners. Expected: **both** report `[FAIL] parse-multiline-blank-fill`.
Revert.

Then temporarily change only `expect.normalized`. Expected: the TS runner fails, the Python runner passes — proving `normalized` is genuinely TS-only rather than silently ignored everywhere.

- [ ] **Step 8: Typecheck and run the full gate**

Run: `cd clients/word && npx tsc --noEmit && cd ../.. && bash scripts/check.sh`
Expected: no type errors; `all checks passed`.

- [ ] **Step 9: Commit**

```bash
git add clients/word/src/eval/runner.ts evals/cases/ evals/baseline.json
git commit -m "feat(evals): parser-agreement cases and the TS-only normalization stage"
```

---

### Task 7: `scripts/eval.sh`, the push gate, and docs

**Files:**
- Create: `scripts/eval.sh`
- Modify: `scripts/check.sh`
- Modify: `docs/wiki.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: `evals/run_parse.py` (Task 1) and `clients/word/src/eval/runner.ts` (Tasks 4-6).
- Produces: `bash scripts/eval.sh` — runs both, prints the score block, exits non-zero if either regresses.

- [ ] **Step 1: Write `scripts/eval.sh`**

```bash
#!/usr/bin/env bash
# Deterministic eval harness (Tier 1). No LLM calls; safe to run anywhere.
#
# The gate is "the score must not decrease", never "all cases pass". A case
# promoted from a tester's complaint is currently-failing by construction, so a
# binary gate would mean every promotion breaks the push gate until someone
# fixes the bug — and nobody would ever promote one. Known failures live in
# evals/baseline.json with a reason, and are counted on every run.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> eval: backend parser"
uv run python -m evals.run_parse

echo "==> eval: client parser, matcher, apply"
(cd clients/word && npx tsx src/eval/runner.ts)

echo "==> eval: no regressions"
```

Then: `chmod +x scripts/eval.sh`

- [ ] **Step 2: Run it**

Run: `bash scripts/eval.sh`
Expected: both score blocks, `no regressions`, exit 0.

- [ ] **Step 3: Prove the gate fails on a regression**

Temporarily edit `evals/cases/match-single-word-whole-word.json` and set `expect.matched` to `"NOPE"`.
Run: `bash scripts/eval.sh`
Expected: `[FAIL] match-single-word-whole-word`, and a **non-zero exit** (`echo $?` → 1). `set -e` means the script stops before printing `no regressions`.
Revert and re-run to confirm exit 0.

- [ ] **Step 4: Prove the baseline is honoured in both directions**

Add `{"match-single-word-whole-word": "temporary probe"}` to `evals/baseline.json`.
Run: `bash scripts/eval.sh`
Expected: `[now-passing] match-single-word-whole-word …` and a **non-zero exit** — a baselined case that passes needs a human as loudly as a regression.
Revert `baseline.json` to its real content and re-run to confirm exit 0.

- [ ] **Step 5: Wire it into the push gate**

In `scripts/check.sh`, insert immediately before the final `echo "==> all checks passed"`:

```bash
echo "==> eval harness"
bash scripts/eval.sh
```

- [ ] **Step 6: Run the full gate**

Run: `bash scripts/check.sh`
Expected: backend tests, typecheck, the add-in assertions at their current count, both eval blocks, then `all checks passed`.

- [ ] **Step 7: Update `docs/wiki.md`**

Add a row to **Shipped Since Last Update** for `feat/eval-harness`, covering: what it is (Tier 1, deterministic, zero LLM); the two gaps it closes (the matcher had **no** automated coverage — `word.test.ts` was 57 lines of pure helpers and said so in its own header; and nothing anywhere checked the two edit parsers agree on the same string); the score-plus-baseline contract and why binary gating would have killed the promoter loop before it started; the fake's eight rules and the fact that `edit_failed` rows falsify it; the exact case counts and any baseline entries with their reasons; and the one-line change to shipped code (`export findClauseRange`).

Update the test counts in the wiki header: backend `493` → `493 + <new tests in tests/test_eval_runner.py>`, frontend `221` → the final `EXPECTED_PASS_COUNT`.

Add follow-up rows for anything the run turned up: every `baseline.json` entry that is a genuine `word.ts` gap needs its own roadmap row, since this plan deliberately did not fix them.

Refresh the three follow-up rows the spec names as next: the feedback→fixture promoter, Tier 2 (LLM-scored, including the relaxing-preference safety gate), and calibrating the fake against `edit_failed` rows.

- [ ] **Step 8: Update `CLAUDE.md`**

`CLAUDE.md` is at its **150-line cap** — it was 144 lines after the last change. Adding without removing is not an option; consolidate or drop the lowest-value line rather than appending.

Add one bullet to the **Backend** or a new **Evals** heading, covering only what a future session cannot re-derive:

- `bash scripts/eval.sh` is the deterministic harness; `evals/cases/*.json` is data, `evals/baseline.json` holds known failures with reasons.
- The gate is **score-must-not-decrease**, not all-pass — a case promoted from a tester complaint is failing by construction.
- The Word fake (`clients/word/src/eval/wordFake.ts`) encodes eight documented `body.search` gotchas. **It must never be written from `word.ts`** — that would make the eval confirm itself. `edit_failed` rows from production are what falsify it.
- `parse` cases run through **both** parsers; a divergence is a finding, not a config.

Run: `wc -l CLAUDE.md` — must be ≤ 150.

- [ ] **Step 9: Final gate and commit**

```bash
bash scripts/check.sh
git add scripts/eval.sh scripts/check.sh docs/wiki.md CLAUDE.md
git commit -m "feat(evals): eval.sh, push-gate wiring, and docs"
```

- [ ] **Step 10: Report what the corpus found**

Before finishing the branch, write a short summary for the user covering: the final score per kind, every `baseline.json` entry and whether it is a genuine `word.ts` gap or a limitation being pinned, and — most importantly — **whether the parser-agreement check found a real divergence**, since that bug class has never been visible before.

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| Slice 1 — case format | 1 (shape, `expect.edits`), 4 (`match`), 5 (`apply`), 6 (`expect.normalized`) |
| Slice 2 — Python runner | 1 |
| Slice 3 — the Word fake, 8 rules | 2 (all eight, each mutation-proven) |
| Slice 4 — TS runner | 4 (match), 5 (apply), 6 (parse) |
| Slice 5 — scoring, baseline, gating | 1 (`score`), 7 (`eval.sh`, `check.sh`) |
| Seed corpus — 6 parse / 7 match / 5 apply | 1 (3 parse), 4 (7 match), 5 (5 apply), 6 (3 parse) |
| On the fake's fidelity | 2 (header comment, blind spots), 7 (CLAUDE.md) |
| Testing the harness itself | 1 (Step 7), 2 (Step 6), 3 (Step 6), 6 (Step 7), 7 (Steps 3-4) |
| Config — none | no task; nothing to do |
| Follow-ups | 7 (Step 7) |

**Deviations from the spec, both deliberate and both narrowing:** one export instead of two (candidate order is unasserted, so the helpers need not be exported); eval sources in `src/eval/` instead of flat. Both are stated in Global Constraints. The spec's "type-checked for free" claim is corrected there too.

**Type consistency, checked:** `createFakeWord` / `FakeParagraph` / `searchSync` /
`install` / `uninstall` / `rawText` / `reviewedText` / `trackingModeLog` are spelled
identically in Tasks 2-6; `load_cases` / `load_baseline` / `score` / `run_case` in Tasks 1
and 7. Three fixes came out of this pass: `rawTextOf` / `rawSlice` now precede
`makeRange` rather than trailing it, and the runner's `Case` type is declared with real
field types instead of `Record<string, unknown>` — under `strict`, comparing an `unknown`
against a boolean or string is an error, so every handler would otherwise have needed a
cast (and a cast is exactly how a wrong shape reaches production silently).

**`changeTrackingMode` restore:** the spec listed it as its own apply case. It is folded into `apply-replace-all-snapshot-two-matches` via `expect.trackingModeLog`, keeping the apply count at five as the spec's score example assumed.
