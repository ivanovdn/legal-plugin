# Legal Agent — Upgrade Research

> Date: 2026-07-07 · Status: research capture (no build committed) · Author: brainstorm session

## How this started

A "what's next" brainstorm that began from a concrete question — *"can we use
`gemma4:31b-coding-mtp-bf16`?"* — and widened through: model evaluation → LLM-as-judge →
competitor UX/logic ideas (incl. the `Open-Legal-Products/mike` repo) → **adding a
self-improving ("self-harness") agent-improvement loop**, which is the through-line the
user most wants.

## TL;DR

1. **`gemma4:31b-coding-mtp-bf16` is a no** — 64 GB weights won't fit the 48 GB M4 Pro, and
   it's the *coding* variant (wrong domain). The realistic challenger is **`gemma4:26b`
   (MoE, 3.8B active) @ Q5** — fits (~18 GB) and decodes fast. Don't swap by vibes; route it
   through an eval.
2. **"Self-improving" under "SKILL.md is the ceiling" ≠ the model rewriting its own prompts.**
   It means a **data-and-eval flywheel**: measure → curate → gate → auto-tune only the
   *engineering* seams → route legal-prompt changes to humans (versioned, eval-gated).
3. **It's all one machine.** The Gemma A/B, the cloud judge, and the reliability ideas from
   the competitor sweep are the *experiment*, the *judge*, and the *signals/gates* of the same
   self-harness.
4. **Highest-leverage first step: R1 — a confidence/disagreement signal bus into Langfuse.**
   Cheap, ceiling-safe, and it's the raw material everything else needs.

---

## 1. The model question (Gemma 4 on a 48 GB M4 Pro)

| Tag | Verdict | Why |
|---|---|---|
| `gemma4:31b-coding-mtp-bf16` | ❌ | ~64 GB weights (30.7B × bf16) > 48 GB RAM; also *coding*-specialized (legal review is not a coding task) |
| `gemma4:26b` (MoE, 3.8B active) @ **Q5_K_M** | ✅ **best challenger** | ~18 GB weights + ~5 GB KV @ 32k → ~25 GB headroom; memory set by total (26B), speed set by active (3.8B) → fast decode/prefill, targets the ~30 s grounded-turn pain |
| `gemma4:12b` | ✅ | Smaller, general/instruct, easy fit |
| `gemma4:31b` dense, quantized | ⚠️ | Fits at Q4 (~18 GB) but dense-31B is the slow option |
| `gemma4:31b-cloud` | 🚩 | Runs off-machine → re-opens the attorney-client-privilege / data-residency gate |

Rules: use the **instruct/general** variant, not `-coding`; **Q5/Q6** quant (precision high
enough to limit improvisation, but memory-bound so bf16 is out); **evaluate, don't vibe-swap**
— an ad-hoc model change could silently regress the reliability work, and fixes must stay
**model-neutral** per `feedback-fix-in-code-not-prompt`.

---

## 2. Ideas from `Open-Legal-Products/mike`

`mike` is a **different product** — a case-**law research** assistant (chat with legal docs +
CourtListener case-law citation lookup), cloud-first (Next.js + Supabase + Cloudflare R2),
**user-selectable models** (Anthropic/Gemini/OpenAI). Not a contract-review / redline / Word
tool. Transferable value is UI/architecture patterns, not legal logic:

- **Matter/project organization** *(M)* — `mike` organizes by project → documents. Ours is
  per-single-document. A matters layer (client → matter → docs → reviews) is the home for the
  roadmapped *cross-matter precedent recall* and chat-history persistence.
- **Model-selection settings panel** *(S)* — `mike`'s "Models & API Keys" surface = the UX
  face of the A/B harness (config-driven model registry; stays local for us).
- **Local bulk-cache for citations** *(M)* — `mike` bulk-loads case-law JSON locally to cite
  without live API calls: an air-gap-friendly grounding pattern if we ever want statute/reg
  citations beside the playbook.
- **A real web dashboard to replace Chainlit** *(L)* — if it's `mike`'s clean look you like,
  the highest-leverage move is upgrading the Chainlit web client into a proper
  matters → docs → findings → chat dashboard.

---

## 3. Competitor landscape — ideas worth stealing

Surveyed: Spellbook, Harvey, Robin AI, Luminance, Legora, Ironclad, LawGeex, Diligen (+ OSS:
llmware, OpenContracts, `legal-redline-tools`, `claude-legal-skill`, `ally-legal-assistant`,
`redline-llm`). Already-shipped features excluded. Effort S/M/L; all fit air-gap unless noted.

### Group 1 — Word add-in / redline UX
1. **"Go to clause" jump + click-to-highlight from any finding** *(S–M)* — reuse
   `findClauseRange`/`searchCandidates` in `clients/word/src/word.ts` to `range.select()`+scroll
   on click. Best UX-per-effort in the list.
2. **Per-suggestion rationale card (what changed / why / which playbook rule)** *(S–M)* — add
   `rationale`+`playbook_ref` to the edit schema (`skills/legal_research.py` /
   `parseEditBlocks.ts`), render on the edit card. Makes every edit auditable; reinforces the
   ceiling by making grounding visible.
3. **"Ask / redline this selection"** *(M)* — act on `context.document.getSelection()` instead
   of whole-doc. Smaller context = faster on Ollama.
4. **Graduated redline options + posture control (General vs Negotiate; represented party)**
   *(M; multiplies local latency — flag it)*.
5. **Bulk actions + presentation toggle (apply-all / accept-many / tracked-vs-comment-vs-clean)**
   *(S)* — frontend on top of `applyEdit`/`replaceAll`; a comment-only first-pass mode is valuable.

### Group 2 — Review presentation
6. **Playbook-as-checklist: rules as pass / fail / not-addressed + coverage % + evidence span**
   *(M)* — surfaces *missing* clauses (gap-completeness), strengthens the No-Signature Gate,
   cleaner substrate for `deriveBlockers`. Maps 1:1 to the ContractNLI dataset format.
7. **"Your standard says X vs contract says Y" deviation panel** *(M — the Spellbook
   differentiator)* — we already hold preferred positions in the playbook bundle
   (`skills/grounding.py`); surface `{playbook_preferred, contract_actual, deviation}` per
   finding, render side-by-side. Highest strategic payoff.
8. **Findings filter/sort chips (severity, owner, blockers-only, clause type)** *(S)* —
   frontend on `parser.ts` output.
9. **Live step-transparency during the review run** *(S–M)* — coarse per-node status from the
   graph to the pane; attacks perceived latency (the local-LLM tax).

### Group 3 — Chat & logic
10. **Inline citations + click-to-source highlight in chat** *(M)* — model tags claims with the
    source id we already feed it; reuse #1's matcher to highlight on click. Biggest
    anti-hallucination/trust win for an unverifiable local model.
11. **Fallback-position ladder + auto-compromise** *(M–L)* — propose the firm's pre-approved
    fallback ("our fallback here is 2× cap"), not just "reject." Needs the playbook to encode
    fallbacks per clause (extend `scripts/build_playbook.py`). The clearest step from
    *risk-flagger* → *negotiation assistant*; hard for cloud tools to beat on a private playbook.
12. **Multi-round / counterparty-edit awareness** *(L)* — read the tracked-change *set* + authors,
    classify ours-vs-counterparty, review "what changed since last round." Robin's headline feature.

### Group 4 — Infra / product-logic
13. **Post-generation evidence/grounding verification node** *(M — top reliability pick)* — after
    the LLM emits findings, a code step verifies each quoted "current text" appears in the doc and
    each cited rule resolves; drop/flag the rest. Slots into the graph after `attorney_review`.
    Pure local, model-neutral, textbook "fix in code not prompt" — kills the hallucinated-quote
    class already hit (the `body.text` false-placeholder bug). **Also a self-harness signal.**
14. **Deterministic threshold rule engine over LLM findings** *(M)* — hard numeric gates in code
    (cap < N months → red, notice < N days → critical, uncapped indemnity, auto-renewal). Local
    models are unreliable at numeric comparison; reconcile into findings like `deriveBlockers`
    reconciles counts.
15. **Structured JSON output for `contract_review`** *(M–L — the keystone)* — validated typed
    findings; the enabler under #2/#6/#7/#8/#10. Already on the roadmap. Pairs with an offline
    eval harness using **CUAD** (510 contracts, 41 clause types), **ContractNLI** (607 NDAs;
    entailed/contradicted/not-mentioned + evidence), **LEDGAR** (80k provisions; cheap clause
    classifier).

### Group 5 — Knowledge base / research grounding
16. **Legal-reference-corpus RAG (research lane, cited, click-to-source)** *(M; air-gap: perfect)*
    — ingest a corpus of legal reference material (statutes, regulations, case law, practice
    guides, treatises) and let the **research/chat** path retrieve, cite, and *show* the relevant
    passages to the user — the `mike`/Harvey/Legora experience, done locally.
    - **You already have the rails** — this is an *ingest + surface* task, not new infra:
      the **`legal_docs` Qdrant collection** already holds *"Contracts, legislation, templates,
      policies"*; the **ingest pipeline** parses PDF/DOCX (with the lossless extractor from
      Fix #5); **`legal_research.py`** already runs a **ReAct RAG agent** with
      `search_legal`/`get_document`; citations already use `doc_id`/`doc_title` (hard rule #4).
    - **Display:** ride on **#10** (inline citations + click-to-source highlight) for the
      "According to [Treatise X §Y]…" → jump-to-passage UX; optionally a Legora-style
      **source-scope selector** (Playbook / Reference corpus / This document).
    - **Draws from:** `mike` CourtListener + **local bulk-cache** (air-gap-friendly grounding,
      no live API); Legora multi-scope "Sources"; OpenContracts grounded citations.
    - **⚠️ Ceiling separation (non-negotiable):** legal-books RAG lives in the **research/chat**
      lane ONLY — **never** in `contract_review` findings. The review must stay playbook-grounded
      ("SKILL.md is the ceiling"); letting it cite external treatises reintroduces the
      non-playbook-opinion / improvisation class already logged. *Research answers* may pull from
      the corpus; *review verdicts* stay on the playbook.
    - **⚠️ Licensing:** statutes / regulations / case law are safe to ingest; commercial
      treatises (Westlaw/Lexis/etc.) are copyrighted — a content-licensing decision, not a
      technical one.

**Top 5 picks:** ①#13 evidence-verification node · ②#7 deviation panel · ③#1+#2 go-to-clause +
rationale card · ④#6 playbook-checklist w/ coverage% (needs #15) · ⑤#11 fallback ladder.
Honorable mention: **#16 legal-reference-corpus RAG** — biggest *scope-expanding* idea (turns the
tool from contract-reviewer into a research assistant too) and runs on existing RAG rails.

**Strategic observation:** *no* surveyed commercial tool ships a truly air-gapped/local
deployment — all are cloud + third-party models, mitigated only by contract. Our
local-Ollama, zero-egress posture is a genuine moat. The directly-adaptable *engineering* lives
in the open-source local-first stack (llmware, OpenContracts, CUAD/ContractNLI/LEDGAR), which
shares our constraint.

---

## 4. Self-improving harness — the plan

### The reframe (what "self-improving" can mean under the ceiling)

**The model cannot rewrite its own legal brain.** Every instruction-rewriting optimizer (OPRO,
TextGrad, GEPA, PromptBreeder, MIPRO's instruction stage) mutates the task instruction — i.e.
the canonical playbook the legal team owns — forking truth from the `.docx` and breaking the
ceiling. (They also degrade on small local models; reflection needs a strong reflector we lack
air-gapped.) So self-improvement = a **data-and-eval flywheel**:

> **measure** cheap signals → **curate** a golden set (active learning + legal labeling) →
> **gate** every change behind offline eval → **auto-tune only the engineering seams** →
> **route legal-prompt improvements to the legal team** (human-in-the-loop, versioned,
> never auto-applied).

The system improves continuously; the legal prompt changes only through a human-owned,
eval-gated path.

### Boundary — off-limits vs OK for automated change

| Seam / component | Owner | Auto-optimize? |
|---|---|---|
| Playbook bundle text (`skills/contract_review/playbook/`, from the `.docx`) | Legal | **NO — the ceiling.** Human change via `build_playbook.py`; eval-gated; versioned |
| Per-type `SKILL.md` clause rules / source position | Legal | **NO** |
| `CHAT_SYSTEM_PROMPT` legal-behavior / SCOPE rules | Eng (behavior spec) | **NO auto-rewrite** — keep principle-based, human-owned |
| Intent router / `task_type` classification | Eng | **YES** — DSPy demo-selection, eval-gated |
| Retrieval query formulation (`rag/search_legal`) | Eng | **YES** — demo-selection |
| Edit-block extraction/normalization | Eng | **YES** — heuristic tuning from mined failures |
| Contract-type detection heuristic | Eng | **YES** — tune keywords/thresholds from labels |
| Anchor-matcher threshold (85% guard), grounding-gate keyword list | Eng | **YES** — tune from labeled apply/miss & needs-grounding outcomes |

Rule of thumb: **legal judgment is frozen; retrieval/routing/parsing plumbing is fair game** —
and only via *demonstration/threshold* tuning, never instruction rewriting.

### Recommendations (R1–R7)

- **R1 — Confidence/disagreement signal bus into one sink ★ highest leverage** *(S–M, low risk)*
  — every brittle heuristic emits a structured Langfuse *score* on the trace: detected-vs-model-
  stated contract-type; edit-parser fallback rung (clean → tolerant-JSON → stacked/array →
  `format='json'` → lossy); anchor match-ratio / wildcard-retry / refusal; grounding attach-skip +
  truncation; **improvisation rate** (findings citing no known rule ID). Inference-time logging,
  zero model change, engineering seams only.
- **R2 — Golden dataset + offline regression harness that gates *every* change** *(M, low–med)*
  — 30→100+ labeled non-privileged/synthetic contracts in Langfuse Datasets; Experiments in
  CI. Tier the checks: deterministic assertions first (sections present, blocker reconciliation,
  JSON parse, type match, improvisation ceiling), judge only for subjective quality. A change
  ships only if it passes — **including legal-team playbook rebuilds** (their safety net).
- **R3 — Validated LLM-as-judge (cloud offline for synthetic, local for privileged)** *(M, med)*
  — binary decomposed checklist rubric, per-dimension isolated judges (not Likert). Lane 1:
  **strong cloud judge OFFLINE on synthetic/non-privileged only** (privileged text never leaves).
  Lane 2: local Ollama judge for privileged data. **Validate the judge vs 100+ legal labels
  (TPR/TNR) before trusting it.** Consider a cheap diverse **panel (PoLL)**. Eval-time only,
  never the live path. Gotchas: guarantee only sanitized data reaches the cloud judge; the local
  judge must emit OpenAI-style tool-calling structured output.
- **R4 — DSPy demo-optimization of engineering seams only** *(M–L, med; lower priority)* —
  `BootstrapFewShot`/`LabeledFewShot`/`KNNFewShot` (select/bootstrap demos, never rewrite
  instructions) on router / retrieval / edit-extraction / type-classifier. Metric = R2/R3 suite.
  Freeze the legal playbook module out of scope. Do only after R1–R3 exist to measure benefit.
- **R5 — Trace mining → failure taxonomy → prioritized labeling queue (active learning)** *(M, low)*
  — open-code the first upstream failure, axial-code a taxonomy, cluster high-disagreement traces
  from R1, prioritize labeling by frequency + uncertainty. Legal experts label via Langfuse
  Annotation Queues → grows R2, aligns R3. Expect rubric/criteria drift.
- **R6 — Prompt-version ↔ trace ↔ score tracking + human-approval gate for legal changes** *(M, low–med)*
  — version the assembled bundle in Langfuse Prompt Management; link versions to traces. Legal
  changes: prompt-version webhook → CI regression → green **+ legal sign-off** → only then move
  the `production` label. Caveats: prompt→generation linking is flaky through raw LangGraph nodes
  (tag metadata on the trace as fallback); protected labels / RBAC are Enterprise-only (enforce
  in CI otherwise).
- **R7 — Explicit NON-recommendation: no inference-time self-critique / reflection / self-consistency
  on the live path.** Self-consistency needs temp > 0 (no-op at our temp=0). Intrinsic
  self-correction empirically *degrades* accuracy; "are you sure?" flips answers via sycophancy.
  On a weak local model at temp=0 this is worst-case latency + regressions. Reflection is allowed
  **offline** only (draft candidate labels / failure explanations). The only inference-time
  verifier to keep is the **cheap deterministic** one (#13/#14) — external check, not self-critique.

### 3-phase build order

- **Phase 1 — Measure & gate (ship first):** R1 signal bus + R2 v0 (deterministic-only regression
  in CI) + R6 v0 (version bundle, link to traces). **The Gemma 4 A/B is R2's first experiment.**
- **Phase 2 — Judge + flywheel:** R3 validated judge; R5 trace-mining → annotation queue → grow
  the golden set; R6 full human-approval gate.
- **Phase 3 — Bounded auto-tune:** R4 DSPy demo-optimization of engineering seams, gated by Phase-2.

**Single highest-leverage first step: R1** — converts traces already collected into a
labeled-failure feed; makes R2/R3/R5 possible; ceiling-safe; already on the roadmap.

---

## 5. The convergence — it's one machine

| Discussed as | Role in the self-harness |
|---|---|
| Gemma 4 A/B + "free" metrics (improvisation rate, structural validity, latency) | First **experiment** on the regression harness (R2) |
| Cloud judge on synthetic/CUAD only | **R3** — validated cloud-offline judge (matches best practice: validate vs labels, binary rubric, model panel) |
| Evidence-verification node, threshold engine, structured output, improvisation rate | The **signals + gates** the harness runs on |
| `mike` matter-org + model-picker | Later **UX surfaces** (the model-picker is the A/B's face) |

The reliability ideas are not a separate track — they are the **substrate** that makes
self-improvement measurable.

---

## 6. Recommended next step + open decisions

- **Recommended first milestone:** *Self-harness Phase 1* = **R1 signal bus + R2 v0 deterministic
  regression harness**, with the **Gemma 4 A/B as its first experiment**. Cheap, ceiling-safe,
  unblocks everything.
- **Open decision — cloud-judge data boundary** (only relevant when R3 lands): synthetic/CUAD-only
  (start anytime) vs real client contracts (needs an explicit attorney-client-privilege /
  data-residency sign-off — the initiative-level gate).
- **Open decision — first milestone scope:** just R1 · R1+R2 v0 (recommended) · or add R3 now.

---

## Sources (external research)

- **Products/UX:** spellbook.com/features (Review, Benchmarks, Playbooks); help.harvey.ai
  (Word, review tables); robinai.com (Word add-in, playbooks); luminance.com (Agent Lumi);
  legora.com/product; Ironclad (AI Playbooks, Precise Redlining); lawgeex.com/cra; diligen.com.
- **OSS:** Open-Legal-Products/mike; llmware-ai/llmware; Open-Source-Legal/OpenContracts;
  evolsb/legal-redline-tools + claude-legal-skill; Azure-Samples/ally-legal-assistant;
  MarcusElwin/redline-llm; TheAtticusProject/cuad.
- **Evals/judge:** Hamel & Shankar "Evals FAQ" + error-analysis field guide; Anthropic
  "Demystifying evals"; MT-Bench (arXiv:2306.05685); G-Eval (2303.16634); PoLL (2404.18796);
  judge-bias survey (2604.23178).
- **Prompt-opt:** DSPy (2310.03714) + optimizer docs; GEPA (2507.19457); OPRO (2309.03409) +
  small-model critique (2405.10276); TextGrad (2406.07496); PromptBreeder (2309.16797).
- **Reflection/limits:** Reflexion (2303.11366); self-consistency (2203.11171); self-correction
  limits (2310.01798); FlipFlop (2311.08596); CoVe (2309.11495).
- **Trace mining / UQ:** DiscoUQ (2603.20975); Beyond Logprobs (2606.24420); EvalGen (2404.12272);
  Langfuse Datasets/Experiments/Prompt-Management/Annotation-Queues docs.
- **Datasets:** CUAD, ContractNLI, LEDGAR.
