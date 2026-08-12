# Repo Cleanup + `legal_research` Split — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove accumulated drag from the repo (dead code, stale docs, an unfailable frontend test harness) and split the 952-line `skills/legal_research.py` into a five-module package — with zero change to product behavior.

**Architecture:** Two tiers on one branch. Tier 1 (Tasks 1–4) touches no production code path. Tier 2 (Tasks 5–10) converts `skills/legal_research.py` into the package `skills/legal_research/`, then lifts symbols out one module per commit, cutting along the pure/impure line so three of the five modules import nothing from this project. Every move is verbatim; the suite must be green after every task.

**Tech Stack:** Python 3.12 + uv, pytest (401 tests, testcontainers Postgres — Docker required), TypeScript 5.6 + tsx (165 hand-rolled asserts), Office.js Word add-in.

**Spec:** `docs/superpowers/specs/2026-08-12-cleanup-and-legal-research-split-design.md`

## Global Constraints

- **Branch is already cut:** `chore/cleanup-and-legal-research-split`. Never work on `main`.
- **All imports at top of file.** No lazy imports inside functions (CLAUDE.md rule 1).
- **No backwards-compat shims.** Change call sites instead (CLAUDE.md rule 5). Specifically: do **not** re-export private helpers from `__init__.py` to keep old patch strings working.
- **No prompt text may change.** Not one byte. Task 10 proves this by hash.
- **No behavior change inside moved functions** — including obvious tidy-ups. Move verbatim.
- **`CLAUDE.md` is at exactly 150 lines, its own stated cap.** Any line added must displace one.
- **Docker must be running** for the backend suite (`tests/conftest.py` spins an ephemeral Postgres).
- **`uvicorn` does not auto-reload.** Backend changes need `bash scripts/start.sh` restarted before any manual check.
- Baseline to preserve: **401 backend tests pass, 165 frontend asserts pass, `tsc --noEmit` clean.**

## Verified Facts This Plan Depends On

These were measured, not assumed. If any turns out false, stop and re-plan.

| Fact | Value |
|---|---|
| Backend tests | 401 pass, ~9s |
| Frontend asserts | 165 pass across 7 files, **all exit 0 regardless of outcome** |
| `skills/schemas.py` importers | zero |
| Production importers of `legal_research` | exactly one: `graph/graph.py:25` |
| `patch("skills.legal_research.X")` strings | **19** — `_build_agent`×10, `_build_llm`×7, `_build_json_llm`×2 (test_skills.py ×14, test_graph.py ×5) |
| `monkeypatch.setattr(<module>, …)` sites | **56** (test_skills.py ×45, test_legal_research_conversation.py ×5, test_stale_recall_reconciliation.py ×3, test_observability.py ×2, test_preferences_chat_injection.py ×1) |
| Module-level mutable state | `_agent_cache` (:108), `_llm_cache` (:109) → both belong with the LLM builders in the entry module |
| `@types/node` | **not installed**; `tsconfig.json` pins `"types": ["office-js", "vite/client"]` |
| `setTimeout`/`setInterval` in `src/` | none — so no Node/DOM timer type collision either way |
| `docs/archive/` | does not exist yet |
| External inbound links to the docs being archived | exactly one: `docs/superpowers/specs/2026-06-29-chat-memory-grounding-design.md:5` |

### The safety property that makes Tier 2 mechanically safe

Both `unittest.mock.patch` and `pytest`'s `monkeypatch.setattr` **raise `AttributeError` when the target name does not exist on the target object.** So every stale patch target left behind by a move fails *loudly* — there is no silent degradation.

**One exception, and it is the single real hazard in this plan:** `get_settings` will be imported by **both** `context.py` and the entry module. A test that patches it on the wrong one still finds the attribute, so the patch succeeds and silently does nothing. This affects the 5 sites in `tests/test_legal_research_conversation.py`. Task 9 verifies them by mutation rather than trusting a green suite.

---

## File Structure

**Created:**
- `clients/word/src/testAssert.ts` — shared `pass()` that sets a non-zero exit code
- `scripts/check.sh` — the pre-push gate (backend + typecheck + frontend asserts)
- `docs/archive/` — home for the five shipped step docs
- `skills/legal_research/__init__.py` — single re-export, matching the house pattern
- `skills/legal_research/prompts.py` — four model-facing constants, no imports
- `skills/legal_research/edit_parsing.py` — LLM prose → structured edits/preferences
- `skills/legal_research/review_recall.py` — stored review → safe injectable block
- `skills/legal_research/context.py` — memory loads, grounding gate, budget cap

**Deleted:**
- `skills/schemas.py`

**Moved:**
- `skills/legal_research.py` → `skills/legal_research/legal_research.py`
- `docs/00_PLAN.md`, `docs/01_reliability_floor.md`, `docs/02_persist_findings.md`, `docs/03_msa_playbook_on_chat.md`, `docs/04_context_cap.md` → `docs/archive/`

**Modified:** the 7 `clients/word/src/*.test.ts` files, `CLAUDE.md`, `docs/wiki.md`, `docs/superpowers/specs/2026-06-29-chat-memory-grounding-design.md`, and the 8 backend test files listed per task.

---

## Task 1: Frontend asserts that can actually fail

**Files:**
- Create: `clients/word/src/testAssert.ts`
- Modify: all 7 of `clients/word/src/{attorneyIdentity,findingFilters,normalize,parseEditBlocks,parsePreferenceBlocks,parser,word}.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: `pass(cond: boolean, label: string): void` — exported from `clients/word/src/testAssert.ts`. Prints `PASS: <label>` or `FAIL: <label>`, and on failure sets `process.exitCode = 1` **without** terminating, so every remaining assertion still runs and reports. Task 2 depends on this exit code.

**Why `declare const process`:** `@types/node` is not installed and `tsconfig.json` pins an explicit `"types"` allowlist, so a bare `process.exitCode` fails `tsc --noEmit` with "Cannot find name 'process'". Adding `@types/node` would pull Node globals across the whole browser/Office.js `src` typecheck surface. A module-scoped `declare` is the minimal fix. **This was verified empirically: `tsc --noEmit` clean, runtime exit code 1, both assertions still printed.**

- [ ] **Step 1: Create the shared helper**

Create `clients/word/src/testAssert.ts`:

```ts
// Shared assertion helper for the hand-rolled *.test.ts scripts.
// Run a test file with: npx tsx src/<name>.test.ts
//
// Sets a non-zero exit code on failure so scripts/check.sh can actually fail
// the run. Deliberately does NOT throw or exit immediately — every assertion
// in a file still runs and reports, so one failure doesn't hide the rest.
//
// `declare const process` rather than @types/node: tsconfig pins
// "types": ["office-js", "vite/client"], and pulling in Node globals would
// change typing across the browser/Office.js sources for no benefit here.
declare const process: { exitCode?: number };

export const pass = (cond: boolean, label: string): void => {
  if (cond) {
    console.log(`PASS: ${label}`);
  } else {
    process.exitCode = 1;
    console.log(`FAIL: ${label}`);
  }
};
```

- [ ] **Step 2: Prove it fails before migrating anything**

Create a throwaway `clients/word/src/__probe.test.ts`:

```ts
import { pass } from "./testAssert";
pass(true, "this passes");
pass(false, "this fails");
```

Run:
```bash
cd clients/word && npx tsx src/__probe.test.ts; echo "exit=$?"
```
Expected: prints `PASS: this passes` then `FAIL: this fails`, and `exit=1`.

Then confirm the old behavior for contrast — the existing files still exit 0:
```bash
cd clients/word && npx tsx src/normalize.test.ts; echo "exit=$?"
```
Expected: `exit=0` (this is the bug being fixed).

- [ ] **Step 3: Delete the probe**

```bash
rm clients/word/src/__probe.test.ts
```

- [ ] **Step 4: Migrate all 7 test files**

In each file, delete its local `pass` definition and import the shared one instead. The signature is unchanged, so **no call site changes** — only the import line and the deleted definition.

Six files (`attorneyIdentity`, `findingFilters`, `normalize`, `parseEditBlocks`, `parsePreferenceBlocks`, `word`) have this two-line form to delete:

```ts
const pass = (cond: boolean, label: string) =>
  console.log(cond ? `PASS: ${label}` : `FAIL: ${label}`);
```

`parser.test.ts` has it as a **one-liner at line 57**, not at the top:

```ts
const pass = (cond: boolean, label: string) => console.log(cond ? `PASS: ${label}` : `FAIL: ${label}`);
```

In each file add, alongside the existing imports:

```ts
import { pass } from "./testAssert";
```

- [ ] **Step 5: Verify all 7 still pass and typecheck**

```bash
cd clients/word
npx tsc --noEmit
for f in src/*.test.ts; do out=$(npx tsx "$f"); code=$?; \
  printf "%-34s exit=%s PASS=%s FAIL=%s\n" "$f" "$code" \
  "$(echo "$out" | grep -c '^PASS')" "$(echo "$out" | grep -c '^FAIL')"; done
```
Expected: `tsc` silent; all 7 files `exit=0`, `FAIL=0`, and the PASS counts unchanged at
`attorneyIdentity=7, findingFilters=9, normalize=4, parseEditBlocks=64, parsePreferenceBlocks=4, parser=57, word=20` (**165 total**).

- [ ] **Step 6: Prove a real regression now fails**

Temporarily corrupt one assertion — in `clients/word/src/normalize.test.ts` change
`"whitespace collapsed"`'s expected value from `"multi line whitespace"` to `"WRONG"`:

```bash
cd clients/word && npx tsx src/normalize.test.ts; echo "exit=$?"
```
Expected: one `FAIL:` line and `exit=1`.

Revert the corruption:
```bash
git checkout clients/word/src/normalize.test.ts
```
Then re-add the import line that the checkout just reverted, and re-run Step 5 to confirm 4 PASS / exit=0.

- [ ] **Step 7: Commit**

```bash
git add clients/word/src/testAssert.ts clients/word/src/*.test.ts
git commit -m "test(word): shared assert helper that sets a non-zero exit code

The 7 hand-rolled *.test.ts scripts each defined a local pass() that only
console.log'd PASS/FAIL — every file exited 0 regardless of outcome, so a
regression could go red silently and still pass any gate.

Extracts one shared pass() that sets process.exitCode = 1 on failure while
still running every remaining assertion. Uses a module-scoped
'declare const process' rather than adding @types/node, because tsconfig
pins types: [office-js, vite/client] and Node globals would change typing
across the browser sources.

Assertion count unchanged (165). Verified a corrupted assert now exits 1."
```

---

## Task 2: `scripts/check.sh` pre-push gate

**Files:**
- Create: `scripts/check.sh`
- Modify: `CLAUDE.md` (Common commands section — must stay ≤150 lines)

**Interfaces:**
- Consumes: the non-zero exit code from Task 1's `pass()`.
- Produces: `bash scripts/check.sh` → exit 0 only when backend tests, typecheck, and all frontend asserts pass. Every later task uses this as its verification command.

**Why this exists:** the project runs no CI by choice, and the VM pulls from `ado` directly — nothing sits between a push and production. A local gate is the only enforceable one.

- [ ] **Step 1: Create the script**

Create `scripts/check.sh`:

```bash
#!/usr/bin/env bash
# Pre-push gate: everything that can be checked without a live LLM.
#
# There is no CI on this project — the VM pulls from `ado` directly, so this
# is the only gate between a change and production. Run it before every push.
#
# Requires Docker (tests/conftest.py spins an ephemeral Postgres).
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> backend tests"
uv run pytest tests/ -q

echo "==> word add-in typecheck"
cd clients/word
npx tsc --noEmit

echo "==> word add-in assertions"
for f in src/*.test.ts; do
  echo "--- $f"
  npx tsx "$f"
done

echo "==> all checks passed"
```

- [ ] **Step 2: Make it executable and run it**

```bash
chmod +x scripts/check.sh
bash scripts/check.sh
```
Expected: `401 passed`, silent `tsc`, 7 assert files each printing PASS lines, then `==> all checks passed`, exit 0.

- [ ] **Step 3: Prove the gate actually gates**

Corrupt the same `normalize.test.ts` assertion as in Task 1 Step 6, then:

```bash
bash scripts/check.sh; echo "exit=$?"
```
Expected: a `FAIL:` line, and a **non-zero exit** (the `for` loop's `npx tsx` returns 1 under `set -e`).

Revert:
```bash
git checkout clients/word/src/normalize.test.ts
```
Re-run `bash scripts/check.sh` and confirm it passes again.

- [ ] **Step 4: Document it in CLAUDE.md within the line cap**

`CLAUDE.md` is at **exactly 150 lines**, its own stated cap. Add the gate to the `## Common commands` block:

```bash
# Pre-push gate (backend tests + typecheck + add-in assertions)
bash scripts/check.sh
```

Then bring the file back to ≤150 lines by consolidating — do not simply append. Verify:

```bash
wc -l CLAUDE.md
```
Expected: ≤ 150.

- [ ] **Step 5: Commit**

```bash
git add scripts/check.sh CLAUDE.md
git commit -m "chore: add scripts/check.sh as the pre-push gate

No CI on this project by choice, and the VM pulls from ado directly — so
nothing sits between a push and production. This is the one enforceable
gate: backend tests (401), tsc --noEmit, and all 7 frontend assert files,
each of which can now actually fail (see previous commit).

Documented in CLAUDE.md's Common commands, kept within the file's 150-line cap."
```

---

## Task 3: Delete dead `skills/schemas.py`

**Files:**
- Delete: `skills/schemas.py`

**Interfaces:**
- Consumes: nothing. Produces: nothing. This module has zero importers.

- [ ] **Step 1: Re-verify it is unreferenced**

```bash
grep -rn "skills.schemas\|from skills import schemas\|GeneratedContract" \
  --include='*.py' . | grep -v '\.venv'
```
Expected: only the definition inside `skills/schemas.py` itself. **If anything else appears, stop — the premise is wrong.**

- [ ] **Step 2: Delete it**

```bash
git rm skills/schemas.py
```

- [ ] **Step 3: Verify the suite is unaffected**

```bash
bash scripts/check.sh
```
Expected: `401 passed`, all checks pass.

- [ ] **Step 4: Commit**

```bash
git commit -m "chore: delete unused skills/schemas.py

86 lines of Pydantic output models with zero importers, verified by
repo-wide grep. Written for a structured-JSON skill output that was never
wired up; the project went markdown-only. The deferred structured-JSON
review item will need a fresh schema shaped to the Word parser anyway,
so this is not the seed of that work."
```

---

## Task 4: Documentation truth-up

**Files:**
- Create: `docs/archive/`
- Move: `docs/00_PLAN.md`, `docs/01_reliability_floor.md`, `docs/02_persist_findings.md`, `docs/03_msa_playbook_on_chat.md`, `docs/04_context_cap.md` → `docs/archive/`
- Modify: `docs/superpowers/specs/2026-06-29-chat-memory-grounding-design.md:5`, `docs/wiki.md`

**Interfaces:** documentation only; nothing consumes or produces code.

- [ ] **Step 1: Move the five shipped step docs**

```bash
mkdir -p docs/archive
git mv docs/00_PLAN.md docs/01_reliability_floor.md docs/02_persist_findings.md \
       docs/03_msa_playbook_on_chat.md docs/04_context_cap.md docs/archive/
```

`00_PLAN.md`'s own pointers to `01`–`04` are bare relative filenames and stay valid because all five move together.

- [ ] **Step 2: Mark each as shipped**

Insert as the **second line** of each of the five files (directly under its `#` heading):

```markdown
> **SHIPPED** — archived 2026-08-12. Kept for the decision rationale; see the wiki's "Shipped Since Last Update" table for what actually landed.
```

- [ ] **Step 3: Repoint the one external inbound link**

In `docs/superpowers/specs/2026-06-29-chat-memory-grounding-design.md`, line 5 reads:

```markdown
> **Source ideas:** `docs/00_PLAN.md` + `docs/01_reliability_floor.md` … `docs/04_context_cap.md`,
```

Change the two paths to `docs/archive/00_PLAN.md` and `docs/archive/04_context_cap.md`.

- [ ] **Step 4: Verify no broken references remain**

```bash
grep -rn "docs/00_PLAN\|docs/01_reliability_floor\|docs/02_persist_findings\|docs/03_msa_playbook_on_chat\|docs/04_context_cap" \
  --include='*.md' . | grep -v node_modules | grep -v 'docs/archive/'
```
Expected: no output.

- [ ] **Step 5: Correct the wiki header**

In `docs/wiki.md` line 3, the header currently claims `Last updated: 2026-07-23 | 380 tests + 161 frontend asserts passing`. Change to `Last updated: 2026-08-12 | 401 tests + 165 frontend asserts passing`, and extend the trailing feature list with `+ OpenTelemetry tracing migration + repo cleanup & legal_research split`.

- [ ] **Step 6: Correct the architecture diagram**

In `docs/wiki.md`, the ASCII diagram's last branch reads:

```
                                                    └── Tracing → Langfuse
```

Change to:

```
                                                    └── Tracing → OTel (Langfuse local / Phoenix VM)
```

- [ ] **Step 7: Prune the roadmap table — mechanical only**

In the `## Follow-ups / Roadmap` table:

1. Delete every row whose Feature cell is struck through (`~~…~~`) — there are ~10, and each is already recorded verbatim in the Shipped table above it.
2. Merge the three overlapping harness rows — **Attorney preference memory → self-improving agent harness (NORTH STAR)**, **System-wide self-improving harness**, and **Deterministic placeholder / signature-block safety-net — Layer 2** — into the single NORTH STAR row, with the other two becoming sub-bullets in its Notes cell.

**Do not re-order, re-prioritise, or re-word any surviving row.** Sequencing is Phase A's deliverable, not this task's.

- [ ] **Step 8: Verify and commit**

```bash
bash scripts/check.sh
git add -A docs/ && git commit -m "docs: archive shipped step plans, correct stale wiki claims

- Move 00_PLAN + 01-04 to docs/archive/ with a SHIPPED banner; they read as
  forward-looking plans for work that landed months ago, but hold decision
  rationale the wiki's Shipped rows compress away. Repoints the one external
  inbound link (2026-06-29-chat-memory-grounding-design.md).
- Wiki header: 380 -> 401 tests, 161 -> 165 asserts, date 2026-07-23 -> 2026-08-12.
- Architecture diagram: Tracing -> Langfuse became Tracing -> OTel
  (Langfuse local / Phoenix VM) after the OTel migration.
- Roadmap: drop ~10 struck-through DONE rows already duplicated in Shipped;
  merge the three overlapping self-improving-harness rows into one.

Mechanical only — no row re-sequenced or re-prioritised. That is Phase A."
```

---

## Task 5: Convert to a package (no content change)

**Files:**
- Move: `skills/legal_research.py` → `skills/legal_research/legal_research.py`
- Create: `skills/legal_research/__init__.py`
- Modify: `tests/test_skills.py`, `tests/test_graph.py`, `tests/test_stale_recall_reconciliation.py`, `tests/test_legal_research_conversation.py`, `tests/test_preferences_chat_injection.py`, `tests/test_observability.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: the package `skills.legal_research`, whose `__init__.py` exports exactly one name — `legal_research(state: LegalAgentState) -> LegalAgentState`. The submodule `skills.legal_research.legal_research` holds every symbol the old flat file held, unchanged. Tasks 6–9 lift symbols out of that submodule.

**This task changes zero lines inside the module.** It is purely the package conversion plus the test-side retargeting that conversion forces. Keeping it separate is what lets a reviewer of Tasks 6–9 see moves rather than moves-plus-plumbing.

- [ ] **Step 1: Move the file into a package**

```bash
mkdir -p skills/legal_research_pkg
git mv skills/legal_research.py skills/legal_research_pkg/legal_research.py
git mv skills/legal_research_pkg skills/legal_research
```

(The two-step dance avoids a name collision between the file and the directory.)

- [ ] **Step 2: Add the re-export, matching the house pattern**

Create `skills/legal_research/__init__.py` — identical in shape to `skills/contract_review/__init__.py`:

```python
from skills.legal_research.legal_research import legal_research

__all__ = ["legal_research"]
```

- [ ] **Step 3: Run the suite to see exactly what breaks**

```bash
uv run pytest tests/ -q 2>&1 | tail -30
```
Expected: **failures**, and specifically `AttributeError` from patch targets — `skills.legal_research` is now a package that exposes only `legal_research`, so `patch("skills.legal_research._build_agent")` no longer resolves. This is the loud-failure property working as intended. `graph/graph.py:25` should **not** be among the failures.

- [ ] **Step 4: Retarget the 19 `patch(...)` strings**

All three patched symbols (`_build_agent`, `_build_llm`, `_build_json_llm`) live in the entry submodule. Rewrite every occurrence:

```bash
cd /Users/dmytroivanov/projects/legal-plugin
sed -i '' 's/patch("skills\.legal_research\./patch("skills.legal_research.legal_research./g' \
  tests/test_skills.py tests/test_graph.py
grep -c 'patch("skills\.legal_research\.legal_research\.' tests/test_skills.py tests/test_graph.py
```
Expected: `tests/test_skills.py:14` and `tests/test_graph.py:5` — **19 total**.

The resulting `skills.legal_research.legal_research._build_agent` stutter is intentional; it matches `skills.contract_review.contract_review`, which the codebase already reads this way.

- [ ] **Step 5: Retarget the module-object imports**

The 56 `monkeypatch.setattr(<module>, …)` sites all reach the module through an alias. Changing the **import line** fixes every site beneath it, because the submodule holds all the same names.

| File | Line | From | To |
|---|---|---|---|
| `tests/test_skills.py` | 14 occurrences of `import skills.legal_research as lr` (inside test functions) | `import skills.legal_research as lr` | `import skills.legal_research.legal_research as lr` |
| `tests/test_stale_recall_reconciliation.py` | 2 | `import skills.legal_research as lr` | `import skills.legal_research.legal_research as lr` |
| `tests/test_stale_recall_reconciliation.py` | 3 | `from skills.legal_research import _reconcile_review_with_doc` | `from skills.legal_research.legal_research import _reconcile_review_with_doc` |
| `tests/test_legal_research_conversation.py` | 4 | `import skills.legal_research as lr` | `import skills.legal_research.legal_research as lr` |
| `tests/test_preferences_chat_injection.py` | 3 | `from skills import legal_research` | `import skills.legal_research.legal_research as legal_research` |
| `tests/test_observability.py` | 294 | `from skills import legal_research as mod` | `import skills.legal_research.legal_research as mod` |

**Note on the last two:** those files need the *module object* (they do `monkeypatch.setattr(legal_research, "traced_invoke", …)`), not the function. Importing `from skills.legal_research import legal_research` would bind the **function** via the re-export and every `setattr` would fail. Use the explicit `import … as …` form shown.

`tests/test_skills.py:14`'s top-level `from skills.legal_research import legal_research` binds the function and **needs no change** — the `__init__.py` re-export covers it.

- [ ] **Step 6: Verify green**

```bash
bash scripts/check.sh
```
Expected: `401 passed`. Test count and assertions are unchanged — only import paths moved.

- [ ] **Step 7: Confirm the production import is untouched**

```bash
git diff HEAD --stat -- graph/
```
Expected: **no output** — `graph/graph.py:25` is byte-identical.

- [ ] **Step 8: Commit**

```bash
git add -A skills/ tests/
git commit -m "refactor(legal_research): convert to a package, no content change

skills/legal_research.py -> skills/legal_research/legal_research.py with an
__init__.py re-export, matching the shape of skills/contract_review/ and
skills/contract_generation/. graph/graph.py:25 is byte-identical.

Forced test-side retargeting: 19 patch() strings and 6 module-alias imports
now point at the submodule. Both mock.patch and monkeypatch.setattr raise
AttributeError on a missing name, so every stale target failed loudly rather
than silently no-opping.

Zero lines changed inside the module itself — that is deliberate, so the
per-module extractions that follow read as moves, not moves plus plumbing."
```

---

## Task 6: Extract `prompts.py`

**Files:**
- Create: `skills/legal_research/prompts.py`
- Modify: `skills/legal_research/legal_research.py`, `tests/test_skills.py`

**Interfaces:**
- Consumes: the package from Task 5.
- Produces: `skills.legal_research.prompts` exporting four `str` constants — `RESEARCH_SYSTEM_PROMPT`, `CHAT_SYSTEM_PROMPT`, `_CHAT_MSA_NOTE`, `_JSON_RETRY_SYSTEM`. No imports, no logic. Task 9's `context.py` imports `_CHAT_MSA_NOTE` from here.

- [ ] **Step 1: Record the byte-identity baseline**

Before touching anything, capture the hashes Task 10 will check against:

```bash
uv run python -c "
import hashlib
from skills.legal_research import legal_research as m
for n in ['RESEARCH_SYSTEM_PROMPT','CHAT_SYSTEM_PROMPT','_CHAT_MSA_NOTE','_JSON_RETRY_SYSTEM']:
    print(n, hashlib.sha256(getattr(m, n).encode()).hexdigest()[:16])
" | tee /tmp/prompt-hashes-before.txt
```

Keep `/tmp/prompt-hashes-before.txt` until Task 10.

- [ ] **Step 2: Create the module**

Create `skills/legal_research/prompts.py` with a module docstring, then **cut and paste verbatim** — do not retype — these four constants from `skills/legal_research/legal_research.py`:

| Constant | Current line |
|---|---|
| `RESEARCH_SYSTEM_PROMPT` | :30 |
| `CHAT_SYSTEM_PROMPT` | :59 |
| `_CHAT_MSA_NOTE` | :102 |
| `_JSON_RETRY_SYSTEM` | :161 |

Header for the new file:

```python
# skills/legal_research/prompts.py
"""Model-facing prompt constants for the research + doc-chat paths.

Isolated in their own module so a prompt change is a one-file diff. That
matters for eval cleanliness: prompt edits and code edits must never be
indistinguishable in a review. Nothing here imports anything — keep it that way.
"""
```

- [ ] **Step 3: Import them back in the entry module**

Delete the four definitions from `legal_research.py` and add, with the other top-of-file imports:

```python
from skills.legal_research.prompts import (
    CHAT_SYSTEM_PROMPT,
    RESEARCH_SYSTEM_PROMPT,
    _CHAT_MSA_NOTE,
    _JSON_RETRY_SYSTEM,
)
```

All four names stay reachable as `legal_research.<NAME>`, so existing call sites at :197, :744, :845 and inside `_run_doc_chat` need no change.

- [ ] **Step 4: Verify the constants did not change**

```bash
uv run python -c "
import hashlib
from skills.legal_research import prompts as m
for n in ['RESEARCH_SYSTEM_PROMPT','CHAT_SYSTEM_PROMPT','_CHAT_MSA_NOTE','_JSON_RETRY_SYSTEM']:
    print(n, hashlib.sha256(getattr(m, n).encode()).hexdigest()[:16])
" > /tmp/prompt-hashes-after-t6.txt
diff /tmp/prompt-hashes-before.txt /tmp/prompt-hashes-after-t6.txt && echo "IDENTICAL"
```
Expected: `IDENTICAL`. **If this differs, the paste was not verbatim — revert and redo.**

- [ ] **Step 5: Repoint the two prompt-reading tests**

`tests/test_skills.py:471` and `:488` import the prompt constants. Change:

```python
from skills.legal_research.legal_research import CHAT_SYSTEM_PROMPT
from skills.legal_research.legal_research import CHAT_SYSTEM_PROMPT, _JSON_RETRY_SYSTEM
```

to:

```python
from skills.legal_research.prompts import CHAT_SYSTEM_PROMPT
from skills.legal_research.prompts import CHAT_SYSTEM_PROMPT, _JSON_RETRY_SYSTEM
```

The single `lr.CHAT_SYSTEM_PROMPT` attribute read still works via the entry module's import.

- [ ] **Step 6: Verify and commit**

```bash
bash scripts/check.sh
git add -A skills/ tests/
git commit -m "refactor(legal_research): extract prompts.py

The four model-facing constants (RESEARCH_SYSTEM_PROMPT, CHAT_SYSTEM_PROMPT,
_CHAT_MSA_NOTE, _JSON_RETRY_SYSTEM) move to their own dependency-free module,
so a prompt change shows up as a one-file diff instead of hiding inside a
large refactor. Verified byte-identical by sha256 before and after."
```

---

## Task 7: Extract `edit_parsing.py`

**Files:**
- Create: `skills/legal_research/edit_parsing.py`
- Modify: `skills/legal_research/legal_research.py`, `tests/test_skills.py`, `tests/test_preference_suggestion.py`

**Interfaces:**
- Consumes: the package from Task 5.
- Produces: `skills.legal_research.edit_parsing`, importing only `json`, `logging` and `re`. Public surface used elsewhere:
  - `_extract_proposed_edits(prose: str) -> list[dict]`
  - `_extract_proposed_preferences(prose: str) -> list[str]`
  - `_looks_like_edit_promise(prose: str) -> bool`
  - `_parse_json_edits(raw: str) -> list[dict]`

  Internal-only: `_tolerant_json_loads`, `_iter_json_values`, `_flatten_edit_values`, `_escape_unescaped_whitespace_in_strings`, `_JSON_BLOCK_RE`, `_PREFERENCE_BLOCK_RE`, `_EDIT_PROMISE_RE`, `_VALID_ACTIONS`.

This is the Python mirror of the frontend's `parseEditBlocks.ts`.

- [ ] **Step 1: Create the module**

Move these verbatim from `legal_research.py`, ordering them so definitions precede use (`_parse_json_edits` currently sits at :145, before helpers it calls at :254/:267 — module-level functions resolve at call time so either order runs, but put helpers first for readability):

| Symbol | Current line |
|---|---|
| `_JSON_BLOCK_RE`, `_PREFERENCE_BLOCK_RE` | :219–220 |
| `_escape_unescaped_whitespace_in_strings` | :223 |
| `_tolerant_json_loads` | :254 |
| `_iter_json_values` | :267 |
| `_flatten_edit_values` | :296 |
| `_parse_json_edits` | :145 |
| `_EDIT_PROMISE_RE` | :318 |
| `_looks_like_edit_promise` | :326 |
| `_VALID_ACTIONS` | :334 |
| `_extract_proposed_edits` | :337 |
| `_extract_proposed_preferences` | :365 |

Header:

```python
# skills/legal_research/edit_parsing.py
"""Parse structured edit / preference proposals out of the LLM's prose.

The Python mirror of the Word add-in's parseEditBlocks.ts, and it must stay
tolerant of the same three shapes the local model actually emits inside one
fenced block: a single object, an array, and stacked top-level objects.

Imports nothing from this project — pure text in, structured data out.
"""
import json
import logging
import re

logger = logging.getLogger(__name__)
```

- [ ] **Step 2: Import them back in the entry module**

Delete the moved definitions and add:

```python
from skills.legal_research.edit_parsing import (
    _extract_proposed_edits,
    _extract_proposed_preferences,
    _looks_like_edit_promise,
    _parse_json_edits,
)
```

- [ ] **Step 3: Repoint the tests**

In `tests/test_skills.py`, these in-function imports (currently `from skills.legal_research.legal_research import …` after Task 5) move to `from skills.legal_research.edit_parsing import …`:

- `_extract_proposed_edits` — lines 393, 410, 428, 441, 449, 501, 512, 533, 556, 585
- `_parse_json_edits` — lines 669, 683
- `_looks_like_edit_promise` — lines 763, 783
- `_tolerant_json_loads` — line 575

In `tests/test_preference_suggestion.py:2`:

```python
from skills.legal_research.edit_parsing import _extract_proposed_preferences
```

- [ ] **Step 4: Confirm the module is dependency-free**

```bash
grep -E "^(from|import) " skills/legal_research/edit_parsing.py
```
Expected: exactly `import json`, `import logging`, `import re` — nothing from this project.

- [ ] **Step 5: Verify and commit**

```bash
bash scripts/check.sh
git add -A skills/ tests/
git commit -m "refactor(legal_research): extract edit_parsing.py

Prose -> structured edits/preferences, including the tolerant JSON layer that
handles the three shapes the local model emits in one fenced block (single
object, array, stacked objects). Imports only json/logging/re — the Python
mirror of the add-in's parseEditBlocks.ts. Moves are verbatim."
```

---

## Task 8: Extract `review_recall.py`

**Files:**
- Create: `skills/legal_research/review_recall.py`
- Modify: `skills/legal_research/legal_research.py`, `tests/test_stale_recall_reconciliation.py`, `tests/test_skills.py`

**Interfaces:**
- Consumes: the package from Task 5.
- Produces: `skills.legal_research.review_recall`, importing only `logging`, `re`, `unicodedata`. Public surface — **only two functions**:
  - `_strip_redlines_section(markdown: str) -> str`
  - `_reconcile_review_with_doc(review_markdown: str, doc_text: str) -> tuple[str, list[str]]`

  Everything else is internal: `_reconcile_gate_verdict`, `_surviving_blocker_count`, `_normalize_for_match`, `_placeholder_candidates`, and eight module-level regexes.

**The property that makes this a real module:** `_reconcile_gate_verdict` is called from *inside* `_reconcile_review_with_doc` (line 523), not by any outside caller. The whole gate-verdict machinery is hidden behind a two-function surface.

- [ ] **Step 1: Create the module**

Move verbatim, in this order:

| Symbol | Current line |
|---|---|
| `_strip_redlines_section` | :381 |
| `_MARKER_IN_SPAN_RE`, `_BARE_LABEL_RE`, `_SOURCE_TAG_RE` | :416–418 |
| `_normalize_for_match` | :421 |
| `_placeholder_candidates` | :433 |
| `_KEY_FINDINGS_HEADING_RE`, `_HEADING_RE`, `_BLOCKER_RATINGS` | :537–539 |
| `_surviving_blocker_count` | :542 |
| `_GATE_HEADING_RE`, `_OVERALL_STATUS_RE`, `_GATE_NEUTRAL_STATUS` | :585–587 |
| `_reconcile_gate_verdict` | :594 |
| `_reconcile_review_with_doc` | :460 |

`_reconcile_review_with_doc` goes **last** because it calls `_reconcile_gate_verdict`; `_HEADING_RE` must be defined before `_reconcile_review_with_doc` uses it at :490.

Header:

```python
# skills/legal_research/review_recall.py
"""Make a stored review safe to re-inject into a later chat turn.

Three deterministic, conservative passes over the recalled markdown:
strip the Suggested Redlines section so chat does not re-propose fills; drop
placeholder findings the current document proves were filled afterwards; then
reconcile the No-Signature gate verdict against what survived.

Conservative by construction — it never drops a live blocker and never asserts
a signature go-ahead. _reconcile_gate_verdict is called from inside
_reconcile_review_with_doc, so the gate machinery is internal to this module.

Imports nothing from this project — markdown in, markdown out.
"""
import logging
import re
import unicodedata

logger = logging.getLogger(__name__)
```

- [ ] **Step 2: Import them back in the entry module**

```python
from skills.legal_research.review_recall import (
    _reconcile_review_with_doc,
    _strip_redlines_section,
)
```

- [ ] **Step 3: Repoint the tests**

`tests/test_stale_recall_reconciliation.py` — lines 2–3 become:

```python
import skills.legal_research.review_recall as rr
from skills.legal_research.review_recall import _reconcile_review_with_doc
```

Then rename the `lr.` prefix to `rr.` **only** for the symbols that moved: `lr._reconcile_gate_verdict` (7 sites) and `lr._surviving_blocker_count` (6 sites) become `rr.…`.

**Keep `lr` as a second import** for what stayed behind — `monkeypatch.setattr(lr, "load_latest_review", …)` at lines 110 and 118, and `monkeypatch.setattr(lr, "_reconcile_review_with_doc", _boom)` at line 123. Those target names in the *entry* module's namespace and stay valid until Task 9 moves `_load_prior_review_block`.

In `tests/test_skills.py`, the `lr._reconcile_gate_verdict` / `lr._surviving_blocker_count` attribute reads (lines ~1338–1422) become `rr.…` with a `import skills.legal_research.review_recall as rr` added to those test functions.

- [ ] **Step 4: Confirm the module is dependency-free**

```bash
grep -E "^(from|import) " skills/legal_research/review_recall.py
```
Expected: exactly `import logging`, `import re`, `import unicodedata`.

- [ ] **Step 5: Verify and commit**

```bash
bash scripts/check.sh
git add -A skills/ tests/
git commit -m "refactor(legal_research): extract review_recall.py

Stored review -> safe injectable block: strip redlines, drop placeholder
findings the current doc disproves, reconcile the No-Signature gate verdict.
Two public functions over six internal helpers and eight regexes —
_reconcile_gate_verdict is called from inside _reconcile_review_with_doc, so
the gate machinery never leaves the module. Imports only logging/re/unicodedata."
```

---

## Task 9: Extract `context.py`

**Files:**
- Create: `skills/legal_research/context.py`
- Modify: `skills/legal_research/legal_research.py`, `tests/test_skills.py`, `tests/test_legal_research_conversation.py`, `tests/test_stale_recall_reconciliation.py`

**Interfaces:**
- Consumes: `skills.legal_research.prompts._CHAT_MSA_NOTE` (Task 6), `skills.legal_research.review_recall.{_reconcile_review_with_doc, _strip_redlines_section}` (Task 8).
- Produces: `skills.legal_research.context` with:
  - `_load_prior_review_block(state: LegalAgentState, uploaded_text: str) -> str`
  - `_load_prior_conversation(state: LegalAgentState) -> list[dict]`
  - `_needs_grounding(question: str) -> bool`
  - `_build_chat_grounding(state: LegalAgentState, uploaded_text: str) -> tuple[str, str]`
  - `_cap_chat_context(messages: list[dict], uploaded_text: str, request: str) -> None` (mutates in place)

**This is the highest-churn task in the plan** — it carries most of the 56 monkeypatch sites, because the names those tests patch (`load_latest_review`, `load_recent`, `detect_contract_type`, `load_playbook_bundle`, `attach_parent_msa`) are imported into *this* module after the move.

- [ ] **Step 1: Create the module**

Move verbatim:

| Symbol | Current line |
|---|---|
| `_load_prior_review_block` | :637 |
| `_load_prior_conversation` | :674 |
| `_GROUNDING_TRIGGER_RE` | :695 |
| `_needs_grounding` | :720 |
| `_build_chat_grounding` | :730 |
| `_cap_chat_context` | :752 |

Header and imports:

```python
# skills/legal_research/context.py
"""Assemble the doc-chat prompt's context, within budget.

Four sources feed a chat turn: the prior review for this document, the durable
per-(document, attorney) conversation, the playbook + governing MSA grounding,
and the document itself. Every read is best-effort — a memory or grounding
failure degrades the turn, never breaks it — and the budget cap truncates only
the document, never the grounding.
"""
import logging
import re

from config import get_settings
from graph.state import LegalAgentState
from memory.conversation_store import load_recent
from memory.review_store import load_latest_review
from skills.grounding import (
    attach_parent_msa,
    detect_contract_type,
    load_playbook_bundle,
)
from skills.legal_research.prompts import _CHAT_MSA_NOTE
from skills.legal_research.review_recall import (
    _reconcile_review_with_doc,
    _strip_redlines_section,
)

logger = logging.getLogger(__name__)
```

- [ ] **Step 2: Clean up the entry module's now-unused imports**

`legal_research.py` no longer uses `load_recent`, `load_latest_review`, `detect_contract_type`, `load_playbook_bundle`, `attach_parent_msa`, `_reconcile_review_with_doc`, `_strip_redlines_section`, or `_CHAT_MSA_NOTE`. Delete those import lines and add:

```python
from skills.legal_research.context import (
    _build_chat_grounding,
    _cap_chat_context,
    _load_prior_conversation,
    _load_prior_review_block,
    _needs_grounding,
)
```

Keep `get_settings` — the entry module still uses it in `_build_llm`, `_build_json_llm` and `_run_doc_chat`.

`preferences_block_for_state` stays imported in the entry module: it is called directly from `_run_doc_chat`, not from `context.py`.

- [ ] **Step 3: Run the suite and read the failures**

```bash
uv run pytest tests/ -q 2>&1 | tail -40
```
Expected: a batch of `AttributeError` failures from `monkeypatch.setattr(lr, "load_latest_review", …)` and friends — those names are gone from the entry module. That loud failure is the safety property; work through the list.

- [ ] **Step 4: Retarget the moved patch sites**

Add `import skills.legal_research.context as ctx` to the affected test functions, and switch **only these names** from `lr.` to `ctx.`:

`load_latest_review`, `load_recent`, `detect_contract_type`, `load_playbook_bundle`, `attach_parent_msa`, `_load_prior_review_block`, `_load_prior_conversation`, `_needs_grounding`, `_reconcile_review_with_doc`.

Leave on `lr.`: `_build_llm`, `_build_json_llm`, `_build_agent`, `traced_invoke`, `_llm_cache`, `_agent_cache`.

Most affected tests patch **both** groups and so need both aliases. Worked example — `tests/test_skills.py` around line 1152 becomes:

```python
import skills.legal_research.legal_research as lr
import skills.legal_research.context as ctx
...
monkeypatch.setattr(lr, "_build_llm", lambda: object())
monkeypatch.setattr(lr, "traced_invoke", fake_traced_invoke)
monkeypatch.setattr(ctx, "load_latest_review",
                    lambda document_id: {"markdown": review_md})
```

Affected line groups in `tests/test_skills.py`: ~1152–1154, 1189–1193, 1215–1217, 1237–1243, 1271–1279, 1302–1308, 1380, 1429–1435, 1454–1460, 1479–1485.
In `tests/test_stale_recall_reconciliation.py`: 110, 118, 123.

- [ ] **Step 5: Handle the `get_settings` hazard explicitly**

`tests/test_legal_research_conversation.py` patches `get_settings` at lines 17, 24, 29, 35 and `load_recent` at line 38. `_load_prior_conversation` now lives in `context.py` and reads `context.get_settings`.

**`get_settings` exists on both modules**, so patching the wrong one succeeds and silently does nothing — the only place in this plan where a stale target does not fail loudly. Change all five to target `ctx`:

```python
import skills.legal_research.context as ctx
...
monkeypatch.setattr(ctx, "get_settings", lambda: _settings())
monkeypatch.setattr(ctx, "load_recent", _boom)
```

- [ ] **Step 6: Prove those five patches actually bite (mutation check)**

A green suite is not sufficient evidence here. Temporarily invert the guard in `context.py::_load_prior_conversation` — change

```python
    if not settings.conversation_store_enabled:
        return []
```

to

```python
    if settings.conversation_store_enabled:
        return []
```

Then:

```bash
uv run pytest tests/test_legal_research_conversation.py -q
```
Expected: **failures.** If this still passes, the patches are not reaching the code under test and the tests are vacuous — fix the targeting before continuing.

Revert the inversion:
```bash
git checkout skills/legal_research/context.py
```
…then re-apply Steps 1–2 if the checkout discarded them (safer: undo the two-character edit by hand rather than using `git checkout`).

- [ ] **Step 7: Verify the dependency graph is acyclic**

```bash
uv run python -c "
import skills.legal_research.context, skills.legal_research.legal_research
print('imports clean, no cycle')
"
grep -E "^(from|import) " skills/legal_research/prompts.py
```
Expected: `imports clean, no cycle`, and `prompts.py` shows **no import lines at all**.

- [ ] **Step 8: Verify and commit**

```bash
bash scripts/check.sh
git add -A skills/ tests/
git commit -m "refactor(legal_research): extract context.py

Prior-review recall, durable conversation load, the conditional-grounding
keyword gate, playbook/MSA attach, and the context budget cap move into one
module: everything that assembles the doc-chat prompt's context.

Carries the bulk of the test retargeting, since the names those tests patch
(load_latest_review, load_recent, detect_contract_type, load_playbook_bundle,
attach_parent_msa) are imported here now. Stale targets raise AttributeError
and failed loudly — except get_settings, which exists on both modules and so
would silently no-op; the five sites in test_legal_research_conversation.py
were verified by mutation rather than by a green suite.

Entry module is now orchestration only: LLM builders + the two run paths."
```

---

## Task 10: Prove behavior is unchanged

**Files:** none modified. This task is verification only.

**Interfaces:** consumes everything from Tasks 5–9.

- [ ] **Step 1: Confirm the final shape**

```bash
wc -l skills/legal_research/*.py
```
Expected, roughly: `__init__.py` 3, `prompts.py` ~110, `edit_parsing.py` ~190, `review_recall.py` ~265, `context.py` ~150, `legal_research.py` ~260. Total within ~30 lines of the original 952 (the delta is module docstrings and import lines).

- [ ] **Step 2: Prompt byte-identity against the Task 6 baseline**

```bash
uv run python -c "
import hashlib
from skills.legal_research import prompts as m
for n in ['RESEARCH_SYSTEM_PROMPT','CHAT_SYSTEM_PROMPT','_CHAT_MSA_NOTE','_JSON_RETRY_SYSTEM']:
    print(n, hashlib.sha256(getattr(m, n).encode()).hexdigest()[:16])
" > /tmp/prompt-hashes-final.txt
diff /tmp/prompt-hashes-before.txt /tmp/prompt-hashes-final.txt && echo "PROMPTS IDENTICAL"
```
Expected: `PROMPTS IDENTICAL`. This is the guard against silently invalidating every past eval observation.

- [ ] **Step 3: Confirm the production call site never moved**

```bash
git diff main...HEAD -- graph/ api/ clients/word/src/word.ts clients/word/src/parser.ts
```
Expected: **no output.** The graph, the API, and the two client files with the most gotchas are untouched by this branch.

- [ ] **Step 4: Full gate**

```bash
bash scripts/check.sh
```
Expected: `401 passed`, `tsc` silent, 165 frontend asserts with zero FAIL, `==> all checks passed`.

- [ ] **Step 5: Restart the backend**

`uvicorn` does not auto-reload, and every file in this branch is one it has already imported.

```bash
bash scripts/start.sh
```

Confirm the add-in dev server is up in its own terminal (`cd clients/word && npm run dev`). No rebuild is needed — this branch changes no frontend source except test files.

- [ ] **Step 6: Word sideload smoke — five steps, one per extracted module**

Open a real Trinetix NDA in the sideloaded Word add-in and work through the table. Each row targets a different module so a failure localises immediately.

| # | Action | Passes when | Proves |
|---|---|---|---|
| 1 | Findings tab → **Review** | Findings table renders; blockers card populated; contract type chip correct | `contract_review` path untouched |
| 2 | Chat: *"who signs this?"* | Answers from the document; **no** playbook attached (~10s, not ~30s) | entry path + `context._needs_grounding` lean branch |
| 3 | Open a **SOW**, Chat: *"does this conflict with the governing MSA?"* | Answer references MSA terms; latency reflects the heavier grounded prompt | `context._build_chat_grounding` + MSA attach |
| 4 | Chat: *"fill the signature block with Suzy Quatro"* | A PROPOSED EDIT card renders and **Apply** writes a tracked change into the document | `edit_parsing` end-to-end |
| 5 | Back on the NDA from step 1, Chat: *"what were the findings?"* | Recalls the stored review; no already-filled placeholder reported as unfilled | `review_recall` + `context._load_prior_review_block` |

- [ ] **Step 7: Confirm tracing is intact**

Open the trace UI (local Langfuse `http://localhost:3000`, or Phoenix on the VM) and find the turn from smoke step 4. The span tree must show the same shape as before the split: a `query` root, a `legal_research` span, and a nested `doc_chat` GENERATION carrying token usage.

- [ ] **Step 8: Record the outcome in the wiki**

Add a row to `docs/wiki.md`'s "Shipped Since Last Update" table naming the branch, the two tiers, the final module sizes, the prompt-hash result, and the five smoke steps with their observed outcomes. If any smoke step revealed a limitation, record it plainly rather than omitting it.

- [ ] **Step 9: Commit**

```bash
git add docs/wiki.md
git commit -m "docs(wiki): record cleanup + legal_research split

Tier 1: dead schemas.py deleted, five shipped step docs archived, wiki header
and architecture diagram corrected, roadmap DONE/duplicate rows pruned,
frontend asserts made failable, scripts/check.sh added as the pre-push gate.

Tier 2: skills/legal_research.py (952 lines) split into a five-module package.
Prompt constants verified byte-identical by sha256. graph/, api/ and the two
gotcha-dense client files are untouched by the branch. Word sideload smoke
green across all five paths."
```

---

## Self-Review

**Spec coverage.** Every spec item maps to a task: Tier 1 items 1→Task 3, 2→Task 4, 3→Task 4, 4→Task 4, 5→Task 1, 6→Task 2. Tier 2's five modules → Tasks 5–9. Verification section → Tasks 6 (hash baseline) and 10 (hash check, smoke). The spec's two named risks — patch targets and import cycles — are Tasks 5/9 Step 4 and Task 9 Step 7.

**Two corrections to the spec, discovered while planning.** Both are recorded here rather than silently absorbed:

1. **The spec said "four" patch targets. The real number is 19 patch strings plus 56 `monkeypatch.setattr` sites.** The spec's "tests move with their module" line materially understated Tier 2's test-side work. It does not change the design — the module boundaries are unaffected — but Task 9 is substantially larger than the spec implied.
2. **The spec's module map omitted `_agent_cache` and `_llm_cache`** (lines 108–109), module-level mutable state. Both belong with the LLM builders in the entry module; no ownership ambiguity, but they needed placing.

Neither invalidates the approved design. The mitigating discovery is that `mock.patch` and `monkeypatch.setattr` both raise `AttributeError` on a missing name, so stale targets fail loudly — with the single `get_settings` exception, which Task 9 Step 6 covers by mutation testing.

**Placeholder scan.** No TBD/TODO. Every code step carries real content; every verification step carries a runnable command and its expected output.

**Type consistency.** `pass(cond: boolean, label: string): void` is used identically in Tasks 1 and 2. The five `context.py` signatures in Task 9's Interfaces match their call sites in `_run_doc_chat`. The `lr` / `ctx` / `rr` aliases are introduced in Tasks 5, 9 and 8 respectively and used consistently thereafter.
