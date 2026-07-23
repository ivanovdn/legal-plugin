# VM / O365 deployment — design

- **Date:** 2026-07-23
- **Status:** Draft — awaiting review
- **Spec 2 of 2** in the "run on company O365 / SharePoint" effort. Spec 1 (Postgres store migration) is **shipped & merged** (`aceb470`) — this spec builds on it (the backend's durable stores now live in a Postgres `app-db` that travels with the docker-compose stack).

## Context & motivation

Today the add-in only runs on the author's Mac: the front-end is a Vite dev server on `https://localhost:3001` (self-signed cert), the backend + stack are `docker compose` + a local Ollama on the same machine, the manifest is hard-wired to `localhost`, and sideload is the Mac `wef/` folder. The goal is to get it in front of **a couple of legal-team testers** working in Word with contracts stored in **SharePoint**, then eventually the wider team.

This spec covers making the app **hostable and reachable** for those testers. It deliberately stops short of the fully-automated org rollout (Centralized Deployment) and real SSO, which are staged as future phases.

## Goals

- **Host** the front-end (static Vite build) + backend behind **one reverse proxy on `SRV-AGENT-01`**, same origin, one trusted cert.
- **Run the LLM off-box**: point the backend at **Ollama on the "spark" GPU box**; drop the local model container.
- **Producible manifest** pointing at the hosted internal hostname (a prod variant, dev localhost variant kept).
- **Sideload** the add-in to a couple of testers on whatever Word surface they use.
- Keep identity as the per-install UUID (SSO seam stays dormant).
- Decompose the work so the **prereq-free parts can be built and tested now**, and the parts gated on VPN/cert/IT are clearly separated.

## Non-goals (deferred to future phases, in this doc's "Future")

- **Centralized Deployment** via the M365 admin center (Integrated Apps) — the automated org-wide distribution. Manual sideload first.
- **SSO turned on**: the client wiring (`getAccessToken()` + manifest `WebApplicationInfo` + dialog fallback) and the Azure app registration. The backend `resolve_user_id` seam already exists, dormant.
- **AppSource** publishing; **WebSocket/SSE** streaming.
- **Deep SharePoint integration** — ingesting a SharePoint contracts library into RAG, or writing results back to SharePoint. Testers just *open* contracts from SharePoint in Word; nothing is built inside SharePoint.
- Structured-JSON `contract_review` output / generate-clause tab (pre-existing backlog, unrelated).

## Prerequisites (owned by you / IT — these gate the "prereq-gated" work below)

1. **VPN reachability** to `SRV-AGENT-01` (`172.20.1.10`, internal VPN). Per memory `project_agent_vm_access`, the server-side split-include add and the username are still TBD. Testers' machines must reach the VM (on VPN or corp network) for the pane to load and call `/api/`.
2. **Trusted cert + hostname** on the VM. Office.js **refuses self-signed certs for anyone but the author** and Word-for-web will warn in the browser. Two acceptable mechanisms (pick one — see Open Decisions):
   - (a) public cert via a public DNS name resolving internally (split-horizon DNS + Let's Encrypt DNS-01), or
   - (b) an internal-CA cert the corp machines already trust.
3. **Word surface** the testers use — Windows desktop, Mac desktop, or Word-for-web (opening from SharePoint often lands in Word-for-web). This drives the sideload path and which surfaces get smoke-tested. See Open Decisions.
4. **Spark box (`172.20.0.22`)**: reachable from `SRV-AGENT-01` over the private network, `ollama serve` bound to the LAN (`OLLAMA_HOST=0.0.0.0`), the model pulled, and `OLLAMA_KEEP_ALIVE`/`OLLAMA_NUM_PARALLEL` set for the hardware. Verify before deploy: `curl http://172.20.0.22:11434/api/tags`. Never expose Ollama publicly.

## Reference implementation — `../compliance-bot` (already on the VM)

A sibling repo is already deployed on this VM/network. **Mirror its container pattern** for consistency and to de-risk: its `Dockerfile` (python:3.12-slim + build-essential + git, a slim runtime-requirements file, copy runtime code only, `PYTHONPATH=/app` + `PYTHONUNBUFFERED=1`), its `.dockerignore`, and its `docker-compose-remote.yml` (`build: .` + `env_file: .env` + **in-network service-name env overrides** + data-dir volumes + `restart: unless-stopped` + `depends_on` healthchecks). Deploy flow to copy: `scp` a configured `.env` to the host, then `docker compose -f docker-compose-remote.yml up -d --build`; verify Spark connectivity first. **Caveat:** compliance-bot is a Teams bot (outbound) — it has **no reverse proxy, cert, or inbound HTTPS**, so the Caddy + cert + pane-serving parts of this spec have no precedent to copy and are genuinely new.

## Target architecture

Core decision (from brainstorming): **co-host the front-end and backend behind one reverse proxy on `SRV-AGENT-01`, same origin.** One cert, no CORS, and it mirrors the current Vite `^/api/` proxy — just in production.

```
User's Word  (desktop Win/Mac  OR  Word-for-web in a browser)
   │  task-pane webview loads the add-in over HTTPS
   ▼
https://legal-triage.internal.trinetix.net            ← ONE trusted cert, ONE origin
   │
[ Reverse proxy: Caddy ]  on SRV-AGENT-01 (172.20.1.10, internal VPN)
   ├─ /            → static Vite build (taskpane.html + assets)
   └─ /api/        → FastAPI :8000        ┐
                       ├ Redis (checkpointer)     │
                       ├ Qdrant (RAG)             ├ docker compose on SRV-AGENT-01
                       ├ Postgres app-db (:5434)  │  (shipped in spec 1)
                       ├ Langfuse stack           ┘
                       └ ollama_base_url ──────────► spark GPU box: `ollama serve` on the LAN
```

## Design decisions (with rationale)

- **Front-end hosting = co-host behind the proxy (not a separate static host).** Same origin kills CORS, one cert, and a public static host calling a VPN-only backend would be a mixed-network mess. Overkill to split for a couple of users.
- **Reverse proxy = Caddy.** Automatic HTTPS, ~5-line config, docker-native. (Traefik is an acceptable alternative if compose-label config is preferred; nginx works but is more config.)
- **Cert = a trusted cert, mechanism per Open Decisions** — this is the single make-or-break item; self-signed is a stopgap only (author's machine).
- **LLM off-box** — `config.ollama_base_url` → `http://172.20.0.22:11434` (the Spark GPU box; confirmed from `../compliance-bot`). **There is no local `ollama` container to remove** — Ollama is host/remote-installed here, never containerized. `num_ctx` is already pinned in code (spec-1-era `ollama_num_ctx`), so remote calls stay correctly grounded.
- **Manifest = a prod variant** with every `https://localhost:3001` replaced by the hosted origin (`SourceLocation`, `AppDomains`, `IconUrl`/`SupportUrl`, `bt:Url`s). Keep `manifest.localhost.xml` for dev so the two don't collide; generate from a template or keep two tracked files.
- **Identity unchanged** — per-install `localStorage` UUID as `X-User-ID`. Accepted caveat: per-machine/per-browser, so a tester using both desktop and web gets two identities → their preferences/conversation memory won't follow them across surfaces. That's the concrete argument for SSO, deferred.

## Work decomposition

### A. Buildable & testable NOW (no VPN/cert/IT dependency)

1. **Prod manifest variant** — templated URLs; a build/substitution step or two tracked files. Validate with `office-addin-manifest validate`.
2. **Caddy config** (`Caddyfile`) — serve the static build at `/`, reverse-proxy `^/api/` → `:8000`. Testable locally against a local cert / `localhost` before the real hostname exists.
3. **Remote-Ollama wiring** — `ollama_base_url` via env; a compose variant for the VM that omits the local `ollama` container; document the spark-side env. Testable by pointing a local backend at any remote Ollama.
4. **Front-end production build** — `vite build` → static bundle served by Caddy (not the dev server). Confirm the built bundle works behind the proxy (relative `/api/` calls, no hard-coded localhost).
5. **VM compose overlay** — a `docker-compose.vm.yml` (or overlay) that runs the app stack (FastAPI + Redis + Qdrant + app-db + Langfuse + Caddy) on the VM, minus local Ollama; persistent volumes + restart policies + `app-db` backup note.
6. **Deploy/runbook doc** — how the VM boots the stack, the `app-db` hard-dep discipline (up + restart), and the sideload steps per surface.

### B. Gated on the prereqs (needs VPN/cert/hostname/surface)

7. Provision the **hostname + trusted cert** on the VM; wire Caddy to it.
8. **Deploy** the stack to `SRV-AGENT-01`; point the spark Ollama; bring up `app-db` first.
9. **Sideload** to the testers per their surface (Windows shared-folder catalog / Mac `wef` / Word-for-web Upload My Add-in).
10. **End-to-end smoke on a tester machine**: pane loads over the trusted cert, a review runs and persists to `app-db`, reachability confirmed on VPN.

## Config & code touch-points (indicative — detailed in the plan)

- `config.py`: `ollama_base_url` already exists (env-overridable) — set it for the VM; no new field likely needed. Consider an explicit `cors_allow_origins` only if the front-end ever leaves the proxy origin (not now — same-origin keeps CORS moot; today's `allow_origins=["*"]` is acceptable behind the VPN proxy but worth tightening in a hardening pass).
- `clients/word/manifest.*.xml`: prod variant.
- `clients/word/vite.config.ts` / build: production build path (the dev proxy/no-store/self-signed bits are dev-only and stay).
- New infra: `Caddyfile`, `docker-compose.vm.yml` (overlay), deploy notes in `docs/`.
- `docs/wiki.md` + `CLAUDE.md`: deployment topology + the `app-db`/restart discipline (partially added in spec 1).

## Identity & multi-user

- Per-install UUID now (partitioning key, not auth). SSO seam dormant (`sso_enabled=False`).
- **Ollama contention**: one shared model on the spark box; concurrent grounded calls (~30s each) queue — set `OLLAMA_NUM_PARALLEL` per VRAM.
- **app-db**: persistent volume + backups (reviews are real work product); it is a **hard dependency** (backend fails / hangs ~30s then errors if it's down — spec-1 lesson).

## Risks & trade-offs

- **Cert/DNS is the make-or-break** — everything downstream (pane loading on tester machines, Word-for-web) depends on a trusted cert. Treat as the critical-path prereq.
- **Word-for-web surface**: if testers open SharePoint docs in Word-for-web, some Office.js behaviors differ from the Mac-desktop-tested paths (documented `body.search`/tracked-changes quirks) — needs its own smoke, may surface add-in bugs not seen on desktop.
- **VPN-only reachability** limits testers to on-VPN use; a public ingress (deferred) would remove that but enlarges the security surface and needs sign-off.
- **Manual sideload** doesn't scale beyond a handful — acceptable for the tester phase, replaced by Centralized Deployment later.
- **Per-install identity** splits a tester's memory across surfaces — acceptable short-term, resolved by SSO.

## Validation

- **A (now)**: prod manifest validates; built bundle loads behind Caddy locally; backend reaches a remote Ollama; VM compose overlay comes up clean.
- **B (after prereqs)**: the pane loads in the testers' Word over the trusted cert; a real contract review runs and its row lands in `app-db` (`review_store` + `audit_log`, per the spec-1 smoke); a tester on VPN can complete a review end-to-end. Smoke every surface the team actually uses.

## Future (explicitly deferred)

- **Centralized Deployment** via M365 admin center (one manifest → assigned users, all surfaces).
- **SSO on**: Azure app registration + client `getAccessToken()` + manifest `WebApplicationInfo` + dialog fallback (backend seam already shipped).
- **Deep SharePoint**: ingest a contracts library into RAG (Microsoft Graph), or write reviewed/finalized docs back to a library.
- Public HTTPS ingress (no-VPN access) if the team needs off-VPN use.

## Open decisions (confirm before implementation)

1. **Cert mechanism** — (a) public cert via split-horizon DNS (Let's Encrypt DNS-01) or (b) internal-CA cert the corp machines already trust? Whichever the company already has.
2. **Word surface** — Windows desktop / Mac desktop / Word-for-web / mixed? Drives sideload path + which surfaces to smoke (the design accommodates all; this narrows the sideload + test work).
3. **Hostname** — the internal DNS name for the VM (placeholder `legal-triage.internal.trinetix.net`).

These don't block writing the **A (prereq-free)** implementation plan; they must be settled before the **B (gated)** tasks.
