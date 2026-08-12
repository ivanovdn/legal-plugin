# Repo cleanup + `legal_research` module split — design

> Date: 2026-08-12 · Branch: `chore/cleanup-and-legal-research-split` · Status: design, approved for planning

## Why

A project review found the codebase healthy but accumulating drag: 401 backend tests
and 165 frontend asserts all pass, every service runs, but dead code, stale docs, an
unfailable frontend test harness, and a 952-line god module have built up across ~15
feature branches.

This is **Phase C (clean house)** of a two-phase effort. Phase A (what to build next)
follows and is deliberately out of scope here — the one place the two touch is the
roadmap prune, which stops at mechanical de-duplication and leaves re-sequencing to A.

Nothing in this spec changes product behavior. That is the defining constraint: every
change is either a deletion of unreferenced code, a documentation correction, a new
test-only gate, or a verbatim move of a function between files.

## Non-goals

Explicitly **not** in this work:

- Re-sequencing the roadmap or deciding what gets built next (Phase A).
- Touching Chainlit, `drafting`, `compliance_check`, or the `human_review` / resume
  subsystem. The review flagged that these serve a surface which does not deploy to
  the VM; that is a **product** decision, deliberately deferred.
- Any prompt-text change. Isolating `prompts.py` is what makes such a change visible;
  making one here would defeat the purpose.
- Any behavior change inside the moved functions, including obvious-looking tidy-ups
  (see Follow-ups).
- CI. The project runs no CI by choice; the gate is local and pre-push.

## Context: how the affected code is reached

`legal_research` is not a peripheral skill — it is the **entire Word Chat tab**.

```
skill_dispatcher.py:15   SKILL_MAP["research"] → "legal_research"
skill_dispatcher.py:29   SKILL_MAP.get(task_type, "legal_research")   ← default fallback
```

| Path | Pulls in | Surface | On the VM? |
|---|---|---|---|
| `_run_doc_chat` | ~700 lines (prompts, edit parsing, review recall, context) | **Word Chat tab**, Chainlit-with-file | yes |
| `_run_kb_research` | ~90 lines (ReAct + research prompt) | Chainlit only | no |

Word's Chat tab posts `task_type:"research"` with the full document on every turn
(`clients/word/src/api.ts:80`), which makes `intent_router` skip its classifier and
routes straight here. Word's Findings tab does **not** reach this file — that is
`contract_review`.

This traffic profile is why the split is worth doing and why it must be provably
behavior-neutral.

---

## Tier 1 — zero behavior risk

| # | Change | Rationale / evidence |
|---|---|---|
| 1 | Delete `skills/schemas.py` | 86 lines, zero importers (verified by repo-wide grep). Pydantic output models written and never wired up — the ghost of the deferred structured-JSON-review item, which will need a fresh schema anyway. |
| 2 | Move five docs to `docs/archive/` (new dir): `00_PLAN.md`, `01_reliability_floor.md`, `02_persist_findings.md`, `03_msa_playbook_on_chat.md`, `04_context_cap.md` | All four steps shipped. Archived rather than deleted: they hold decision rationale ("two stores, two roles") that the wiki's Shipped rows compress away. Each gains a one-line `> SHIPPED — see wiki "Shipped Since Last Update"` header so they stop reading as forward-looking plans. **Link handling:** `00_PLAN.md`'s own pointers to `01`–`04` are bare relative filenames and stay valid (all five move together). One external inbound link — `docs/superpowers/specs/2026-06-29-chat-memory-grounding-design.md:5` — must be repointed to `docs/archive/`. Verified by grep to be the only one. |
| 3 | Correct `docs/wiki.md` header + architecture diagram | Header claims `380 tests` (actual 401) and `Last updated 2026-07-23` (OTel shipped 2026-08-11). Diagram still reads `Tracing → Langfuse` after the migration that made the backend swappable; becomes `Tracing → OTel (Langfuse local / Phoenix VM)`. |
| 4 | Prune the roadmap table | Drop the ~10 struck-through DONE rows (already duplicated verbatim in Shipped). Merge the three overlapping self-improving-harness rows (NORTH STAR / System-wide harness / Layer 2) into one row with sub-bullets. **Mechanical only** — no re-prioritisation, no re-ordering. |
| 5 | Extract `clients/word/src/testAssert.ts`; migrate all 7 `*.test.ts` files | Today `pass()` is `console.log(cond ? "PASS" : "FAIL")` with no exit code — verified: all 7 files exit `0` regardless of outcome. 165/165 currently green, but a regression would print `FAIL` and pass any gate silently. New helper sets `process.exitCode = 1` on failure. |
| 6 | Add `scripts/check.sh` | `set -e`; backend `uv run pytest tests/ -q`; then `tsc --noEmit`; then each `src/*.test.ts` via `npx tsx`. The only enforceable gate this workflow has room for: the VM pulls from `ado` directly, so nothing sits between a push and production. Documented in CLAUDE.md's Common commands. |

**CLAUDE.md constraint.** The file sits at exactly 150 lines — its own stated cap. The
`scripts/check.sh` line must displace a lower-value line rather than extend the file.

**Tier 1 acceptance:** `scripts/check.sh` exits 0; `grep -r "skills.schemas"` returns
nothing; deliberately breaking one frontend assert makes `check.sh` exit non-zero
(then reverted).

---

## Tier 2 — split `skills/legal_research.py` (952 lines)

### Shape

`skills/legal_research.py` becomes the package `skills/legal_research/`. This **matches
the existing house convention** — `skills/contract_review/__init__.py` and
`skills/contract_generation/__init__.py` are both a single re-export line — so it is
not a backwards-compat shim (CLAUDE.md rule 5) but the pattern already in use.

```
skills/legal_research/
  __init__.py          from skills.legal_research.legal_research import legal_research
  prompts.py      ~110   PURE   model-facing constants only
  edit_parsing.py ~190   PURE   LLM prose → structured edits / preferences
  review_recall.py ~265  PURE   stored review → safe injectable block
  context.py      ~150          memory loads, grounding gate, budget cap
  legal_research.py ~260        LLM builders + the two run paths + node entry
```

`graph/graph.py:25` (`from skills.legal_research import legal_research`) stays
**byte-identical** — it is the only production importer.

### What moves where

Source line numbers are from the current `skills/legal_research.py`.

**`prompts.py`** — four constants, no imports, no logic:

| Symbol | From | Consumed by |
|---|---|---|
| `RESEARCH_SYSTEM_PROMPT` | :30 | `legal_research.py` (`_build_agent`) |
| `CHAT_SYSTEM_PROMPT` | :59 | `legal_research.py` (`_run_doc_chat`) |
| `_CHAT_MSA_NOTE` | :102 | `context.py` (`_build_chat_grounding`) |
| `_JSON_RETRY_SYSTEM` | :161 | `legal_research.py` (JSON retry) |

**`edit_parsing.py`** — depends only on `json` + `re`:
`_parse_json_edits` (:145), `_JSON_BLOCK_RE`/`_PREFERENCE_BLOCK_RE` (:219–220),
`_escape_unescaped_whitespace_in_strings` (:223), `_tolerant_json_loads` (:254),
`_iter_json_values` (:267), `_flatten_edit_values` (:296), `_EDIT_PROMISE_RE` (:318),
`_looks_like_edit_promise` (:326), `_VALID_ACTIONS` (:334),
`_extract_proposed_edits` (:337), `_extract_proposed_preferences` (:365).

Public surface: `_extract_proposed_edits`, `_extract_proposed_preferences`,
`_looks_like_edit_promise`, `_parse_json_edits`. This is the Python mirror of the
frontend's `parseEditBlocks.ts`.

**`review_recall.py`** — depends only on `re` + `unicodedata`:
`_strip_redlines_section` (:381), the placeholder machinery (:416–459),
`_reconcile_review_with_doc` (:460), the blocker-count machinery (:537–584),
`_reconcile_gate_verdict` (:594).

Public surface is just **two** functions: `_strip_redlines_section` and
`_reconcile_review_with_doc`. `_reconcile_gate_verdict` is called *inside*
`_reconcile_review_with_doc` (:523), so the gate machinery is entirely internal —
a small public surface over a lot of hidden machinery, which is what makes this a
real module boundary rather than an arbitrary cut.

**`context.py`** — assembles the chat prompt's context within budget:
`_load_prior_review_block` (:637), `_load_prior_conversation` (:674),
`_GROUNDING_TRIGGER_RE`/`_needs_grounding` (:695, :720), `_build_chat_grounding` (:730),
`_cap_chat_context` (:752).

**`legal_research.py`** — orchestration only: `_build_llm` (:112), `_build_json_llm`
(:126), `_build_agent` (:179), `_extract_uploaded_text` (:205), `_run_doc_chat` (:773),
`_run_kb_research` (:866), `legal_research` (:909).

### Dependency graph (acyclic)

```
prompts.py         → (nothing)
edit_parsing.py    → json, re
review_recall.py   → re, unicodedata
context.py         → prompts, review_recall, config, graph.state,
                     memory.review_store, memory.conversation_store, skills.grounding
legal_research.py  → prompts, edit_parsing, context, config, graph.state,
                     langchain_ollama, langgraph.prebuilt, rag.tools, observability
```

Three of the five modules import nothing from this project, so ~565 lines become
dependency-free text transforms that test in milliseconds.

### The one real gotcha: `unittest.mock.patch` targets

`tests/test_skills.py` patches `"skills.legal_research._build_agent"` in four places.
After the split, `skills.legal_research` is a **package** whose `__init__.py` re-exports
only `legal_research` — so that target no longer resolves and those patches fail.

Targets become `"skills.legal_research.legal_research._build_agent"`.

The stutter is accepted for consistency with `skills.contract_review.contract_review`
and `skills.contract_generation.contract_generation`, which already read this way.

**Rejected alternative:** re-exporting the private helpers from `__init__.py` to keep
the old patch strings working. That is precisely the backwards-compat shim CLAUDE.md
rule 5 forbids; the call sites change instead.

### Test migration rule

Tests move with the module they cover, and import from its new path:

| Test file | New import target |
|---|---|
| `test_stale_recall_reconciliation.py` | `review_recall` |
| `test_skills.py` (edit-block cases) | `edit_parsing` |
| `test_skills.py` (prompt-text assertions) | `prompts` |
| `test_skills.py` (`_build_agent` patches, entry behavior) | `legal_research.legal_research` |
| `test_preference_suggestion.py` | `edit_parsing` |
| `test_legal_research_conversation.py` | `context` (+ entry) |
| `test_preferences_chat_injection.py` | `legal_research.legal_research` (`_run_doc_chat`) |
| `test_observability.py`, `test_audit.py`, `test_graph.py` | unchanged — they use the package-level `legal_research` |

Test **count and assertions stay identical.** A test that needs rewriting rather than
re-importing means something moved that should not have.

---

## Verification

### 1. Automated — must be green before the smoke

`scripts/check.sh`: 401 backend tests + `tsc --noEmit` + 165 frontend asserts.
The suite already covers every moved helper directly, so a rename or dropped line
turns it red.

### 2. Prompt byte-identity

A one-off check that each of the four prompt constants hashes identically before and
after the move. Mechanical proof that no prompt byte changed — the risk that would
otherwise silently invalidate every past eval observation.

### 3. Sideload smoke in Word (the real gate)

Backend-only change, so no add-in rebuild — but `uvicorn` does not auto-reload, so
`bash scripts/start.sh` must be restarted first.

| # | Action | Proves |
|---|---|---|
| 1 | Findings tab → Review a real NDA | `contract_review` path untouched |
| 2 | Chat: *"who signs this?"* | entry path + `_needs_grounding` lean branch (no playbook attach) |
| 3 | Chat on a SOW: *"does this conflict with the MSA?"* | `context.py` — grounding gate fires, playbook + governing MSA attach |
| 4 | Chat: *"fill the signature block with Suzy Quatro"* | `edit_parsing.py` — PROPOSED EDIT card renders and **Apply** writes a tracked change |
| 5 | Chat after step 1: *"what were the findings?"* | `review_recall.py` — prior review injected, reconciliation intact |
| 6 | Inspect the trace in Phoenix/Langfuse | span structure unchanged |

Steps 2–5 are chosen so that each one exercises a different extracted module; a
failure localises immediately.

## Risks

| Risk | Mitigation |
|---|---|
| A moved function is silently altered | Move verbatim; the 401 tests cover each helper directly; prompt constants hash-checked |
| Broken `patch()` targets | Known and enumerated above; caught by the test suite, not by production |
| Import cycle between new modules | Dependency graph verified acyclic before the move; `prompts`/`edit_parsing`/`review_recall` import nothing from this project |
| Circular-import at package init | `__init__.py` imports exactly one name from one submodule, as the two sibling skill packages already do |
| Reviewer cannot tell a move from an edit | Split lands as its own commit, separate from Tier 1, with no other changes in it |

## Follow-ups (deliberately not in this work)

- **Duplicated document-wrapper string.** The `--- ATTACHED DOCUMENT … ---` block is
  built in both `_run_doc_chat` and `_cap_chat_context`. Hoisting it to one constant is
  safe and desirable, but it changes bytes inside a move-only commit and would blur what
  the smoke test proves. Separate commit, after this lands.
- **Chainlit / `drafting` / `compliance_check` / `human_review` resolution** — the
  non-deployed branch identified in the review. Product decision, feeds Phase A.
- **`compliance_check` has no playbook** and can return ungrounded pretraining output at
  low risk (unlike `drafting`, which `route_risk` always gates through `human_review`).
  Raise in Phase A.
- **Phase A proper** — sequencing the pruned roadmap; the review's headline finding was
  that no eval harness exists, which gates Stage 2 of the self-improving-harness north star.
