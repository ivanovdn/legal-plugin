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

`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` only matter if you bring up the Langfuse services (Step 4).

---

## Step 2 — Cert wiring (Bucket B — gated)

An internal hostname with no public DNS can't get an automatic Caddy cert. Once IT hands you a cert + key for `<hostname>`, add a `tls` directive to the `Caddyfile` site block:

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

Without this, Caddy falls back to its own internal (self-signed) cert — fine for a local dry run, refused by Office.js for anyone other than the author.

---

## Step 3 — Build the pane (Bucket A)

```bash
cd clients/word && npm ci && npm run build
```

Produces `clients/word/dist` (the static bundle `docker-compose.remote.yml`'s `caddy` service mounts read-only at `/srv`).

---

## Step 4 — Bring up the stack

**Bucket A** as a local dry run (`ADDIN_ORIGIN_HOST` unset → `localhost` + Caddy's internal cert); **Bucket B** for the real deploy on `SRV-AGENT-01` (needs VPN reachability there first).

```bash
docker compose -f docker-compose.yml -f docker-compose.remote.yml \
  up -d --build redis qdrant app-db backend caddy
```

Langfuse tracing is optional — add `langfuse-web langfuse-worker postgres clickhouse minio` to the same command if you want it.

> **`app-db` is a hard dependency.** The backend needs it up and **healthy** (audit log, review store, and per-attorney conversations all live there) — bring it up first if you're staging services incrementally, and don't tear it down while the backend is running. Its data is a **named volume** (`app_db_data`) — reviews are attorney work product, so back it up (`pg_dump` on a schedule, or snapshot the volume) same as any production database.

---

## Step 5 — Verify

**Bucket A** against `localhost`; **Bucket B** against the real `<hostname>` once Steps 2 and the VM deploy are done.

```bash
curl -k https://<hostname>/api/preferences -H "X-User-ID: test"   # expect 200
```

Then load the pane in Word (see Step 6) and run a review. Confirm it persisted:

```bash
docker compose -f docker-compose.yml -f docker-compose.remote.yml \
  exec app-db psql -U legal -d legal -c "SELECT count(*) FROM review_store;"
```

---

## Step 6 — Manifest + sideload

Rendering the manifest is **Bucket A**; actually sideloading to testers is **Bucket B** (needs the trusted-cert hostname from Step 2 live, and — for Windows/Word-for-web — a shared catalog or upload path testers can reach).

```bash
ADDIN_ORIGIN=https://<hostname> python scripts/build_manifest.py
```

Writes `clients/word/manifest.prod.xml` (validates in memory first — a failed render never touches the file on disk; see `scripts/build_manifest.py`). Sideload per surface:

- **Windows (shared-folder catalog):** point a trusted network share at `manifest.prod.xml`, register it as an add-in catalog (Word Options → Trust Center → Trusted Add-in Catalogs), then insert from **My Add-ins → Shared Folder**.
- **Mac (`wef` folder):** `cp clients/word/manifest.prod.xml ~/Library/Containers/com.microsoft.Word/Data/Documents/wef/legal-triage.manifest.xml`, then quit/reopen Word and insert from **My Add-ins → Shared Folder** (see `clients/word/README.md` for the full walkthrough — written for the dev manifest, same mechanism).
- **Word-for-web:** **Insert → Add-ins → Upload My Add-in**, upload `manifest.prod.xml` directly (per-user, no catalog needed).

---

## Troubleshooting

- **Pane loads but every call 404s / CORS-fails:** check `ADDIN_ORIGIN_HOST` matches the hostname you're actually browsing to — Caddy's site address is keyed on it.
- **Office.js refuses to load the add-in at all:** almost always the cert — confirm it's trusted (not self-signed) on the tester's machine, per Step 2.
- **Backend can't reach the LLM:** re-run the Spark `curl` check from Prerequisites; also confirm `LLM_MODEL` is actually pulled on Spark.
- **`app-db` unhealthy / backend won't start:** `docker compose -f docker-compose.yml -f docker-compose.remote.yml logs app-db` — usually `APP_DB_PASSWORD` mismatch between `.env` and a stale volume from a prior password.
