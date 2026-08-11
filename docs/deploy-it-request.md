# IT request — internal hostname + TLS cert for the Legal Triage add-in

*Two requests below, each in its own box. **Request 1** (DNS + cert) goes to whoever provisions internal DNS/certificates. **Request 2** (Centralized Deployment) goes to a Microsoft 365 Global Admin — it can wait until Request 1 is done and 1–2 testers have validated the add-in. Everything after the boxes is context for us, not for IT.*

---

## Request 1 — DNS + TLS certificate

**Subject: Internal DNS name + HTTPS certificate for a Word add-in on SRV-AGENT-01**

We're running an internal web app — a Microsoft Word add-in for the legal team — on **SRV-AGENT-01 (`172.20.1.10`)**. It's deployed and working; the last step before we can roll it out is serving it over HTTPS with a certificate the users' machines trust. Word will not load an add-in over a self-signed certificate, so we need a proper internal one. Could you please provide:

1. **A DNS name** for the VM's internal address — e.g. `legal-triage.internal.trinetix.net` → `172.20.1.10`.
2. **A TLS certificate + private key for that hostname, issued by our internal CA** (so corporate Windows and Mac machines trust it automatically). If internal certificates are normally issued a different way, please let us know the standard process.
3. Confirmation that the **legal team's machines can reach `172.20.1.10` on TCP port 443** (over VPN / the corporate network) and **trust the issuing CA**.

It's an internal-only tool (no public internet exposure) — access stays on the corporate network / VPN.

Thank you!

---

## Request 2 — Centralized Deployment (team-wide distribution)

**Subject: Deploy an internal Word add-in to the Legal team via Integrated Apps**

We have an internal Microsoft Word add-in ("Legal Triage") we'd like made available to the legal team so it appears automatically on their Word ribbon — no per-machine installation. Could a **Microsoft 365 Global Admin** please:

1. In the **Microsoft 365 admin center → Settings → Integrated apps → Upload custom apps**, upload our add-in **manifest** (`manifest.prod.xml` — we'll provide the file).
2. Assign it to a **"Legal Team" security/M365 group** (we'll provide the member list) — or a small pilot subset first.
3. Accept the requested permission (the add-in only reads/writes the **currently open document** — no mailbox or tenant data).

Notes for the admin:
- This is an **internal line-of-business add-in** (not from AppSource) — hence "Upload custom apps."
- Once assigned, it appears in **Word on Windows, Mac, and the web**, tied to each user's M365 account — so it shows up whether they open a local file or one from **SharePoint/OneDrive**. Initial propagation can take up to ~24h.
- **This depends on Request 1.** The manifest points at the internal hostname from the DNS/cert request, and the add-in loads live from `SRV-AGENT-01` — so please deploy this only after that hostname/cert is in place, and confirm the assigned users can reach that host on the corporate network / VPN (the add-in is dark for anyone who can't).

Thank you!

---

## Request 3 — Entra app registration for SSO (later, optional)

*This is only needed to replace the interim self-entered name with verified O365 identity. Not required for the tester phase.*

**Subject: Entra app registration for the Legal Triage Word add-in (Office SSO)**

To let the add-in identify users by their verified O365 identity, please register an app in the **Trinetix Inc** tenant (`3df46721-ba07-4b23-968c-cb40dee5230e`):

1. **Single-tenant** app registration named "Legal Triage".
2. **Expose an API:** set the Application ID URI to `api://<add-in-hostname>/<client-id>` (hostname = the internal DNS name from Request 1); add a delegated scope `access_as_user`.
3. **Pre-authorize the Office host client IDs** for that scope (the standard Microsoft Office desktop/web/mobile app IDs) so `getAccessToken()` works without a per-user consent prompt.
4. **Grant admin consent** for the scope.
5. Return the **Application (client) ID**.
6. Confirm **SRV-AGENT-01 (`172.20.1.10`) can reach `login.microsoftonline.com`** (the backend validates tokens against Microsoft's JWKS there) — its outbound egress is currently restricted.

Once provided, we set `sso_enabled=True` + `sso_tenant_id` + `sso_client_id`, add `WebApplicationInfo` to the manifest, and wire the client `getAccessToken()`. Until then, users self-enter their name (no Azure needed).

*(Note: the requester cannot create this app registration themselves — the Azure portal returns 401 "You do not have access" — so it needs an Entra/Global admin.)*

---

## Context (for us, not IT)

- **Why a cert at all:** a Word task-pane add-in is a web page loaded *inside Word on the user's machine*; it fetches the pane and calls the backend directly over HTTPS. Office.js refuses any origin whose certificate the machine doesn't already trust — so a trusted cert is mandatory for anyone other than the developer.
- **Status when this was written (2026-08-10):** the full stack runs on SRV-AGENT-01 (backend + Caddy + Redis + app-db, LLM + Qdrant on Spark `172.20.0.22`). Validated end-to-end: pane served, `/api/` proxied, a real NDA review generated by `qwen3.6` and persisted to Postgres. Only the trusted-cert/hostname is missing.
- **What we do once IT delivers it** (see `docs/deploy-vm.md`):
  1. Set `ADDIN_ORIGIN_HOST=<hostname>` in `.env` and add the Caddy `tls` block + mount the cert/key.
  2. Rebuild the pane, then `ADDIN_ORIGIN=https://<hostname> python scripts/build_manifest.py`.
  3. Sideload `clients/word/manifest.prod.xml` and test in real Word.
  4. Hand the manifest to the couple of legal-team testers.
- **Preferred hostname:** `legal-triage.internal.trinetix.net` (placeholder — adjust to whatever fits the internal naming convention).
- **Distribution staging** (Request 2 is the last step, not the first):
  1. **Testers (now):** manual sideload of `manifest.prod.xml` — **no admin needed**, just Request 1's cert. Windows = shared-folder catalog · Mac = `wef` folder · Word-web = Upload My Add-in.
  2. **Team-wide:** **Centralized Deployment** (Request 2) — a Global Admin uploads the *same* manifest once and assigns it to the Legal group; it then appears automatically for everyone.
  3. Both paths install the same manifest (a pointer); the add-in always loads live from `SRV-AGENT-01`, so Request 1's cert + VPN reachability is required for either.
- **Manifest `<Id>`:** the prod manifest reuses the dev `<Id>`, so a machine can't have both the dev and prod add-in sideloaded at once (only matters for the developer's own machine; testers are unaffected).
