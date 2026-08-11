# Tracing scope: one instrumentation, swappable backend (Langfuse local / Phoenix VM)

## Problem
The backend is instrumented with the **Langfuse SDK** (`@observe`, `langfuse_context`). Langfuse doesn't fit the VM (~3–4 GB v3 stack: Postgres + ClickHouse + MinIO + Redis + 2 Node services; the 7.3 GB box already runs compliance-bot + Phoenix + our stack). compliance-bot already runs **Phoenix**. Reusing Phoenix on the VM while keeping Langfuse locally = **two vendor instrumentation paths** — the maintenance smell to avoid.

## Goal
**One vendor-neutral instrumentation (OpenTelemetry / OpenInference); the trace backend is a config choice** via `OTEL_EXPORTER_OTLP_ENDPOINT`. Identical code everywhere; environment picks the destination. Both Phoenix (native) and Langfuse v3 (OTLP endpoint) ingest OTLP.

## Current footprint (what changes)
- **Core module `observability/` (2 files):** `langfuse.py` (startup client init) + `tracing.py` (a *manual* Ollama GENERATION span + token-usage mapping — deliberately NOT LangChain-auto, because one LLM path is raw `httpx`, not ChatOllama).
- **~20 `@observe` spans** — graph nodes (intake, intent_router, llm_caller, planner, rag_retriever, risk_assessor, memory_writer, output_formatter, skill_dispatcher, history_appender, human_review) + skills (contract_review, legal_research, compliance_check, drafting, contract_generation) + routes (query, resume).
- **~10 `langfuse_context.update_current_trace / update_current_observation`** calls — the **custom metadata**: identity (`user_id`, `user_name`, `session_id`, input in query.py) + domain signals (contract type, risk verdict).
- **Config** (3 keys: host/public/secret) + **deps** (`langfuse`) + **docker-compose** (the Langfuse v3 block) + **2 test files** (`test_observability.py`, `test_config.py`).
- **No OTel/OpenInference present yet** — greenfield on that side.

## Two decisions
### D1 — Local backend
- **(a) Phoenix everywhere, drop Langfuse** — simplest: one tool, one UI, lightest local footprint (single container). Code is identical to (b); this only changes what `docker-compose` runs locally.
- **(b) Langfuse local, Phoenix on VM** — same single OTel instrumentation, export endpoint swapped by env (Langfuse v3 accepts OTLP). Keeps Langfuse's UI/evals in dev; still runs the heavy Langfuse stack locally.
- **✅ DECIDED: (b)** — keep Langfuse locally, **a _dedicated_ Phoenix on the VM in legal-plugin's OWN compose** (NOT compliance-bot's — see resolved open question). One OTel instrumentation; the OTLP endpoint is swapped by env. (Instrumentation code is identical to (a); only the local compose differs.)

### D2 — Span strategy
- **(a) Port the hand-rolled spans** — replace `@observe(name=…)` with a thin `@traced(name)` OTel decorator (written once); keep the deliberate per-node span tree + the manual httpx token-usage span. Mechanical over ~20 sites.
- **(b) Auto-instrument** via OpenInference LangChain/LangGraph — less code, but a *different* (auto) trace shape, and it **misses the raw-httpx `llm_caller` path** (that one still needs a manual span).
- **✅ DECIDED: (a)** — port the hand-rolled spans to `@traced`; preserves the per-node tree + the raw-httpx `llm_caller` path.

## Work breakdown (DECIDED: D1b — Langfuse local + Phoenix VM · D2a — port spans)
| # | Change | Kind |
|---|---|---|
| 1 | `observability/otel.py` (replaces `langfuse.py`): OTel TracerProvider + OTLP span exporter (endpoint + optional headers from env — **Langfuse OTLP needs Basic-auth `public:secret`**) + OpenInference LangChain instrumentor | **real logic** |
| 2 | `observability/tracing.py`: rewrite the GENERATION wrapper → OTel span with OpenInference attrs (`llm.model_name`, `llm.token_count.*`, in/out) | **real logic** |
| 3 | `observability/spans.py` (new): `@traced(name)` decorator + `set_trace_attributes()` / `set_gen_attributes()` helpers over the OTel API | **write once** |
| 4 | Sweep ~20 sites: `from langfuse.decorators import observe` → `from observability.spans import traced`; `@observe(name="x")` → `@traced("x")` | mechanical |
| 5 | Sweep ~10 metadata sites: `langfuse_context.update_current_trace(user_id=…)` → `set_trace_attributes(user_id=…, session_id=…, user_name=…)` | mechanical |
| 6 | `config.py`: drop 3 langfuse keys → add `otel_exporter_otlp_endpoint`, `otel_service_name`, `tracing_enabled` (unset ⇒ tracing off, as today) | small |
| 7 | `requirements(-runtime).txt`: + `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`, `openinference-instrumentation-langchain`; − `langfuse` | small |
| 8 | docker-compose: local **keeps** the Langfuse v3 block; set local `OTEL_EXPORTER_OTLP_ENDPOINT` → Langfuse OTLP (`http://localhost:3000/api/public/otel`, Basic-auth = public:secret). remote overlay → **add a dedicated `phoenix` service** (own named volume, `restart: unless-stopped`) to legal-plugin's stack; endpoint = `http://phoenix:6006/v1/traces` (HTTP) or `phoenix:4317` (gRPC). **Independent of compliance-bot's lifecycle.** | small |
| 9 | tests: rewrite `test_observability.py` (assert span attrs via an in-memory OTel span exporter instead of monkeypatching `langfuse_context`); update `test_config.py` keys | medium |
| 10 | docs: README observability section, CLAUDE.md backend note, `.env(.remote).example` | small |

## Size / risk
- **~20–25 files touched, but ~80% are 2-line mechanical swaps.** Real logic only in items 1–3. **Effort: Medium** — a focused day, or a subagent-driven sweep (mechanical items 4–5 fan out cleanly).
- **Tracing must stay non-fatal.** Today: Langfuse disabled ⇒ `@observe` no-ops. Preserve exactly: exporter unset/unreachable ⇒ spans no-op, never break a turn. The current `test_observability.py` "disabled" tests are the guardrail to carry over.
- **Identity mapping.** `user_name` (just shipped) → an OTel attribute. Phoenix shows it as a span attribute; Langfuse's OTLP ingestion maps `user.id`/`session.id` to its native fields — confirm the exact attribute names Langfuse expects during item 1.
- **Not urgent.** `audit_log` already gives per-user attribution on the VM today; do this when you want the trace *UI*, not before testers.

## Open questions
- Does Langfuse's OTLP ingestion map our custom `user_name` cleanly, or only `user.id`/`session.id`? (Verify in item 1; worst case `user_name` rides as a generic attribute — fine for Phoenix.)
- ~~Reuse compliance-bot's Phoenix or dedicated?~~ **RESOLVED → dedicated.** Reusing compliance-bot's Phoenix couples our tracing to *their* lifecycle: `docker compose down` on compliance-bot stops `rag-phoenix-1` and takes our trace sink with it. A dedicated Phoenix in legal-plugin's compose (~few hundred MB; VM has ~5 GB free) is lifecycle-independent, isolates the two teams' traces + retention, and needs no cross-team coordination on restarts/upgrades. Cost: one small container — worth it.
