# VM Deploy Runbook

Hosts the backend + built Word add-in behind one reverse proxy on **SRV-AGENT-01** (`172.20.1.10`), same origin, LLM off-box on **Spark** (`172.20.0.22:11434`). Companion to `docs/superpowers/specs/2026-07-23-vm-o365-deployment-design.md` ("spec 2 of 2" in the O365/SharePoint effort) — mirrors `../compliance-bot/SETUP.md`'s Docker Deployment + Remote Stack sections.

```
User's Word (desktop Win/Mac or Word-for-web)
   │  task-pane webview loads the add-in over HTTPS
   ▼
https://<hostname>                              ← ONE trusted cert, ONE origin
   │
[ Caddy ]  on SRV-AGENT-01 (172.20.1.10, internal VPN)
   ├─ /            → static Vite build (clients/word/dist)
   └─ /api/*       → FastAPI backend :8000
                        ├ Redis (checkpointer) · Qdrant (RAG) · app-db (Postgres)
                        └ OLLAMA_BASE_URL ────► Spark (172.20.0.22:11434)
```

This doc has two kinds of step. **Bucket A** steps use only artifacts already in this repo and can be run today on any machine with Docker. **Bucket B** steps are gated on things outside this repo's control — mark them clearly so nobody tries to "finish" a Bucket B step with code.

## Prerequisites

**Available now (Bucket A):** Docker + Docker Compose on the deploy host; Node.js 18+ to build the pane.

**Gated (Bucket B):**

| Prereq | Why it blocks |
|---|---|
| VPN reachability to `SRV-AGENT-01` (`172.20.1.10`) | Testers' machines must reach the VM (VPN or corp network) for the pane to load and call `/api/*`. |
| Trusted cert + internal hostname | Office.js refuses self-signed certs for anyone but the author — a real deploy needs a cert the testers' machines already trust. |
| Spark (`172.20.0.22:11434`) reachable from the deploy host | The backend has no local LLM fallback once `OLLAMA_BASE_URL` points off-box. |

Verify Spark before deploying:

```bash
curl http://172.20.0.22:11434/api/tags
```

---

## Step 1 — Configure (Bucket A)

Shorthand used throughout this doc — every compose command needs both files, so
set it once per shell:

```bash
DC="docker compose -f docker-compose.yml -f docker-compose.remote.yml"
```

```bash
cp .env.remote.example .env
```

Set:

| Variable | Value |
|---|---|
| `OLLAMA_BASE_URL` | `http://172.20.0.22:11434` |
| `ADDIN_ORIGIN_HOST` | `<hostname>` — the internal DNS name the add-in will be served from (Bucket B decides the real value; `localhost` works for a local dry run) |
| `APP_DB_PASSWORD` | must equal the `app-db` container's `POSTGRES_PASSWORD` |
| `LLM_MODEL` / `EMBEDDING_MODEL` / `QDRANT_VECTOR_DIM` | must match what Spark serves **and** what the Qdrant collection was built with — a mismatch on the embedding model/dim silently breaks retrieval |

`OTEL_EXPORTER_OTLP_HEADERS` is already left unset in `.env.remote.example` — the VM's tracing backend is the dedicated Phoenix service (Step 4), which needs no auth header. Only set it if you deliberately point the VM backend at a Langfuse instance instead.

> **Reusing an existing `.env` (not a fresh copy)?** Config uses pydantic `extra="forbid"` on `.env`-*file* keys, so any key that's no longer a settings field crashes the backend on startup (`ValidationError: Extra inputs are not permitted`). The OTel migration removed `langfuse_*`/`phoenix_host`, so purge stale lines before bringing the stack up:
> ```bash
> sed -i.bak -E '/^(LANGFUSE_|PHOENIX_)/d' .env && rm .env.bak
> ```

---

## Step 2 — Cert wiring (Bucket B — gated)

The tracked `Caddyfile` ships with `tls internal` — Caddy mints a self-signed cert from its own CA, which works for `localhost` **and** any internal hostname (an internal-only VM can't complete public ACME). That covers a local dry run and testing in real Word via the dev-cert-trust workaround. Once IT hands you a real cert + key for `<hostname>`, **replace** the `tls internal` line with an explicit cert path:

```caddyfile
{$ADDIN_ORIGIN_HOST:localhost} {
	tls /etc/caddy/cert.pem /etc/caddy/key.pem

	encode gzip
	handle /api/* {
		reverse_proxy backend:8000
	}
	handle {
		root * /srv
		try_files {path} /taskpane.html
		file_server
	}
}
```

and mount the cert + key into the `caddy` service in `docker-compose.remote.yml`:

```yaml
  caddy:
    volumes:
      - ./cert.pem:/etc/caddy/cert.pem:ro
      - ./key.pem:/etc/caddy/key.pem:ro
      # ...existing volumes...
```

Until you do, the default `tls internal` serves a self-signed cert — fine for a local dry run and the dev-cert-trust workaround, but refused by Office.js for any tester who hasn't trusted the internal CA.

---

## Step 3 — Build the pane (Bucket A) — **now automatic, nothing to run**

The pane is built **inside the `caddy` image** ([clients/word/Dockerfile](../clients/word/Dockerfile)): a `node:20-alpine` stage runs `npm ci && npm run build`, and the result is copied into the Caddy stage at `/srv`. Step 4's `up -d --build` therefore produces the backend and the pane from the same commit, and **the deploy host needs no Node at all**.

> **Why this changed.** The pane used to be built by hand here and bind-mounted (`./clients/word/dist:/srv:ro`). That could not stay current: `dist/` is gitignored so `git pull` never updated it, `up --build` rebuilds only the backend image, and `SRV-AGENT-01` has no `npm`. On 2026-08-13 the VM was found serving a bundle built **2026-08-11** while its backend had been redeployed twice since — apply-path guards that were on `main` were absent in production, with nothing reporting the mismatch. Bundle age is checkable: `stat -c '%y' clients/word/dist/taskpane.html` under the old scheme; now it is the image's build date.

**Prerequisite:** the deploy host must be able to pull `node:20-alpine`. Check before deploying, since it is the one new base image this introduces:

```bash
docker pull node:20-alpine
```

A stale host `clients/word/dist/` is now ignored — it is no longer mounted, and `.dockerignore` keeps it out of the build context. Deleting it is optional tidying.

---

## Step 4 — Bring up the stack

**Bucket A** as a local dry run (`ADDIN_ORIGIN_HOST` unset → `localhost` + Caddy's internal cert); **Bucket B** for the real deploy on `SRV-AGENT-01` (needs VPN reachability there first).

```bash
docker compose -f docker-compose.yml -f docker-compose.remote.yml \
  up -d --build redis app-db backend caddy
```

> **Always name the services.** A bare `up -d` (no list) starts *everything* defined in the base `docker-compose.yml` — including the heavy local-dev Langfuse stack (`langfuse-web langfuse-worker postgres clickhouse minio`), which will thrash a constrained VM. The lean list above (+ `phoenix`, pulled in by `backend`'s `depends_on`) is the whole VM footprint.

**Qdrant:** the command above omits it — set `QDRANT_REMOTE_URL` in `.env` to reuse an external Qdrant (e.g. Spark `http://172.20.0.22:6333`, alongside compliance-bot). For a self-contained deploy instead, add `qdrant` to the `up` list and leave `QDRANT_REMOTE_URL` unset.

**Tracing:** `phoenix` comes up automatically — it's a `backend` dependency (`depends_on: phoenix`), and `docker-compose.remote.yml` already points `OTEL_EXPORTER_OTLP_ENDPOINT` at `http://phoenix:6006` with no auth header needed. This is unrelated to the local-dev Langfuse stack (`langfuse-web langfuse-worker postgres clickhouse minio` from `docker-compose.yml`) — that's the *local* trace backend and isn't needed on the VM.

> **`app-db` is a hard dependency.** The backend needs it up and **healthy** (audit log, review store, and per-attorney conversations all live there) — bring it up first if you're staging services incrementally, and don't tear it down while the backend is running. Its data is a **named volume** (`app_db_data`) — reviews are attorney work product, so back it up (`pg_dump` on a schedule, or snapshot the volume) same as any production database.

**Phoenix smoke test — confirm a trace lands:**

```bash
# Run one query through the add-in, or directly through Caddy (the backend
# publishes no host port in this overlay — /api/* is only reachable via Caddy,
# same as Step 5's curl):
curl -sk https://<hostname>/api/query \
  -H "Content-Type: application/json" \
  -H "X-User-ID: smoke" \
  -d '{"request": "what is an NDA?", "task_type": "research"}'
```

Then browse Phoenix at **`http://<vm-ip>:6007`** over the VPN — `docker-compose.remote.yml` publishes the dedicated Phoenix on host port `6007` (host `6006` belongs to compliance-bot's *separate* Phoenix on this box; the backend still reaches ours in-network at `phoenix:6006`). Expect the trace tree to show `query → intent_router / contract_review → generation spans with token counts`, routed to Phoenix (no `OTEL_EXPORTER_OTLP_HEADERS` — Phoenix needs no auth). The local-dev equivalent is simpler: submit any query, then confirm the trace in the Langfuse UI at http://localhost:3000.

> ⚠ **Phoenix has no auth and traces carry contract text**, so anyone who can reach the VM on `6007` can read them. If that exposure isn't acceptable, change the mapping to `127.0.0.1:6007:6006` (localhost-only) and reach the UI via `ssh -N -L 16006:localhost:6007 <user>@<vm>`, or front it with a Caddy `/phoenix` route + auth.

If no trace shows up, confirm `phoenix` is healthy (`docker compose -f docker-compose.yml -f docker-compose.remote.yml logs phoenix`), that the backend picked up `OTEL_EXPORTER_OTLP_ENDPOINT=http://phoenix:6006` (`docker compose ... exec backend env | grep OTEL`), and note that recreating `phoenix` clears its data — re-run the query to repopulate.

---

## Step 5 — Verify

**Bucket A** against `localhost`; **Bucket B** against the real `<hostname>` once Steps 2 and the VM deploy are done.

```bash
curl -k https://<hostname>/api/preferences -H "X-User-ID: test"   # expect 200
```

**Confirm the served pane is the commit you just deployed.** A green
`docker ps` says nothing about which bundle Caddy is handing out — that is
exactly how the VM served an 11-Aug bundle against a twice-redeployed backend
with nothing reporting the mismatch. Probe the bundle for strings only the new
frontend contains:

```bash
HOST=$(grep -E '^ADDIN_ORIGIN_HOST=' .env | cut -d= -f2)
ASSET=$(curl -sk --resolve "$HOST:443:127.0.0.1" "https://$HOST/taskpane.html" \
  | grep -o '/assets/taskpane-[^"]*\.js')
BUNDLE=$(curl -sk --resolve "$HOST:443:127.0.0.1" "https://$HOST$ASSET")
for str in "saved yet" "Send feedback" "usually the wrong field"; do
  printf '%-26s %s\n' "$str" "$(printf '%s' "$BUNDLE" | grep -o -F "$str" | wc -l)"
done
```

Each must be ≥ 1. Use `grep -o … | wc -l`, **not** `grep -c`: a Vite bundle is
essentially one line, so `grep -c` with several `-e` patterns returns `1` when
*any* single pattern matches and tells you nothing about the others.

**Confirm the backend is logging.** App records only reach the log because
`api/main.py::configure_logging()` runs at import — uvicorn configures its own
loggers and leaves root at WARNING. A restart is enough to test it:

```bash
$DC restart backend
$DC logs --since 2m backend | grep "Legal plugin API started"
```

Empty output means app-level logging is dead: every `logger.info` is being
discarded, including the in-flight turn lines that are the only way to tell a
slow LLM from a wedged one. Check `LOG_LEVEL` in `.env`.

Then load the pane in Word (see Step 6) and run a review. Confirm it persisted:

```bash
docker compose -f docker-compose.yml -f docker-compose.remote.yml \
  exec app-db psql -U legal -d legal -c "SELECT count(*) FROM review_store;"
```

Finally, the two feedback endpoints and the report — see
[`docs/feedback-loop.md`](feedback-loop.md) "Health check" and "Read it back".

---

## Step 6 — Manifest + sideload

Rendering the manifest is **Bucket A**; actually sideloading to testers is **Bucket B** (needs the trusted-cert hostname from Step 2 live, and — for Windows/Word-for-web — a shared catalog or upload path testers can reach).

```bash
ADDIN_ORIGIN=https://<hostname> python scripts/build_manifest.py
```

Writes `clients/word/manifest.prod.xml` (validates in memory first — a failed render never touches the file on disk; see `scripts/build_manifest.py`). Recommended: run the authoritative Office schema validator against the generated manifest before sideloading — `build_manifest.py` only checks XML well-formedness + template substitution, not the actual Office manifest schema:

```bash
npx office-addin-manifest validate clients/word/manifest.prod.xml
```

Sideload per surface:

- **Windows (shared-folder catalog):** point a trusted network share at `manifest.prod.xml`, register it as an add-in catalog (Word Options → Trust Center → Trusted Add-in Catalogs), then insert from **My Add-ins → Shared Folder**.
- **Mac (`wef` folder):** `cp clients/word/manifest.prod.xml ~/Library/Containers/com.microsoft.Word/Data/Documents/wef/legal-triage.manifest.xml`, then quit/reopen Word and insert from **My Add-ins → Shared Folder** (see `clients/word/README.md` for the full walkthrough — written for the dev manifest, same mechanism).
- **Word-for-web:** **Insert → Add-ins → Upload My Add-in**, upload `manifest.prod.xml` directly (per-user, no catalog needed).

Note: `manifest.prod.xml` reuses the dev manifest's `<Id>` (`D57831EF-…`), since it's rendered from the same `manifest.template.xml`. Office keys sideloaded add-ins by `<Id>`, so a single machine can't have both the dev and prod add-in sideloaded at once. If the machine doing a prod smoke test also has the dev add-in sideloaded (e.g. the author's own machine), remove the dev add-in first — testers on other machines are unaffected.

---

## Troubleshooting

- **Pane loads but every call 404s / CORS-fails:** check `ADDIN_ORIGIN_HOST` matches the hostname you're actually browsing to — Caddy's site address is keyed on it.
- **Office.js refuses to load the add-in at all:** almost always the cert — confirm it's trusted (not self-signed) on the tester's machine, per Step 2.
- **Backend can't reach the LLM:** re-run the Spark `curl` check from Prerequisites; also confirm `LLM_MODEL` is actually pulled on Spark.
- **`app-db` unhealthy / backend won't start:** `docker compose -f docker-compose.yml -f docker-compose.remote.yml logs app-db` — usually `APP_DB_PASSWORD` mismatch between `.env` and a stale volume from a prior password.

### `ValueError: bad marshal data (invalid reference)` on any `python` in the backend container

Hit on SRV-AGENT-01 2026-08-19. A corrupt `.pyc` inside the image's
`site-packages`, baked by `pip`'s byte-compilation and then **frozen in the
build cache** — so `up -d --build` reported `Built 3.4s`, reused the poisoned
layer, and could never fix it. The fix is to make the pip layer re-run:

```bash
docker builder prune -f
$DC build --no-cache backend
$DC up -d backend
$DC run --rm --no-deps backend python -c "import config; print('ok')"
```

**Two things make this dangerous rather than merely annoying.**

The *running* backend keeps working — it holds its modules in memory — so the
symptom shows up only when you start a second process (the feedback report, a
one-off script). Everything looks healthy right up until the container is
recreated or the VM reboots, at which point the backend does not come back.
Treat a failed `run --rm … python -c "import config"` as an outage waiting to
happen, not a tooling annoyance.

And `rm -rf __pycache__` inside the running container *appears* to fix it. It
does not: that writes an overlayfs whiteout into one container's writable
layer, which is discarded on recreation. Confirm the fix against a **fresh
container** (`run --rm`), never `exec`.

Diagnosis, if you want to confirm before rebuilding — a valid magic number with
a plausible size rules out truncation and a partial write, which points at the
cached layer rather than the disk:

```bash
$DC exec backend python -c "
import importlib.util
p='/usr/local/lib/python3.12/site-packages/pydantic/plugin/__pycache__/_schema_validator.cpython-312.pyc'
d=open(p,'rb').read()
print('size', len(d), 'magic', d[:4].hex(), 'expected', importlib.util.MAGIC_NUMBER.hex())"
```

If it recurs after a `--no-cache` rebuild, stop treating it as bad luck and
remove the bytecode from the image entirely: `pip install --no-compile` plus
`ENV PYTHONDONTWRITEBYTECODE=1`. Costs a couple of seconds of startup compile
and is immune to the whole class.
