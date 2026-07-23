# VM Deployment — Bucket A (prereq-free artifacts) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce the deployment artifacts that let `SRV-AGENT-01` run the whole legal-plugin stack with Docker — backend container, Caddy reverse proxy serving the built Word task-pane + proxying `/api/`, a VM compose overlay wiring them to the existing infra, a prod manifest, and a deploy runbook — all **buildable and testable locally now**, before the VPN/cert/hostname prereqs (bucket B) exist.

**Architecture:** Mirror the proven `../compliance-bot` container pattern (Dockerfile + `.dockerignore` + `docker-compose-remote.yml` overlay + slim runtime requirements + `env_file` + in-network service-name overrides + `restart`). Add the Word-add-in-specific piece a bot never needed: a **Caddy** reverse proxy that serves the static `vite build` at `/` and proxies `/api/*` to the backend container (same origin → no CORS). Ollama is remote (Spark `172.20.0.22`), never containerized.

**Tech Stack:** Docker + docker compose · Python 3.12 (`python:3.12-slim` image) · Caddy 2 · Vite build (existing) · the spec-1 Postgres `app-db`.

## Global Constraints

- **Mirror `../compliance-bot`** deployment conventions (its `Dockerfile`, `.dockerignore`, `docker-compose-remote.yml`, slim-requirements split, `env_file: .env`, in-network service-name env overrides, `restart: unless-stopped`, `scp .env` → `docker compose -f … up -d --build` flow). Consistency with the ops team's existing muscle memory.
- **Front-end calls are all relative** (`fetch("/api/…")`); the only external URL is Office's `office.js` CDN (keep). Caddy serves the pane and proxies `/api/` on the **same origin** — no CORS, no absolute backend URL in the build.
- **Backend routes:** `/health` is NOT under `/api` (it's `GET /health`); the `/api/*` routes are `query` (POST), `documents`, `preferences`. Caddy must **preserve** the `/api` prefix (use `handle`, not `handle_path`).
- **In-network service URLs** (containerized backend reaches the compose services by name, not localhost): `redis://:myredissecret@redis:6379`, `http://qdrant:6333`, `postgresql://legal:${APP_DB_PASSWORD}@app-db:5432/legal`, `http://langfuse-web:3000`. `OLLAMA_BASE_URL` = the Spark box `http://172.20.0.22:11434` (external to the compose network — reachable via LAN routing), supplied via `.env`.
- **Secrets/env via `env_file: .env`** — never baked into the image (`.env`/`.env.*` in `.dockerignore`). `data/` (runtime state incl. attorney `USER.md` preferences) is a mounted volume, never baked.
- **Backend image is slim**: excludes `chainlit` (separate web client, not imported by `api.main`) and the test stack (`pytest`/`pytest-asyncio`/`testcontainers`). Accepted DRY trade-off (two requirements files) — mirrors compliance-bot's `requirements.txt` vs `requirements-bot.txt`.
- **Bucket A is testable LOCALLY** (Docker running) with **no VPN/cert/Spark**: Caddy uses its internal cert for `localhost`; `/health`/preferences smoke needs neither Ollama nor Qdrant. The trusted-cert + real hostname wiring is **bucket B** (out of scope here).
- **No application logic changes** — this is packaging/config only. Don't touch `api/`, `graph/`, `skills/`, etc. (except that `api.main` is imported as a smoke check).
- Python 3.12; `app-db` is a hard dependency at backend startup (spec-1 lesson).

## File Structure

**Created:**
- `Dockerfile` — backend image (mirrors compliance-bot).
- `.dockerignore` — exclude non-runtime (clients/, tests/, data/, docs/, .venv, .git, .superpowers, …).
- `requirements-runtime.txt` — slim runtime deps for the image.
- `Caddyfile` — reverse proxy: static pane at `/`, `/api/*` → backend.
- `docker-compose.remote.yml` — overlay adding `backend` + `caddy` on top of the base compose.
- `.env.remote.example` — VM env template (Spark Ollama, `APP_DB_PASSWORD`, `ADDIN_ORIGIN_HOST`, models).
- `clients/word/manifest.template.xml` — manifest with `${ADDIN_ORIGIN}` placeholder.
- `scripts/build_manifest.py` — render `manifest.prod.xml` from the template + `ADDIN_ORIGIN` env, self-validating.
- `docs/deploy-vm.md` — deploy runbook (mirrors compliance-bot SETUP style).

**Modified:**
- `docs/wiki.md` — "Shipped / In progress" note for the bucket-A artifacts.
- `CLAUDE.md` — one-line pointer to `docs/deploy-vm.md` (only if it fits the 150-line cap; drop a low-value line otherwise).

---

### Task 1: Backend image — slim requirements + Dockerfile + .dockerignore

**Files:**
- Create: `requirements-runtime.txt`
- Create: `Dockerfile`
- Create: `.dockerignore`

**Interfaces:**
- Produces: a buildable image `legal-plugin-backend:latest` running `uvicorn api.main:app` on `:8000`, consumed by Task 3's compose overlay.

- [ ] **Step 1: Create `requirements-runtime.txt`** (= `requirements.txt` minus chainlit + test stack)

```
# Slim runtime deps for the backend Docker image (Word add-in API).
# = requirements.txt minus chainlit (separate web client, not imported by
# api.main) and the test stack (pytest/pytest-asyncio/testcontainers).
# For local dev + tests use requirements.txt.

# API
fastapi>=0.115,<1.0
uvicorn[standard]>=0.34,<1.0

# LangGraph
langgraph>=0.4,<1.0
langchain-ollama>=0.3,<1.0
langchain-qdrant>=0.2,<1.0

# RAG
qdrant-client>=1.13,<2.0

# BM25
rank-bm25>=0.2,<1.0

# Document parsing
pdfplumber>=0.11,<1.0
python-docx>=1.1,<2.0

# Config
pydantic>=2.0,<3.0
pydantic-settings>=2.0,<3.0

# Session / checkpointer
redis>=5.0,<6.0
langgraph-checkpoint-redis>=0.1,<1.0

# App relational store (Postgres)
psycopg[binary]>=3.2,<4.0
psycopg-pool>=3.2,<4.0

# Observability
langfuse>=2.0,<3.0

# HTTP client (reranker, Ollama direct calls)
httpx>=0.28,<1.0

# Auth (O365 SSO — dormant until sso_enabled)
PyJWT[crypto]>=2.9,<3.0
```

- [ ] **Step 2: Create `.dockerignore`**

```
# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
venv/
.pytest_cache/

# Secrets & env — provided at runtime via env_file, never baked
.env
.env.*

# Runtime state (mounted as volumes in production)
data/

# Tests / eval / notebooks — not needed at runtime
tests/
eval/
notebooks/

# Word add-in source + node_modules + build (served separately by Caddy, not the backend)
clients/

# Docs / planning
docs/
*.md
!README.md

# SDD controller scratch
.superpowers/

# VCS / tooling / build artifacts
.git/
.gitignore
.DS_Store
dist/
```

- [ ] **Step 3: Create `Dockerfile`** (mirrors `../compliance-bot/Dockerfile`)

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# System deps for building any wheels lacking manylinux builds (mirrors ../compliance-bot).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first for better layer caching. Slim runtime set.
COPY requirements-runtime.txt .
RUN pip install --no-cache-dir -r requirements-runtime.txt

# Copy the backend runtime code (see .dockerignore for exclusions).
COPY . .

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 4: Build the image**

Run: `docker build -t legal-plugin-backend:test .`
Expected: build succeeds (deps install, code copies).

- [ ] **Step 5: Import smoke — the image has a working app + all backend packages**

Run: `docker run --rm --entrypoint python legal-plugin-backend:test -c "import api.main; print('import ok')"`
Expected: prints `import ok` (proves the slim deps + every backend package `api.main` transitively imports are present; imports don't open connections, so this works offline). If it fails on a missing module, that module was wrongly excluded by `.dockerignore` or missing from `requirements-runtime.txt` — fix and rebuild.

- [ ] **Step 6: Commit**

```bash
git add requirements-runtime.txt Dockerfile .dockerignore
git commit -m "feat(deploy): backend Docker image (slim runtime, mirrors compliance-bot)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Prod manifest — template + generator

**Files:**
- Create: `clients/word/manifest.template.xml`
- Create: `scripts/build_manifest.py`

**Interfaces:**
- Produces: `scripts/build_manifest.py` writing `clients/word/manifest.prod.xml` from `ADDIN_ORIGIN` env; consumed by the deploy runbook (Task 4). Dev `clients/word/manifest.xml` is left untouched.

- [ ] **Step 1: Create `clients/word/manifest.template.xml`**

Copy `clients/word/manifest.xml` verbatim, then replace **every** `https://localhost:3001` occurrence with `${ADDIN_ORIGIN}` (there are ~10: `IconUrl`, `HighResolutionIconUrl`, `SupportUrl`, the `AppDomain`, `SourceLocation`, the three `bt:Image` icon URLs, `GetStarted.LearnMoreUrl`, and `Taskpane.Url`). Leave the `Id`, strings, and structure identical. Verify no `localhost` remains in the template:

Run: `grep -c "localhost" clients/word/manifest.template.xml`
Expected: `0`. And `grep -c '${ADDIN_ORIGIN}' clients/word/manifest.template.xml` ≥ 10.

- [ ] **Step 2: Create `scripts/build_manifest.py`** (self-validating renderer)

```python
# scripts/build_manifest.py
"""Render clients/word/manifest.prod.xml from manifest.template.xml + $ADDIN_ORIGIN.

Usage: ADDIN_ORIGIN=https://legal-triage.internal.trinetix.net python scripts/build_manifest.py

Validates the output: well-formed XML, the origin substituted, no localhost left.
Exits non-zero on any failure.
"""
from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

TEMPLATE = Path("clients/word/manifest.template.xml")
OUT = Path("clients/word/manifest.prod.xml")


def main() -> int:
    origin = os.environ.get("ADDIN_ORIGIN", "").strip().rstrip("/")
    if not origin.startswith("https://") or origin == "https:":
        print("ERROR: set ADDIN_ORIGIN to the https origin (e.g. https://host.internal)", file=sys.stderr)
        return 2

    text = TEMPLATE.read_text(encoding="utf-8").replace("${ADDIN_ORIGIN}", origin)

    # Validate the rendered text IN MEMORY before touching the output file, so a
    # failed run never writes (or overwrites a previously-good) manifest.prod.xml.
    try:
        ET.fromstring(text)  # raises ParseError on malformed XML (e.g. unescaped & in origin)
    except ET.ParseError as e:
        print(f"ERROR: rendered manifest is not well-formed XML: {e}", file=sys.stderr)
        return 1
    if "localhost" in text:
        print("ERROR: localhost still present in rendered manifest (incomplete template?)", file=sys.stderr)
        return 1
    if origin not in text:
        print("ERROR: origin not present in rendered manifest", file=sys.stderr)
        return 1

    OUT.write_text(text, encoding="utf-8")  # write only after all checks pass
    print(f"wrote {OUT} with origin {origin}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Run it and verify**

Run: `ADDIN_ORIGIN=https://legal-triage.internal.trinetix.net python scripts/build_manifest.py`
Expected: prints `wrote clients/word/manifest.prod.xml with origin https://legal-triage.internal.trinetix.net`, exit 0.

Run: `grep -c localhost clients/word/manifest.prod.xml`
Expected: `0`.

- [ ] **Step 4: Verify the failure path**

Run: `python scripts/build_manifest.py` (no `ADDIN_ORIGIN`)
Expected: prints the ERROR, exit code 2 (non-zero) — the renderer refuses to write a localhost/blank manifest.

- [ ] **Step 5: Gitignore the generated output, commit the template + script**

Add `clients/word/manifest.prod.xml` to `.gitignore` (it's generated per-deploy). Then:

```bash
git add clients/word/manifest.template.xml scripts/build_manifest.py .gitignore
git commit -m "feat(deploy): prod manifest template + build_manifest.py renderer

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Caddy + VM compose overlay + local co-hosting smoke

**Files:**
- Create: `Caddyfile`
- Create: `docker-compose.remote.yml`
- Create: `.env.remote.example`

**Interfaces:**
- Consumes: the `legal-plugin-backend` image (Task 1) and the existing base `docker-compose.yml` services (`redis`, `qdrant`, `app-db`, `langfuse-*`).
- Produces: `docker compose -f docker-compose.yml -f docker-compose.remote.yml …` bringing up `backend` + `caddy`; Caddy serves the built pane at `/` and proxies `/api/*` to `backend:8000`.

- [ ] **Step 1: Create `Caddyfile`** (preserve the `/api` prefix — routes are `/api/*`)

```
{$ADDIN_ORIGIN_HOST:localhost} {
	encode gzip

	# Backend API — keep the /api prefix (backend routes are /api/*)
	handle /api/* {
		reverse_proxy backend:8000
	}

	# Everything else = the built task pane (fallback to taskpane.html)
	handle {
		root * /srv
		try_files {path} /taskpane.html
		file_server
	}
}
```

(For local testing `ADDIN_ORIGIN_HOST` is unset → `localhost` → Caddy serves an internal-CA cert. On the VM it's the real hostname; the **trusted-cert wiring is bucket B** — for a hostname without public DNS, add `tls /etc/caddy/cert.pem /etc/caddy/key.pem` + mount the cert. Documented in Task 4.)

- [ ] **Step 2: Create `docker-compose.remote.yml`** (overlay adding `backend` + `caddy`)

```yaml
services:
  backend:
    build: .
    image: legal-plugin-backend:latest
    env_file:
      - .env
    environment:
      # In-network service discovery (overrides the localhost defaults from config.py;
      # `environment` takes precedence over env_file).
      - REDIS_URL=redis://:myredissecret@redis:6379
      - QDRANT_URL=http://qdrant:6333
      - DATABASE_URL=postgresql://legal:${APP_DB_PASSWORD:-legal}@app-db:5432/legal
      - LANGFUSE_HOST=http://langfuse-web:3000
      # OLLAMA_BASE_URL is supplied by .env (the Spark box: http://172.20.0.22:11434)
    volumes:
      - ./data/attorneys:/app/data/attorneys   # attorney USER.md preferences persist
    depends_on:
      redis:
        condition: service_healthy
      app-db:
        condition: service_healthy
      qdrant:
        condition: service_started
    restart: unless-stopped

  caddy:
    image: caddy:2
    ports:
      - "80:80"
      - "443:443"
    environment:
      - ADDIN_ORIGIN_HOST=${ADDIN_ORIGIN_HOST:-localhost}
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - ./clients/word/dist:/srv:ro
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      - backend
    restart: unless-stopped

volumes:
  caddy_data:
  caddy_config:
```

- [ ] **Step 3: Create `.env.remote.example`**

```
# ── Backend runtime env for the VM (docker-compose.remote.yml) ──
# Spark GPU box — Ollama LLM + embeddings (see ../compliance-bot: 172.20.0.22).
OLLAMA_BASE_URL=http://172.20.0.22:11434
LLM_MODEL=qwen3.6:latest            # match the model pulled on Spark
EMBEDDING_MODEL=nomic-embed-text    # match the model the Qdrant collection was built with
QDRANT_VECTOR_DIM=768

# App Postgres (app-db service) — must equal the app-db POSTGRES_PASSWORD.
APP_DB_PASSWORD=change-me

# Public hostname the add-in loads from (Caddy site address). Leave unset for
# local testing → Caddy uses "localhost" + an internal cert. On the VM set the
# real internal hostname (needs a trusted cert — see docs/deploy-vm.md, bucket B).
ADDIN_ORIGIN_HOST=legal-triage.internal.trinetix.net

# Langfuse (optional — omit the langfuse-* services for a lean tester deploy).
LANGFUSE_PUBLIC_KEY=pk-lf-local
LANGFUSE_SECRET_KEY=sk-lf-local
```

- [ ] **Step 4: Validate the merged compose**

Run: `docker compose -f docker-compose.yml -f docker-compose.remote.yml config -q`
Expected: exit 0, no output (valid).

- [ ] **Step 5: Build the task pane** (Caddy serves `clients/word/dist`)

Run: `cd clients/word && npm ci && npm run build && cd ../..`
Expected: `clients/word/dist/taskpane.html` + hashed assets exist. Verify the built pane has no hardcoded backend host: `grep -rc "localhost:8000\|localhost:3001" clients/word/dist || true` → `0`.

- [ ] **Step 6: Local co-hosting smoke** (Docker up; no VPN/cert/Ollama needed)

Prepare a local env (the smoke needs `APP_DB_PASSWORD`; `/api/preferences` + `/` need neither Ollama nor Qdrant):

```bash
cp .env.remote.example .env.smoke
# for the smoke, point env_file at a localhost origin + a real app-db password:
printf '\nAPP_DB_PASSWORD=legal\nADDIN_ORIGIN_HOST=localhost\n' >> .env.smoke
docker compose --env-file .env.smoke -f docker-compose.yml -f docker-compose.remote.yml up -d --build redis app-db qdrant backend caddy
```

Wait for the backend to be healthy (give it ~10s; `docker compose … logs backend` should show uvicorn startup + `Store schema initialized`). Then:

```bash
# Caddy serves the pane:
curl -sk https://localhost/ | grep -qi "Legal Triage" && echo "PANE OK"
# Caddy proxies /api/* to the backend (GET /api/preferences returns 200 with an X-User-ID):
curl -sk -o /dev/null -w "api:%{http_code}\n" https://localhost/api/preferences -H "X-User-ID: smoke-test"
```

Expected: `PANE OK` and `api:200`. This proves the whole co-hosting mechanism: Caddy serves the built pane at `/` and proxies `/api/` to the containerized backend, which reached `app-db` on the compose network at startup.

Note: if the backend fails to start without the Langfuse stack, its observability init should be best-effort (per the degraded-memory philosophy) — confirm `observability/langfuse.py` doesn't hard-raise; if it does, also bring up `langfuse-web` (and its deps) for the smoke, or set the langfuse env to a no-op. Do not change app code to work around it.

- [ ] **Step 7: Tear down + clean the smoke env**

```bash
# Tear down ONLY the added services — a bare `down` would also stop any
# pre-existing dev infra (redis/app-db/qdrant/langfuse) that was already running.
docker compose -f docker-compose.yml -f docker-compose.remote.yml rm -sf backend caddy
rm -f .env.smoke
```

- [ ] **Step 8: Commit**

`.env.smoke` is transient (removed above); `.env`/`.env.*` are already gitignored. Commit the artifacts:

```bash
git add Caddyfile docker-compose.remote.yml .env.remote.example
git commit -m "feat(deploy): Caddy reverse proxy + VM compose overlay (backend + caddy)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Deploy runbook + docs

**Files:**
- Create: `docs/deploy-vm.md`
- Modify: `docs/wiki.md`
- Modify: `CLAUDE.md` (one-line pointer, only if ≤150 lines holds)

- [ ] **Step 1: Write `docs/deploy-vm.md`** (mirrors `../compliance-bot/SETUP.md` "Docker Deployment" style)

Include, in this order:

1. **Prerequisites (bucket B — gated):** VPN reachability to `SRV-AGENT-01` (`172.20.1.10`); a **trusted cert + internal hostname** (Office.js refuses self-signed for others); Spark (`172.20.0.22`) reachable — verify: `curl http://172.20.0.22:11434/api/tags`.
2. **Configure:** `cp .env.remote.example .env`; fill `OLLAMA_BASE_URL=http://172.20.0.22:11434`, `ADDIN_ORIGIN_HOST=<hostname>`, `APP_DB_PASSWORD`, and `LLM_MODEL`/`EMBEDDING_MODEL`/`QDRANT_VECTOR_DIM` to match what Spark serves + what the Qdrant collection was built with.
3. **Cert wiring (gated):** for an internal hostname without public DNS, add `tls /etc/caddy/cert.pem /etc/caddy/key.pem` to the `Caddyfile` site block and mount the cert + key into the `caddy` service.
4. **Build the pane:** `cd clients/word && npm ci && npm run build`.
5. **Bring up:** `docker compose -f docker-compose.yml -f docker-compose.remote.yml up -d --build redis qdrant app-db backend caddy` (Langfuse services optional — add `langfuse-web langfuse-worker postgres clickhouse minio` if tracing is wanted). Note **`app-db` is a hard dep** (bring it up + it must be healthy) and **volumes/backups** (reviews are work product).
6. **Verify:** `curl -k https://<hostname>/api/preferences -H "X-User-ID: test"` → 200; load the pane in Word; run a review and confirm the row in `app-db` (`docker compose … exec app-db psql -U legal -d legal -c "SELECT count(*) FROM review_store;"`).
7. **Manifest + sideload:** `ADDIN_ORIGIN=https://<hostname> python scripts/build_manifest.py` → `clients/word/manifest.prod.xml`; sideload per surface (Windows shared-folder catalog / Mac `wef` / Word-for-web Upload My Add-in).

- [ ] **Step 2: Update `docs/wiki.md`**

Add an "In progress / Shipped" note: bucket-A deployment artifacts landed (backend Dockerfile + slim runtime reqs, Caddy reverse proxy, `docker-compose.remote.yml` overlay, prod manifest renderer, `docs/deploy-vm.md`), mirroring `../compliance-bot`; bucket B (provision hostname + trusted cert, deploy to `SRV-AGENT-01`, sideload, tester smoke) is gated on VPN/cert/surface.

- [ ] **Step 3: Update `CLAUDE.md` (only if it stays ≤150 lines)**

Add a one-line pointer under the repo-layout or a deployment note: e.g. `docs/deploy-vm.md   VM deploy runbook (Docker: backend image + Caddy + compose overlay; Ollama on Spark 172.20.0.22)`. Run `wc -l < CLAUDE.md`; if it would exceed 150, consolidate or drop the lowest-value existing line instead.

- [ ] **Step 4: Commit**

```bash
git add docs/deploy-vm.md docs/wiki.md CLAUDE.md
git commit -m "docs(deploy): VM deploy runbook + wiki/CLAUDE notes (bucket A)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage** — maps to spec 2's "A. Buildable & testable NOW":
- Prod manifest variant → Task 2. Caddy config → Task 3. Remote-Ollama wiring (env → Spark) + VM compose overlay → Task 3 (`.env.remote.example` + `docker-compose.remote.yml`; no local Ollama container exists to remove — corrected per the compliance-bot finding). Front-end production build → Task 3 Step 5. Backend containerization (net-new vs the spec's wording, required because the backend wasn't containerized) → Task 1. Deploy runbook → Task 4. Mirrors `../compliance-bot` throughout (Global Constraints).
- Not in bucket A (correctly absent): hostname/cert provisioning, VM deploy, sideload, tester smoke (all bucket B); Centralized Deployment, SSO-on, SharePoint (spec-2 Future).

**2. Placeholder scan** — no TBD/TODO; every file has complete content; `${ADDIN_ORIGIN}` / `ADDIN_ORIGIN_HOST` / `legal-triage.internal.trinetix.net` are intentional template placeholders (the hostname is spec-2 Open Decision #3), not gaps. Each task ends with concrete commands + expected output.

**3. Type/name consistency** — image name `legal-plugin-backend:latest` (Task 1 produces, Task 3 compose references) matches; `ADDIN_ORIGIN` (build_manifest env) vs `ADDIN_ORIGIN_HOST` (Caddy site-address env) are deliberately distinct (full origin vs host) and each used consistently; service names (`backend`, `caddy`, `redis`, `qdrant`, `app-db`, `langfuse-web`) match the base compose; `/api/*` prefix preserved end-to-end (front-end relative calls → Caddy `handle /api/*` → backend routes).
