# CLAUDE.md

Project guidance for AI assistants. Read this before making changes.

## Stack

- **Python 3.12** (not 3.13/3.14 — stability)
- **uv** for venv + deps (`uv venv`, `uv pip install -r requirements.txt`)
- LangGraph + LangChain + ChatOllama (local LLM)
- FastAPI backend on `:8000`, Chainlit web client on `:8080`, Word add-in Vite dev server on `:3001`
- Redis (checkpointer), Qdrant (RAG), Postgres + Langfuse (traces) — all via `docker compose`

## Repo layout

```
api/                FastAPI routes — POST /api/query is the single entry
graph/              LangGraph state, nodes, checkpointer
  state.py            LegalAgentState (TypedDict) — chat_history reducer caps at 2*N turns
  nodes/              intent_router, attorney_review, etc.
skills/             Domain skills invoked by the graph
  base.py             load_skill_prompt + load_bundle (per-type playbook assembly)
  contract_review/    Per-type playbook bundle (auto-detects NDA/MSA/SOW/BAA)
    playbook/         GENERATED — do not hand-edit (run scripts/build_playbook.py)
  legal_research.py   ReAct agent — handles uploaded_docs + chat_history
data/contract_review_skills/   CANONICAL legal-team source — do not hand-edit
  Trinetix_Contract_Playbook_2026.docx + per-type SKILL.md + shared references
clients/
  web/                Chainlit frontend
  word/               Office.js task-pane add-in (React + Vite + TS)
rag/                Qdrant search tools (search_legal, get_document, escalate)
docs/wiki.md        Architecture + shipped/follow-up log — keep current
docs/playbook_cross_reference.md   Crosswalk: Playbook × SKILL.md × References
scripts/start.sh    Boots backend + Chainlit (Word add-in is `npm run dev` in clients/word/)
scripts/build_playbook.py   .docx + team skill folders → playbook/ markdown (idempotent)
```

## Hard rules (from memory + past incidents)

1. **All imports at top of file.** No lazy imports inside functions.
2. **SKILL.md is the ceiling.** Skill prompts must follow the playbook verbatim — never improvise from pretraining.
3. **Always filter by `client_id`** in RAG tools. Never cross-tenant.
4. **Cite with `doc_id` and `doc_title`** in research output — the parser greps for `doc_id:` patterns.
5. **Don't add backwards-compat shims.** Change call sites instead.

## Solved problems — don't re-discover these

### Word add-in (clients/word/)

- **Vite proxy must use regex `^/api/` with trailing slash.** Pattern `/api` matches `/api.ts` (the source module) and breaks the dev server. See [vite.config.ts:18](clients/word/vite.config.ts#L18).
- **HTTPS is mandatory** even on localhost — Office.js refuses HTTP. Cert is provisioned by `office-addin-dev-certs` into the Mac keychain.
- **Sideload path on Word for Mac:** copy manifest into `~/Library/Containers/com.microsoft.Word/Data/Documents/wef/`. The "Upload My Add-in" UI button is gated and often missing.
- **`body.search()` limits:** 255-char max, cannot cross paragraph breaks. For multi-paragraph clauses use head + tail snippets with progressive shortening, then `range.expandTo()`. See `findClauseRange` in [clients/word/src/word.ts](clients/word/src/word.ts).
- **Always normalize before search:** NFC, curly→straight quotes, nbsp→space. See [clients/word/src/normalize.ts](clients/word/src/normalize.ts).
- **`body.search` matches the RAW doc, not normalized text.** A full quote can silently miss even when the text is present (soft breaks, char differences). Fix: try progressively shorter word-aligned prefixes + a tail, then span by **match boundaries** (head-match start → tail-match end) — never whole paragraphs, or fragment rewrites over-replace. See `findClauseRange`/`searchCandidates` in [clients/word/src/word.ts](clients/word/src/word.ts).
- **`body.search` treats `[](){}<>?*` as wildcards** even with `matchWildcards:false` on Mac — won't match them literally. Fall back to the clean leading run before the first special char. (Generated drafts carry `[Source: doc_id]` heading tags — real contracts don't.)
- **Chat-driven inserts:** anchor on a single line (last line of anchor for "after"), use `insertParagraph` per clause line — don't `insertText` a multi-paragraph blob (raw `\n` renders literally; placement splits the section).
- **NEVER let `tsc` emit `.js` into `clients/word/src/`.** Vite resolves `.js` before `.tsx`, so a stray compiled file silently shadows the source and no cache-clear fixes it. tsconfig is `noEmit:true`; `.js`/`.tsbuildinfo` are gitignored.
- **Vite must bind `host:"127.0.0.1"`** — Word for Mac's webview reaches localhost over IPv4; `host:"localhost"` binds IPv6 `::1` only and the pane fails to load. The dev server also sends `Cache-Control: no-store` (the webview caches hard and HMR can't connect over the self-signed cert).
- **Track Changes restore:** wrap redline replacement in try/finally and restore `doc.changeTrackingMode` to the user's prior value.
- **Tab state persistence:** keep both tabs mounted and toggle `display: none` — conditional rendering wipes chat history on tab switch. See [clients/word/src/App.tsx](clients/word/src/App.tsx).
- **Chat tab grounding:** [skills/legal_research.py](skills/legal_research.py) reads `uploaded_docs` from state and embeds the doc text + a conversational response-style directive into the user message. ChatTab posts with `task_type: "research"` to skip the intent router.

### Backend

- **LangGraph 0.6 interrupt detection** uses the `__interrupt__` key on the result (not `state.next`). See [api/routes/query.py](api/routes/query.py).
- **`chat_history` reducer** in [graph/state.py](graph/state.py) caps at `2 * chat_history_n_turns` and is idempotent on no-op nodes — so passing `messages` through every node is safe.
- **RedisSaver checkpointer** refreshes TTL on every interaction — sessions survive resume.
- **CORS is `allow_origins=["*"]`** in [api/main.py](api/main.py) — both Chainlit and the Word add-in call same backend.

### Playbook bundle (skills/contract_review/)

- **Don't hand-edit `skills/contract_review/playbook/`.** It's generated by [scripts/build_playbook.py](scripts/build_playbook.py) from `data/contract_review_skills/`. Edit the source, re-run the script.
- **Canonical sources by concern:** see [docs/playbook_cross_reference.md](docs/playbook_cross_reference.md). Playbook .docx owns risk rating, approval matrix, and per-type clause matrices; `references/` owns role + golden rules + output format + No-Signature Gate; per-type `SKILL.md` owns source position + clause review rules.
- **`load_bundle()` concatenates files in `BUNDLE_ORDER`** ([skills/base.py](skills/base.py)) — role first, No-Signature Gate last (it's the most-recent instruction the model reads before producing output).
- **Contract type detection is a heading-keyword heuristic** ([skills/contract_review/contract_review.py](skills/contract_review/contract_review.py)). Defaults to NDA on ambiguous text and logs a warning. Surfaced via `state["contract_type_detected"]` → `report.contract_type_detected`.
- **Output is markdown tables, not the old `CLAUSE:/RISK:` lines.** Sections required: `# Review Summary`, `# Key Findings`, `# Red and Missing Context Items`, `# Approved Deviations`, `# Suggested Redlines / Fallbacks`, `# Business Questions`, `# No Signature Checklist Result`. Word add-in parser at [clients/word/src/parser.ts](clients/word/src/parser.ts) is the consumer.
- **Word add-in extracts "current text" from quoted substrings inside the "Issue" cell.** The team's required output format has no current-text column; the LLM emits quotes naturally per AI Review Procedure §10.2.

## Common commands

```bash
# Backend + Chainlit
bash scripts/start.sh

# Word add-in (separate terminal)
cd clients/word && npm run dev    # https://localhost:3001/taskpane.html

# Tests
uv run pytest tests/ -v

# One-off graph debug
uv run python -m scripts.debug_query "..."
```

## When making changes

- **Editing a skill's behavior?** Update its `SKILL.md` first, then the Python wrapper. The playbook is the contract.
- **Adding a graph node?** Update `LegalAgentState` if new state fields are needed and check the `chat_history` reducer still makes sense.
- **Touching the Word add-in?** Smoke-test by sideloading in Word for Mac — `npx tsc --noEmit` is not enough.
- **Shipping a feature?** Update [docs/wiki.md](docs/wiki.md) "Shipped Since Last Update" + refresh follow-ups list.

## Out of scope (deferred — see wiki follow-ups)

- Structured JSON output for `contract_review` (would unlock Phase 2 playbook citations in Word)
- Generate-clause tab in Word add-in (Phase 6)
- AppSource publishing — sideload only
- WebSocket/SSE streaming responses
- Real auth — `X-User-ID: anonymous` header for now
