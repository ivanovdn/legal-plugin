# Design — OpenTelemetry tracing migration (one instrumentation, backend by env)

**Status:** approved design · **Date:** 2026-08-11 · **Supersedes:** `docs/tracing-otel-migration-scope.md` (scope/decisions D1b, D2a)

## Problem

The backend is instrumented with the **Langfuse SDK v2** (`from langfuse.decorators import observe, langfuse_context`, `langfuse.model.ModelUsage`). Langfuse's ~3–4 GB v3 server stack doesn't fit the VM, and compliance-bot already runs Phoenix there. Reusing Phoenix on the VM while keeping Langfuse locally would mean **two vendor instrumentation paths** — the maintenance smell to avoid. Today the VM's `docker-compose.remote.yml` even points `LANGFUSE_HOST` at a `langfuse-web` container that isn't in the VM's `up` list, so tracing is **silently dead on the VM**.

## Goal

**One vendor-neutral instrumentation (OpenTelemetry + OpenInference semantic conventions); the trace backend is a config choice** via the OTLP endpoint env var. Reproduce **today's exact span tree, 1:1** — local dev exports to Langfuse v3 (OTLP), the VM exports to a dedicated Phoenix. Identical code and identical trace shape everywhere.

## Scope decision (locked)

**Port only — faithful swap.** Reproduce our hand-rolled span tree on OTel; do **not** enable the OpenInference LangChain auto-instrumentor. Rationale: keeping our manual `traced_invoke` generation spans *and* auto-instrumenting LangChain would emit **two overlapping LLM spans** for every skill `.invoke()` (double-counted tokens, a divergent tree). Auto-instrumentation is a separate, later enhancement. We pull only the tiny `openinference-semantic-conventions` (attribute-name constants), **not** `openinference-instrumentation-langchain`.

Backend/local decisions carried from the scope doc: keep Langfuse **locally** (dev UI/evals), run a **dedicated** Phoenix on the VM inside legal-plugin's *own* compose (lifecycle-independent of compliance-bot); port the hand-rolled spans (not auto-instrument).

## Current footprint (what changes)

- **`observability/langfuse.py`** — startup client init (`init_observability()`, `auth_check()`, sets `LANGFUSE_*` env, `is_enabled()`).
- **`observability/tracing.py`** — pure token-mapping fns (`ollama_usage`, `message_usage`, `_message_model`) + two GENERATION wrappers (`traced_invoke`, `traced_agent_invoke`) that record model + token usage.
- **~18 `@observe` decorator sites** (+ the 2 `tracing.py` wrappers, handled in #3) — 11 graph nodes (`intake`, `intent_router`, `planner`, `llm_caller`, `rag_retriever`, `risk_assessor`, `memory_writer`, `output_formatter`, `skill_dispatcher`, `history_appender`, `human_review`), 5 skills (`contract_review`, `legal_research`, `compliance_check`, `drafting`, `contract_generation`), 2 routes (`query`, `resume`). Generation spans use `as_type="generation"`.
- **~10 `langfuse_context.*` metadata calls**, split:
  - **Trace-level** (`update_current_trace`): `query.py` (`user_id`, `session_id`, `input`, `metadata={user_name}`), `resume.py` (`session_id`, `input`), `intake.py` (`user_id`, `session_id`, `tags`), `intent_router.py` (`tags`), `risk_assessor.py` (`metadata={review_risk_level, requires_attorney}`), `contract_review.py` (`metadata={contract_type_detected, contract_type_ambiguous}` and `{msa_attached, msa_doc_title}`).
  - **Observation-level** (`update_current_observation`): `intent_router`, `llm_caller`, `planner` (input/output/model/usage/metadata) + `traced_invoke`/`traced_agent_invoke` (name/input/output/model/usage).
- **Config** (`config.py`): `langfuse_host/public/secret` + a stale unused `phoenix_host`.
- **Deps**: `langfuse>=2.0,<3.0` in both `requirements.txt` and `requirements-runtime.txt`.
- **Compose**: local Langfuse v3 block (`docker-compose.yml`), remote overlay (`docker-compose.remote.yml`).
- **Tests**: `tests/test_observability.py` (monkeypatches `langfuse_context`), `tests/test_config.py`.
- **Startup seam**: `api/main.py:22` calls `init_observability()` in the lifespan.

## Architecture — three real-logic modules

### `observability/otel.py` (replaces `langfuse.py`)
`init_observability()`:
- Read config. If `tracing_enabled` is false or the endpoint is empty → log "tracing disabled" and **return without setting a provider** (OTel's default no-op tracer takes over).
- Else build `TracerProvider(resource=Resource.create({"service.name": otel_service_name}))`, attach `BatchSpanProcessor(OTLPSpanExporter(endpoint=..., headers=...))`, call `trace.set_tracer_provider(...)`. Wrap in try/except → on failure log + leave the no-op provider (mirrors today's `auth_check` failure → disabled).
- Keep `is_enabled()`.
- Called from the same `api/main.py` lifespan seam.

### `observability/spans.py` (new — the import seam for the whole codebase)
- `tracer = trace.get_tracer("legal-triage")`
- `@traced(name, kind=None)` — decorator replacing `@observe(name=...)`. Runs the wrapped fn inside `tracer.start_as_current_span(name)`; `kind="LLM"` sets `openinference.span.kind = "LLM"` (replaces `as_type="generation"`). All call sites are synchronous defs. **Root-span registration:** on entry, if the root-span contextvar is empty, set it to this span (save the token); reset on exit. So the outermost `@traced` span (the `query`/`resume` route span) becomes the trace root.
- `set_trace_attributes(**kw)` — replaces `langfuse_context.update_current_trace`. Writes to the **root span** from the contextvar (falls back to the current span if none). Maps to OpenInference conventions (see table). Best-effort — never raises, guards `None`/non-recording spans.
- `set_gen_attributes(*, name=None, input=None, output=None, model=None, usage=None, metadata=None)` — replaces `langfuse_context.update_current_observation`. Writes to the **current** span. Best-effort.

**Root-span contextvar** is safe here: the graph runs synchronously in the request thread (sync FastAPI `def`), so context propagation via `contextvars` correctly scopes one root per request.

### `observability/tracing.py` (rewritten)
- **Keep byte-for-byte:** `ollama_usage`, `message_usage`, `_message_model` (pure token/model mappers). Replace the `from langfuse.model import ModelUsage` import with a local `TypedDict` of the same `{input, output, total, unit}` shape.
- **Rewrite the two wrappers:** `traced_invoke`/`traced_agent_invoke` keep their exact signatures and value pass-through; decorate with `@traced(name, kind="LLM")` and call `set_gen_attributes(...)` instead of `langfuse_context.update_current_observation(...)`.

## Attribute mapping (OpenInference — both Langfuse v3 and Phoenix ingest these)

| Today (langfuse) | OTel / OpenInference attribute | Span |
|---|---|---|
| trace `user_id` | `user.id` | root |
| trace `session_id` | `session.id` | root |
| trace `tags=[task_type]` | `tag.tags` | root |
| trace `input` (request) | `input.value` | root |
| trace `metadata={user_name, contract_type_detected, contract_type_ambiguous, review_risk_level, requires_attorney, msa_attached, msa_doc_title}` | `metadata.<key>` (one attr per key) | root |
| obs `model` | `llm.model_name` | current |
| obs `usage.input` / `.output` / `.total` | `llm.token_count.prompt` / `.completion` / `.total` | current |
| obs `input` / `output` | `input.value` / `output.value` | current |
| obs `metadata={classified_as, skill_plan, task_type, chunks_count, temperature}` | `metadata.<key>` | current |
| `@observe(as_type="generation")` | `openinference.span.kind = "LLM"` | that span |

`user_name` rides as `metadata.user_name` — a generic attribute that Phoenix shows on the span and Langfuse keeps as metadata. This **resolves the scope doc's open question**: no Langfuse-specific `user_name` mapping is needed because Phoenix is the VM (production) target; local Langfuse keeps it as metadata, which is fine for dev.

## Config & auth (backend-neutral)

Drop `langfuse_host`, `langfuse_public_key`, `langfuse_secret_key`, **and** the stale `phoenix_host`. Add:

```python
otel_exporter_otlp_endpoint: str = "http://localhost:3000/api/public/otel"  # local → Langfuse v3 OTLP
otel_exporter_otlp_headers:  str = ""    # opaque; local Langfuse needs Basic <b64 public:secret>
otel_service_name:           str = "legal-triage"
tracing_enabled:             bool = True
```

Auth is an **opaque header string**, not vendor-named keys — config stays backend-neutral (honors hard rule #5: no compat shims, clean cutover). `.env.example` ships the local Basic header for `pk-lf-local:sk-lf-local`; Phoenix needs no header. (The OTel SDK also natively reads `OTEL_EXPORTER_OTLP_ENDPOINT` / `OTEL_EXPORTER_OTLP_HEADERS` from env — we pass them explicitly from config so the `tracing_enabled` gate and `service.name` are governed in one place.)

## Compose / deploy

- **Local** (`docker-compose.yml`): the Langfuse v3 block **stays**. Backend env gains `OTEL_EXPORTER_OTLP_ENDPOINT=http://langfuse-web:3000/api/public/otel` + `OTEL_EXPORTER_OTLP_HEADERS` (Basic auth for the local keys).
- **VM** (`docker-compose.remote.yml`): **add a dedicated `phoenix` service** — `arizephoenix/phoenix` image, port `6006`, its own named volume, `restart: unless-stopped`. Backend env: replace the dead `LANGFUSE_HOST=http://langfuse-web:3000` with `OTEL_EXPORTER_OTLP_ENDPOINT=http://phoenix:6006/v1/traces` (no auth header). Independent of compliance-bot's `docker compose` lifecycle; ~few hundred MB (VM has ~5 GB free).

## Non-fatal guarantee (carried over verbatim)

Tracing disabled or endpoint unreachable ⇒ spans no-op, **never break a turn**:
- `tracing_enabled=false` / no endpoint → no provider set → OTel default no-op tracer → non-recording spans, attribute-sets are free no-ops.
- `BatchSpanProcessor` exports **off-thread**, so an unreachable collector can't block or fail the request path.
- `init_observability` is best-effort (try/except → log + leave no-op), mirroring today's `auth_check`-failure-disables behavior.
- Every `set_*` helper guards a `None` / non-recording span and never raises.

The current `test_observability.py` "disabled → pass-through, never raises" guardrail is carried over as the primary safety test.

## Testing & Definition of Done

- **Unit (CI, backend-agnostic):** rewrite `tests/test_observability.py` to assert against an **`InMemorySpanExporter`** (`SimpleSpanProcessor` in a fixture) instead of monkeypatching `langfuse_context`:
  - span names + `openinference.span.kind` for generation spans,
  - `llm.token_count.*`, `input.value`/`output.value`, `llm.model_name`,
  - root-span trace attrs (`user.id`, `session.id`, `tag.tags`, `metadata.*`),
  - **disabled → no-op pass-through, never raises** (the safety guardrail).
  - Keep the pure-fn tests (`ollama_usage`, `message_usage`) unchanged.
  - The node tests (`test_llm_caller_reports_generation_usage`, `test_planner_*`, `test_intent_router_*`) swap `monkeypatch mod.langfuse_context` → run under the in-memory exporter and assert the exported span's attributes.
  - `tests/test_config.py` → new key names.
- **Live local smoke:** `bash scripts/start.sh` → run a query → trace appears in the local **Langfuse** UI with user/session/tokens on the tree.
- **VM smoke (deploy-time checklist in `docs/deploy-vm.md`, not a CI gate):** bring up the dedicated Phoenix → run a query → trace appears in **Phoenix**. Phoenix isn't reachable from dev, so this is validated at deploy, not in CI.

## Work breakdown

| # | Change | Kind |
|---|---|---|
| 1 | `observability/otel.py` — TracerProvider + `BatchSpanProcessor(OTLPSpanExporter)` + best-effort init; `is_enabled()` | real logic |
| 2 | `observability/spans.py` — `@traced`, `set_trace_attributes`, `set_gen_attributes`, root-span contextvar | real logic |
| 3 | `observability/tracing.py` — rewrite the 2 GEN wrappers onto `spans.py`; keep pure fns; drop `ModelUsage` import | real logic |
| 4 | Sweep ~18 `@observe` → `@traced` sites (11 nodes + 5 skills + 2 routes); `as_type="generation"` → `kind="LLM"` | mechanical |
| 5 | Sweep ~10 `langfuse_context.*` → `set_trace_attributes` / `set_gen_attributes` | mechanical |
| 6 | `config.py` — drop `langfuse_*` + `phoenix_host`; add 4 `otel_*`/`tracing_enabled` keys | small |
| 7 | `requirements.txt` + `requirements-runtime.txt` — `+opentelemetry-sdk +opentelemetry-exporter-otlp-proto-http +openinference-semantic-conventions −langfuse` | small |
| 8 | `docker-compose.yml` (local OTLP→Langfuse) + `docker-compose.remote.yml` (dedicated `phoenix` service, backend endpoint) | small |
| 9 | `tests/test_observability.py` rewrite (in-memory exporter), `tests/test_config.py` keys | medium |
| 10 | docs: `README` observability section, `CLAUDE.md` backend note, `.env.example` + `.env.remote.example`, `docs/deploy-vm.md` Phoenix step | small |

~20–25 files, ~80% mechanical 2-line swaps; real logic only in #1–3. **Effort: Medium** — a focused pass, or a subagent-driven sweep of #4–5 once the `spans.py` seam (#2) exists. Recommended order: land #1–3 (the seam) → sweep #4–5 → #6–8 → #9 tests → #10 docs.

## Risks

- **Root-span attribute propagation** is the one non-mechanical piece — validated by an in-memory-exporter test asserting `user.id`/`session.id` land on the *root* span while a nested node calls `set_trace_attributes`.
- **Off-thread export must stay non-fatal** — the disabled/unreachable pass-through test is the guardrail; do not regress it.
- **Langfuse OTLP auth** — local dev needs the Basic header exactly right; if wrong, spans silently 401. Verify in the live local smoke (item 1 of DoD).

## Out of scope

- OpenInference **LangChain auto-instrumentation** (deferred enhancement — would enrich the ReAct research path but duplicates our manual generation spans).
- Running Langfuse on the VM.
- Any change to `audit_log` — it already provides per-user attribution on the VM independent of the trace UI.
