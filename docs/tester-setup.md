# Legal Triage — tester setup (interim, self-signed cert)

**What this is:** how to load the Legal Triage Word add-in on a tester's machine **before IT issues a trusted certificate**. It reproduces the developer workaround: trust the server's own (self-signed) certificate authority, point the hostname at the VM, and sideload the add-in.

**This is temporary.** Once IT delivers a real cert + DNS name (see `docs/deploy-it-request.md`, Request 1), steps 2–3 below disappear and testers just sideload the manifest. Manually trusting a private CA is a security-sensitive action — keep this to a small, informed pilot group and loop in security for anything wider.

**Prerequisite for everyone:** the tester's machine must reach **`172.20.1.10:443`** over the corporate network / VPN. No VPN → the pane can't load and none of this works.

---

## Part A — Operator (run once, then hand out the files)

Done on the VM (`sa.ivanov@srv-agent-01`) unless noted. Pick a hostname — this guide uses `legal-triage.internal.trinetix.net`; use the same string everywhere.

1. **Point the deployment at the hostname.** In `~/legal-plugin/.env` set:
   ```
   ADDIN_ORIGIN_HOST=legal-triage.internal.trinetix.net
   ```

2. **Rebuild the pane and restart Caddy** (Caddy mints a self-signed cert for that hostname via its internal CA — the `tls internal` line in the `Caddyfile`):
   ```bash
   cd ~/legal-plugin
   docker run --rm -v "$PWD/clients/word":/app -w /app node:20 sh -c "npm ci && npm run build"
   docker compose -f docker-compose.yml -f docker-compose.remote.yml up -d --force-recreate caddy
   ```

3. **Extract the root CA** testers will trust (persisted in the `caddy_data` volume, so this file is stable across redeploys):
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.remote.yml \
     cp caddy:/data/caddy/pki/authorities/local/root.crt ./caddy-root-ca.crt
   ```

4. **Generate the manifest** (can run on the Mac or anywhere with Python + the repo — the manifest is just URLs, it doesn't touch the VM):
   ```bash
   ADDIN_ORIGIN=https://legal-triage.internal.trinetix.net python scripts/build_manifest.py
   # writes clients/word/manifest.prod.xml
   ```

5. **Hand each tester three files:** `caddy-root-ca.crt`, `manifest.prod.xml`, and this guide.

> ⚠️ Never run `docker compose … down -v` on the VM — it deletes `caddy_data` and regenerates the CA, invalidating every tester's trust (they'd all have to re-import).

---

## Part B — Each tester (5–10 min, needs local admin)

### 1. Get on the corporate network / VPN
Confirm you can reach the server:
```bash
curl -k https://172.20.1.10/api/preferences -H "X-User-ID: test"   # expect a JSON 200
```

### 2. Make the hostname resolve to the VM
Add one line to your hosts file (needs admin/sudo):

- **Mac / Linux:** `sudo nano /etc/hosts` → add:
  ```
  172.20.1.10   legal-triage.internal.trinetix.net
  ```
- **Windows:** open Notepad **as Administrator** → `C:\Windows\System32\drivers\etc\hosts` → add the same line.

### 3. Trust the root CA (`caddy-root-ca.crt`)
This is what lets Word (and your browser) accept the server's certificate.

- **Mac:**
  ```bash
  sudo security add-trusted-cert -d -r trustRoot \
    -k /Library/Keychains/System.keychain caddy-root-ca.crt
  ```
- **Windows** (elevated PowerShell / Command Prompt):
  ```
  certutil -addstore -f Root caddy-root-ca.crt
  ```
- **Firefox only** (it ignores the OS store): Settings → Privacy & Security → Certificates → View Certificates → Authorities → Import → select `caddy-root-ca.crt` → trust for websites.

### 4. Sanity check
Open this in your browser: **https://legal-triage.internal.trinetix.net/taskpane.html**
- Clean padlock, page loads → you're good, continue.
- Certificate warning → step 2 or 3 didn't take; fix before sideloading (in Word-for-web a bad cert = a silently blank pane).

### 5. Sideload the add-in
Pick your Word surface:

- **Word for Mac (desktop):**
  ```bash
  cp manifest.prod.xml \
    ~/Library/Containers/com.microsoft.Word/Data/Documents/wef/legal-triage.manifest.xml
  ```
  Quit and reopen Word → **Home → Add-ins → (dialog) → Shared Folder → Legal Triage**.

- **Word for Windows (desktop):** put `manifest.prod.xml` in a shared folder, then Word → **File → Options → Trust Center → Trust Center Settings → Trusted Add-in Catalogs** → add the folder's network path, tick **Show in Menu** → restart Word → **Insert → My Add-ins → Shared Folder → Legal Triage**.

- **Word for the web (browser):** open a document from SharePoint/OneDrive → **Home (or Insert) → Add-ins → Upload My Add-in** → select `manifest.prod.xml` → Upload.

### 6. Identify yourself
Open the add-in → **Preferences tab → "Your name"** → enter your name. It tags your activity in the audit log (backend attribution; there's no login yet).

### 7. Telling us what it got wrong

This is the part of the pilot that matters most. **Work on real contracts the way you normally would** — the point is to find where the assistant is wrong, not to exercise every button.

**The ⚑ button** sits on every finding, every proposed edit, and every chat reply. Use it whenever something is wrong: a finding that isn't a real problem, an edit that changes the wrong thing, an answer that's incorrect. One sentence is plenty — *"this clause is fine as written"*, *"wrong field"*, *"we never require this"*.

**The "Send feedback" button at the top** is for what the assistant **missed** — *"it didn't flag the assignment clause"*. Please use it. A missed issue leaves no trace anywhere; if you don't tell us, nothing else can.

**You don't need to report anything else.** Which edits you Apply and which you Discard is already recorded, so a Discard is itself a signal. Just work normally.

**What gets sent:** your note, the text of the document you're working on, and the specific finding or edit you flagged. Your name is attached.

**Where it goes:** to the development team, so we can reproduce the exact case. **It does not train the assistant** — flagging something will not change its behaviour tomorrow. It gets the problem fixed properly instead.

> **Save your document before you close it.** On an unsaved document, Word can't store the id that ties your feedback to that contract, so reports from a closed-unsaved draft can't be grouped with later ones. **The pane tells you when this applies** — a blue "This document isn't saved yet" notice above the tabs. Save the file and it clears. If you don't see the notice, you're fine. (The underlying limitation is a known bug; the notice is so it never costs you work silently.)

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Blank pane in Word-for-web | Browser doesn't trust the CA (iframes can't prompt) | Redo step 3 for **that browser** (Firefox needs its own import) |
| "Add-in could not be loaded" / cert error (desktop) | CA not trusted, or hostname not resolving | Redo steps 2 + 3; re-run the step-4 browser check |
| Pane loads but every action errors | Can't reach the VM | Confirm VPN; re-run the step-1 `curl` |
| "Upload My Add-in" missing (web) | Tenant admin disabled user add-in installs | Needs Centralized Deployment (IT — Request 2) |
| Worked yesterday, cert now rejected | Someone ran `down -v` on the VM → CA regenerated | Operator re-extracts (Part A step 3), everyone re-imports |

Once IT provides a real cert + DNS (Request 1), steps 2 and 3 go away entirely — testers will just do step 5.
