# Trace coverage — design

- **Date:** 2026-09-16
- **Status:** Draft — awaiting review
- **Branch:** `feat/trace-coverage` (cut from `main`)
- **Part B of an observability audit.** Part A (remote trace access / backend topology) and Part C (metrics + logs) are separate and deferred. **One slice of A is pulled in here** — closing the unauthenticated Phoenix port — because B's whole job is to put *more* signal on spans, and shipping more contract text to an open port would knowingly widen a hole this audit found.

---

## Context & motivation

### 1. The application degrades; it does not raise

`api/ graph/ skills/ memory/ rag/` **catch nearly everything** — 36 `except Exception` sites when this spec was written, 37 as merged (the branch added one itself, see *Accounting*) — and nearly every one is load-bearing and correct. The codebase states the rule repeatedly: *tracing must never break a turn*, *retrieval must never break a review*, *telemetry must never break an Apply*, *Redis down → stateless run*, *Postgres down → log and flag*.

OpenTelemetry marks a span `ERROR` only when an exception **propagates out of** the `with start_as_current_span(...)` block. In this codebase almost nothing propagates. Confirmed at two independent layers:

- [graph/nodes/llm_caller.py:180](../../../graph/nodes/llm_caller.py) — an Ollama failure is caught **inside** its own `@traced(kind="LLM")` span and written into state as `"Error: LLM call failed — {e}"`. The span closes clean, status unset, with an error string recorded as the generation's `output`.
- [api/routes/query.py:211](../../../api/routes/query.py) — everything else is caught and returned as **HTTP 200** with `status="error"`.

**Consequence:** a turn where Ollama timed out, the fallback ran, and the attorney read an error message is byte-identical in Phoenix to a healthy turn. Failures cannot be filtered. They cannot even be counted.

This is the root finding. Everything else in this spec follows from it.

### 2. The diagnostics already exist and are dropped at the seam

The project has invested heavily in *computing* the right signals — `memory_degraded`, `context_truncated`, `context_breakdown`, `review_persist_error`, `token_usage`, `contract_type_detected`, and the `[compaction] auto=… can=… pct=…` decision line. Almost all of them reach the pane or the log. **None reach a span.**

So this is not "we don't know what to record." It is "we compute it and drop it on the floor at the tracing seam."

### 3. Ollama's timings are discarded

`observability/tracing.py::ollama_usage` reads `prompt_eval_count` and `eval_count` from a response that also carries `total_duration`, `load_duration`, `prompt_eval_duration` and `eval_duration`. Those four are thrown away.

The `ollama_num_ctx` incident recorded in CLAUDE.md — a 4.7-second model reload on **every call** against a shared Spark box — was sitting in `load_duration` in the response payload the entire time.

### 4. Content exposure (why one slice of Part A is in scope)

`llm_caller` sets `input=messages`: the assembled prompt, meaning the **full uploaded contract**, the **governing MSA**, and the **firm's playbook bundle** — plus `output=content`, the full review. `traced_invoke` does the same for doc-chat, compaction and generation.

Destination on the VM: Phoenix, **no auth**, published on `0.0.0.0:6007`, reachable by anyone on the internal VPN. The playbook is confidential in its own right — `data/contract_review_skills/` is gitignored precisely because it is canonical legal-team IP. `docker-compose.remote.yml` already carries a warning comment naming this exact risk.

---

## Goals

- `status = ERROR` on a trace means exactly **"the attorney did not get their answer."**
- One root-level field answers **"how did this turn go?"** without reading children.
- Degradations the attorney was **never told about** become queryable.
- Ollama's latency breakdown lands on the LLM span, so model-reload thrash is attributable.
- Compaction stops producing orphan traces with no owner.
- Postgres store calls get named spans.
- The VM's trace store stops being readable by anyone on the VPN.

## Non-goals

- **Metrics and logs** (Part C). No counters, no histograms, no OTel log bridge, no `turn_id` logging filter. Traces only.
- **Remote trace topology** (Part A) beyond the port bind: no Langfuse-on-VM, no exporter fan-out, no SSH tunnel automation.
- **The RAG pipeline.** Explicitly descoped by the user on 2026-09-16: no spans on `embed_query` / `bm25_search` / `rerank` / `hybrid_search`, no `@traced` on document ingest, no reranker or BM25 degradation codes, no qdrant instrumentor.
- **Any new attorney-facing surface.** B is trace-only. No new pane banners. If traces later show attorneys *should* be told when their preferences were silently dropped, that is a separate change with its own design.
- **Redaction / content policy.** Deliberately deferred — see D3.
- **Fixing the reranker misconfiguration** found during this audit. Recorded below, not fixed.

---

## Decisions

### D1 — Outcome is a derived rollup on the root span

`app.outcome` ∈ `{ok, degraded, failed}`, stamped on the **root** span, derived **once**, from **one** source, and read back rather than recomputed.

Rejected alternative: attribute-existence filtering (`degraded.memory = true OR degraded.context_truncated = true OR status = ERROR`). It works, but it makes "show me the bad turns" an OR-chain across attributes you have to remember, and it has no single field to sort or group by.

The one-source discipline is deliberate. `degradations()` is complete **by construction**: every **producer** of a report flag (`memory_degraded`, `context_truncated`, `review_persist_error`) is in the vocabulary below, so the accumulator already sees everything the pane sees. *Producer, not site* — `context_truncated` has two, and wiring only one leaves this premise false while the vocabulary gate still reads green, because the gate counts codes. Also OR-ing the report flags would create two sources that can disagree — the failure mode the gate-verdict rule exists to prevent.

### D2 — Three classes of bad, and class 4 stays invisible

The codebase already makes the key distinction. [skills/legal_research/context.py:66](../../../skills/legal_research/context.py) sets `memory_degraded = True` on a store read failure, but `:76` pointedly does **not** on a reconciliation failure — the comment says `memory_degraded` "is reserved for real store failures."

So `memory_degraded` already means **"the attorney was told."** This spec extends that convention rather than inventing one:

| Class | Meaning | Treatment |
|---|---|---|
| **1 — Failed** | No answer, or an error string as the answer | `mark_failed()` → span status `ERROR` |
| **2 — Announced** | Answer lands, banner warns | `record_degradation(announced=True)` → span event |
| **3 — Silent** | Answer lands, quality quietly worse, nobody told | `record_degradation(announced=False)` → span event |
| **4 — Telemetry self-catch** | Observability failing to observe | **Nothing.** Untouched, on principle |

Class 4 is excluded deliberately: `spans.py:113/153/169`, `otel.py:65`, `risk_assessor:164`, `contract_review:143/205`, and the quiet `interaction_event` / `append_turn` writes. Tracing reporting its own best-effort catches as application degradations is how a dashboard becomes decoration.

Class 3 is the reason this work exists. [context.py:195](../../../skills/legal_research/context.py) catches a grounding failure and answers **with no playbook and no MSA** — a fluent, confident, ungrounded answer with no banner, no span, and no queryable record anywhere. One `logger.warning`, with no `turn_id`, interleaved with every concurrent turn.

### D3 — Full content fidelity; fix access, not content

Spans keep whole prompts. Confidentiality is addressed at the network layer instead (see *Access* below).

Rejected: truncating `input`/`output` to N chars (usually fails both ways — too short to debug from, still long enough to leak) and metadata-only-with-a-capture-flag (loses the exact prompt exactly where it is most needed: production, on the turn that went wrong).

**Why deferring a capture flag is safe:** both content paths funnel through exactly two functions, `set_trace_attributes` and `set_gen_attributes`. A single choke point means a flag can be added later without touching one call site. A reversible decision is one worth not making yet.

### D4 — Seam extension plus targeted auto-instrumentation

Hand-rolled instrumentation through `observability/spans.py`, **plus** `opentelemetry-instrumentation-httpx`.

| Library | Decision | Why |
|---|---|---|
| **httpx** | **On**, newly declared | Real network timing on every Ollama call; also covers `qdrant-client`'s REST calls for free |
| **redis** | Config-gated, **default off** (`otel_instrument_redis`) | The checkpointer issues many RediSearch ops per turn; that chatter would bury everything else. One flag to flip for a checkpointer investigation |
| **fastapi** | **Off** | Would become the trace root and push `app.outcome` onto a child, breaking D1 |
| **langchain / ollama** | **Off** | Would emit a second LLM span and a second token count per call, against `traced_invoke` / `ollama_usage` |
| **qdrant** | **Off** | Descoped with RAG; httpx covers the REST calls anyway |

Rejected alternative — **hand off wholesale to Traceloop's auto-instrumentors**, already present in the venv, and delete `traced_invoke`. Rejected on four counts: `traceloop-sdk` is **not declared** in `requirements.txt` (it arrives via `chainlit → literalai → traceloop-sdk`, so a chainlit bump can silently blind production); the fastapi instrumentor breaks D1; `llm_caller` uses raw `httpx.post` rather than the `ollama` package, so the single most important LLM call would not be covered at all; and it would double-count wherever `traced_invoke` survived.

> **Declare what you depend on.** `opentelemetry-instrumentation-httpx` goes in `requirements.txt` explicitly. Depending on an undeclared transitive is the exact fragility that sank the Traceloop option; accepting it by omission would be the same bug with better manners.

> **Landmine, no code:** if anything ever calls `Traceloop.init()`, it will try to set a second global `TracerProvider`; OTel will warn and ignore it. Dormant today — `literalai` only activates with an API key.

---

## Design

### The seam — `observability/spans.py`

```python
def mark_failed(reason: str, *, exc: BaseException | None = None, detail: str = "") -> None:
    """Set the CURRENT span to ERROR and record `exc`.

    For failures this app CATCHES and converts into a degraded answer. OTel
    marks a span ERROR only when an exception propagates out of the `with`
    block; this codebase degrades rather than raises, so almost nothing does
    and status has to be set by hand at the point of the catch."""

def record_degradation(reason: str, *, announced: bool, detail: str = "") -> None:
    """Record a degradation as a span EVENT. Never touches span status —
    a fallback that worked is not an error.

    `announced` has NO default on purpose: whether the attorney was told is
    exactly the thing you must not get wrong by accident."""

def set_outcome(outcome: str) -> None:
    """Stamp app.outcome on the ROOT span. Called once per request root —
    submit_query, resume_query, post_compact. When outcome == "failed",
    ALSO sets root status to ERROR with the reason codes as description."""

def degradations() -> list[str]:
    """Reason codes accumulated this turn, for the rollup to read back."""
```

**Events, not attributes,** for degradations: one span can degrade twice. Attributes overwrite; events are repeatable and timestamped, so both survive in order.

Accumulation reuses machinery already in `spans.py` — a `_root_degradations` contextvar alongside the existing `_root_metadata`, same lifecycle, reset in `traced`'s `finally`.

`set_outcome("failed")` setting root status to `ERROR` is the point of the exercise: afterwards, `status = ERROR` in Phoenix means precisely *"the attorney did not get their answer."*

### The vocabulary — closed, 22 codes

Free-text reasons fragment into things you cannot filter on. Constants live in a new `observability/degradations.py`.

| Class | Reason code | Site(s) |
|---|---|---|
| **Failed** (8) | `llm_call_failed` | `llm_caller:180` |
| | `legal_research_failed` | `legal_research.py:428` — **the Word chat path** |
| | `contract_generation_failed` | `contract_generation.py:77` (revision) / `:173` (agent) — one code, distinguished by `detail` |
| | `compaction_failed` | `api/routes/compact.py` — **moved here by Task 13**, two exits: the unguarded store read and `result["error"]`. NOT `compaction.py`'s own excepts: generation retries twice internally, so per-attempt recording would mark a run failed that then succeeded |
| | `graph_invoke_failed` | `query.py:211` |
| | `stateless_fallback_failed` | `query.py:227` |
| | `resume_state_load_failed` | `query.py:260` — `get_state` failed |
| | `resume_failed` | `query.py:279` — graph invoke failed on resume |
| **Announced** (7) | `checkpointer_unavailable` | `api/routes/query.py` — the mid-invoke Redis branch **and** a per-turn startup-absent check, twice (submit + resume). **Moved here by ruling R12**, NOT `checkpointer.py:34`: `_get_graph()` caches the compiled graph, so `build_checkpointer()` runs exactly once and a record there would fire on turn #1 and never again. `graph/checkpointer.py` is deliberately unwired |
| | `audit_write_failed` | `memory_writer:55` |
| | `review_persist_failed` | `memory_writer:72` |
| | `prior_review_load_failed` | `context.py:66` |
| | `summary_load_failed` | `context.py:122` |
| | `prior_conversation_load_failed` | `context.py:136` |
| | `context_truncated` | `llm_caller` (detects overflow, does not cut) **and** `_cap_chat_context`'s caller in `legal_research.py` (actually cuts) — a condition, not an `except`. **Both** producers must be wired; only the first was, until the final fix wave. See *Accounting* |
| **Silent** (7) | `chat_grounding_failed` | `context.py:195` — answers with no playbook and no MSA |
| | `review_reconciliation_failed` | `context.py:76` |
| | `compressible_history_read_failed` | `context.py:264` — silently disarms compaction |
| | `preferences_load_failed` | `grounding.py:104` |
| | `msa_lookup_failed` | `contract_review.py:171` — SOW reviewed without its MSA |
| | `intent_classification_failed` | `intent_router.py:81` — silently defaults to research |
| | `planning_failed` | `planner.py:77` — silently falls back to the first skill in the plan |

`msa_lookup_failed` is retained despite the RAG descope: it is a review-**grounding** degradation, not part of the retrieval pipeline, and its failure mode is a legal-quality problem.

#### Accounting — every `except Exception` site

The count moved four times while writing this spec (14 → 19 → 23 → 22) because it was estimated rather than enumerated. Enumerating it exposed a second problem: **the figure is only meaningful with its scoping**, because this branch *adds* `except` sites while classifying them. Stated precisely, and reproducible:

```bash
grep -rn "except Exception" api graph skills memory rag | grep '\.py:' | wc -l   # 37 as merged (36 before this branch)
grep -rn "except Exception" observability          | grep '\.py:' | wc -l   # 10 — Class 4 by definition; this branch grew it from 4
```

The original "40" was 36 application-directory sites plus the four `observability/` sites the first version of this table listed by name. The figure is deliberately **not** restated in the living docs (`CLAUDE.md`, `docs/wiki.md`, `docs/testing-observability.md`, `observability/spans.py`): a number a reader checks with one `grep`, and which drifted during the branch that introduced it, does not belong in five files. The superseded plan document still carries "40" — it records what was *planned*, not what shipped. Every application site below is either wired or explicitly excluded; nothing is unclassified.

**This table is NOT what the `check.sh` assertion checks against.** `scripts/check_degradation_vocabulary.py` compares the *constants used at call sites* in `api graph skills memory` against the constants *declared* in `observability/degradations.py`, both ways — it never reads this document. The distinction matters: the gate counts **codes**, not **producers**, so a second producer of an already-used code is invisible to it. That is exactly how `_cap_chat_context`'s `context_truncated` shipped unwired.

| Disposition | Count | Sites |
|---|---|---|
| **Wired** | 21 | the sites above, minus the four that are conditions rather than `except` blocks. `query.py`'s outer `except` hosts two of them (`checkpointer_unavailable` on the Redis branch, `graph_invoke_failed` on the fall-through) |
| **Wired — a condition, not an `except`** | +4 *(outside the total; these are not `except` sites)* | `query.py` startup-absent checkpointer, `compact.py`'s `result["error"]`, `llm_caller`'s headroom check, `legal_research`'s post-truncation check |
| **Class 4 — telemetry self-catch, excluded on principle** | 5 (+10 in `observability/`) | `risk_assessor:164`, `contract_review:144/207`, `memory_writer:92` (best-effort `append_turn`), `feedback_store:164` (quiet `interaction_event`) |
| **RAG — descoped 2026-09-16** | 3 | `documents.py:62`, `reranker.py:119`, `bm25_index.py:179` |
| **Dormant SSO** (`sso_enabled=False`) | 2 | `auth.py:67/145` — will need codes when SSO turns on; noted, not wired |
| **Startup, not the turn path** | 1 | `main.py:41` — a bad `.env` must still produce a readable traceback |
| **Off-turn endpoint, not instrumented** | 1 | `feedback_store.py:142` — LOUD by design, has its own store and report |
| **Deliberately excluded — real but low-stakes** | 1 | `checkpointer.py:55` — a failed `refresh_ttl` shortens session life silently. Dropped from the vocabulary on review (2026-09-16) to keep it tight; recorded here so the accounting still closes and a future reader knows it was considered, not overlooked |
| **Deliberately unwired — ruling R12** | 1 | `checkpointer.py:34` — recorded per turn in `query.py` instead; see the `checkpointer_unavailable` row above |
| **Recording moved to the route — Task 13** | 2 | `compaction.py:546/611` — generation retries twice internally; `api/routes/compact.py` owns the code |
| **Total (application directories)** | **37** | |

**So: 22 codes, 26 call sites** — four codes have two producers each (`checkpointer_unavailable`, `contract_generation_failed`, `compaction_failed`, `context_truncated`). The earlier "22 codes across 23 sites" was wrong in both halves; the codes-to-sites ratio is not 1:1 and never was.

> **Line numbers in the tables above are from design time and drift.** The live index is the `grep` in this section plus `scripts/check_degradation_vocabulary.py`. **Three rows were moved by rulings taken during execution — R12 (`checkpointer_unavailable`), Task 13 (`compaction_failed`) and the final fix wave (`context_truncated`'s second producer).** Every ruling, with its reasoning, is in the execution ledger: [`.superpowers/sdd/2026-09-16-trace-coverage/progress.md`](../../../.superpowers/sdd/2026-09-16-trace-coverage/progress.md). Where this spec and the merged code disagree, the code and the ledger win.

### Outcome derivation

`submit_query` is the only place that can see all three inputs: the finished report (`memory_writer` runs **after** `output_formatter`, so its flag exists only on the returned report), the exception handlers, and the root span itself.

```python
FAILED_REASONS = frozenset({...})   # the 8 above

def _derive_outcome(reasons: list[str]) -> str:
    if FAILED_REASONS & set(reasons):  return "failed"
    if reasons:                        return "degraded"
    return "ok"
```

The taxonomy falls out naturally on the Redis path: checkpointer dies → `record_degradation("checkpointer_unavailable", announced=True)`, fallback runs, turn answers → **degraded**. Fallback also dies → `mark_failed("stateless_fallback_failed")` → **failed**.

`resume_query` gets the same treatment — it is a second root. **So does `post_compact`**, which becomes a third root once it is `@traced` (see *New spans*); its outcome is derived from the same accumulator over its own request.

**Expect ERROR on two spans, not one.** `mark_failed` sets the status of the span it is called in — for `llm_call_failed` that is `llm_caller` — and `set_outcome("failed")` then sets the root as well. That is intended, not duplication: the node span says *where* it broke, the root says *the attorney got nothing*. An implementer should not "fix" it by suppressing either.

Root span also carries `app.degradations` (JSON list of reason codes).

### Ollama timings — `observability/tracing.py`

```python
class OllamaTimings(TypedDict):
    total_ms: int; load_ms: int; prompt_eval_ms: int; eval_ms: int

def ollama_timings(response_json) -> OllamaTimings | None:   # ns → ms
```

Surfaced as `llm.ollama.load_ms` / `.prompt_eval_ms` / `.eval_ms` / `.total_ms`, alongside OpenInference's `llm.token_count.*`. Vendor-scoped deliberately — these are not standard attributes, the namespace says so, and a future GenAI convention cannot collide with them.

### New spans

| Add | Why |
|---|---|
| `@traced("compact")` on `post_compact`, plus trace attrs (`user_id`, `document_id`, `reclaim_chars`) | Kills the orphan root. `compact_conversation`'s `traced_invoke` currently becomes its own parentless trace with no user, session or document. Afterwards it nests where it belongs |
| `@traced` on 8 store functions: `write_audit_log`, `save_review`, `load_latest_review`, `append_turn`, `load_recent`, `load_segments`, `latest_to_id`, `row_lengths_after` | Named per operation. Wrapping `get_pool().connection()` instead would produce a pile of spans all called "db" |

**Not added:** `/api/feedback`, `/api/events`, `/api/preferences`, `/health`. They have their own stores and `scripts/feedback_report`, and none answers "why was this answer worse." Spans there would be volume, not signal.

A **net-benefit refusal in compaction is not a degradation** — it is the guard working correctly. But it disarms the auto latch, which is operationally significant, so it lands as `compaction.refused_reason` on the span. That keeps `app.outcome` honest while making the latch's cause visible — the thing that cost three round trips on the VM (2026-08-27).

### Auto-instrumentation

`_instrument_libraries()` runs in `init_observability()` after the provider is set, **inside** the `tracing_enabled` guard, each instrumentor guarded independently so one bad one cannot take down the others.

One new setting: `otel_instrument_redis: bool = False`. `get_settings` is `@lru_cache`'d → needs a `start.sh` restart, as with every `otel_*` field.

### Access

`docker-compose.remote.yml`: `"6007:6006"` → `"127.0.0.1:6007:6006"`, and the existing warning comment updated to record that it is closed.

**This is a real behavior change:** the Phoenix UI stops being browsable at `http://<vm>:6007` from anywhere on the VPN. `docs/deploy-vm.md` gains the replacement — `ssh -L 6007:localhost:6007 <vm>`, then `http://localhost:6007`.

---

## Verification

### Local environment (verified 2026-09-16)

Docker is running with the full local stack: Langfuse `:3000`, Qdrant, Redis, `app-db` `:5434`, ClickHouse, MinIO. A **Phoenix is also running locally on `:6006`** (`hermes-phoenix-1`, another project's container). Ollama is local (`http://localhost:11434`).

So **both trace backends are reachable locally.** Everything in this spec is developable and testable on the Mac except the access change, which is a VM compose binding — reviewable, not exercisable.

> **Trap:** the Redis drill is self-defeating against local Langfuse. CLAUDE.md already records that `docker compose stop redis` takes Langfuse ingestion down with it — shared Redis. You would degrade the turn correctly and then have no trace to look at, which reads as "the instrumentation didn't work." **That drill must run against Phoenix on `:6006`**, which does not touch our Redis.

### The three tests that must be RED first

This repo has been burned twice by tests that passed while testing nothing — `caplog` greening a logging config that was inert in production, and `setattr` on the wrong module silently no-op'ing. The acceptance criterion is not "tests pass," it is **these three fail before the change and pass after**:

1. Inject an Ollama failure → the `llm_caller` span's status is `ERROR`. *Today: `UNSET`.*
2. Inject a grounding failure at `context.py:195` → a `degradation` event with `announced=False` on the turn. *Today: no event exists anywhere.*
3. Call `/api/compact` → the LLM span has a parent. *Today: it is an orphan root.*

Each is written first, run, and confirmed red. TDD entry point and mutation proof in one.

### The hazard a test suite cannot reach

A perfect `test_record_degradation_emits_event` can pass with **21 of 22 sites unwired** — the unit test passes, the call sites do not exist. Same shape as the `setattr`-on-the-wrong-module hazard.

So a `scripts/check.sh` assertion, checked **both ways** like the eval baseline:

- a declared constant appearing at no call site → **a site you forgot to wire**
- a reason string at a call site that is not declared → **a typo silently creating a new category**

Cheap, static, and it catches the failure mode unit tests structurally cannot.

### Other tests

- One integration test per class, driving the real path with an injected failure.
- A contextvar-leak test: two sequential turns must not share degradations (`_root_degradations` resets in `traced`'s `finally`, as `_root_metadata` already does).
- `init_observability()` with `tracing_enabled=False` must **not** instrument httpx.

**One structural harness change:** the `InMemorySpanExporter` + provider fixture currently lives inside `tests/test_observability.py`, so no other test file can assert on spans. It moves to `conftest.py`. This makes spans real in *every* test; the existing no-op test asserts behavior outside any span, where `INVALID_SPAN` stays non-recording, so it still holds.

### Failure drills — `docs/testing-observability.md` (new)

Mirroring `docs/testing-compaction.md`, "the manual checks no gate can reach":

| Drill | Expected |
|---|---|
| Stop Ollama, run one turn | root `status=ERROR`, `app.outcome=failed` |
| Stop `app-db`, run one turn | `app.outcome=degraded`, `audit_write_failed`, turn still answers |
| Stop Redis (**against Phoenix `:6006`**), run one turn | `app.outcome=degraded`, `checkpointer_unavailable`, amber banner |
| Force a grounding failure, run a doc-chat turn | `app.outcome=degraded`, `chat_grounding_failed`, `announced=false` — **and nothing in the pane** |

The last is the thesis: an answer the attorney has no reason to distrust, with the reason it got worse now recorded.

These also settle the one expectation flagged but not verified: whether httpx instrumentation catches `llm_caller`'s module-level `httpx.post`. It patches `Client.send`, and `httpx.post` builds a client internally, so it should — but "should" is not "does."

---

## Slices

Each independently shippable, on `feat/trace-coverage`:

1. **Seam** — `spans.py` additions, `degradations.py`, `ollama_timings`, conftest fixture move, tests.
2. **Wire the 22 sites + outcome derivation.** ← the main win lands here.
3. **New spans** — `compact` root, 8 store functions.
4. **httpx auto-instrumentation** + `requirements.txt` + `otel_instrument_redis`.
5. **Access fix** + docs + drills.

---

## Found but out of scope

**The reranker has never run.** `RERANKER_ENABLED=true` with `RERANKER_URL=` empty. `rerank()` guards on `reranker_enabled` and on empty results, but **not on an empty URL** ([rag/reranker.py:74-80](../../../rag/reranker.py)), so every call reaches `_call_rerank` → `httpx.post("")`. Verified in the project venv:

```
httpx.UnsupportedProtocol: Request URL is missing an 'http://' or 'https://' protocol.
```

Caught by the bare `except Exception` at `rag/reranker.py:119` → one `logger.warning` → `return results[:n]`. **Config says the reranker is on; it has never run. Every local RAG answer has been unreranked.**

`.env.remote.example` sets no reranker keys either, so the VM inherits the same defaults — though the VM's live `.env` was not inspected, so nothing stronger is claimed.

Not fixed here: whether the reranker should be configured or switched off is a product decision, not an observability one. Recorded so it is not lost. It is also a textbook class-3 degradation, which is the argument for this spec in miniature.

## Docs to update

- **`docs/wiki.md` Observability section is stale** — it still describes the Langfuse v2 `@observe` SDK and asserts "Langfuse = agent traces. Phoenix = RAG evals. Don't mix," which the OTel migration inverted. Rewrite, plus Shipped / follow-ups rows (including the reranker finding).
- **`CLAUDE.md`** is at its 150-line cap and the maintenance rule says consolidate rather than append. The degradation-not-exception lesson **replaces and absorbs** the existing tracing bullet.
- **`docs/deploy-vm.md`** — the Phoenix bind change and the SSH-tunnel replacement.
- **`docs/testing-observability.md`** — new.

## Open questions

None blocking. Two judgment calls made and flagged, reversible if wrong:

1. `msa_lookup_failed` retained despite the RAG descope (grounding, not retrieval).
2. `app.degradations` as a JSON list on the root, with no separate root-level "degraded silently" boolean. If filtering a JSON array proves awkward in Phoenix, add the boolean then — not before.
