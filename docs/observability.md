# Legal Plugin — Observability

> Part of the [project wiki](wiki.md).

## Observability

### OpenTelemetry, not the Langfuse SDK

Tracing is **OTel end to end**. The Langfuse v2 `@observe` SDK is gone; `observability/langfuse.py` no longer exists. Two modules and one rule:

- **`observability/spans.py` is the seam.** Everything the application records goes through it: `@traced(name, kind=...)` wraps a function in a span, `set_trace_attributes` / `set_gen_attributes` write OpenInference-convention attributes, and `mark_failed` / `record_degradation` / `set_outcome` / `degradations` carry the outcome rollup below. Its companion `observability/tracing.py` holds `traced_invoke` / `traced_agent_invoke` (LangChain calls the decorator cannot reach) and the Ollama payload readers. **Nothing in `api/ graph/ skills/ memory/ rag/` imports `opentelemetry` directly** — verified by grep, and the reason a capture flag or a backend swap is a one-file change.
- **`observability/otel.py::init_observability()` is the bootstrap** — a `TracerProvider` with an OTLP/HTTP `BatchSpanProcessor`, called once at FastAPI lifespan startup. Best-effort by construction: disabled, misconfigured or unreachable → no provider is set, OTel's no-op tracer takes over, and every `@traced` becomes a transparent pass-through. **A turn is never broken by tracing.**
- **The backend is swappable via `OTEL_EXPORTER_OTLP_ENDPOINT`** — local **Langfuse v3** over its OTLP ingest by default (`http://localhost:3000/api/public/otel`, basic-auth header), a dedicated **Phoenix** service on the VM (`docker-compose.remote.yml`, `http://phoenix:6006`). The application code is identical either way.

The old rule *"Langfuse = agent traces, Phoenix = RAG evals, don't mix"* is **dead** — the OTel migration inverted it. Both are OTLP sinks for the same spans; which one you get is one env var. (Phoenix files our traces under the project **`default`**, not under `otel_service_name` — it keys projects off an `openinference.project.name` resource attribute we do not set. Verified on a live turn 2026-09-16; look in `default` or you will conclude nothing was exported.)

Auto-instrumentation is deliberately narrow (`_instrument_libraries()`, inside the `tracing_enabled` guard, each instrumentor independently try/excepted):

| Library | Status | Why |
|---|---|---|
| **httpx** | **on** | Real network timing on every Ollama call, and qdrant-client's REST calls for free. Confirmed live to cover `llm_caller`'s *module-level* `httpx.post` — the instrumentor patches `Client.send` and `httpx.post` builds its client internally, so this was an expectation until a real turn showed the `POST` CLIENT span as the direct child of `llm_caller` |
| **redis** | config-gated, default **off** (`otel_instrument_redis`) | The checkpointer issues many RediSearch ops per turn; that chatter buries everything else. One flag for a checkpointer investigation. Default-off is about SIGNAL, not the dependency: `opentelemetry-instrumentation-redis` is declared in **both** `requirements.txt` and `requirements-runtime.txt` and imported at the top of `otel.py`. R18 retired a lazy-import exception arguing that *"declaring it buys the same protection for one line"* — true of the dev venv, **false of the image**, which installs `requirements-runtime.txt` alone and died at `uvicorn api.main:app` with `ModuleNotFoundError` until that file declared both instrumentors too |
| **fastapi** | off | Would become the trace root and push `app.outcome` onto a child |
| **langchain / ollama** | off | Would emit a second LLM span and a second token count per call, against `traced_invoke` / `ollama_usage` |

### `app.outcome` — one field for "how did this turn go?"

OTel marks a span `ERROR` only when an exception **propagates out of** the `with start_as_current_span(...)` block. **This application catches nearly everything on the turn path, so almost nothing propagates** — deliberately (*tracing must never break a turn*, *retrieval must never break a review*, *Redis down → stateless run*, *Postgres down → log and flag*). The consequence, until 2026-09-16: a turn where Ollama timed out and the attorney read an error string was **byte-identical in the trace UI to a healthy turn**. Failures could not be filtered. They could not be counted.

So status is now set **by hand at the point of the catch**:

- `mark_failed(reason, exc=...)` → span status `ERROR`, plus `degradation.reason` / `degradation.detail` as span attributes so a trace says *which* failure, not just *that* one happened.
- `record_degradation(reason, announced=..., detail=...)` → a span **event** (repeatable and timestamped — one span can degrade twice; attributes would overwrite). **Never** touches span status: a fallback that worked is not an error.
- `set_outcome(...)` on the **request root** stamps `app.outcome ∈ {ok, degraded, failed}` and `app.degradations` (JSON list of codes), and on `failed` also sets the root to `ERROR`.

`app.outcome` is **derived once, from one source** — `degradations()`, the per-request accumulator — and read back rather than recomputed. Report flags are deliberately *not* OR-ed in: every **producer** of one (`memory_degraded`, `context_truncated`, `review_persist_error`) also records a reason code, so the accumulator already sees everything the pane sees, and two sources that can disagree is the failure mode the gate-verdict rule exists to prevent. *Producer, not site* — `context_truncated` has two, and wiring only one leaves this premise quietly false while the vocabulary gate still reads green, because **the gate counts codes, not producers**.

There are three request roots: **`query:<task_type>`** (renamed from `query` by `submit_query`, so there is no span called plain `query`), **`resume:<session_id>`**, and **`compact`**.

Afterwards, **`status = ERROR` on the root means exactly "the attorney did not get their answer."** Expect ERROR on *two* spans on a failed turn — the node says where it broke, the root says the attorney got nothing.

### The vocabulary — closed, 23 codes, gated both ways

Free-text reasons fragment into things you cannot filter on, so the codes are constants in `observability/degradations.py`, in three classes:

| Class | Meaning | Treatment | Codes |
|---|---|---|---|
| **1 — Failed** | No answer, or an error string as the answer | `mark_failed()` → span `ERROR` | 8 — `llm_call_failed`, `legal_research_failed`, `contract_generation_failed`, `compaction_failed`, `graph_invoke_failed`, `stateless_fallback_failed`, `resume_state_load_failed`, `resume_failed` |
| **2 — Announced** | Answer lands, quality worse, attorney **meant** to be told — a banner only if that tab renders it ([drills 2 and 3](testing-observability.md)) | `record_degradation(announced=True)` | 8 — `checkpointer_unavailable`, `audit_write_failed`, `review_persist_failed`, `prior_review_load_failed`, `summary_load_failed`, `prior_conversation_load_failed`, `context_truncated`, `edit_retry_failed` |
| **3 — Silent** | Answer lands, quality quietly worse, **nobody told** | `record_degradation(announced=False)` | 7 — `chat_grounding_failed`, `review_reconciliation_failed`, `compressible_history_read_failed`, `preferences_load_failed`, `msa_lookup_failed`, `intent_classification_failed`, `planning_failed` |
| **4 — Telemetry self-catch** | Observability failing to observe | **nothing, on principle** | none — tracing reporting its own best-effort catches as application degradations is how a dashboard becomes decoration |

`announced` has **no default**, on purpose: whether the attorney was told is exactly the thing you must not get wrong by accident. The convention is not invented here — `skills/legal_research/context.py` already used `memory_degraded` to mean *"the attorney was told"*, setting it on a store read failure and pointedly **not** on a reconciliation failure.

**Class 3 is why this work exists.** A grounding failure answers with no playbook and no MSA — fluent, confident, ungrounded, with no banner and (until now) no queryable record anywhere.

A unit test structurally cannot catch an unwired site: a green `test_record_degradation_emits_event` passes with 22 of 23 codes missing, because the call sites simply do not exist. So **`scripts/check_degradation_vocabulary.py` runs in `scripts/check.sh`** and checks both directions — a declared constant at no call site is *a site you forgot to wire*; a reason string at a call site that is not declared is *a typo silently creating a new category*. It reads both positional and keyword `reason=` forms.

Two more sources of signal on the spans:

- **Ollama's latency breakdown** — `llm.ollama.load_ms` / `.prompt_eval_ms` / `.eval_ms` / `.total_ms` (`observability/tracing.py::ollama_timings`, ns → ms), vendor-scoped so a future GenAI convention cannot collide. The `ollama_num_ctx` incident — a 4.7 s model reload on *every* call against a shared box — sat in `load_duration` in the response payload the whole time and was thrown away. Measured on a drill turn against a cold model: `load_ms: 8722`; warm, the same field reads `13`.
- **Named store spans** on the eight Postgres functions on the turn path (`db.write_audit_log`, `db.save_review`, `db.load_latest_review`, `db.append_turn`, `db.load_recent`, `db.load_segments`, `db.latest_to_id`, `db.row_lengths_after`) — named per operation, because wrapping `get_pool().connection()` would produce a pile of spans all called "db".

**The failure drills are in [docs/testing-observability.md](testing-observability.md)** — Ollama down, `app-db` down, Redis down, grounding down, each with the real trace ids and observed values from the 2026-09-16 run. They are the manual checks no gate can reach, in the same spirit as [docs/testing-compaction.md](testing-compaction.md).

### Trace content

Every request creates a trace with:
- **Nested spans** for each graph node (intake → intent_router → skill → rag_retriever → llm_caller → ...)
- **LLM spans with token usage + model** on every LLM call. The httpx nodes (`llm_caller`, `intent_router`, `planner`) carry `@traced(kind="LLM")` and attach Ollama's `prompt_eval_count`/`eval_count` (`observability/tracing.py::ollama_usage`) plus the four `llm.ollama.*_ms` latencies. The skill LLM calls — the Word chat-tab direct `ChatOllama` (`legal_research._run_doc_chat`), its `format='json'` retry, and both ReAct agents — run inside `traced_invoke`/`traced_agent_invoke`, which record a nested LLM span with usage read from the returned `AIMessage.usage_metadata`. These were previously **invisible**.
- **Skill spans** — all five skills (`legal_research`, `contract_generation`, `contract_review`, `drafting`, `compliance_check`) carry their own `@traced` span.
- **User and session tracking** (`user.id`, `session.id` from the request), **`turn_id`** in metadata — the join key for `feedback` / `interaction_event` rows and for `scripts/feedback_report`.
- **Task type tags**, **model metadata** (model name, temperature, chunk count), `contract_type_detected`, `review_risk_level` / `requires_attorney`.
- **Full prompt and response content** — `input` on the LLM span is the assembled prompt: the uploaded contract, the governing MSA, and the firm's playbook bundle. Deliberate (D3 of the design spec: a truncated prompt fails both ways — too short to debug from, still long enough to leak), and why it matters that the VM's Phoenix is reachable on the VPN with no auth (see *Phoenix* below). Both content paths funnel through exactly two functions, `set_trace_attributes` and `set_gen_attributes`, so a capture flag can be added later without touching a call site.

Token **cost** stays null for local Ollama models unless a model price is registered in the Langfuse UI (Settings → Models). NB: Langfuse's *LangChain* callback handler is **not** usable here — it imports the full `langchain` package, which isn't a dependency (only `langchain-core`/`langchain-ollama`) — hence the manual `traced_invoke` wrapping.

### Postgres Audit Log (app-db)

Every skill invocation writes to `audit_log` table: timestamp, session_id, user_id, user_name, skill_name, task_type, request_summary, risk_level, review_status, review_notes, duration_ms, turn_id.

Each row also carries the request's `turn_id`, which joins it to `turn_log` — the per-request record written by the request handler, failures and compaction included (`docs/feedback-loop.md`, *A session, turn by turn*).

### Phoenix

Local dev: `http://localhost:6006` (shared with compliance-bot — no setup needed, and the target for the Redis drill, which takes local Langfuse's ingestion down with it). VM: our own dedicated `phoenix` service, `http://phoenix:6006` in-network, published on host `6007` on all interfaces — browse `http://172.20.1.10:6007` from the VPN.

**Phoenix has no auth and its spans carry contract text, the governing MSA and the playbook**, so anyone on the VPN who knows the address can read them. Published there deliberately, for direct browsing — it was bound to loopback on 2026-09-16, re-opened on the VM on 2026-09-18, and the repo was brought in line on 2026-10-08. See [docs/deploy-vm.md](deploy-vm.md) Step 4.
