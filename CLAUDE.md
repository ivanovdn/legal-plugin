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
  contract_review/    markdown-output skill (SKILL.md is source of truth)
  legal_research.py   ReAct agent — handles uploaded_docs + chat_history
clients/
  web/                Chainlit frontend
  word/               Office.js task-pane add-in (React + Vite + TS)
rag/                Qdrant search tools (search_legal, get_document, escalate)
docs/wiki.md        Architecture + shipped/follow-up log — keep current
scripts/start.sh    Boots backend + Chainlit (Word add-in is `npm run dev` in clients/word/)
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
- **Track Changes restore:** wrap redline replacement in try/finally and restore `doc.changeTrackingMode` to the user's prior value.
- **Tab state persistence:** keep both tabs mounted and toggle `display: none` — conditional rendering wipes chat history on tab switch. See [clients/word/src/App.tsx](clients/word/src/App.tsx).
- **Chat tab grounding:** [skills/legal_research.py](skills/legal_research.py) reads `uploaded_docs` from state and embeds the doc text + a conversational response-style directive into the user message. ChatTab posts with `task_type: "research"` to skip the intent router.

### Backend

- **LangGraph 0.6 interrupt detection** uses the `__interrupt__` key on the result (not `state.next`). See [api/routes/query.py](api/routes/query.py).
- **`chat_history` reducer** in [graph/state.py](graph/state.py) caps at `2 * chat_history_n_turns` and is idempotent on no-op nodes — so passing `messages` through every node is safe.
- **RedisSaver checkpointer** refreshes TTL on every interaction — sessions survive resume.
- **CORS is `allow_origins=["*"]`** in [api/main.py](api/main.py) — both Chainlit and the Word add-in call same backend.

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
