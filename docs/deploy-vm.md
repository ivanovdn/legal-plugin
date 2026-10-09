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

The pane is built **inside the `caddy` image** ([clients/word/Dockerfile](../clients/word/Dockerfile)): a pinned `node:20.20.2-alpine@sha256:…` stage runs `npm ci && npm run build`, and the result is copied into the Caddy stage at `/srv`. Step 4's `up -d --build` therefore produces the backend and the pane from the same commit, and **the deploy host needs no Node at all**.

> **Why this changed.** The pane used to be built by hand here and bind-mounted (`./clients/word/dist:/srv:ro`). That could not stay current: `dist/` is gitignored so `git pull` never updated it, `up --build` rebuilds only the backend image, and `SRV-AGENT-01` has no `npm`. On 2026-08-13 the VM was found serving a bundle built **2026-08-11** while its backend had been redeployed twice since — apply-path guards that were on `main` were absent in production, with nothing reporting the mismatch. Bundle age is checkable: `stat -c '%y' clients/word/dist/taskpane.html` under the old scheme; now it is the image's build date.

**Prerequisite:** the deploy host must be able to pull the pinned base images — the `FROM` lines of `Dockerfile` and `clients/word/Dockerfile` (`tag@sha256`). A build pulls them itself; to check ahead of a deploy, `docker pull` each exact reference, e.g. the Node one:

```bash
docker pull "$(grep -oE 'node:[^ ]+' clients/word/Dockerfile)"
```

A stale host `clients/word/dist/` is now ignored — it is no longer mounted, and `.dockerignore` keeps it out of the build context. Deleting it is optional tidying.

---

## Step 4 — Bring up the stack

**Bucket A** as a local dry run (`ADDIN_ORIGIN_HOST` unset → `localhost` + Caddy's internal cert); **Bucket B** for the real deploy on `SRV-AGENT-01` (needs VPN reachability there first).

> **Local dry run on a Mac: prefix the command with `LOG_DRIVER=json-file`.** `backend` and `caddy` log to journald (see *Logs* below), and Docker Desktop has none — without the override they fail to start with `journald is not enabled on this host`. Set it in the shell, never in `.env`: `config.py` rejects unknown `.env` keys.

**⚠ Upgrading a host that predates 2026-10-08? Carry Phoenix's traces into its volume FIRST — before any `up`.** The old config kept Phoenix's database in the container's own layer, not in the `phoenix_data` volume. Any `up` that names `backend` also recreates `phoenix` whenever its config changed — and this change alters both its image tag and its env — which deletes that layer and every trace in it, permanently. While the old container is still running, confirm it runs the pinned version, then snapshot into the volume it already mounts (SQLite's online backup, so a live WAL database copies consistently; the image has no shell, only Python):

```bash
docker exec legal-plugin-phoenix-1 python3 -c "import phoenix; print(phoenix.__version__)"   # must print 15.2.0
docker exec legal-plugin-phoenix-1 python3 -c "import sqlite3; s=sqlite3.connect('file:/root/.phoenix/phoenix.db?mode=ro', uri=True); d=sqlite3.connect('/mnt/data/phoenix.db'); s.backup(d); print(d.execute('PRAGMA integrity_check').fetchone()[0])"   # must print ok
```

If the version is not `15.2.0`, stop and pin the image to what is running instead — an older Phoenix may refuse a newer schema. Traces written between the snapshot and the recreate are lost, so do it when no one is mid-session. Then bring the stack up:

```bash
{ echo "== $(git rev-parse --short HEAD) $(date -u +%FT%TZ)"
  docker compose -f docker-compose.yml -f docker-compose.remote.yml \
    up -d --build redis app-db backend caddy
  echo "== exit $?"
} 2>&1 | tee -a "data/deploy-logs/deploy-$(date -u +%F)-$(git rev-parse --short HEAD).log"
```

The `tee` keeps the whole build output — which steps ran, which came from cache, which containers were recreated — and the exit status in `data/deploy-logs/` (*Working files*, below). On 2026-10-08 two such logs were what showed the unpinned rebuild re-resolving the Python packages, and the pinned one reproducing the pane byte for byte.

> **Everything the stack runs is pinned** — images as `tag@sha256` in both compose files and both Dockerfiles, Python packages through `requirements-runtime.lock` — so a rebuild changes nothing a commit didn't. Until 2026-10-08 a rebuild re-resolved version ranges whenever the VM's build cache had been evicted, and silently moved production to fastapi 0.143.0, whose built-in tracing took over every trace root. Bump a pin on purpose (CLAUDE.md, *Stack*).

> **Always name the services.** A bare `up -d` (no list) starts *everything* defined in the base `docker-compose.yml` — including the heavy local-dev Langfuse stack (`langfuse-web langfuse-worker postgres clickhouse minio`), which will thrash a constrained VM. The lean list above (+ `phoenix`, pulled in by `backend`'s `depends_on`) is the whole VM footprint.

**Qdrant:** the command above omits it — set `QDRANT_REMOTE_URL` in `.env` to reuse an external Qdrant (e.g. Spark `http://172.20.0.22:6333`, alongside compliance-bot). For a self-contained deploy instead, add `qdrant` to the `up` list and leave `QDRANT_REMOTE_URL` unset.

Either way, **create `legal_docs` once.** On a Qdrant of your own, the idempotent script creates all three of this app's collections:

```bash
$DC run --rm --no-deps backend python scripts/create_collections.py
```

On a **shared** Qdrant (Spark), create only `legal_docs` — the one collection a Word turn reads. `memory` has no reader and `case_history` serves only contract generation, which the pane cannot reach; both are generic names to claim on another team's instance. This reads the URL and vector size from the app's own settings, so the dimension always matches the embedding model:

```bash
$DC run --rm --no-deps backend python -c "from qdrant_client import QdrantClient; from qdrant_client.models import Distance, VectorParams; from config import get_settings; s = get_settings(); c = QdrantClient(url=s.qdrant_url); c.collection_exists('legal_docs') or c.create_collection('legal_docs', vectors_config=VectorParams(size=s.qdrant_vector_dim, distance=Distance.COSINE)); print(sorted(x.name for x in c.get_collections().collections))"
```

Every grounded SOW chat turn and every SOW review looks up the governing MSA; without `legal_docs` that lookup 404s and is recorded as a degradation, so `app.outcome=degraded` fires on all of them and stops meaning anything. Found on the VM 2026-10-08: Spark's Qdrant had none of `legal_docs` / `case_history` / `memory`; only `legal_docs` was created there. An empty `legal_docs` is the honest state — no MSA on file — and a real Qdrant outage still records `msa_lookup_failed`. **Before seeding it**, know that `get_parent_msa` takes the one MSA on file for the client (`internal` for every Word turn), so seeding the demo MSA would compare every SOW against Trinetix's model MSA, not the counterparty's agreement.

**Tracing:** `phoenix` comes up automatically — it's a `backend` dependency (`depends_on: phoenix`), and is **recreated** by any `up` that names `backend` whenever its own config changed (hence the ⚠ callout above), and `docker-compose.remote.yml` already points `OTEL_EXPORTER_OTLP_ENDPOINT` at `http://phoenix:6006` with no auth header needed. This is unrelated to the local-dev Langfuse stack (`langfuse-web langfuse-worker postgres clickhouse minio` from `docker-compose.yml`) — that's the *local* trace backend and isn't needed on the VM.

> **`app-db` is a hard dependency.** The backend needs it up and **healthy** (audit log, review store, and per-attorney conversations all live there) — bring it up first if you're staging services incrementally, and don't tear it down while the backend is running. Its data is a **named volume** (`app_db_data`) — reviews are attorney work product, so it is dumped every night (*Working files*, below).

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

**Browse Phoenix directly at `http://172.20.1.10:6007`** (`http://<vm-ip>:6007`) from any machine on the VPN — `docker-compose.remote.yml` publishes it on all interfaces. Expect the trace tree to show `query:<task_type> → intent_router / contract_review → generation spans with token counts`, routed to Phoenix (no `OTEL_EXPORTER_OTLP_HEADERS` — Phoenix needs no auth). The root span carries `app.outcome` (`ok`/`degraded`/`failed`) — `status = ERROR` means the attorney did not get their answer; see [docs/testing-observability.md](testing-observability.md). The local-dev equivalent is simpler: submit any query, then confirm the trace in the Langfuse UI at http://localhost:3000.

> Phoenix has **no auth**, and its spans carry the full uploaded contract, the governing MSA and the firm's playbook bundle (`data/contract_review_skills/` is gitignored because it is canonical legal-team IP) — anyone on the VPN who knows the address can read them. It is published this way deliberately, for direct browsing. Host `6006` on the VM belongs to compliance-bot's *separate* Phoenix; ours is on `6007`, and the backend reaches it in-network at `phoenix:6006`.

If no trace shows up, confirm `phoenix` is healthy (`docker compose -f docker-compose.yml -f docker-compose.remote.yml logs phoenix`), that the backend picked up `OTEL_EXPORTER_OTLP_ENDPOINT=http://phoenix:6006` (`docker compose ... exec backend env | grep OTEL`), and that `PHOENIX_WORKING_DIR=/mnt/data` is set (`docker inspect legal-plugin-phoenix-1 --format '{{json .Config.Env}}'`).

**Traces persist across recreates** in the `phoenix_data` volume. Until 2026-10-08 they did not: without `PHOENIX_WORKING_DIR` Phoenix wrote `phoenix.db` to `/root/.phoenix` inside the container and the volume stayed empty, so every recreate wiped every trace. A host still on the old config must carry its data over before its first `up` — the ⚠ callout at the top of this step.

The image is pinned (`15.2.0`) because the database in the volume carries that version's schema — upgrade deliberately, not by whatever `latest` resolves to on the day of a pull.

### Logs

`backend` and `caddy` log to **journald**, not Docker's default `json-file`. A `json-file` log belongs to its container and is deleted with it, so every redeploy erased the app log — the 2026-10-08 pilot audit found nothing older than the last recreate. `$DC logs` still reads the current container; earlier containers are in the journal under the same container name:

```bash
sudo journalctl CONTAINER_NAME=legal-plugin-backend-1 --since 2026-10-01 --no-pager
```

`sudo` because the deploy user is not in `systemd-journal`; `sudo usermod -aG systemd-journal $USER` (then log in again) drops it. Retention is journald's own (`journalctl --disk-usage`; default `SystemMaxUse` is 10% of the filesystem, capped at 4 GiB). The journal survives a reboot only on persistent storage — the default here, because `/var/log/journal` exists (checked on SRV-AGENT-01 2026-10-08).

### Working files — deploy logs and backups

What we make on the VM by hand — deploy logs, backups, the exported root certificate — goes in one of two folders inside the checkout, never loose in `~` and never anywhere else in the repo:

| Folder | Holds | Kept |
|---|---|---|
| `data/deploy-logs/` | One log per deploy, written by the command above: the build output and the exit status. Named `deploy-<UTC date>-<commit>.log`; a second run of the same commit that day appends. | Always — about 40 KB each. |
| `data/backups/` | A copy of production data taken right before something that could destroy it (below). Named `<what>-<UTC date>-<reason>`, e.g. `app-db-2026-10-15-pre-sp2.sql.gz`. | Until the operation it protected is verified, then deleted — most hold contract text. The Caddy CA stays. |

**Why `data/`:** it is excluded by both `.gitignore` and `.dockerignore`. The backend image is built from the whole checkout (`build: .`, then `COPY . .`), so a file anywhere else in the repo goes into the image on the next build — a Phoenix backup there would put client contract text inside an image layer. Only `data/attorneys` is mounted into a container, so these two folders are invisible to the stack. `data/` itself belongs to root (Docker created it for that mount), so the two folders are made once, owned by the deploy user and closed to everyone else — files inside need no `chmod`:

```bash
sudo install -d -o "$USER" -g "$USER" -m 700 data/backups data/deploy-logs
```

**`app-db` is dumped every night** by the deploy user's crontab — its only entry, installed 2026-10-09. At 02:17 UTC it writes `data/backups/app-db-<UTC date>-nightly.sql.gz`, deletes nightly dumps older than 14 days (never a manual backup: those lack the `-nightly` suffix), and appends one line per run to `data/backups/app-db-nightly.log` — `ok <file> <bytes>`, or `FAILED` with the error. `pipefail`, plus a `.part` file renamed only on success, keep a failed dump from passing for a good one. **Nothing alerts on a `FAILED`**, so read the log's last line whenever you are on the VM:

```bash
tail -3 data/backups/app-db-nightly.log
crontab -l   # the deployed copy of the entry below — reinstall it from here if it is ever lost
```

```
SHELL=/bin/bash
MAILTO=""
# legal-plugin: nightly app-db dump into data/backups, last 14 kept, one log line per run (docs/deploy-vm.md, Working files)
17 2 * * * cd "$HOME/legal-plugin" && f="data/backups/app-db-$(date -u -I)-nightly.sql.gz" && { set -o pipefail; docker compose -f docker-compose.yml -f docker-compose.remote.yml exec -T app-db pg_dump -U legal -d legal </dev/null | gzip > "$f.part" && mv "$f.part" "$f" && find data/backups -maxdepth 1 -name 'app-db-*-nightly.sql.gz*' -mtime +13 -delete && echo "$(date -u -Is) ok $f $(wc -c < "$f") bytes" || echo "$(date -u -Is) FAILED $f"; } >> data/backups/app-db-nightly.log 2>&1
```

There is no `%` in it (`date -I`, `wc -c`) on purpose: cron turns an unescaped `%` into a newline. Installed with `crontab -` and then run once exactly as cron would (`env -i HOME=… PATH=/usr/bin:/bin SHELL=/bin/bash`): it logged `ok … 91184 bytes`.

**What else to back up, and when.** A nightly dump can be a day old, so before anything risky take a manual one too:

- **`app-db`**, before any deploy that changes its schema or the Postgres version:

  ```bash
  $DC exec -T app-db pg_dump -U legal -d legal | gzip > "data/backups/app-db-$(date -u +%F)-<reason>.sql.gz"
  ```

- **Phoenix**, before a Phoenix version bump — the new version migrates the database when it starts, so going back needs the copy taken before. SQLite's online backup, so the live database copies consistently (the image has no shell, only Python):

  ```bash
  $DC exec -T phoenix python3 -c "import sqlite3; s=sqlite3.connect('file:/mnt/data/phoenix.db?mode=ro', uri=True); d=sqlite3.connect('/tmp/backup.db'); s.backup(d); print(d.execute('PRAGMA integrity_check').fetchone()[0])"   # must print ok
  $DC cp phoenix:/tmp/backup.db "data/backups/phoenix-$(date -u +%F)-<reason>.db"
  $DC exec -T phoenix python3 -c "import os; os.remove('/tmp/backup.db')"
  ```

- **Caddy's CA**, once — it does not change. If `caddy_data` is lost, Caddy mints a new CA and every attorney's install breaks until they import a new certificate (Step 7's ⚠); `root.crt` + `root.key` put the old one back (*Restoring Caddy's CA*, below). SRV-AGENT-01's was taken 2026-10-09: `data/backups/caddy-ca-2026-10-09`, root fingerprint `04:63:41:EF…:52:7B`.

  ```bash
  $DC cp caddy:/data/caddy/pki/authorities/local "data/backups/caddy-ca-$(date -u +%F)"
  ```

  `root.key` can sign a certificate for **any** site, and every attorney's machine will trust it. It never leaves the VM.

These copies sit on the same disk as what they protect: they cover our mistakes — a bad migration, a `down -v`, a broken upgrade — not the loss of the VM. Each backup command was dry-run on SRV-AGENT-01 on 2026-10-09 without writing anything: the dump came to 91 KB gzipped, Phoenix's online backup passed its integrity check in memory, and the CA listed its four files.

**Checking an `app-db` dump restores** — load it into a throwaway Postgres from the same image (no network, data in memory, removed on stop) and compare every table's row count with the live database. Rehearsed 2026-10-09 on the first nightly dump: all six tables matched exactly (`audit_log` 98, `conversation_store` 168, `conversation_summary` 3, `feedback` 2, `interaction_event` 156, `review_store` 14). Rows written after the dump show up as a difference.

```bash
IMG=$(docker inspect legal-plugin-app-db-1 --format '{{.Image}}')
docker run -d --rm --name app-db-restore-check --network none --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_USER=legal -e POSTGRES_PASSWORD=check -e POSTGRES_DB=legal "$IMG"
# -h 127.0.0.1: during init the image runs a socket-only server, so TCP answers only once the real one is up
until docker exec app-db-restore-check pg_isready -h 127.0.0.1 -U legal -d legal -q; do sleep 1; done
gunzip -c data/backups/app-db-<date>-nightly.sql.gz \
  | docker exec -i -e PGPASSWORD=check app-db-restore-check psql -h 127.0.0.1 -U legal -d legal -v ON_ERROR_STOP=1 -q
Q="SELECT format('SELECT %L, count(*) FROM %I', table_name, table_name) FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name \gexec"
diff <(echo "$Q" | $DC exec -T app-db psql -U legal -d legal -At) \
     <(echo "$Q" | docker exec -i -e PGPASSWORD=check app-db-restore-check psql -h 127.0.0.1 -U legal -d legal -At) \
  && echo "every table matches"
docker stop app-db-restore-check
```

Restore with the same image's `psql`, not an older client: the dump carries `\restrict` / `\unrestrict` meta-commands, which `pg_dump` gained in the 2025 security releases. **Still not rehearsed:** swapping a dump into the live `app-db`, and any Phoenix restore — rehearse on a scratch stack before counting on either.

**Restoring Caddy's CA** — only when the live root no longer matches the backup (compare them with the last two commands below). Only `root.crt` and `root.key` go back: Caddy issues a fresh intermediate and site certificate from them, so it does not matter that the intermediate in the backup has long expired.

```bash
$DC rm -sf caddy                           # stop and remove the container; its volumes stay
docker volume rm legal-plugin_caddy_data   # ONLY this volume, never `down -v`: it holds the replacement CA nobody trusts
$DC up --no-start --no-deps caddy          # a fresh, empty caddy_data; Caddy not started, backend untouched
docker run --rm -v legal-plugin_caddy_data:/data -v "$PWD/data/backups/caddy-ca-<date>":/backup:ro \
  --entrypoint sh legal-plugin-pane:latest \
  -c 'mkdir -p /data/caddy/pki/authorities/local && cp /backup/root.crt /backup/root.key /data/caddy/pki/authorities/local/'
$DC up -d --no-deps caddy
$DC exec -T caddy cat /data/caddy/pki/authorities/local/root.crt | openssl x509 -noout -fingerprint -sha256
openssl x509 -in data/backups/caddy-ca-<date>/root.crt -noout -fingerprint -sha256   # must print the same
```

Rehearsed 2026-10-09 on a scratch stack built from the same pane image, with a stand-in `backend` that Caddy depends on: losing the volume and bringing Caddy back up minted a new CA that the old root no longer verified (`curl --cacert <old root.crt>` exit 60 — what every attorney's machine would see); after these steps the old root verified again (exit 0), the live root's fingerprint matched the backup's, and the stand-in backend was never restarted.

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

- **Windows:** try **Insert → Add-ins → Upload My Add-in** first (per-user, no admin, no share) — it is present on some Word builds and absent on others. Otherwise a **shared-folder catalog**: put `manifest.prod.xml` on a **UNC share** (`\\server\share` — a local `C:\…` path is silently rejected), register the *folder* under Word Options → Trust Center → Trusted Add-in Catalogs, tick *Show in Menu*, restart Word, then insert from **My Add-ins → Shared Folder**. As with Mac, hand a legal-team user [`docs/tester-setup.md`](tester-setup.md) (Part B) rather than walking them through this.
- **Mac (`wef` folder):** `cp clients/word/manifest.prod.xml ~/Library/Containers/com.microsoft.Word/Data/Documents/wef/legal-triage.manifest.xml`, then quit/reopen Word and insert from **My Add-ins → Shared Folder**. Don't walk a legal-team user through this — hand them [`docs/tester-setup.md`](tester-setup.md), which covers the same mechanism plus the hosts/CA steps, written for a non-engineer. (`clients/word/README.md` is the dev-manifest equivalent.)
- **Word-for-web: not sideloadable on this tenant.** The self-service **Upload My Add-in** entry is gone from the browser Apps store (verified 2026-08-11) — the browser/SharePoint surface needs Centralized Deployment by a Global Admin ([`docs/deploy-it-request.md`](deploy-it-request.md) Request 2). Don't promise it to a tester as a fallback for a desktop problem.

Note: **dev and prod coexist on one machine, deliberately.** Office keys sideloaded add-ins by `<Id>`, and the two differ — `clients/word/manifest.xml` is `A8A5F2CD-…` / "Legal Triage (Dev)" pointing at `https://localhost:3001`, while the rendered `manifest.prod.xml` is `D57831EF-…` / "Legal Triage" pointing at the deployed hostname. So a prod smoke test on the author's own machine needs no uninstall, and both appear side by side in Word's Add-ins panel (confirmed on Word for Mac 2026-09-14, listed under *Developer Add-ins*). **An earlier version of this note said they shared an `<Id>` and that one had to be removed before testing the other; that was wrong** — check the two files before repeating the claim, since a future edit to `manifest.template.xml` could make it true again.

---

## Step 7 — Hand-off pack for the legal team (Bucket B — interim, self-signed cert)

Produces the two files [`docs/tester-setup.md`](tester-setup.md) tells a legal-team
user to expect. That guide covers **desktop Word on both Mac (Part A) and Windows
(Part B)** and assumes this step was run. Word-for-web is not covered there because
it can't be hand-installed on this tenant — see Step 6.

Pick the hostname and use the **same string everywhere** — this doc uses
`legal-triage.internal.trinetix.net`.

1. **Point the deployment at the hostname.** In `.env` on the VM:

   ```
   ADDIN_ORIGIN_HOST=legal-triage.internal.trinetix.net
   ```

2. **Rebuild the pane image and recreate Caddy.** Caddy mints a self-signed cert for
   that hostname from its internal CA (the `tls internal` line in the `Caddyfile`):

   ```bash
   $DC up -d --build --force-recreate caddy
   ```

   > `--build` because the pane is built **inside** the caddy image (Step 3), so this
   > is also what makes the served bundle match the current commit. `--force-recreate`
   > so the new `ADDIN_ORIGIN_HOST` is definitely picked up. **Do not hand-build
   > `clients/word/dist` and expect Caddy to serve it** — that bind mount is gone, and
   > building by hand is exactly how the VM came to serve a two-day-old bundle on
   > 2026-08-13 with nothing reporting the mismatch.

3. **Extract the root CA** the users will trust. It lives in the `caddy_data` volume,
   so this file is stable across redeploys:

   ```bash
   $DC cp caddy:/data/caddy/pki/authorities/local/root.crt data/backups/caddy-root-ca.crt
   ```

   Into `data/backups/`, not the checkout root: a file there would be copied into
   the backend image on the next build (Step 4, *Working files*).

4. **Render the manifest.** Just URLs — runs anywhere with Python + the repo, no VM
   access needed:

   ```bash
   ADDIN_ORIGIN=https://legal-triage.internal.trinetix.net python scripts/build_manifest.py
   npx office-addin-manifest validate clients/word/manifest.prod.xml
   ```

5. **Windows needs nothing extra from you.** Part B5 of the guide is
   self-contained: a PowerShell block creates `C:\LegalTriage`, shares it read-only
   to the user's own account, and writes the catalog entry straight into
   `HKCU:\…\Office\16.0\WEF\TrustedCatalogs` — the same entry the Trust Center UI
   writes. It derives the UNC path from `$env:COMPUTERNAME`, so there is no
   "hand out a share path" step and no dependency on a file server. (An earlier
   draft did have one; it made the install wait on an email from us.) **Upload My
   Add-in** is documented only as an optional shortcut, since it is absent on most
   builds.

   The PowerShell itself is checked without a Windows machine by
   [`scripts/test-windows-install.ps1`](../scripts/test-windows-install.ps1)
   (`brew install powershell`, then `pwsh -NoProfile -File …`). It stubs the
   Windows-only cmdlets and pins what the prose claims: the hosts line lands on
   its own row whether or not the file ends in a newline, the GUID keeps its
   braces, a second run adds nothing, and an unrelated catalogue already on the
   machine is neither edited nor removed. Deliberately not in `check.sh` — the
   repo has no other pwsh dependency. Run it after editing any PowerShell in the
   guide, along with a parse check of every block.

   Three things Microsoft's own page on this
   ([network-share sideload](https://learn.microsoft.com/en-us/office/dev/add-ins/testing/create-a-network-shared-folder-catalog-for-task-pane-and-content-add-ins))
   pins down, all now reflected in the guide: the `TrustedCatalogs` GUID **must
   keep its enclosing braces** in both the subkey name and the `Id` value (hence
   `NewGuid().ToString('B')`); the share needs at least **read/write**, not
   read-only; and the backslash-doubling in their `.reg` example is `.reg` file
   escaping only — a PowerShell `New-ItemProperty` takes the literal path, which
   is why B5 writes the key directly instead of shipping a `.reg`. **The dialog
   moved**: on current builds the ribbon's Add-ins button opens a store panel and
   SHARED FOLDER is behind its **Advanced** button. Two limitations to remember
   when this stops being a pilot: network-share sideload is **explicitly not
   supported for production** (Centralized Deployment, Request 2, is the
   production route), and a manifest update that **changes the ribbon** forces
   every user to reinstall.

6. **Send each person three things:** `caddy-root-ca.crt`, `manifest.prod.xml`, and
   [`docs/tester-setup.md`](tester-setup.md) (plus the UNC path from step 5 if it
   applies). The guide expects both files in their **Downloads** folder under
   exactly those names.

   > **Windows needs local admin** for the hosts entry and the root-CA import
   > (`certutil -addstore -f Root`, machine store — *Current User* is not enough for
   > Word's WebView2). The guide tells users without admin to stop and come back to
   > us rather than chase IT, so expect that question instead of a failed install.

> ⚠ **Never run `docker compose … down -v`.** It deletes `caddy_data`, which
> regenerates the CA and invalidates every install — everyone has to re-import the
> new `caddy-root-ca.crt`. The user guide's troubleshooting table has a row for this
> symptom ("worked yesterday, today Word rejects the certificate"); don't make anyone
> use it.

> **Verification status of the user guide (2026-09-11).** Part A (Mac) is the
> path the author's own machine runs. Part B **B1–B4 are verified on real Windows**
> — rehearsed against a Mac-hosted Caddy on the LAN (hostname pointed at the Mac
> instead of the VM; the manifest needs no change because it carries only the
> hostname). The PowerShell `Add-Content` escaping, `certutil -addstore -f Root`,
> and the resulting trust all worked, and **Chrome on Windows reads the same root
> store as Word's WebView2**, so B4 achieves its purpose for Word.
>
> **B5 is verified too, via the PowerShell route (2026-09-14).** On a second,
> licensed Windows machine the B5.1 block installed cleanly, the add-in appeared
> under **SHARED FOLDER** exactly as Microsoft documents (not the *Developer
> Add-ins* heading a Mac shows), and the pane rendered. A full review then ran
> end-to-end from Windows Word against the Mac-hosted stack: `review_store` holds
> a 3,371-char `nda` review, `interaction_event` a `findings_rendered` with
> `detail=4`, and the row carries a **different `attorney_id` from the Mac's** —
> the per-install `localStorage` UUID, which is what proves it came from a
> separate install rather than the developer's own machine. **Upload My Add-in**
> therefore stays documented only as an optional shortcut; the PowerShell catalog
> route is the one with a run behind it.
>
> **The apply path runs on Windows Word too (same session).** *"set legal name is
> Sony"* proposed one edit and it applied 5.2 s later against
> `target_ref = [Legal Name]` — a **bracketed** placeholder, i.e. the exact class
> `CLAUDE.md` documents as the `body.search` wildcard hazard (`[](){}<>?*` are
> treated as wildcards even with `matchWildcards:false`). Every gotcha in that
> section was measured on Word for Mac; this is the first evidence the escaping
> and fallback logic behaves the same on Windows. A second turn, *"who signs?"*,
> recorded `edits_proposed = 0` — the SCOPE rule holding on a factual question.
> Across the whole Windows install: **1 `edit_applied`, 2 `edits_proposed`,
> 1 `findings_rendered`, and zero `edit_failed` / `redline_failed` /
> `*_jump_notfound`.** Chat and `conversation_store` are exercised as well.
>
> **Still unproven:** **VPN reachability to `172.20.1.10:443` from a Windows
> machine.** The rehearsal used the LAN, and a non-corporate machine can never
> answer that one — the first corporate Windows laptop will. Note what this does
> *not* cover: multi-paragraph clause spans, tab-separated signature blocks and
> `replace_all` have still only been exercised on Mac.

> Asking someone to trust a private CA by hand is **security-sensitive**. Keep it to
> a small, informed pilot and loop in security before going wider. Once Request 1 in
> [`docs/deploy-it-request.md`](deploy-it-request.md) lands (real DNS + a cert from
> the internal CA), steps 2–4 of the user guide disappear and this whole hand-off pack
> reduces to sending `manifest.prod.xml`.

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
