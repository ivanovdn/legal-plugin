# Word Add-in — Legal Triage

A Microsoft Word task-pane add-in that triages the open contract using the legal-plugin backend.

MVP scope: read the document body, send it to `POST /api/query` with `task_type=contract_review`, render the per-clause findings with severity badges in the pane.

## Prerequisites

- macOS with Word for Mac
- Node 18+ (`node --version`)
- The legal-plugin backend running on `http://localhost:8000` (use `bash scripts/start.sh` from the repo root)

## First-time setup

```bash
cd clients/word
npm install
npx office-addin-dev-certs install   # one-time: trust the dev cert in the keychain
```

## Run the add-in

```bash
cd clients/word
npm run dev                          # Vite dev server on https://localhost:3001
```

In a separate shell, make sure the backend is up:

```bash
bash scripts/start.sh                # from the repo root
```

## Sideload into Word for Mac

Word for Mac does **not** have an "Upload My Add-in" button (that's Windows-only). Instead, sideloading on Mac uses the `wef` shared folder:

```bash
mkdir -p ~/Library/Containers/com.microsoft.Word/Data/Documents/wef
cp clients/word/manifest.xml ~/Library/Containers/com.microsoft.Word/Data/Documents/wef/legal-triage.manifest.xml
```

Then:

1. **Quit Word** completely (`Cmd+Q`) and reopen it
2. Open or create a document
3. On the **Home** tab (or **Insert** tab) click the **Add-ins** button (puzzle-piece icon)
4. In the dialog, click **My Add-ins** → look for **SHARED FOLDER** at the top
5. **Legal Triage** appears in the list — click it to insert
6. The task pane opens on the right

After the first insert, click the **Review** button in the Home tab's Legal Triage group to reopen the pane in any document.

To remove the add-in, delete the manifest from `~/Library/Containers/com.microsoft.Word/Data/Documents/wef/` and restart Word.

## Smoke test

1. Open or paste the body of a sample contract into Word (e.g., `data/demo_contracts/svc_nordex_2023.docx`)
2. Click **Review this contract** in the task pane
3. Wait ~30–60 s while the backend runs `contract_review`
4. Findings render as cards sorted RED → YELLOW → GREEN, with a summary row showing counts and overall risk

## Architecture

```
Word ── webview ──▶ https://localhost:3001/taskpane.html   (Vite dev server)
                              │
                              │ fetch /api/query  (Vite proxy → http://localhost:8000)
                              ▼
                    FastAPI backend ──▶ contract_review skill ──▶ RAG + LLM
```

The Vite dev server proxies `/api/*` to the backend on `:8000` so the task pane (HTTPS) never makes a mixed-content request to the HTTP backend.

## Project layout

```
clients/word/
├── manifest.xml              # Office add-in manifest (sideload this)
├── package.json
├── tsconfig.json
├── vite.config.ts            # HTTPS via office-addin-dev-certs + /api proxy
├── assets/                   # Icons referenced by manifest (served as static)
└── src/
    ├── taskpane.html         # HTML shell, loads office.js + main.tsx
    ├── main.tsx              # Office.onReady → React mount
    ├── App.tsx               # Review button + status + findings rendering
    ├── api.ts                # submitReview() — POST /api/query
    ├── parser.ts             # contract_review markdown → Finding[]
    ├── styles.css
    └── components/
        ├── RiskBadge.tsx
        └── FindingCard.tsx
```

## Troubleshooting

- **Pane fails to load (blank task pane):** Open Word → View → Developer → Open Add-in Developer Tools (right-click on the pane). Check the console for cert / fetch errors.
- **Cert errors in browser preview:** Visit `https://localhost:3001/taskpane.html` directly and accept the cert once.
- **"Backend returned 500":** Check `/tmp/uvicorn.log` for the stacktrace. The most common cause is Ollama not loaded — verify with `curl http://localhost:11434/api/ps`.
- **No findings parsed:** The contract_review skill may have changed output format. Compare against `skills/contract_review/SKILL.md` and update `src/parser.ts` regexes.

## Follow-ups

See the `docs/wiki.md` Follow-ups section for the planned phases beyond MVP (playbook citations, inline comments, tracked-changes redlines, Q&A tab, generate-clause tab).
