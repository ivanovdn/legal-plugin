# Testing the failure drills by hand

## What this document is for

`scripts/check.sh` proves the seam is *wired*: every one of the 22 reason codes
is declared and used, `mark_failed` sets a span ERROR, `record_degradation`
emits an event, `app.outcome` derives correctly from a reason list. What it
cannot prove is that a **real outage** reaches a trace — that the exception
Ollama actually throws is the one `llm_caller` catches, that a stopped Postgres
container produces `audit_write_failed` and not a hang, that a turn which loses
its grounding still answers and still says nothing to the attorney.

Those are four drills. This document is how to run them, and what happened when
they were run.

Read it alongside [docs/testing-compaction.md](testing-compaction.md), which is
the same kind of document for the other feature with no automatable gate.

## Why the drills matter more here than usual

OpenTelemetry marks a span `ERROR` only when an exception **propagates out of**
the `with start_as_current_span(...)` block. This application has 40
`except Exception` sites and almost nothing propagates — that is deliberate and
correct (*tracing must never break a turn*, *retrieval must never break a
review*, *Redis down → stateless run*). The consequence, until 2026-09-16, was
that a turn where Ollama timed out and the attorney read an error string was
**byte-identical in the trace UI to a healthy turn**.

So every signal these drills look for is set *by hand at the point of the
catch*. A wiring mistake does not make anything crash. It makes a bad turn look
fine — which is exactly the state the feature was built to leave.

## What you are looking for

| Field | Where | Means |
|---|---|---|
| `app.outcome` | request **root** span only | `ok` / `degraded` / `failed` — the one field to sort and filter on |
| `app.degradations` | request root span | JSON list of the reason codes accumulated this turn |
| span `status_code = ERROR` on the **root** | root | **the attorney did not get their answer** |
| span `status_code = ERROR` on a **node** | `llm_caller`, `db.*`, … | where it broke |
| `degradation.reason` / `.detail` | attributes of the **failed** span | which of the 8 failure codes, and the exception class |
| `degradation` **event** | on whichever span recorded it | a degradation — carries `.reason`, `.announced`, `.detail` |
| `announced=true` | degradation event | the attorney was told (a pane banner) |
| `announced=false` | degradation event | **class 3** — silently worse, nobody told |

Two spans going ERROR on a failed turn is intended, not duplication: the node
says *where*, the root says *the attorney got nothing*. Do not "fix" either.

The request root is named **`query:<task_type>`** (`query:contract_review`,
`query:research`, …) — `submit_query` renames it via `set_trace_attributes`.
There is no span called plain `query`; searching for one finds nothing. Resume
is `resume:<session_id>`, and `/api/compact` is `compact`.

## Setting up

All four drills run against **Phoenix**, not Langfuse. Drill 3 *requires* it
(see that drill), and using one backend for all four keeps the read-back
commands identical.

```bash
# .env — comment out the Langfuse basic-auth header, point at Phoenix:
#   OTEL_EXPORTER_OTLP_HEADERS=
#   OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:6006
bash scripts/start.sh 2>&1 | tee /tmp/backend.log
```

`otel_*` is `@lru_cache`'d in `get_settings`, so **every `.env` change here
needs a backend restart** — that bites twice, in setup and again in drill 4.

**Phoenix files our spans under the project `default`** — observed, not
assumed: after a live turn, `GET /v1/projects` listed no `legal-triage` and the
spans were in `default`. Phoenix does not name projects from `service.name` /
`otel_service_name`; it keys off an `openinference.project.name` resource
attribute we do not set. Look in `default` or you will conclude nothing was
exported.

Read a trace back without the UI — this is what every drill below uses:

```bash
# All spans of one trace, outcome first. $TID is the trace_id the API returned.
curl -s "http://localhost:6006/v1/projects/default/spans?limit=400" \
| python3 -c '
import json,sys
tid=sys.argv[1]
for s in sorted([x for x in json.load(sys.stdin)["data"]
                 if x["context"]["trace_id"]==tid], key=lambda x:x["start_time"]):
    a=s["attributes"]
    keep={k:v for k,v in a.items()
          if k.startswith(("app.","degradation","llm.ollama")) or k=="http.url"}
    print(f"{s[\"name\"]:<22} {s[\"status_code\"]:<6} {keep}")
    if s["status_message"]: print("    msg:", s["status_message"][:160])
    for e in s["events"]:
        if e["name"]=="degradation": print("    EVENT", json.dumps(e["attributes"]))
' "$TID"
```

`POST /api/query` returns the `trace_id` in its payload, so `$TID` never has to
be hunted for in a UI.

**The pane's three degradation signals** — the only things an attorney ever
sees — are `memory_degraded` (top level, **chat tab only**),
`report.context_truncated` (both tabs) and `report.review_persist_error`
(findings tab). Checking those three in the JSON response *is* checking the
pane; nothing else in the payload renders as a warning.

---

# The four drills

Every drill below was executed on **2026-09-16** against the local stack —
macOS, Ollama `qwen3.6:latest` native on `:11434`, Docker for Qdrant / Redis /
`app-db`, Phoenix `hermes-phoenix-1` on `:6006`. Real trace ids and real
observed values; nothing here is reasoned from the code.

---

## Drill 1 — Ollama down

**Expect:** root `status=ERROR`, `app.outcome=failed`, `llm_call_failed`, and
ERROR on **both** the root and `llm_caller`.

### Stop it

The macOS Ollama app **supervises** `ollama serve` and respawns it. Killing the
server alone does nothing:

```bash
pkill -f "Resources/ollama serve"      # NOT ENOUGH — a new pid appears in ~2s
```

`osascript -e 'quit app "Ollama"'` is also unreliable — it returned
`execution error: Ollama got an error: User canceled. (-128)` and the port
stayed up. Kill the tray app first, then the server:

```bash
pkill -f "Ollama.app/Contents/MacOS/Ollama"
sleep 1
pkill -f "Resources/ollama serve"
curl -s -m 3 -o /dev/null -w "%{http_code}\n" http://localhost:11434/api/tags   # want 000
```

### Run one review turn

```bash
curl -s http://localhost:8000/api/query -H 'Content-Type: application/json' \
  -H 'X-User-ID: drill1' \
  -d '{"request":"Review this NDA.","task_type":"contract_review",
       "uploaded_text":"MUTUAL NON-DISCLOSURE AGREEMENT\n\n1. Confidential Information. ..."}'
```

### Result — 2026-09-16, **PASS**

Trace `53ff85d0549ab733b482695b5b0c05da`, turn returned in **0.5 s**.

```
query:contract_review  ERROR  {'app.degradations': '["llm_call_failed"]', 'app.outcome': 'failed'}
    msg: llm_call_failed
intake                 UNSET
intent_router          UNSET
skill_dispatcher       UNSET
contract_review        UNSET
rag_retriever          UNSET
llm_caller             ERROR  {'degradation.reason': 'llm_call_failed', 'degradation.detail': 'ConnectError'}
    msg: ConnectError
POST                   ERROR  {'http.url': 'http://localhost:11434/api/chat'}
    msg: ConnectError: [Errno 61] Connection refused
risk_assessor          UNSET
output_formatter       UNSET
history_appender       UNSET
memory_writer          UNSET
db.write_audit_log     UNSET
db.save_review         UNSET
```

Three things in that listing are the whole point of the feature:

1. **The HTTP response was `{"status": "ok"}` with 200.** It always was. That is
   the root finding of the design spec, reproduced: the API cannot tell you this
   turn failed, and before this branch the trace could not either. `app.outcome`
   is now the only place in the system that says `failed`.
2. **ERROR on exactly two spans** — `llm_caller` (where) and the root (the
   attorney got nothing). `degradation.reason` on `llm_caller` names *which* of
   the eight failure codes, so a filter for `llm_call_failed` finds this turn
   without reading the stack trace.
3. **The httpx `POST` child carries the real cause** —
   `ConnectError: [Errno 61] Connection refused` against
   `http://localhost:11434/api/chat`. That span exists because of the httpx
   auto-instrumentation, and it is the independent confirmation that
   `llm_caller`'s **module-level `httpx.post`** is instrumented: the
   instrumentor patches `Client.send` and `httpx.post` builds its client
   internally, so this was an expectation rather than a fact until it was seen
   on a live turn.

### Restore

```bash
open -a Ollama
for i in $(seq 1 30); do
  curl -s -m 2 -o /dev/null -w '%{http_code}\n' http://localhost:11434/api/tags | grep -q 200 && break
  sleep 1
done
curl -s http://localhost:11434/api/tags   # models listed again
```

Verified back at `200` with all five models 24 s after the drill started.

---

## Drill 2 — `app-db` down

**Expect:** `app.outcome=degraded`, `audit_write_failed` with `announced=true`,
the turn still answers, amber banner in the pane.

```bash
docker compose stop app-db
# ...run the same contract_review turn as drill 1...
docker compose start app-db          # restore
```

### Result — 2026-09-16, **PASS**

Trace `15993baba82923806678a9a2213ea7a2`, turn answered in **102.8 s**,
`{"status": "ok"}`.

```
query:contract_review  UNSET  {'app.degradations': '["audit_write_failed", "review_persist_failed"]',
                               'app.outcome': 'degraded'}
llm_caller             UNSET  {'llm.ollama.load_ms': 8722, 'llm.ollama.prompt_eval_ms': 9542,
                               'llm.ollama.eval_ms': 83871, 'llm.ollama.total_ms': 102166}
memory_writer          UNSET
    EVENT {"degradation.reason": "audit_write_failed",   "degradation.announced": true, "degradation.detail": "AdminShutdown"}
    EVENT {"degradation.reason": "review_persist_failed", "degradation.announced": true, "degradation.detail": "AdminShutdown"}
db.write_audit_log     ERROR
    msg: AdminShutdown: terminating connection due to administrator command
db.save_review         ERROR
    msg: AdminShutdown: terminating connection due to administrator command
```

Pane payload: `memory_degraded: true`,
`review_persist_error: "terminating connection due to administrator command"` —
the class-2 promise kept, on the findings tab.

**Expect two codes, not one.** A review turn writes the audit row *and* the
review, so a dead `app-db` fails both. `review_persist_failed` is LOUD by design
and reaches the pane on its own; `audit_write_failed` is the one that would
otherwise be invisible. Root status stays `UNSET` — correct: an answer landed,
so this is not an error.

**The two `db.*` spans are doing real work here.** They are named per operation
(`db.write_audit_log`, not a pile of spans called "db"), and each carries
Postgres's own `AdminShutdown` message. Without them the only evidence would be
`detail: "AdminShutdown"` on the event.

**Free observation worth knowing about:** `llm.ollama.load_ms = 8722`. Ollama
had been restarted 30 seconds earlier, so the model was cold and the call paid
an 8.7-second **reload** before a single token. Compare drill 3's `13 ms` on a
warm model. This is the attribute added because the `ollama_num_ctx` incident —
a 4.7 s reload on *every* call against a shared box — sat in `load_duration` in
the response payload the whole time and was thrown away.

### Restore

```bash
docker compose start app-db
docker ps --filter name=app-db --format '{{.Status}}'     # want "(healthy)"
```

Healthy 4 s after start.

---

## Drill 3 — Redis down

**Expect:** `app.outcome=degraded`, `checkpointer_unavailable`, the turn still
answers, amber banner.

> **Run this against Phoenix. Not Langfuse.** `docker compose stop redis` also
> takes **Langfuse ingestion** down — they share the Redis container. You would
> degrade the turn correctly and then have no trace to look at, which reads as
> broken instrumentation. Measured during this drill: the Langfuse worker logged
> **19,814** Redis error lines in three minutes while Redis was stopped. Phoenix
> does not touch our Redis.

```bash
docker compose stop redis
# ...run the same contract_review turn...
docker compose start redis           # restore
```

### Result — 2026-09-16, **PASS**

Trace `67ad2cc2675625f67ff621a4d42600ab`, turn answered in **84.2 s**,
`{"status": "ok"}`.

```
query:contract_review  UNSET  {'app.degradations': '["checkpointer_unavailable"]', 'app.outcome': 'degraded'}
    EVENT {"degradation.reason": "checkpointer_unavailable", "degradation.announced": true,
           "degradation.detail": "ConnectionError"}
llm_caller             UNSET  {'llm.ollama.load_ms': 13, 'llm.ollama.prompt_eval_ms': 79,
                               'llm.ollama.eval_ms': 83962, 'llm.ollama.total_ms': 84075}
db.write_audit_log     UNSET
db.save_review         UNSET
```

Backend log, same second:

```
ERROR api.routes.query: Checkpointer (Redis) failed mid-invoke
  (Error 61 connecting to localhost:6379. Connection refused.)
  — degrading to a stateless run; chat_history is lost this turn (memory_degraded=True).
```

Pane payload: `memory_degraded: true`.

**Note where the event lands:** on the **root**, not on a node — because
`record_degradation` was called from `submit_query`'s own `except` branch, which
runs inside the root span. Events land on whichever span was current at the
catch. Do not go looking for it under `memory_writer`.

**There are two producers of `checkpointer_unavailable` and this drill only
exercises one.** This is the *mid-invoke* path: Redis was up when the backend
booted, so `_checkpointer_active` was True and the failure surfaced as a
`RedisError` out of `graph.invoke`, caught, degraded to the stateless graph.
The other producer is `_record_startup_checkpointer_degradation()`, for a
checkpointer that was **never** available in this process. To exercise that one,
stop Redis *before* `start.sh` — `_get_graph()` caches the compiled graph, so
`build_checkpointer()` runs exactly once and the startup-absent condition has to
be re-checked per turn in the route handler rather than recorded at the build
site. Expect `detail: "startup_absent"` instead of an exception class.

### Restore

```bash
docker compose start redis
docker ps --filter name=legal-plugin-redis --format '{{.Status}}'   # want "(healthy)"
```

Healthy 4 s after start. Langfuse ingestion recovers on its own once Redis is
back; the worker's error flood stops.

---

## Drill 4 — Grounding failure (the thesis)

**Expect:** `app.outcome=degraded`, `chat_grounding_failed` with
`announced=false`, and **nothing whatsoever in the pane**.

This is the drill the whole feature exists for. A doc-chat turn that loses its
grounding answers *fluently and confidently*, with no banner, no error, and —
before this branch — no record anywhere except one `logger.warning` with no
`turn_id` in it, interleaved with every concurrent turn.

```bash
# .env:  QDRANT_URL=http://localhost:6399     (a dead port)
bash scripts/start.sh          # REQUIRED — get_settings is @lru_cache'd
```

Then send a **doc-chat** turn on an uploaded **SOW**, with a question that trips
`_needs_grounding`:

```bash
curl -s http://localhost:8000/api/query -H 'Content-Type: application/json' \
  -H 'X-User-ID: drill4' \
  -d '{"request":"Does the termination notice in this SOW conflict with the governing MSA?",
       "task_type":"research",
       "uploaded_text":"STATEMENT OF WORK NO. 3\n\nThis Statement of Work is issued under ..."}'
```

Three conditions, all required, and each silently no-ops the drill if missed:

- `task_type: "research"` **with** `uploaded_text` — that is the doc-chat path.
- The document must **detect as a SOW**, because the Qdrant call is the
  governing-MSA lookup. Check with
  `python -c "from skills.grounding import detect_contract_type; print(detect_contract_type(open('sow.txt').read()))"`
  → `('sow', False)`.
- The question must be **grounded**. Chat grounding is conditional
  (`chat_conditional_grounding`, default True); a lean question like *"who signs
  this?"* never calls `_build_chat_grounding` at all and the drill passes
  vacuously. *"conflict with the governing MSA"* trips it. See
  [docs/testing-compaction.md](testing-compaction.md) for the grounded/lean list.

### Result — 2026-09-16, **PASS**

Trace `ab913ed17ee12104c081120cd1d6dfd8`, turn answered in **15.0 s**,
`{"status": "ok"}`.

```
query:research         UNSET  {'app.degradations': '["chat_grounding_failed"]', 'app.outcome': 'degraded'}
legal_research         UNSET
    EVENT {"degradation.reason": "chat_grounding_failed", "degradation.announced": false,
           "degradation.detail": "ResponseHandlingException"}
db.load_latest_review  UNSET
POST                   ERROR  {'http.url': 'http://localhost:6399/collections/legal_docs/points/scroll'}
    msg: ConnectError: [Errno 61] Connection refused
db.latest_to_id        UNSET
db.load_segments       UNSET
db.load_recent         UNSET
db.row_lengths_after   UNSET
doc_chat               UNSET
POST                   UNSET  {'http.url': 'http://localhost:11434/api/chat'}
...
db.write_audit_log     UNSET
db.append_turn         UNSET
```

**The pane, verbatim from the response payload:**

```
memory_degraded:      false
context_truncated:    null
review_persist_error: null
```

All three absent. Nothing amber, nothing red, no notice of any kind. Exactly as
designed, and exactly the problem.

**And here is what the attorney got — against a control run of the identical
question on the identical document with Qdrant back up** (trace
`6bc215b2c1027b647a878f0aff4f5132`, the restore-verification turn below).

| | degraded | healthy control |
|---|---|---|
| prompt | 39,346 chars / **8,420** input tokens | 64,657 chars / **13,224** input tokens |
| `GOVERNING MSA` block in prompt | **absent** | present |
| `SOW-015` (playbook) in prompt | present | present |
| turn time | **15.0 s** | 14.6 s |
| `app.outcome` | `degraded` | `ok` |
| anything in the pane | **nothing** | nothing |

> **Degraded:** "Yes, Section 4 creates a direct conflict by **overriding the
> MSA's notice period**. … This deviation from the standard framework is a Red
> item under SOW-015 and requires Finance, Delivery, and CLCO approval before
> signature."
>
> **Healthy:** "Yes, Section 4 of the SOW conflicts with the MSA. The SOW allows
> termination for convenience with only ten (10) days' written notice, whereas
> **the MSA does not specify a termination-for-convenience notice period in the
> provided excerpt**. … Per the Approval Matrix, termination for convenience
> shorter than 60 days requires Finance, Delivery, and CLCO approval." *(plus a
> proposed redline to 60 days)*

Read those two together. The ungrounded answer asserts the MSA **has** a notice
period that Section 4 overrides. The grounded answer, having actually read the
MSA, says it **does not specify one** — and then gives the attorney a redline.
The degraded answer's central factual claim about the MSA is fabricated, and it
is fabricated *fluently*, in the house style, citing a real playbook rule id and
the correct approval chain. Nothing about its tone, its length, its citations or
its confidence marks it as the worse answer.

**4,804 tokens of governing MSA went missing and the turn was not even faster**
(15.0 s vs 14.6 s). Timing is not a tell. The answer is not a tell either — it
is wrong, but only demonstrably so against a control run that nobody has in
production. The pane says nothing either way. `app.outcome` on the root is the
only place in the entire system where these two turns are distinguishable.

**A detail worth knowing before you read a `chat_grounding_failed` in the
field:** `_build_chat_grounding` wraps *three* steps in one `try` — contract
type detection, playbook load, and the MSA attach. The playbook had already
loaded when the MSA lookup threw, so this turn lost the MSA and kept the
playbook; a failure one step earlier would lose both. The code means *partial or
total* — read `degradation.detail` (here `ResponseHandlingException`, the
qdrant-client wrapper around the refused connection) and the httpx child span to
tell which.

**This drill needs an MSA actually on file**, or it degrades into a tautology: a
failed lookup and a successful lookup that finds nothing produce the same empty
MSA block. Locally there is one — `Trinetix Model Msa 2025 (3)-1`, 73,152 chars
under `client_id="internal"`, put there by `scripts/ingest_demo_msa.py`. Confirm
before you start:

```bash
uv run python -c "from rag.related_docs import get_parent_msa; \
r=get_parent_msa('internal'); print(r[0], len(r[1]))"
```

### Restore

```bash
# .env:  QDRANT_URL=http://localhost:6333
bash scripts/start.sh          # REQUIRED — @lru_cache again
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:6333/collections   # want 200
```

---

## Leaving the stack the way you found it

After the last drill, put `.env` back to the shipped values — **both** the
Qdrant URL and the OTLP endpoint/headers — restart, and prove the whole path
works on the default configuration rather than assuming it:

```bash
# expect app.outcome "ok" and no app.degradations
AUTH=$(echo -n 'pk-lf-local:sk-lf-local' | base64)
curl -s -H "Authorization: Basic $AUTH" \
  "http://localhost:3000/api/public/traces/$TID" \
| python3 -c 'import json,sys; a=(json.load(sys.stdin)["metadata"] or {}).get("attributes",{}); \
print(a.get("app.outcome"), a.get("app.degradations"))'
```

Done on 2026-09-16: trace `6bc215b2c1027b647a878f0aff4f5132`, a grounded
doc-chat turn on the restored config, **`app.outcome: ok`**, no
`app.degradations`, 22 observations — in **Langfuse**, confirming the default
local backend still receives everything. Send the *same question on the same
document* as drill 4 and this turn doubles as that drill's control: 13,224 input
tokens against the degraded run's 8,420, `GOVERNING MSA` present, and an answer
that contradicts the ungrounded one.

---

## Things that look like bugs and are not

**A `GraphInterrupt` marks `human_review` ERROR.** A high-risk research turn
routes to `human_review`, which raises `GraphInterrupt` to pause the graph. That
exception *propagates out* of the node's span, so OTel's default handling marks
it ERROR and attaches an `exception` event — with the entire interrupt payload,
including the drafted answer, in the message. Nothing failed: the root still
stamps `app.outcome: ok`. Observed 2026-09-16 on trace
`c429416c497f26bede194c837152b528` (a baseline *"what is an NDA?"* turn), where
`human_review` is ERROR and `query:research` is `UNSET` / `app.outcome: ok`.
This is why the contract that matters is **`ERROR` on the root**, not `ERROR`
anywhere in the trace. Filter on the root.

**`/api/compact`'s two early 4xx guards carry no `app.outcome`.** Compaction
disabled (403) and missing `document_id` (400) raise `HTTPException` before any
outcome is stamped. Checked live 2026-09-16 — `POST /api/compact` with an empty
`document_id` returned 400 and produced trace
`f7ab9f7bbbe28c3dfc664422c765032c`, a `compact` span at **level ERROR** with
`statusMessage: HTTPException: 400: document_id is required` and **no
`app.outcome` attribute at all**. So the span is honest, but an analyst
filtering strictly on `app.outcome != "ok"` will not see it and has to fall back
to status. Deliberate — there is no reason code for either guard, and stamping
`ok` before a 4xx would be worse.

**An expired session on resume stamps `ok`.** `resume_query`'s
empty-prior-state branch — where `get_state` *succeeds* and finds nothing — is
deliberately reason-code-free. A checkpoint that has aged out is TTL working as
designed, not a failure, and marking it ERROR would fill the one filter this
work exists to make meaningful with routine expiry. The attorney is still told,
via `status="error"` in the HTTP payload.

**Tracing's own `except` blocks record nothing.** `spans.py`, `otel.py`, and the
best-effort `append_turn` / `interaction_event` writes are class 4 and are
excluded from the vocabulary on principle. Observability reporting its own
best-effort catches as application degradations is how a dashboard becomes
decoration.

**A RAG turn emits ERROR `POST` spans to `http.url: "/"`.** Those are the
reranker — `RERANKER_ENABLED=true` with `RERANKER_URL=` empty means every call
reaches `httpx.post("")` → `httpx.UnsupportedProtocol`, swallowed by the bare
`except` at `rag/reranker.py:119`. Seen on the same baseline trace, alongside
matching backend log lines: `WARNING rag.reranker: Reranker error: Request URL
is missing an 'http://' or 'https://' protocol. — using original ranking`. The
reranker has never run. It is a genuine
silent degradation, it is tracked as a follow-up in
[docs/wiki.md](wiki.md#follow-ups--roadmap), and it is *not* in the vocabulary
because the RAG pipeline was explicitly descoped from this work — so it shows up
as httpx noise rather than as a reason code. Do not chase it as a drill failure.
