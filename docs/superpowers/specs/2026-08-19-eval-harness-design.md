# Eval harness — Tier 1 (deterministic regression armor) — design

> Date: 2026-08-19 · Branch: `feat/eval-harness` · Status: design, approved for planning

## Why

This is Phase A's first branch. Phase A was defined as *"what to build next"*, and the
project review's headline finding was that **no eval harness exists** — which is what
gates Stage 2 of the self-improving-harness north star.

The self-improving loop needs four parts. Three are built:

| Part | Status |
|---|---|
| **Signal** — what went wrong | ✅ `feedback` + `interaction_event`, joined to `turn_id`/`trace_id` (2026-08-18) |
| **Measurement** — is a change better or worse | ❌ **missing — this branch** |
| **Write path** — where an improvement is stored | ✅ Stage 1 `USER.md`, one `grounding` seam |
| **Guardrail** — the playbook outranks memory | ⚠️ structurally held, **not provably enforced** |

The guardrail is unproven *because* the measurement is missing. The roadmap already
records the consequence: relaxing preferences ("don't worry about X") are the dangerous
class, the ceiling holds structurally but is LLM-dependent, and **nothing in the repo
would currently catch a preference that quietly relaxed it**. An agent that writes its
own memory without an eval is an agent that can degrade itself silently. The eval is not
hygiene; it is the precondition for Stage 2.

The immediate, non-north-star payoff is more concrete. Every behavior fix this project
has shipped was driven by a single trace — `cc81804f`, `0f63a143`, `4b24ca1d`,
`02e41ead`, `9e5b804c`, and twenty-odd more are named in `CLAUDE.md` and the wiki. There
is no way to answer *"did this change make things better overall?"*, only *"did it fix
this one trace?"* Twice already a green suite has shipped a test that asserted nothing
(the `get_settings` patch on the wrong module; the frontend `pass()` that only
`console.log`'d). Both were caught by mutation, by hand, once.

## Non-goals

Explicitly **not** in this work:

- **Anything LLM-scored (Tier 2).** No judge model, no rubric, no improvisation rate, no
  ceiling-adherence scoring. This branch runs zero LLM calls and is deterministic
  end to end. Tier 2 is where the taxonomy matters, and the taxonomy is supposed to come
  from the first ~30 real feedback rows, which do not exist yet.
- **The feedback→fixture promoter.** ~5 feedback rows and one real `edit_failed` row
  exist today; a promoter would have nothing to promote. The case format below is
  designed as its target so it lands as a small follow-up once testers are on.
- **Model comparison (qwen vs llama).** Needs a corpus and a scorer to exist first.
- **Any behavior change.** The only edit to shipped code is an `export` keyword on two
  private helpers. No logic changes in `word.ts`, `parseEditBlocks.ts`,
  `edit_parsing.py` or anywhere else. If the eval finds a bug, it is recorded in the
  baseline as a known failure and fixed on its own branch.
- **Prompt changes of any kind.**
- **CI.** The project runs no CI by choice; the gate stays local and pre-push.
- **Replacing the existing unit tests.** `check.sh`'s `EXPECTED_PASS_COUNT` block and all
  50 pytest files stay exactly as they are.

## Context: what exists to build on

**The corpus already exists, in prose.** 27 distinct trace ids are documented across
`CLAUDE.md` and `docs/wiki.md`, each with its failure and its fix written down. Roughly a
third are deterministic-machinery failures and are this branch's seed; the rest are
model-behavior failures and wait for Tier 2. Where the original trace is no longer
retrievable, the written description plus the fix's own test data is enough to
reconstruct the case — the durable artifact was always the description.

**The signal pipe is live.** `interaction_event` now records `edit_failed` with the
matcher's own error string (`detail`, ≤500 chars) and a quoted excerpt of the targeted
clause (`target_ref`, ≤200 chars). That is a finished matcher fixture, generated with no
human labelling at all. The first one arrived on 2026-08-18.

**A pre-push gate exists.** `scripts/check.sh` runs pytest, `tsc --noEmit`, and every
`src/*.test.ts` through `npx tsx`, asserting an exact total of 221 `PASS:` lines.

### The gap is not parsing — it is matching, and agreement

Edit-block *parsing* is already well covered on both sides: 21 tests in
`tests/test_skills.py` (well-formed, multiple blocks, arrays in one fence, stacked
top-level objects, tolerant parse over raw tabs/newlines, malformed, invalid action) and
462 lines in `clients/word/src/parseEditBlocks.test.ts` (`multiline-fill`,
`filled-rewrite`, `tab-bundled`, `dup-single`, `fill-guard`, `backend-list`, …). **This
branch must not restate any of it.** Two things are genuinely uncovered.

#### Gap 1: the matcher's only test is a human

`clients/word/src/word.test.ts` is 57 lines covering four pure helpers, and its own
header says it plainly:

> *"(The Office.js-dependent functions are smoke-tested by sideloading in Word.)"*

So `searchCandidates`, `tailCandidates`, `searchFirst`, `findClauseRange`, the 85%
completeness guard and `replaceAll` — the exact code behind `02e41ead`, `ce45b899`,
`32deb028`, `9e5b804c`, `cc81804f` — have **no automated coverage of any kind**. Their
only verification is a person opening Word for Mac and looking.

That code is simultaneously the most-churned in the repo, the source of the largest
single block of hard-won gotchas in `CLAUDE.md`, and now the one thing production
telemetry actively measures. The highest-value target and the only untested one are the
same code.

#### Gap 2: nothing checks the two parsers agree

`_extract_proposed_edits` (backend) and `extractEditBlocks` (frontend) parse the same
assistant string, and `ChatTab` takes the backend's list whenever it is non-empty
(`backendEdits.length > 0 ? backendEdits : blocks`). Both are well tested — **separately,
against their own fixtures.** No test anywhere runs one string through both and compares.

So if the backend extracts one edit where the frontend would extract two, the attorney
silently gets one, every test on both sides stays green, and nothing reports the
divergence. The two suites growing independently is what makes this drift likely rather
than hypothetical.

## Architecture

Three shapes were considered.

| Shape | Verdict |
|---|---|
| **Fixture corpus + one runner per language, shared JSON case format** | **Chosen** |
| Native tests reading a fixture directory (pytest + the existing `tsx` harness) | **Rejected on the reporting contract** — it inherits `check.sh`'s exact-count binary gate, so a case promoted from production breaks the push gate the day it is added. Every promoted row is by definition currently-failing. That is the intake loop dead on arrival. |
| One TS runner, Python parser reached over a subprocess bridge | Rejected — one number, but a fragile cross-language bridge, and the Python cases end up second-class |

The chosen shape's decisive property is that **the corpus is data, not code**. Adding a
case is adding a JSON file. That is what a promoter can do later without touching a
runner, and it is what lets a known-failing case live in the repo honestly.

## Slice 1 — the case format

`evals/cases/*.json`, one case per file, named `<kind>-<slug>.json`. Every case carries
`id`, `kind`, `why` (one line, and the trace id where there is one), `input`, `expect`.

Three kinds:

| Kind | Input → expected | Run by |
|---|---|---|
| `parse` | raw assistant output → the extracted edit list | **both** runners, compared |
| `match` | document paragraphs + target → matched span text, or `null` | TS |
| `apply` | document paragraphs + proposal → `Result` + resulting document text | TS |

A `parse` case exists to close Gap 2, **not** to re-test extraction. Its job is to run
one string through both parsers and compare, so the case format expects an edit list
rather than a parser-specific shape, and every `parse` case runs through both. There is
no opt-out flag: no legitimate divergence is known today, so a divergence is a finding,
not a configuration.

The selection rule follows from that. A `parse` case earns its place only where the two
implementations could plausibly drift — the shapes each side reimplemented separately
(stacked objects, arrays in one fence, tolerant parse over raw whitespace) and the
normalization stage that exists on one side only. Extraction behavior already pinned by
`test_skills.py` or `parseEditBlocks.test.ts` is not re-asserted here.

A `parse` case therefore carries **two** expectations, because the two pipelines are
genuinely not the same length:

| Field | Asserted by | Is |
|---|---|---|
| `expect.edits` | both runners | what the parser extracted, before any normalization |
| `expect.normalized` | TS only, optional | the list after `normalizeProposals` |

> **Superseded during implementation.** The `expect.edits` row above ("what the parser
> extracted, before any normalization, asserted by both runners") turned out to describe a
> stage that cannot be observed: `extractEditBlocks` bakes `normalizeProposals` into its own
> return value internally (`parseEditBlocks.ts:406`), so there is no pre-normalization
> frontend output to compare against the backend's. Comparing the backend's raw output to
> the frontend's already-normalized output compares two different pipeline stages, not a
> divergence. This spec is left as a point-in-time snapshot rather than rewritten; the
> corrected semantics actually implemented are: `expect.edits` is the **backend's raw
> output** (asserted by the Python runner); `expect.normalized` is `extractEditBlocks().blocks`
> (the frontend's own already-normalized output), defaulting to `expect.edits` when
> extraction is a no-op; and the TS runner additionally asserts
> `normalizeProposals(expect.edits) === extractEditBlocks().blocks` — the real production
> invariant, since `ChatTab` normalizes whichever edit list wins before applying it. See
> `docs/wiki.md` ("Deterministic eval harness (Tier 1)", `feat/eval-harness`) for the shipped
> record.

Two fields, no flags. `kind` alone decides which runners see a case.

`normalizeProposals` (`splitMultilineFieldEdits` → `reduceTabSegment` →
`collapseDuplicateFills`) exists only on the TS side, and `CLAUDE.md` records why it runs
where it does: it must be applied at the point of use in `ChatTab`, because a transform
buried inside `extractEditBlocks` is bypassed whenever the backend supplies the edits.
So `edits` is the stage the two parsers actually share and both are held to it, while
`normalized` pins the TS-only stage on top.

Splitting them this way is worth more than one combined expectation would be. The three
splitter traces (`02e41ead`, `32deb028`, `9e5b804c`) all have the same shape: the parser
correctly extracts one multi-line `replace`, and the *normalizer* is what must turn it
into per-line edits. A case that set only a final expectation could not say which of the
two stages regressed.

## Slice 2 — the Python runner

`evals/run_parse.py`. Loads every `kind: "parse"` case, calls
`skills.legal_research.edit_parsing._extract_proposed_edits`, compares against
`expect.edits`. The runner itself pulls in no pytest, no fixtures and no
Docker — it is a plain script, because it must run identically from
`scripts/eval.sh` and from a developer's shell. (Its own unit tests do live in the
normal suite; see Testing.)

`expect.normalized` is ignored here — it names a stage that exists only in the client.
Every `parse` case is otherwise in scope for this runner; there is no skip list, so a
case cannot quietly stop being checked on one side.

## Slice 3 — the Word fake

`clients/word/src/wordFake.ts`. A `Word.RequestContext` over a plain paragraph array with
character offsets.

**Surface.** The matcher touches seven Office.js members and no more:

- `Word.run(cb)`
- `context.document.body.search(query, { matchCase, matchWildcards, matchWholeWord })`
- `.load(...)` / `context.sync()`
- `Range.text`
- `Range.getRange(Word.RangeLocation.start | .end)` and `Range.expandTo(other)`
- `Range.insertText(text, Word.InsertLocation.replace)`
- `document.changeTrackingMode` (read and write)

Plus the `Word.RangeLocation` / `Word.InsertLocation` / `Word.ChangeTrackingMode` enums.

**Semantics.** These are the part that matters, and they are not invented: the fake is
`CLAUDE.md`'s accumulated knowledge of Word made executable. Eight rules, each with a
documented incident behind it:

1. A query over **255 chars throws** — `searchFirst` catches and returns null. The fake
   models Word's real 255, not the code's own conservative `SEARCH_MAX_LEN = 200`
   filter, so the gap between the two stays visible rather than being defined away.
2. **A match never crosses a paragraph break.** A needle containing `\n` never matches.
3. **`[](){}<>?*` behave as wildcards even at `matchWildcards: false`** (the Word for Mac
   behavior) — a literal needle containing them misses.
4. **`matchWildcards: true` with backslash-escaped metas matches them literally** — the
   `escapeWordWildcards` retry path.
5. `matchCase: false`.
6. `matchWholeWord` bounds on word characters.
7. **Search runs against RAW document text, including tracked-change deletions** — the
   distinction that `readBody`'s `getReviewedText("current")` exists to handle. A case
   can therefore declare a paragraph's raw text and its reviewed text separately.
8. **A literal `\t` in a needle never matches**, even where the document renders one —
   the two-column signature block.

**Why it is type-checked for free.** The fake lives in `clients/word/src/`, whose
tsconfig pins `"types": ["office-js", "vite/client"]`. So `npx tsc --noEmit` — already in
`check.sh` — checks the fake against Microsoft's own Office.js type definitions. Shape
fidelity is enforced by the compiler; only behavioral fidelity is on us.

## Slice 4 — the TS runner

`clients/word/src/eval.ts`, run with `npx tsx`, matching how every existing `*.test.ts`
already runs. It uses the same module-scoped `declare const process` trick as
`testAssert.ts` rather than pulling in `@types/node`, which the pinned `types` array
would reject.

It handles all three kinds: `parse` through `extractEditBlocks` + `normalizeProposals`,
`match` through `findClauseRange` against the fake, `apply` through `applyEdit` against
the fake.

`match` and `apply` require `searchCandidates` and `tailCandidates` to be **exported**.
That is the only change to shipped application code in this branch, and it is an
`export` keyword — no signature, no logic. (`scripts/check.sh` also gains a block; it
is a gate, not shipped code.)

An `apply` case asserts the returned `Result` (ok/fail, and the error string verbatim
where a guard fires) and the resulting document text. It does **not** assert
tracked-change rendering — the fake models `changeTrackingMode` as a recorded call
sequence, enough to pin the try/finally restore, and no further. Revision rendering is
Word's, and simulating it would be fiction.

## Slice 5 — scoring, baseline, gating

`scripts/eval.sh` runs both runners and prints one score:

```
parse    6/6
match    5/7    (2 known-failing)
apply    5/5
------------------------------
total   16/18   baseline 16 — OK
```

`evals/baseline.json` holds the ids of cases known to fail, each with a one-line reason
and, where it exists, the roadmap row it belongs to. A case in the baseline that
**starts passing** is reported as loudly as a regression — it means either a fix landed
or the case rotted, and both need a human.

**The gate is "the score must not decrease", never "all cases pass."** This is the whole
reason the eval is not folded into the existing assert harness. A case promoted from a
tester's complaint is currently-failing by construction; a binary gate would mean every
promotion breaks the push gate until someone fixes the bug, so nobody would ever promote
one. Score-plus-baseline lets a known failure sit in the repo, named and counted, which
is the honest state.

`check.sh` gains one block that calls `scripts/eval.sh` and fails on a decrease. Its
existing `EXPECTED_PASS_COUNT=221` block is untouched.

## The seed corpus

18 cases, drawn from the documented traces and weighted by where coverage is actually
absent — not by where the incidents were loudest.

**`match` and `apply` — the bulk, because nothing covers them today:**

| Case | Kind | Trace |
|---|---|---|
| Bracketed blank `[__]` found via the wildcard retry | `match` | wildcard-escape gotcha |
| Progressive prefix shortening finds a long clause | `match` | `findClauseRange` head/tail |
| Head+tail span uses match boundaries, not paragraphs | `match` | fragment over-replace gotcha |
| Short needle (<200 chars) returns the head, never expands | `match` | early-bail guard |
| Tracked deletion still present in raw search text | `match` | `5f188799` |
| `\t` needle in a two-column signature block misses | `match` | table-tab gotcha |
| Single-word anchor searches whole-word only | `match` | `shouldMatchWholeWord` |
| Ambiguous blank refused for both `replace` and `replace_all` | `apply` | `cc81804f` |
| 85% completeness guard refuses a short prefix match | `apply` | completeness-threshold gotcha |
| `replaceAll` snapshots matches before replacing | `apply` | struck-text re-find gotcha |
| `changeTrackingMode` restored via try/finally | `apply` | Track Changes restore gotcha |
| `simplifyMultilineReplace` collapses a one-line-differs target | `apply` | multi-line collapse gotcha |

**`parse` — agreement only, deliberately few:**

| Case | Kind | Why it can drift |
|---|---|---|
| Stacked top-level objects in one fence | `parse` | reimplemented twice (`_iter_json_values` / `iterJsonValues`) — `cea50c6b`, `f15f8a9b` |
| Array in one fence, and the bare single object | `parse` | the other two shapes both sides accept independently |
| Raw `\n`/`\t` inside a JSON string value | `parse` | reimplemented twice (`_tolerant_json_loads` / `tolerantParse`) |
| Multi-line blank fill (MSA/SOW) | `parse` | `expect.normalized` pins the TS-only split — `02e41ead`, `ce45b899` |
| Filled-block rewrite, Boris→Suzy | `parse` | same, for the specific-value path — `32deb028` |
| Tab-bundled target reduced to the changed column | `parse` | same, for `reduceTabSegment` — `9e5b804c` |

The three normalization cases are in the `parse` list rather than dropped as duplicates
because their `expect.edits` half is the agreement check and their `expect.normalized`
half spans a stage the backend does not have — together they pin exactly where a
multi-line target is supposed to stop being one edit.

**Not duplicated here:** behavior that already has real unit-test coverage stays where
it is. `_sanitize_history` (trace `2ae99ecc`) is the clearest example — four tests in
`tests/test_history_priming.py` already pin it, including the Redis-fallback path. The
eval corpus exists to cover what the suite does not, and a case that restates a passing
unit test adds maintenance without adding a signal.

Deliberately **excluded as Tier 2**: `4b24ca1d` (scope over-reach), `0f63a143`
(fabricated address), `f4b383f0` (129KB MSA missed placeholders), `01d4e2ae` (prose-only
output). These are model behavior, not machinery, and no deterministic assertion covers
them honestly.

## On the fake's fidelity

The accepted risk is that a fake tests our model of Word rather than Word, and this
repo's gotcha list is a record of Word surprising us. Three things bound it.

**It is falsifiable by production data.** Every `edit_failed` row is ground truth from
real Word: a target that a real `body.search` did not find. If the fake matches where
production reported `Couldn't find "…"`, **the fake is wrong and a documented gotcha is
wrong with it.** The telemetry audits the simulator. That is the loop running in the
useful direction, and it is the reason to accept the risk rather than merely tolerate it.

**It must not be written from `word.ts`.** Writing the fake by reading the matcher
encodes the matcher's assumptions, and the eval then confirms itself — the exact
vacuous-test failure mode this project has hit twice. The fake is written from
`CLAUDE.md`'s gotchas and the Office.js documentation. Where the two disagree, the
disagreement is recorded in the case file, not resolved by looking at the caller.

**Its blind spots are stated, not discovered.** The fake cannot model: revision
rendering, `expandTo` across tables, list renumbering, content controls, or anything
about how Word lays out what it matched. A `match` case asserts *which characters*, never
*how they look*. Sideloading remains the only proof of the visual half, and this branch
does not claim otherwise.

## Testing the harness itself

The hazard here is not a failing eval; it is an eval that passes while asserting nothing.
Both prior instances of that in this repo were caught by mutation, by hand, once.

So this branch's own verification is mutation, and it is part of the work rather than a
nicety:

1. **Each of the eight fake rules is proven load-bearing.** Remove the rule, and a named
   case must flip. A rule whose removal changes no case either has no case or does not
   matter — both are findings, and both are fixed before the branch lands.
2. **The score gate is proven to fail.** Add a deliberately-broken case, confirm
   `check.sh` goes red, remove it.
3. **A baseline entry is proven to be checked in both directions** — a known-failing case
   that starts passing must be reported, not silently absorbed.
4. **The dual-parser check is proven** by making one parser return a deliberately
   different list and confirming the case fails on the divergence — not merely on the
   primary expectation, which would pass the mutation while testing nothing.

Standard unit tests are added only where a runner has real logic of its own (case
loading, baseline diffing, the score comparison) — `tests/test_eval_runner.py`. The
corpus itself is not unit-tested; it is data.

## Config

None. No settings field, no `.env` key, no flag. The eval is a script, not a runtime
concern, and adding a config knob would put it on the pydantic-settings surface for no
reason.

## Risks

| Risk | Handling |
|---|---|
| **The fake drifts from Word and the eval goes green on a real bug.** | Falsified by `edit_failed` rows; the fake is written from the gotcha list, not from `word.ts`; blind spots are enumerated above rather than assumed away. |
| **The corpus ossifies — cases pinned to today's exact strings.** | `expect` asserts the edit list and the matched span, never internal candidate order. `searchCandidates`' ordering is deliberately left unasserted so its heuristics stay tunable. |
| **The baseline becomes a dumping ground** — failures parked instead of fixed. | Every baseline entry carries a reason and, where one exists, a roadmap row. The score line prints the known-failing count on every run, so the number is never out of sight. |
| **Exporting private helpers invites their use elsewhere.** | Two `export` keywords, no signature changes. If it becomes a problem the answer is a lint rule, not re-privatizing and losing the coverage. |
| **Two runners drift on the shared case format.** | The format is the narrowest thing that serves both, and every `parse` case fails loudly on divergence — the drift is itself asserted, on every case, with no way to opt out. |

## Follow-ups (deliberately out of scope)

- **The feedback→fixture promoter** — `feedback.id` or an `edit_failed` row id → a case
  file. Small, and it is the point of the format; it waits for tester volume.
- **Tier 2, LLM-scored** — ceiling adherence, improvisation rate, and the relaxing-
  preference safety gate that Stage 2 of the north star actually needs. Wants the
  taxonomy, which wants ~30 real feedback rows.
- **Model comparison** (roadmap: *LLM evaluation across models*) and the Langfuse
  prompt-versioning idea, both of which need this corpus and a scorer first.
- **Calibrating the fake against `edit_failed` rows** as a scheduled job rather than by
  hand — worth it once there is a stream instead of one row.
- **`SEARCH_MAX_LEN = 200` vs Word's 255.** The fake makes the 55-char gap visible for the
  first time. Whether to close it is a separate decision on its own branch.
