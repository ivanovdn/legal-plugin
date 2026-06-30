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
  legal_research.py   Direct ChatOllama for doc-attached chats; ReAct agent for RAG research
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
- **`body.search` treats `[](){}<>?*` as wildcards** even with `matchWildcards:false` on Mac — won't match them literally. Fall back to the clean leading run before the first special char. **For bracketed _blanks_ like `[__]` (a label-less placeholder the clean run can't reach, and below the 12-char prefix filter), retry the search in _wildcard mode_ with the metacharacters escaped (`\[__\]`) — `escapeWordWildcards` + the `matchWildcards:true` retry in `searchFirst`/`replaceAll`.** (Generated drafts carry `[Source: doc_id]` heading tags — real contracts don't.)
- **Refuse `replace_all` on a label-less blank (`[__]`, `___`).** The same generic blank stands for different fields (signatory name, title, entity), so one `new_text` can't fill them — `replace_all` would dump one value into all of them. `isAmbiguousBlankPlaceholder` in [clients/word/src/word.ts](clients/word/src/word.ts) rejects it with a clear message; the doc-chat prompt mirrors the rule (a label-less `[__]` is not a valid `replace_all` target — emit one `replace` per field).
- **Chat-driven inserts:** anchor on a single line (last line of anchor for "after"), use `insertParagraph` per clause line — don't `insertText` a multi-paragraph blob (raw `\n` renders literally; placement splits the section).
- **NEVER let `tsc` emit `.js` into `clients/word/src/`.** Vite resolves `.js` before `.tsx`, so a stray compiled file silently shadows the source and no cache-clear fixes it. tsconfig is `noEmit:true`; `.js`/`.tsbuildinfo` are gitignored.
- **Vite must bind `host:"127.0.0.1"`** — Word for Mac's webview reaches localhost over IPv4; `host:"localhost"` binds IPv6 `::1` only and the pane fails to load. The dev server also sends `Cache-Control: no-store` (the webview caches hard and HMR can't connect over the self-signed cert).
- **Track Changes restore:** wrap redline replacement in try/finally and restore `doc.changeTrackingMode` to the user's prior value.
- **Revision colors are NOT settable via Office.js.** The only revision controls are `changeTrackingMode` (Off/TrackAll/TrackMineOnly) and `markupMode` (Balloon/Inline/Mixed); every `colorIndex` property is for normal font/highlight/shading, not revisions. Tracked insertions + deletions render in Word's per-author revision color (both red by default — "By author"). To get green insertions / red deletions, the reviewer sets **Word → Preferences → Track Changes → Insertions: Green / Deletions: Red** (a per-machine display preference). Do NOT abandon native tracked changes to force colors via direct font formatting: a strikethrough-formatted "deletion" stays as live text in the doc (only real tracked deletions are physically removed on Accept — a legal hazard), and you lose native Accept/Reject. Coloring is a Word setting, not add-in code.
- **Extract document text via `body.getReviewedText("current")`, NOT `body.text`.** `body.text` includes tracked-change *deletions* (struck text), so a placeholder filled as a tracked change (`[__]`→`Suzy Quatro`) still carries the old `[__]` in the extracted string — contract review / chat then flag the already-filled field as an unfilled placeholder (the model reads exactly what we send it; a model-neutral *extraction* bug, not a prompt bug). `getReviewedText("current")` returns the text as if all changes were accepted (insertions kept, deletions dropped); WordApi 1.4, cross-OS (Mac/Windows/web); with no tracked changes it equals `body.text`, so it's a safe drop-in. See `readBody` in [clients/word/src/word.ts](clients/word/src/word.ts). Proven by trace `5f188799` (a redlined NDA produced false "unfilled placeholder" signature blockers for fields that had been filled via redline). NOTE: `body.search` still matches the RAW doc (incl. deletions) — this only changes the text we send to the LLM.
- **Finalize / "clean copy" = `body.getTrackedChanges().acceptAll()` + `changeTrackingMode = off`, IN PLACE.** Office.js can't reliably save-as a NEW named file cross-OS (`getFileAsync`→download is fragile in Word for Mac's WKWebView), so finalize mutates the current doc and the user does Word's File → Save As to name the deliverable. It accepts EVERY tracked change, not just the assistant's — add-in edits are attributed to the current Word user, the same as manual edits, so they can't be told apart by author. Gate behind an explicit **in-pane** confirm (`window.confirm` is unreliable in the Mac webview). See `finalizeDocument` in [clients/word/src/word.ts](clients/word/src/word.ts) + `FinalizeBar` in [clients/word/src/components/FinalizeBar.tsx](clients/word/src/components/FinalizeBar.tsx).
- **Tab state persistence:** keep both tabs mounted and toggle `display: none` — conditional rendering wipes chat history on tab switch. See [clients/word/src/App.tsx](clients/word/src/App.tsx).
- **Chat tab grounding:** [skills/legal_research.py](skills/legal_research.py) reads `uploaded_docs` from state and embeds the doc text + a conversational response-style directive into the user message. ChatTab posts with `task_type: "research"` to skip the intent router.
- **`body.search` re-finds Track-Changes deletions.** A second `body.search` inside the same `Word.run` after `range.insertText('replace')` keeps finding the deletion-marked original at the same position — looping with scope-advancement still failed. For multi-location replace: call `body.search` ONCE upfront, snapshot the `Range[]`, then iterate and replace each. Office.js ranges remain valid after sibling modifications. See `replaceAll` in [clients/word/src/word.ts](clients/word/src/word.ts).
- **`body.search` ignores raw `\t` characters in tables.** Two-column signature blocks where Word renders text as `Signed by: [__]\tSigned by: Boris` won't match a literal-tab needle — fall back to shorter anchors or use a `replace_all` block with the un-tabbed placeholder string.
- **Refuse to apply when the matched range is < 85% of the intended target.** `searchCandidates` falls back to progressively shorter prefixes; without the safety net, `acceptRedline` would inject the full long `new_text` into a short prefix match (silently-wrong track change). Threshold check is in [clients/word/src/word.ts](clients/word/src/word.ts).
- **Multi-line replace targets need single-line collapse.** When `target_text` and `new_text` both have `\n` but differ on exactly one line, `simplifyMultilineReplace` reduces to a single-line replace — `body.search` can't span paragraph breaks, and head+tail expansion absorbs intervening text (eats section numbers, etc.).
- **Frontend fallback uses `length > 0`, not `??`.** `??` only falls back on null/undefined — an empty `proposed_edits` array short-circuits to the empty backend value. Prefer non-empty: `backendEdits.length > 0 ? backendEdits : frontendBlocks`.

### Backend

- **LangGraph 0.6 interrupt detection** uses the `__interrupt__` key on the result (not `state.next`). See [api/routes/query.py](api/routes/query.py).
- **`chat_history` reducer** in [graph/state.py](graph/state.py) caps at `2 * chat_history_n_turns` and is idempotent on no-op nodes — so passing `messages` through every node is safe.
- **RedisSaver checkpointer** refreshes TTL on every interaction — sessions survive resume.
- **CORS is `allow_origins=["*"]`** in [api/main.py](api/main.py) — both Chainlit and the Word add-in call same backend.
- **Doc-attached chat skips the ReAct agent.** `legal_research._run_doc_chat` uses a direct `ChatOllama` call (`reasoning=False`) — the ReAct path with `search_legal`/`get_document` tools made each turn multi-minute on the local LLM with no gain when the doc is already in context. ReAct stays for the no-uploaded-docs research path.
- **SOW reviews auto-attach the governing MSA.** When `contract_review` detects `contract_type == "sow"` (with uploaded text), it calls `skills/grounding.py::attach_parent_msa(text, client_id, max_chars)` (which scrolls Qdrant via `rag/related_docs.py::get_parent_msa` for `doc_type="msa"` + `client_id` and truncates at `config.msa_max_chars`, truncation-marked), appends a `--- GOVERNING MSA (title) ---` block to the user message and adds `_MSA_COMPARISON_DIRECTIVE` as the **last** system message. (Type detection, bundle load, and MSA attach now live in the shared `skills/grounding.py`, used by both `contract_review` and the chat path.) Strictly additive: non-SOW, no-MSA-on-file, or any lookup error → standalone review (wrapped in try/except — retrieval must never break the review). The directive is **structural/model-neutral** — it orchestrates the comparison and defers all legal judgment to the playbook (mirrors `sow/SKILL.md:59` + MSA-001 precedence rules; forbids inventing MSA terms), so **SKILL.md is still the ceiling** (same category as the existing `_OUTPUT_CONSTRAINTS`). This *enables* a check the SOW playbook already mandates (`sow/SKILL.md:6` "conflicts with MSA"; `:28` names the MSA as required input) — it does not add a new legal rule. Demo prep: `scripts/ingest_demo_msa.py` ingests `data/Trinetix Model MSA 2025 (3)-1.docx` as `doc_type="msa"` (clears prior MSA chunks first since parsers assign random-UUID doc_ids; run it once, then restart the backend).
- **`uvicorn` does NOT auto-reload Python changes by default.** Edits to `skills/`, `graph/`, `api/` require restarting `bash scripts/start.sh`. Vite HMR catches frontend, FastAPI does not.
- **Chat path is now grounded + remembers reviews:** `skills/grounding.py` (shared with `contract_review`) attaches the playbook bundle + governing MSA; `memory/review_store.py` (SQLite) persists the markdown review keyed to `memory/document_id.py::resolve_document_id` (preamble hash — interim; Office.js custom-document-property id is the durable upgrade); `legal_research._run_doc_chat` injects the latest review and assembles stable-grounding-first / question-last for Ollama prefix-cache reuse. Restart `bash scripts/start.sh` after changing any of these files.
- **Degraded memory is loud:** checkpointer-absent → `memory_degraded` in the `/api/query` payload + a Word banner; a failed review write surfaces `report['review_persist_error']` (never silent).
- **Chat context is capped** (`config.chat_context_max_chars`, default 120 000 chars) by truncating the **document**, never the grounding (playbook / MSA / findings).

### Chat edit-block parsing

- **A fenced ` ```json ``` ` block can contain a single edit OR an array.** Local LLMs often consolidate multi-location requests into `[{...}, {...}]` inside one block. Both `_extract_proposed_edits` and `extractEditBlocks` accept either shape.
- **JSON strings can have raw `\n`/`\t` mid-value.** When the LLM line-wraps a long string value, `json.loads`/`JSON.parse` throws. `_tolerant_json_loads` / `tolerantParse` walk the text, track in-string state, and escape unescaped whitespace before retrying.
- **Edit-promise detector covers past tense too.** "I have replaced…" needs `\w{0,3}\b` suffix-tolerant verb stems (`replac`, `insert`, `delet`, …). Strict `\breplace\b` silently misses past-tense forms.
- **Ollama `format='json'` mode for the retry.** When the conversational LLM emits prose without a JSON block, retry with `ChatOllama(format='json')` — structurally forces valid JSON output. See `_build_json_llm` in [skills/legal_research.py](skills/legal_research.py).
- **Use `replace_all` for "every X" requests.** The model doesn't have to enumerate positions; the client snapshots all matches via `body.search` upfront. New `replace_all` action lives alongside `replace` / `insert` / `delete` — see `_VALID_ACTIONS` in [skills/legal_research.py](skills/legal_research.py) and `applyEdit` in [clients/word/src/word.ts](clients/word/src/word.ts).
- **A fenced block can also hold STACKED top-level objects** (`{...}\n{...}`), not just a single object or an array — the local LLM uses all three. Parse each via `_iter_json_values` (Python `raw_decode` loop) / `iterJsonValues` (JS brace-depth scan), then flatten. If you only accept single-object/array, a stacked block is dropped → `_extract_proposed_edits` returns `[]` → the **lossy JSON-retry** fires and emits degraded edits (e.g. a destructive `replace_all "[__]"`). Proven by traces `cea50c6b` / `f15f8a9b`.
- **Never tell the model "use `replace_all` for all/every requests with the shortest placeholder."** That phrasing makes it collapse a multi-field fill ("fill all blank signature blocks with name X, title Y") into one `replace_all "[__]"` — one value dumped into every field. `replace_all` = the SAME `new_text` for EVERY match; different-value fills MUST be one `replace` per field. The fix was correcting this guidance (a prompt _correctness_ bug, model-neutral), NOT adding a signature worked-example (which would skew the multi-LLM eval). See `CHAT_SYSTEM_PROMPT` / `_JSON_RETRY_SYSTEM` in [skills/legal_research.py](skills/legal_research.py).
- **Don't put a worked example with a fixed JSON shape in the prompt to "teach" a scenario.** The local LLM copies it verbatim — including a `\t`-joined two-column target (unmatchable) or stacked objects (unparseable before the parser fix). The model was emitting _correct_ per-field edits; the failures were the parser + the unmatchable `[__]`. Fix matching/parsing in code; keep the prompt principle-based.
- **The chat prompt needs an explicit SCOPE rule — change only what's asked.** Without it the local LLM over-reaches: primed by `chat_history` (a prior "fill signatures with John Doe" turn), it volunteered an unrequested edit overwriting the already-filled counterparty block (Boris Bukengolts → John Doe) "to ensure consistency" — and *said* it was going beyond the request (trace `4b24ca1d`). No clean code guard exists (code can't tell a wanted "change Boris to X" from this; and "new_text already in doc → drop" would wrongly block the legit multi-block fill). Fix is a **model-neutral SCOPE rule** in `CHAT_SYSTEM_PROMPT` + `_JSON_RETRY_SYSTEM` ([skills/legal_research.py](skills/legal_research.py)): do only what's asked, don't mirror a prior-turn value "for consistency", and never overwrite a field that already holds a real value — "fill" means an EMPTY placeholder. This is the allowed kind of prompt change per [[feedback-fix-in-code-not-prompt]] (universal behavior spec, not model-specific coaching). NOTE: `chat_history` is *needed* here — it's how "Legal name the same we filled recently" resolves to the earlier value — so it can't just be scoped out.
- **Multi-line signature/field blocks must be split into per-line edits.** The LLM collapses a whole signature block into ONE `replace` whose target is several `Label: value` lines (`Signed by: …\nTitle: …\nfor and on behalf of …`). That target is unapplyable — `body.search` can't cross paragraph breaks, so only the first line matches and the 85% completeness guard rejects it ("Couldn't find the exact target text"); it would also only ever touch the FIRST block. NDA worked only because it has one block. Fix: `splitMultilineFieldEdits` in [parseEditBlocks.ts](clients/word/src/parseEditBlocks.ts) (called via `normalizeProposals`) splits a multi-line `replace` whose changed lines are all **structured fields** (`isFieldLine` = has a colon OR a blank) into one edit per changed line — a labeled blank (`Signed by: [__]`) → `replace_all` (blanks recur across blocks; `replaceAll` snapshots all matches in one pass, no struck-text re-find), a specific value (`Signed by: Boris Bukengolts` → `Suzy Quatro`) → `replace` (one occurrence). Then `collapseDuplicateFills` folds the LLM's duplicate per-block cards into one fill-every edit. Multi-paragraph **prose** (no per-line colon/blank) is left as a single multi-line replace for the head+tail span matcher — splitting it could mis-locate a generic line. Traces: `02e41ead`/`ce45b899` (blank fills, MSA/SOW), `32deb028` (filled-block rewrite Boris→Suzy). Principle-based, model-neutral — no prompt change.
- **Reduce tab-bundled targets to the changed column.** The LLM sometimes prepends a two-column neighbour to a field with a `\t` — e.g. the dotted signature line: `…………\tSigned by: [__]` (trace `9e5b804c`). `body.search` can't reach across a tab, so the bundled target fails ("Couldn't find …"). `reduceTabSegment` (inside the split) keeps only the tab-separated segment that changed (`Signed by: [__]`), dropping the unchanged dotted-line / counterparty column. Mirrors the apply-time `simplifyMultilineReplace` one-line-differs logic, but for tab segments within a line.
- **Apply edit normalization at the point of use, NOT only in the parser.** [ChatTab.tsx](clients/word/src/components/ChatTab.tsx) prefers the BACKEND's `proposed_edits` over the frontend's `extractEditBlocks` blocks when non-empty (`backendEdits.length > 0 ? backendEdits : blocks`). So a transform buried inside `extractEditBlocks` is bypassed whenever the backend emits edits — which is the common case. `normalizeProposals` must run on the FINAL chosen list in ChatTab; it's idempotent, so re-running on the already-normalized frontend path is harmless. (First attempt at the multi-line-fill fix lived only in `extractEditBlocks` and had zero effect for exactly this reason.)

### Playbook bundle (skills/contract_review/)

- **Don't hand-edit `skills/contract_review/playbook/`.** It's generated by [scripts/build_playbook.py](scripts/build_playbook.py) from `data/contract_review_skills/`. Edit the source, re-run the script.
- **`data/contract_review_skills/` is gitignored — only the generated `playbook/` bundle is tracked.** So engineering changes to the assembled prompt live in the tracked build script, NOT the source markdown (which won't persist / isn't reproducible). Established seams: Fix #2's `AI_PROC_DROP_FROM` (drop §10.2–10.4 schemas), Layer 1's `LAYER1_PLACEHOLDER_CUE` (toggle `PLAYBOOK_PLACEHOLDER_CUE=0` to rebuild the pre-cue "first variant" for A/B). **When you drop a bundle section, preserve its *signal*:** Fix #2 removed §10.2/§10.4 (competing output schemas) and inadvertently killed the only "Open placeholders" / "Current wording" cues → unfilled signature blocks stopped surfacing as structured blockers (proven by a temp-0 A/B: cue absent → prose-only, cue present → structured Missing-Context row). Layer 1 re-injects those cues as *instructions* (not the schema) into the No-Signature gate's Automatic blockers + `output_format.md`.
- **Canonical sources by concern:** see [docs/playbook_cross_reference.md](docs/playbook_cross_reference.md). Playbook .docx owns risk rating, approval matrix, and per-type clause matrices; `references/` owns role + golden rules + output format + No-Signature Gate; per-type `SKILL.md` owns source position + clause review rules.
- **`load_bundle()` concatenates files in `BUNDLE_ORDER`** ([skills/base.py](skills/base.py)) — role first, No-Signature Gate last (it's the most-recent instruction the model reads before producing output).
- **Contract type detection is a heading-keyword heuristic** ([skills/contract_review/contract_review.py](skills/contract_review/contract_review.py)). Defaults to NDA on ambiguous text and logs a warning. Surfaced via `state["contract_type_detected"]` → `report.contract_type_detected`.
- **Output is markdown tables, not the old `CLAUSE:/RISK:` lines.** Sections required: `# Review Summary`, `# Key Findings`, `# Red and Missing Context Items`, `# Approved Deviations`, `# Suggested Redlines / Fallbacks`, `# Business Questions`, `# No Signature Checklist Result`. Word add-in parser at [clients/word/src/parser.ts](clients/word/src/parser.ts) is the consumer.
- **Word add-in extracts "current text" from quoted substrings inside the "Issue" cell.** The team's required output format has no current-text column; an explicit "quote the current/offending wording" instruction in `output_format.md` (the Layer 1 "Completeness and current wording" cue) makes the LLM emit those quotes. (This replaced playbook §10.2, which Fix #2 dropped — don't expect §10.2 in the prompt.)
- **Blockers card is derived from Key Findings, not the raw "Red and Missing Context" table.** Per spec the blockers table is a strict subset of Key Findings where rating ∈ {Red, Missing Context}. The LLM is often non-conformant (puts Yellow rows in the blocker table, or omits a Missing Context entry). `deriveBlockers` in [clients/word/src/parser.ts](clients/word/src/parser.ts) rebuilds the list from Key Findings and enriches each row with "why it blocks" + "approver" from the raw table by Issue ID then clause name. Source of truth wins; counts always reconcile.

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
