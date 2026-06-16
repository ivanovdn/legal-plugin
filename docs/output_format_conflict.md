# Output-Format Conflict — source audit + decision doc (#2)

> **Purpose.** Decide how to resolve the contract-review output-format conflict.
> This version is anchored to the **source** materials in `data/contract_review_skills/`
> (the legal team's `.docx` + per-type `SKILL.md` + `references/`), not the generated
> bundle. Nothing is changed in code yet.

## Headline

1. **Our build script parses the source faithfully — there is no parsing bug.** Every
   `TABLE_INDEX` mapping was verified against the real `.docx` (MSA→table 4, SOW→5,
   NDA→6, BAA→7, all global tables); section extraction is clean; per-type `SKILL.md`
   are verbatim copies; `shared_operating_rules.md` is byte-identical across all four
   types. (Reassuring: no second hidden cross-type bug like the contract-type one.)
2. **The conflict lives in the source as a two-document ambiguity.** The legal team
   maintains two source documents that each describe output and never cross-reference
   each other.
3. **One side is endorsed, the other is not.** The operating rules + every `SKILL.md` +
   every `test_prompt.md` point to the 7-section table format. The `.docx` §10 schemas
   carry no authority statement and no deferral.

---

## SOURCE 1 — the endorsed format (operating rules)

`data/contract_review_skills/<TYPE> Review Skill/references/shared_operating_rules.md`
→ section **"Required final output format"** (byte-identical across NDA/MSA/SOW/BAA):

```
## Required final output format
Use this structure in every review:

# Review Summary
Overall status: Ready for legal approval / Not ready / Do not send for signature
Contract type:  /  Counterparty:  /  Trinetix role:  /  Version reviewed:  /  Main business context:

# Key Findings
| Issue ID | Clause / section | Rating | Issue | Required action | Owner |

# Red and Missing Context Items
| Issue ID | Type | Clause / section | Why it blocks signature | Required action | Approver / owner |

# Approved Deviations
| Issue ID | Deviation | Approver | Evidence | Final wording checked |

# Suggested Redlines / Fallbacks
| Clause / section | Action | Proposed wording or instruction | External comment |

# Business Questions
| Question | Why it matters | Owner |

# No Signature Checklist Result
Overall status: Ready for signature / Do not send for signature
Blocking items:  /  Missing context:  /  Final recommendation:
```

The opening line — **"Use this structure in every review"** — is explicit and mandatory.
This is the only format `clients/word/src/parser.ts` understands.

## SOURCE 2 — the unendorsed schemas (the `.docx`)

`data/contract_review_skills/Trinetix_Contract_Playbook_2026.docx` → **"10. AI Review
Procedure"**. §10.1 is behavior rules (no conflict — keep). §10.2/10.3/10.4 are three
*additional* output shapes, verbatim:

```
10. AI Review Procedure
The AI should apply the playbook in a structured way. It should not provide final
legal approval. It should identify issues, explain why the issue matters, propose
fallback wording if available, and state whether escalation is required.
    ← no sentence here names 10.2/10.3/10.4 as THE format,
      and no deferral to SOURCE 1.

10.2 AI output schema: issue list
Issue ID (if available): / Document / clause: / Risk rating: / Playbook rule: /
Current wording / issue: / Why it matters: / Recommended action: /
Fallback wording or position: / Escalation required: / Approver: /
External comment: / Internal note:

10.3 AI output schema: executive summary
Overall status: / Top 5 legal risks: / Top 5 commercial/delivery risks: /
Approvals needed: / Missing business facts: / Recommended next step: / Do not sign until:

10.4 AI output schema: SOW readiness check
SOW complete: Yes/No / MSA reference complete: Yes/No / Engagement type: / Billing model: /
Rate card complete: Yes/No / Committed utilization clear: Yes/No /
Acceptance criteria needed: Yes/No / Client dependencies clear: Yes/No /
Payment / expenses complete: Yes/No / Special approvals needed: / Open placeholders:
```

The 7-section table format does **not** appear anywhere in the `.docx`. The §10 schemas
do **not** appear anywhere in the references/SKILL.md/test_prompt.

## What the rest of the source says (all point to SOURCE 1)

- `data/contract_review_skills/<TYPE> Review Skill/SKILL.md` (all 4):
  **"## Required output — Use the required final output format from `references/shared_operating_rules.md`."**
  and review-workflow step 12 *"Give final recommendation using the required output format."*
- `data/contract_review_skills/<TYPE> Review Skill/test_prompt.md` (all 4): asks for the
  SOURCE-1 sections (Review Summary / Key Findings / Red & Missing / Suggested Redlines /
  Business Questions / No Signature Checklist Result).

---

## Where the two sources collide

| Job | SOURCE 1 (endorsed) | SOURCE 2 §10 (unendorsed) |
|---|---|---|
| Overall verdict | `# Review Summary` + `# No Signature Checklist Result` | §10.3 `Overall status` / `Do not sign until` |
| Per-issue findings | `# Key Findings` **table** | §10.2 flat key:value **list** (+ `Internal note`, `Playbook rule` — fields SOURCE 1 has no slot for) |
| Summary | (none — tables only) | §10.3 `Top 5 legal risks` / `Top 5 commercial risks` (different) |
| SOW completeness | covered inside Key Findings; SOW `SKILL.md` rules #2/#9/#13/#14 | §10.4 separate Yes/No checklist |

Same jobs, incompatible shapes — only SOURCE 1 matches the parser.

## How the conflict reaches the model

`build_playbook.py` faithfully generates one file per source and `load_bundle` concatenates
both into the system prompt:
- [build_playbook.py:213-218](scripts/build_playbook.py#L213) → `output_format.md` (from SOURCE 1)
- [build_playbook.py:220-225](scripts/build_playbook.py#L220) → `ai_review_procedure.md` (from SOURCE 2 §10)

So the model reads the endorsed table format and, a few KB later, three competing schemas —
the likely driver of the 14 client-side workarounds in `parser.ts`. The *proper* long-term
fix is for legal to reconcile §10 in the `.docx`; the engineering options below resolve it
in the bundle now.

---

## Resolution options

All three keep §10.1 (behavior rules), `output_format.md`, the per-type `SKILL.md`, and
`_OUTPUT_CONSTRAINTS`. They differ only on the unendorsed §10.2/10.3/10.4. Any code change
is in `_render_ai_review_procedure` ([build_playbook.py:119](scripts/build_playbook.py#L119));
regenerate the bundle after.

- **Option A — drop §10.2/10.3/10.4 in the build script + flag legal.** Render §10.1 only;
  the bundle's sole output spec becomes the team's endorsed 7-section format. This is the
  same canonical-source reconciliation the build already does (it picks references over the
  `.docx` for risk ratings). Also raise a note for legal to reconcile §10 in the `.docx`.
  *Removes the conflict now; no coverage lost (§10.4 ⊂ `sow/SKILL.md`; §10.2/10.3 ⊂ SOURCE 1).*
- **Option B — subordinate §10.2/10.3/10.4 in the build script.** Keep them, but inject a
  header: *"output_format.md above is the REQUIRED format; the schemas below are internal
  reference only."* Preserves `.docx` content verbatim; relies on the model honoring precedence.
- **Option C — source-only fix (no code now).** Change nothing in code; flag legal to
  reconcile §10 in the `.docx`, then regenerate. Strictly faithful to their materials; the
  in-prompt conflict stays live until the source is updated.

**Recommendation: A** — it fully removes the conflict and aligns the bundle with the document
the team designated as operating rules; pairing it with a legal-team note gets the source
reconciled too.

## Verification (for A / B)

- `python scripts/build_playbook.py` twice → idempotent (clean `git diff` on the second run).
- Grep the regenerated `ai_review_procedure.md` → only §10.1 remains (A) or the precedence
  header is present (B).
- Grep the assembled bundle → exactly one authoritative output-format spec.
- `pytest tests/` green, incl. `tests/test_build_playbook.py`.
- Live MSA review → output still parses (parser.ts unchanged).

---

## Decision & change record

**Decided 2026-06-16: Option A.** Implemented on branch `fix/output-format-conflict`.

**What changed**
- `scripts/build_playbook.py` — `_render_ai_review_procedure` now stops at the first
  "AI output schema" subsection (constant `AI_PROC_DROP_FROM`), so generated
  `global/ai_review_procedure.md` contains the §10 intro + §10.1 only.
- Regenerated `skills/contract_review/playbook/global/ai_review_procedure.md` — §10.2/10.3/10.4
  removed (41 lines).
- `tests/test_build_playbook.py` — added `test_ai_review_procedure_drops_competing_output_schemas`.

**Effect on the system prompt.** The contract_review bundle now carries a **single** output
spec: the 7-section format in `output_format.md` (SOURCE 1), reinforced by each per-type
`SKILL.md` and the runtime `_OUTPUT_CONSTRAINTS`. The §10.2/10.3/10.4 schemas no longer reach
the model. Bundle grep confirms `"AI output schema" / "Top 5 legal risks" / "SOW readiness check"`
are absent.

**Rollback.** No source files were edited — `data/contract_review_skills/` is untouched. To
restore §10.2/10.3/10.4: delete `AI_PROC_DROP_FROM` and the `break` that uses it in
`_render_ai_review_procedure` (`scripts/build_playbook.py`), then re-run
`python scripts/build_playbook.py`; the schemas regenerate verbatim from the `.docx`. (Or
`git revert` the commit.)

**Proper source fix (OPEN — for legal).** Reconcile §10 of
`Trinetix_Contract_Playbook_2026.docx`: either mark §10.2/10.3/10.4 as internal-reference-only,
or have §10 defer to the operating-rules output format. Once the source is reconciled this
build-script exclusion can be removed.
