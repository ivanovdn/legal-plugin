# Context Budget and Loud Truncation — Design

**Status:** ready to implement (slice 1)
**Date:** 2026-08-21
**Branch:** `feat/context-budget`

## Why

Two independent, silent, currently-live bugs cause the agent to answer legal
questions about text it has never seen. Neither is a model-quality problem.
Both are assembly/config bugs of the same class as the `body.text` →
`getReviewedText` fix: *the model reads exactly what we send it, and we were
sending the wrong thing.*

### Bug 1 — we truncate the contract, in our own code

`_cap_chat_context` ([skills/legal_research/context.py](../../../skills/legal_research/context.py))
enforces `chat_context_max_chars = 100_000` by shortening **only the document**,
never the grounding:

```python
keep = max(0, len(uploaded_text) - overflow - len("...[document truncated...]"))
```

Measured by running that function on the real `data/Trinetix Model MSA 2025 (3)-1.docx`
(84,859 chars of extracted text) with the real MSA playbook bundle (38,587 chars):

| scenario | assembled | document the agent sees |
|---|---|---|
| turn 1, **zero history** | 135,281 | 49,592 / 84,859 = **58%** |
| turn 10, 20-message history | 165,281 | 19,592 / 84,859 = **23%** |

It is a *tail* cut (`uploaded_text[:keep]`), so what is discarded is the back of
the contract: limitation of liability, indemnification, term and termination,
governing law, signature blocks — the clauses the playbook weights heaviest and
the No-Signature Gate depends on. `max(0, ...)` means under enough pressure the
document reduces to a marker and the turn still proceeds.

The only signal is a `logger.warning` in the backend log. Nothing reaches the
attorney.

### Bug 2 — Ollama truncates the review, because we ask for too small a window

`contract_review` has **no context cap at all**; only the MSA excerpt is bounded
(`msa_max_chars`). With `ollama_num_ctx = 32768` and
`ollama_num_predict_review = 8192`, the input must fit in **24,576 tokens**.
A measured MSA review input (playbook + full document) is **25,270 tokens**.

It overflows, and Ollama silently middle-drops — which, per CLAUDE.md, removes
"exactly the playbook/MSA". So an MSA review today runs under-grounded, by a
different mechanism than bug 1 and with no log line at all.

### The cause of both: a stale default

`ollama_num_ctx = 32768` was never a hardware limit.

## Evidence

All figures measured 2026-08-21 against the live Spark box, not estimated.

**Spark `172.20.0.22`** — DGX Spark (self-identifies as "DGX Spark AI (Open WebUI)"),
GB10, Ollama 0.21.2, `qwen3.6:latest` (36B Q4_K_M, hybrid SSM/attention MoE,
`context_length` 262144).

| measurement | value |
|---|---|
| model resident **as found** | `ctx=131072`, 30.28 GB |
| weights on disk | 23.94 GB |
| KV + overhead @ 131,072 | **6.34 GB** |
| KV cost | **49.5 MB per 1k tokens** |
| prefill | **1,397 tok/s** |
| generation | **51.4 tok/s** |
| model load | 3.6–4.7 s |

KV cost is low because the model is a **hybrid SSM/attention MoE**:
`full_attention_interval=4` over `block_count=40` means ~10 attention layers,
and the 30 SSM layers hold a constant-size state (`ssm.state_size=128`)
regardless of sequence length. Even the full 262,144 window costs ~12.7 GB.
**Memory is not a design constraint at any window we would want.**

### The box already serves 131,072; we ask for 32,768

```
before:  ctx=131072  (30.28 GB)
--> request with num_ctx=32768, exactly as _build_llm sends it
after:   ctx= 32768  (27.07 GB)     load_duration=4.7s
```

Ollama reloads whenever the requested `num_ctx` differs from the resident
model, **in either direction**. So our app actively downgrades a correctly
configured box and pays 4.7 s to do it — and contends with any other consumer
of that box, flipping the model back and forth.

**Therefore the fix is not "make it bigger" — it is "make it exactly equal to
what the server loads."** 65536 would thrash just as badly as 32768.

### Real token accounting

Full MSA playbook + the entire untruncated Trinetix MSA + a question:

```
123,612 chars = 25,270 real prompt tokens   →   4.89 chars/token
prefill 18.1s + generation 3.9s (200 tok)   =   26.5s wall
```

Two consequences:

1. **`4.89` chars/token, not 4.0.** `config.py:73`'s comment assumes `chars/4`.
   Every char↔token conversion in the codebase is ~22% pessimistic.
2. **Seeing 100% of the contract instead of 58% costs ~3.4 seconds** (18.1 s
   prefill vs ~14.7 s for the truncated 20.5k-token prompt). Eliminating the
   reload thrash refunds 4.7 s. The fix is net faster than the bug.

### Backend comparison (context for follow-up slice 2 — not adopted here)

vLLM on `172.20.0.23`, identical 25k-token real-MSA turn:

| endpoint | model | `max_model_len` | prefill | decode | wall |
|---|---|---|---|---|---|
| Ollama `.22:11434` | qwen3.6 36B Q4_K_M | 262,144 | 1,397 t/s | 51.4 t/s | 26.5 s |
| vLLM `.23:8269` | Qwen3-14B | 40,960 | 1,316 t/s | **7.5 t/s** | **72.1 s** |
| vLLM `.23:8267` | Qwen3-Coder-Next NVFP4 | 262,144 | **2,805 t/s** | 59.6 t/s | **15.6 s** |

Qwen3-14B is unusable — 7.5 tok/s decode, 7× slower than either alternative
despite being the smallest model, which points at a deployment fault
(unquantized weights or memory contention) rather than the model. On the review
path at `num_predict_review=8192` it would take ~18 minutes.

Qwen3-Coder-Next is 41% faster wall-clock with 2× the prefill rate, and both
vLLM models produced correctly-formatted playbook output on the first attempt.
**This slice does not adopt either.** Both are coder- or small-tuned, "SKILL.md
is the ceiling" is the one property this project cannot afford to lose, and
Tier 1 evals are zero-LLM by design and would not catch that regression.
Deferred to slices 2 and 3 in Follow-ups.

## Non-goals

- **Changing the model or inference backend.** Slice 2.
- **A client-side context gauge.** Slice 3 — depends on this slice's payload.
- **History compaction / summarization.** Slice 4. This slice makes it a
  *choice* rather than a necessity by removing the starvation.
- **Cross-session recall.** Slice 5.
- **Fixing Qwen3-14B's deployment.** Not ours; recorded only.
- **Retiring `_cap_chat_context`.** A budget must still exist; this slice
  raises it, changes what it sacrifices, and makes it audible.
- **Auto-tuning the budget from measured throughput.** The derivation is
  documented so retuning is arithmetic. No runtime tuner.

## The change

### 1. Pin `num_ctx` to the server's window

`config.py:59` — `ollama_num_ctx: 32768 → 131072`.

It is a declared pydantic-settings field, so deployments can override without a
code change:

```bash
# .env
OLLAMA_NUM_CTX=131072
```

`get_settings` is `@lru_cache`'d → requires `bash scripts/start.sh` restart.

Three consumers, all already reading the one field — no new call sites:

| site | use |
|---|---|
| `skills/legal_research/legal_research.py:60` | `_build_llm` → `ChatOllama(num_ctx=…)` |
| `skills/legal_research/legal_research.py:80` | `_build_json_llm` (the JSON retry LLM) |
| `graph/nodes/llm_caller.py:107` | `"num_ctx"` in the httpx `options` dict |

This alone fixes bug 2 (review input 25,270 tokens now sits far inside
131,072 − 8,192) and removes the 4.7 s reload thrash.

**Coupling to record:** pinning couples us to the box's
`OLLAMA_CONTEXT_LENGTH`. If the service is retuned, we get reload thrash. That
is the correct failure mode — a visible 4.7 s penalty rather than silent
truncation — but it must be documented in `config.py` beside the value.

### 2. Raise the chat budget, and document its derivation

`config.py:73` — `chat_context_max_chars: 100_000 → 150_000`.

The number is derived from the agreed **30 s** turn ceiling, not chosen:

```
answer      400 tok ÷ 51.4 tok/s  =  7.8 s
remaining        30 − 7.8 = 22.2 s × 1,397 tok/s  =  31,013 tokens
                                  × 4.89 chars/tok  ≈  151,653  →  150,000
```

What 150,000 buys:

| | chars |
|---|---|
| Trinetix MSA, **complete** | 84,859 |
| MSA playbook bundle, complete | 38,587 |
| prior review block | ~5,000 |
| **subtotal** | **128,446** |
| left for history | **~21,500** (~4,400 tok ≈ ~14 messages) |

So the full contract, the full playbook, and roughly today's history depth all
fit at today's latency. The comment must be replaced: it currently claims
`chars/4` and cites the 32,768 window, both now wrong.

**Note the constraint has moved.** 150,000 chars ≈ 30,675 tokens; plus 2,048
output that is ~32.7k against a 131,072 window — 4× headroom. The window is no
longer binding; **prefill latency is.** That is why the budget is not simply
set to the window, and why compaction (slice 4) is about bounding prefill
rather than about fitting.

### 3. Make truncation loud

Today `_cap_chat_context` mutates `messages` in place and returns `None`, so the
only record is a log line. The path from truncation to banner is four explicit
steps:

1. **`_cap_chat_context` returns** `dict | None` — `None` when nothing was cut:

   ```python
   {"doc_chars": 84859, "kept_chars": 49537, "kept_pct": 58}
   ```

2. **`_run_doc_chat` assigns** it to `state["context_truncated"]`, and the
   token usage to `state["token_usage"]`.

3. **`LegalAgentState`** ([graph/state.py](../../../graph/state.py)) gains two
   fields:

   ```python
   context_truncated: dict | None   # NEW — set by the chat path when the document was cut
   token_usage: dict | None         # NEW — real prompt/completion counts from the LLM response
   ```

4. **`output_formatter`** maps them into the report, following the existing
   pattern at [output_formatter.py:15-31](../../../graph/nodes/output_formatter.py)
   — an explicit dict of `state.get(...)` calls, exactly as `memory_degraded`
   does at line 30:

   ```python
   "context_truncated": state.get("context_truncated"),
   "tokens": state.get("token_usage"),
   ```

Because `output_formatter` names every key explicitly, these fields are always
**present** in the payload and carry `null` when there is nothing to report.
The client therefore tests truthiness, and `null` (unknown / nothing cut) is
distinguishable from a populated object. "Absent" is not achievable with this
pattern and must not be specified.

`observability/tracing.py` **already computes real token usage** —
`ollama_usage()` reads `prompt_eval_count`/`eval_count`, `message_usage()`
reads `usage_metadata` then `response_metadata`, both returning the existing
`TokenUsage` TypedDict. Today it flows only to OTel spans. This slice routes
the same value to the response payload. No new extraction logic.

Client side, following the established `memory_degraded` pattern
(`ChatTab.tsx:220`, `.status.warning`):

> ⚠ **I could only read 58% of this document** — the last 35,322 characters
> were not sent. Answers may miss clauses near the end of the contract.

`api.ts` `QueryResponse.data.report` gains `tokens?` and `context_truncated?`.

**Truncation is degraded memory and takes the same posture as
`memory_degraded`: amber, non-blocking, never silent.**

#### Hazard: state vs report

CLAUDE.md records that `memory_writer` runs **after** `output_formatter`, so a
flag set via `state[...]` there "passes every node test and silently never
reaches the banner."

`_cap_chat_context` runs inside the skill, **before** `output_formatter`, so
writing to state is correct *here*. The spec states this explicitly because the
seam is one step from the mistake that has already been made once:

- set **inside a skill** (before `output_formatter`) → `state[...]` is fine
- set **in `memory_writer`** (after `output_formatter`) → must travel on the
  returned report

Any test for this must assert the flag arrives in the **HTTP payload**, not
merely that a state key was set.

### 4. Correct the stale `chars/4` assumption

`config.py:73`'s comment and the two docstrings that cite the old default
(`tests/test_observability.py:321`, `tests/test_skills.py:1431`) state values
this change makes false. They are comments, not assertions — no test pins
`32768` — but leaving them turns them into lies.

### 5. Close the same hole on the review path

Raising the window fixes today's review overflow but does not make the promise
true in general. At 131,072 with `num_predict_review = 8192` the input headroom
is **122,880 tokens ≈ 600,883 chars**. `contract_review` has no cap and no
detection, so a document beyond that would again be middle-dropped by Ollama,
silently — the identical class of bug at a larger threshold.

This slice adds **detection only**:

- compute the assembled review context size before the call
- when it exceeds the derived input headroom, log at ERROR and set
  `state["context_truncated"]` with the same shape used by the chat path, so
  the Findings tab surfaces it through the machinery built in change 3

**Deliberately not added:** truncation logic for reviews. *What* to sacrifice in
a review is a real design question — the chat path's answer (cut the document)
is precisely the bug this spec fixes, so copying it would be wrong. Detection
converts a silent wrong answer into a visible one; choosing the sacrifice is
deferred until a document actually approaches 600k chars.

Residual risk, stated so it is not mistaken for solved: a >600k-char review
still produces a degraded answer. It will now say so.

## Testing

Confirmed: **no test pins the old default.** Every relevant test monkeypatches
its own value (`16384`, `12345`, `8192`), so changing the default is safe.

New tests:

1. **Budget arithmetic** — `_cap_chat_context` at the new budget leaves the
   full Trinetix-MSA-sized document intact; a document large enough to overflow
   still truncates *and* reports `kept_pct`.
2. **Truncation reaches the payload** — drive a chat turn whose assembled
   context overflows and assert `context_truncated` is present in the response
   body. Must go through the HTTP layer, not a node unit test (see hazard).
3. **No truncation → `null`, not a filled object.** `context_truncated` is
   `null` on a healthy turn — never `{"kept_pct": 100}` — and the banner must
   not render. Assert on the payload value, since the key is always present.
4. **`num_ctx` forwarding** — the three existing tests already cover this via
   monkeypatch; extend to assert all three sites read the *same* settings field
   so they cannot drift apart and reintroduce a reload mismatch.
5. **Token usage on the payload** — `report["tokens"]` populated from
   `ollama_usage`/`message_usage`; `null` (not zero) when the LLM returns no
   usage, so the client can distinguish "unknown" from "none".
6. **Review-path overflow detection** — an assembled review context past the
   derived headroom sets `context_truncated`; one below it leaves the flag
   `null`. Assert on the payload, and assert the review still completes — the
   guard reports, it must never block a review.

Not covered by automated tests, and stated so it is not mistaken for covered:

- **Whether the Spark box is actually at 131,072.** A mismatch is a live-config
  fact, invisible to tests. Verify with `curl .../api/ps` after deploy.
- **Real latency at the new budget.** Requires a live LLM; measured by hand
  (26.5 s for the full MSA turn) and to be re-measured on the VM.
- **The pane banner rendering in Word.** Per the add-in rule, `tsc --noEmit` is
  insufficient; needs a sideload smoke test.

## Risks

| risk | mitigation |
|---|---|
| Spark's `OLLAMA_CONTEXT_LENGTH` is retuned → reload thrash returns | Documented coupling at `config.py`; failure is a visible 4.7 s penalty, not silent truncation. Deploy check via `/api/ps`. |
| A deployment points at an Ollama that cannot serve 131,072 | `.env` override exists per-deployment. Local dev boxes with less RAM set a lower value — cost is 49.5 MB/1k tokens, so 32k costs 1.58 GB. |
| Longer prompts push some turns past 30 s | 30 s was chosen with the measured 1,397 tok/s prefill; the full-MSA turn measures 26.5 s. Slice 3's gauge makes overruns visible; slice 4 bounds history growth. |
| Truncation banner fires often enough to be ignored | At 150,000 the common cases fit with headroom. If it fires routinely, that is signal the budget or the doc size needs attention — which is the point. |
| Reviews now send ~25k tokens where Ollama previously dropped some | This is the fix, not a regression: reviews become *more* grounded. Review output quality should be spot-checked against a known MSA, since the model now sees clauses it previously did not. |

## Follow-ups

Ordered; each its own slice and branch. Recorded here so the reasoning built
during this design is not scattered across documents.

| # | Slice | Notes |
|---|---|---|
| 2 | **OpenAI-compatible backend seam** — `ChatOpenAI(base_url=…)` alongside `ChatOllama`, Ollama default, vLLM behind config | Makes Qwen3-Coder-Next a one-env-var experiment. Also the prerequisite for the open "LLM evaluation across models" row. Nothing changes for testers until flipped. |
| 3 | **Context gauge in the pane** — headroom shown *before* asking | Needs a client-side estimate; a `chars/4` estimate measured 22% high (30,903 vs 25,270). Make it **self-calibrating**: `charsPerToken = charsSent / tokens.input`, seeded 4.89, EWMA-smoothed, persisted. No tokenizer in the bundle, and it auto-adapts if slice 2 changes the model. `onParagraphChanged` (WordApi 1.5) debounced ~2 s; "live" means after each turn and on debounced edits, not keystroke-live. |
| 4 | **History compaction** — LLM summarization of older turns | Purpose is bounding **prefill**, not fitting the window. A 20-message window is ~6.1k tokens ≈ 4.4 s of prefill per turn, linear and unbounded if uncapped. Summaries of legal content risk fabrication: keep raw rows, mark the summary non-authoritative, never let it outrank the document or the review, and summarize from `_sanitize_history`-cleaned text so fenced blocks are not echoed. Ships with the per-component breakdown (playbook / doc / history / review) that makes the gauge actionable. |
| 5 | **Cross-session recall node** — query stored conversations by argument | Gated on `document_id` stability (the unsaved-document identity bug, still open) — if the key churns, recall fragments silently. Must be zero-LLM gated like `_needs_grounding`, since the ReAct path costs minutes. Scope strictly to `attorney_id`; conversations are per-attorney while reviews are shared. |
| — | Qwen3-14B serving fault (7.5 tok/s decode) | Not ours. Recorded so it is not rediscovered. |
| — | `ollama_num_predict_review = 8192` is **159 s of generation** at 51.4 tok/s | The review path's bottleneck is decode, not prefill — a different problem from chat, worth its own investigation. |
