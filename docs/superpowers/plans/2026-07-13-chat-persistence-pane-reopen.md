# Chat Persistence Across Pane Reopen — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the Word task pane is closed and reopened on the same document, restore the chat conversation (visible bubbles + the backend `sessionId` thread) from `localStorage`.

**Architecture:** Two new isolated frontend modules — `docIdentity.ts` (a TS port of the backend's `resolve_document_id`, producing a stable per-document key) and `chatSessionStore.ts` (a fail-safe versioned `localStorage` wrapper) — wired into `App.tsx` with a mount-time hydration effect and a save-on-change effect, plus a "Clear conversation" control in `ChatTab.tsx`. No backend, graph, prompt, parser, or LLM change.

**Tech Stack:** React 18 + TypeScript + Vite + Office.js. Web Crypto (`crypto.subtle`) for hashing. Standalone `.ts` unit tests run via `npx tsx`. Spec: `docs/superpowers/specs/2026-07-13-chat-persistence-pane-reopen-design.md`.

## Global Constraints

- **Frontend-only.** No change to `api/`, `graph/`, `skills/`, prompts, or the LLM. Review and chat *outputs* stay byte-identical — this only adds client-side persistence.
- **All imports at top of file** (repo hard rule #1). No lazy imports inside functions.
- **Never let `tsc` emit `.js`/`.tsbuildinfo` into `clients/word/src/`** (tsconfig is `noEmit:true`; both are gitignored). Typecheck is `npm run typecheck` (= `tsc --noEmit`), run from `clients/word/`.
- **Test convention:** standalone `.ts` file, first line `// Run with: npx tsx src/<name>.test.ts`, helper `const pass = (cond: boolean, label: string) => console.log(cond ? \`PASS: ${label}\` : \`FAIL: ${label}\`);`. **Async tests MUST be wrapped in `(async () => { ... })();`** — top-level `await` fails under tsx's transform. All work is done from `clients/word/`.
- **localStorage keys:** per-doc record `legal-triage:chat:<docId>`; index `legal-triage:chat-index`. `SCHEMA_VERSION = 1`, `MAX_DOCS = 20`.
- **Fail-safe storage:** any `localStorage` unavailability / quota throw is a **silent no-op** (this is an enhancement, not the loud `memory_degraded` Redis path). No banner, no crash.
- **Empty `docId` (`""`) ⇒ no persist and no restore**, everywhere. `resolveDocumentId` returns `""` for empty/whitespace input (mirrors the Python).
- **`rawResponse` is stripped before persisting** (debug-only "show raw" payload; would bloat the quota).
- **Hydrate once, at mount, only before the pane's first turn.** Persist only after hydration has run AND `docId` is non-empty AND there is ≥1 message.
- **Golden hashes (verified against `memory/document_id.py::resolve_document_id`):**
  - `"MUTUAL NON-DISCLOSURE AGREEMENT\n\nThis Agreement is entered into by Trinetix LLC and Acme Corp."` → `6855a86de3a484a0a75481fba1dfc2745775502a9d8b807836a9763e274125a2`
  - `"MASTER SERVICES AGREEMENT\n\nBetween Trinetix and Client Co.\n\n1. Definitions\nThe following terms apply."` → `efff2d5d48d34d4f7eb881a1bdd85a8c192793a4aa58d3765c2dda8ec2b1502e`
  - `""` and `"   \n\t  "` → `""`

---

### Task 1: `docIdentity.ts` — stable client-side document id

**Files:**
- Create: `clients/word/src/docIdentity.ts`
- Test: `clients/word/src/docIdentity.test.ts`

**Interfaces:**
- Consumes: nothing (pure module; uses global `crypto.subtle` + `TextEncoder`).
- Produces: `export async function resolveDocumentId(text: string): Promise<string>` — SHA-256 hex of the normalized document preamble; `""` for empty/whitespace input. Later tasks import this from `./docIdentity`.

- [ ] **Step 1: Write the failing test**

Create `clients/word/src/docIdentity.test.ts`:

```ts
// Run with: npx tsx src/docIdentity.test.ts
// Cross-language golden test: the expected hashes are produced by
// memory/document_id.py::resolve_document_id on the identical strings, so this
// guards against the TS port drifting from the backend's identity.
import { resolveDocumentId } from "./docIdentity";

const pass = (cond: boolean, label: string) =>
  console.log(cond ? `PASS: ${label}` : `FAIL: ${label}`);

(async () => {
  const simple =
    "MUTUAL NON-DISCLOSURE AGREEMENT\n\nThis Agreement is entered into by Trinetix LLC and Acme Corp.";
  const numbered =
    "MASTER SERVICES AGREEMENT\n\nBetween Trinetix and Client Co.\n\n1. Definitions\nThe following terms apply.";
  const truncated = "MASTER SERVICES AGREEMENT\n\nBetween Trinetix and Client Co.\n";

  pass(
    (await resolveDocumentId(simple)) ===
      "6855a86de3a484a0a75481fba1dfc2745775502a9d8b807836a9763e274125a2",
    "simple preamble matches Python golden hash",
  );
  pass(
    (await resolveDocumentId(numbered)) ===
      "efff2d5d48d34d4f7eb881a1bdd85a8c192793a4aa58d3765c2dda8ec2b1502e",
    "numbered-section preamble matches Python golden hash",
  );
  pass(
    (await resolveDocumentId(numbered)) === (await resolveDocumentId(truncated)),
    "preamble is cut at the first numbered section (\\n1.)",
  );
  pass((await resolveDocumentId("")) === "", "empty string → empty id");
  pass((await resolveDocumentId("   \n\t  ")) === "", "whitespace-only → empty id");
})();
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `clients/word/`): `npx tsx src/docIdentity.test.ts`
Expected: FAIL — `Cannot find module './docIdentity'` (the module doesn't exist yet).

- [ ] **Step 3: Write the implementation**

Create `clients/word/src/docIdentity.ts`:

```ts
// Stable client-side document id — a faithful TS port of memory/document_id.py.
// Keys the persisted chat conversation in chatSessionStore.ts. Because
// readBody() returns the exact uploaded_text the backend hashes, this yields the
// same id the backend's resolve_document_id produces for the same document.
//
// The durable upgrade (an Office.js custom-document-property UUID) is a swap of
// this one function — see the chat-persistence design spec.

const PREAMBLE_CHARS = 800;

/** SHA-256 hex of the normalized preamble. "" for empty/whitespace input. */
export async function resolveDocumentId(text: string): Promise<string> {
  if (!text || !text.trim()) return "";
  let region = text.slice(0, PREAMBLE_CHARS);
  const m = region.match(/\n\d+\./); // first numbered section ends the preamble
  if (m && m.index !== undefined) region = region.slice(0, m.index);
  region = region.normalize("NFC").toLowerCase().replace(/\s+/g, " ").trim();
  const bytes = new TextEncoder().encode(region);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run (from `clients/word/`): `npx tsx src/docIdentity.test.ts`
Expected: five `PASS:` lines, no `FAIL:`.

- [ ] **Step 5: Typecheck**

Run (from `clients/word/`): `npm run typecheck`
Expected: no output (exit 0). Confirm no `.js` file was emitted into `src/` (`git status` shows only the two new `.ts` files).

- [ ] **Step 6: Commit**

```bash
git add clients/word/src/docIdentity.ts clients/word/src/docIdentity.test.ts
git commit -m "feat(word): client-side document id (preamble hash) for chat persistence"
```

---

### Task 2: `chatSessionStore.ts` — versioned localStorage wrapper

**Files:**
- Create: `clients/word/src/chatSessionStore.ts`
- Test: `clients/word/src/chatSessionStore.test.ts`

**Interfaces:**
- Consumes: `import type { ChatMessage } from "./components/ChatTab"` (type-only; erased at runtime, so importing it does not load React).
- Produces:
  - `export interface StoredSession { v: number; sessionId: string; messages: ChatMessage[]; updatedAt: string }`
  - `export function loadSession(docId: string): StoredSession | null`
  - `export function saveSession(docId: string, sessionId: string, messages: ChatMessage[]): void`
  - `export function clearSession(docId: string): void`

- [ ] **Step 1: Write the failing test**

Create `clients/word/src/chatSessionStore.test.ts`:

```ts
// Run with: npx tsx src/chatSessionStore.test.ts
import { loadSession, saveSession, clearSession } from "./chatSessionStore";
import type { ChatMessage } from "./components/ChatTab";

const pass = (cond: boolean, label: string) =>
  console.log(cond ? `PASS: ${label}` : `FAIL: ${label}`);

// Minimal in-memory localStorage shim. The store reads globalThis.localStorage
// lazily (at call time), so installing a fresh shim before each group works.
function makeShim(throwOnSet = false) {
  const map = new Map<string, string>();
  return {
    getItem: (k: string) => (map.has(k) ? map.get(k)! : null),
    setItem: (k: string, v: string) => {
      if (throwOnSet) throw new Error("QuotaExceededError");
      map.set(k, v);
    },
    removeItem: (k: string) => void map.delete(k),
    clear: () => map.clear(),
    key: (i: number) => Array.from(map.keys())[i] ?? null,
    get length() {
      return map.size;
    },
  } as unknown as Storage;
}

const install = (s: Storage) => {
  (globalThis as { localStorage?: Storage }).localStorage = s;
};

(async () => {
  const msgs: ChatMessage[] = [
    { role: "user", content: "hi" },
    { role: "assistant", content: "hello", rawResponse: "hello + a big raw blob", proposedEdits: [] },
  ];

  // round-trip
  install(makeShim());
  saveSession("docA", "sess-1", msgs);
  const rec = loadSession("docA");
  pass(rec !== null && rec.sessionId === "sess-1", "round-trip returns saved sessionId");
  pass(rec !== null && rec.messages.length === 2, "round-trip returns all messages");
  pass(rec !== null && rec.messages[1].rawResponse === undefined, "rawResponse stripped on save");

  // empty docId is a no-op
  saveSession("", "sess-x", msgs);
  pass(loadSession("") === null, "empty docId → null on load");

  // corrupt record discarded
  install(makeShim());
  (globalThis as { localStorage: Storage }).localStorage.setItem(
    "legal-triage:chat:docB",
    "{not json",
  );
  pass(loadSession("docB") === null, "corrupt JSON → null");

  // version mismatch discarded
  install(makeShim());
  (globalThis as { localStorage: Storage }).localStorage.setItem(
    "legal-triage:chat:docC",
    JSON.stringify({ v: 999, sessionId: "s", messages: [] }),
  );
  pass(loadSession("docC") === null, "version mismatch → null");

  // clearSession removes it
  install(makeShim());
  saveSession("docD", "sess-d", msgs);
  clearSession("docD");
  pass(loadSession("docD") === null, "clearSession removes the record");

  // LRU eviction past MAX_DOCS (20)
  install(makeShim());
  for (let i = 0; i < 25; i++) saveSession(`doc${i}`, `sess${i}`, msgs);
  pass(loadSession("doc0") === null, "oldest doc evicted past MAX_DOCS");
  pass(loadSession("doc24") !== null, "newest doc retained");

  // storage that throws on set → save is a safe no-op (never throws)
  install(makeShim(true));
  let threw = false;
  try {
    saveSession("docE", "sess-e", msgs);
  } catch {
    threw = true;
  }
  pass(!threw, "save with throwing storage does not throw");
})();
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `clients/word/`): `npx tsx src/chatSessionStore.test.ts`
Expected: FAIL — `Cannot find module './chatSessionStore'`.

- [ ] **Step 3: Write the implementation**

Create `clients/word/src/chatSessionStore.ts`:

```ts
// Versioned localStorage wrapper for persisted per-document chat conversations,
// keyed by the docId from docIdentity.ts. All access is fail-safe: a blocked or
// absent localStorage (or a thrown quota error) is a silent no-op, so the
// feature degrades to today's in-memory-only behavior rather than crashing.

import type { ChatMessage } from "./components/ChatTab";

const KEY_PREFIX = "legal-triage:chat:"; // per-doc record: KEY_PREFIX + docId
const INDEX_KEY = "legal-triage:chat-index"; // JSON string[] of docIds, most-recent first
const SCHEMA_VERSION = 1;
const MAX_DOCS = 20;

export interface StoredSession {
  v: number;
  sessionId: string;
  messages: ChatMessage[];
  updatedAt: string; // ISO 8601
}

function storage(): Storage | null {
  try {
    return (globalThis as { localStorage?: Storage }).localStorage ?? null;
  } catch {
    return null;
  }
}

function readIndex(s: Storage): string[] {
  try {
    const raw = s.getItem(INDEX_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

function writeIndex(s: Storage, ids: string[]): void {
  try {
    s.setItem(INDEX_KEY, JSON.stringify(ids));
  } catch {
    /* index is best-effort */
  }
}

// Move docId to the front; evict + delete keys past MAX_DOCS.
function touchIndex(s: Storage, docId: string): void {
  const ids = [docId, ...readIndex(s).filter((id) => id !== docId)];
  for (const id of ids.slice(MAX_DOCS)) {
    try {
      s.removeItem(KEY_PREFIX + id);
    } catch {
      /* ignore */
    }
  }
  writeIndex(s, ids.slice(0, MAX_DOCS));
}

export function loadSession(docId: string): StoredSession | null {
  if (!docId) return null;
  const s = storage();
  if (!s) return null;
  let rec: StoredSession;
  try {
    const raw = s.getItem(KEY_PREFIX + docId);
    if (!raw) return null;
    rec = JSON.parse(raw);
  } catch {
    return null;
  }
  if (
    !rec ||
    rec.v !== SCHEMA_VERSION ||
    typeof rec.sessionId !== "string" ||
    !Array.isArray(rec.messages)
  ) {
    try {
      s.removeItem(KEY_PREFIX + docId);
    } catch {
      /* ignore */
    }
    return null;
  }
  touchIndex(s, docId);
  return rec;
}

export function saveSession(docId: string, sessionId: string, messages: ChatMessage[]): void {
  if (!docId) return;
  const s = storage();
  if (!s) return;
  // Strip rawResponse — a debug-only "show raw" payload; persisting full LLM
  // outputs across many turns and docs would bloat the quota.
  const slim: ChatMessage[] = messages.map((m) => {
    const copy: ChatMessage = { ...m };
    delete copy.rawResponse;
    return copy;
  });
  const payload = JSON.stringify({
    v: SCHEMA_VERSION,
    sessionId,
    messages: slim,
    updatedAt: new Date().toISOString(),
  } satisfies StoredSession);
  try {
    s.setItem(KEY_PREFIX + docId, payload);
  } catch {
    // Quota — evict the oldest indexed doc and retry once.
    const ids = readIndex(s);
    const oldest = ids[ids.length - 1];
    if (!oldest || oldest === docId) return; // nothing else to evict → give up
    try {
      s.removeItem(KEY_PREFIX + oldest);
      writeIndex(s, ids.slice(0, -1));
      s.setItem(KEY_PREFIX + docId, payload);
    } catch {
      return; // still failing → give up silently
    }
  }
  touchIndex(s, docId);
}

export function clearSession(docId: string): void {
  if (!docId) return;
  const s = storage();
  if (!s) return;
  try {
    s.removeItem(KEY_PREFIX + docId);
  } catch {
    /* ignore */
  }
  writeIndex(
    s,
    readIndex(s).filter((id) => id !== docId),
  );
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run (from `clients/word/`): `npx tsx src/chatSessionStore.test.ts`
Expected: ten `PASS:` lines, no `FAIL:`.

- [ ] **Step 5: Typecheck**

Run (from `clients/word/`): `npm run typecheck`
Expected: no output (exit 0). No `.js` emitted into `src/`.

- [ ] **Step 6: Commit**

```bash
git add clients/word/src/chatSessionStore.ts clients/word/src/chatSessionStore.test.ts
git commit -m "feat(word): fail-safe versioned localStorage store for chat conversations"
```

---

### Task 3: Wire hydration + persistence into the pane

**Files:**
- Modify: `clients/word/src/App.tsx` (hydrate on mount, persist on change, `docId` state, settable `sessionId`, turn-start + clear callbacks)
- Modify: `clients/word/src/components/ChatTab.tsx` (accept `onTurnStart` + `onClear`; call `onTurnStart` on send; add a "Clear conversation" control)
- Modify: `clients/word/src/components/FindingsTab.tsx` (accept `onTurnStart`; call it when a review starts)
- Modify: `clients/word/src/styles.css` (a small `.chat-toolbar` rule)

**Interfaces:**
- Consumes: `resolveDocumentId` from `./docIdentity`; `loadSession`/`saveSession`/`clearSession` from `./chatSessionStore`; existing `readBody` from `./word`; existing `ChatMessage` type.
- Produces: `FindingsTab` prop `onTurnStart: () => void`; `ChatTab` props `onTurnStart: () => void` and `onClear: () => void`. The feature end-to-end.

> **No unit test for this task.** The change is React state + Office.js integration, and this repo has no React/DOM test harness (tests are pure-`.ts` only); adding jsdom/vitest is out of scope per the spec. The gate is `npm run typecheck` clean + the existing unit suite still green + the manual smoke checklist (the human gate, run in the finishing phase). This matches how `feat/clause-locator-hardening` and `feat/calm-notfound-styling` verified their React wiring.

- [ ] **Step 1: Rewrite `App.tsx`**

Replace the entire contents of `clients/word/src/App.tsx` with:

```tsx
import { useEffect, useRef, useState } from "react";
import Tabs, { type TabKey } from "./components/Tabs";
import FindingsTab from "./components/FindingsTab";
import ChatTab, { type ChatMessage } from "./components/ChatTab";
import FinalizeBar from "./components/FinalizeBar";
import type { ReviewSummary } from "./parser";
import { readBody } from "./word";
import { resolveDocumentId } from "./docIdentity";
import { loadSession, saveSession, clearSession } from "./chatSessionStore";

export default function App() {
  // session_id is generated once per pane lifetime so the contract_review turn
  // and any subsequent chat turns share chat_history on the backend. On pane
  // reopen the hydration effect below replaces it with the stored id so the
  // backend thread continues too (within its 24h Redis TTL).
  const [sessionId, setSessionId] = useState<string>(() =>
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `addin-${Date.now()}-${Math.random().toString(36).slice(2)}`,
  );
  const [tab, setTab] = useState<TabKey>("findings");
  // All persistent tab state is lifted here so toggling tabs doesn't reset it.
  const [findingsResult, setFindingsResult] = useState<ReviewSummary | null>(null);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);

  // Per-document identity for chat persistence (see docIdentity.ts).
  const [docId, setDocId] = useState<string>("");
  const hydratedRef = useRef(false); // persist only after hydration has run
  const turnStartedRef = useRef(false); // don't swap sessionId after a turn fired

  // Hydrate once on mount (Office is ready — App mounts inside Office.onReady):
  // resolve the doc id, and if a conversation was stored for it AND no turn has
  // fired yet, restore the sessionId + messages.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      let id = "";
      try {
        id = await resolveDocumentId(await readBody());
      } catch {
        id = "";
      }
      if (cancelled) return;
      setDocId(id);
      if (id && !turnStartedRef.current) {
        const rec = loadSession(id);
        if (rec) {
          setSessionId(rec.sessionId);
          setChatMessages(rec.messages);
        }
      }
      hydratedRef.current = true;
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Persist the conversation whenever it changes — but only after hydration has
  // run (so we never overwrite a stored record with the empty pre-hydration
  // state) and only for a real doc with at least one message.
  useEffect(() => {
    if (!hydratedRef.current || !docId || chatMessages.length === 0) return;
    saveSession(docId, sessionId, chatMessages);
  }, [docId, sessionId, chatMessages]);

  const markTurnStarted = () => {
    turnStartedRef.current = true;
  };

  const clearConversation = () => {
    setChatMessages([]);
    if (docId) clearSession(docId);
  };

  return (
    <div className="app">
      <header>
        <h1>Legal Triage</h1>
        <p className="subtitle">Reviews the open document against the firm's standards.</p>
      </header>
      <Tabs active={tab} onChange={setTab} />
      {/* Both tabs always mounted; visibility toggled via CSS so state persists. */}
      <div className={`tab-pane ${tab === "findings" ? "" : "hidden"}`}>
        <FindingsTab
          sessionId={sessionId}
          result={findingsResult}
          setResult={setFindingsResult}
          onTurnStart={markTurnStarted}
        />
      </div>
      <div className={`tab-pane ${tab === "chat" ? "" : "hidden"}`}>
        <ChatTab
          sessionId={sessionId}
          messages={chatMessages}
          setMessages={setChatMessages}
          onTurnStart={markTurnStarted}
          onClear={clearConversation}
        />
      </div>
      {/* Document-level action, available regardless of the active tab. */}
      <FinalizeBar />
    </div>
  );
}
```

- [ ] **Step 2: Add the `onTurnStart` prop to `FindingsTab.tsx`**

In `clients/word/src/components/FindingsTab.tsx`, extend the `Props` interface (currently ends at `setResult`):

```tsx
interface Props {
  sessionId: string;
  result: ReviewSummary | null;
  setResult: React.Dispatch<React.SetStateAction<ReviewSummary | null>>;
  onTurnStart: () => void;
}
```

Update the component signature:

```tsx
export default function FindingsTab({ sessionId, result, setResult, onTurnStart }: Props) {
```

And call `onTurnStart()` as the first line of `onReview` (a review is a "turn" that fixes the backend thread for this pane):

```tsx
  const onReview = async () => {
    onTurnStart();
    setPersistError(null);
    try {
```

- [ ] **Step 3: Add `onTurnStart` + `onClear` and the Clear control to `ChatTab.tsx`**

In `clients/word/src/components/ChatTab.tsx`, extend the `Props` interface:

```tsx
interface Props {
  sessionId: string;
  messages: ChatMessage[];
  setMessages: React.Dispatch<React.SetStateAction<ChatMessage[]>>;
  onTurnStart: () => void;
  onClear: () => void;
}
```

Update the component signature:

```tsx
export default function ChatTab({ sessionId, messages, setMessages, onTurnStart, onClear }: Props) {
```

Call `onTurnStart()` at the top of `send()`, right after the guard:

```tsx
  const send = async () => {
    const question = input.trim();
    if (!question || busy) return;
    onTurnStart();
    setInput("");
    setError(null);
```

Add the "Clear conversation" control as the first child inside the returned `<div className="tab-content chat">` (before `<div className="chat-list" ...>`):

```tsx
  return (
    <div className="tab-content chat">
      {messages.length > 0 && (
        <div className="chat-toolbar">
          <button className="link-button" onClick={onClear}>
            Clear conversation
          </button>
        </div>
      )}
      <div className="chat-list" ref={listRef}>
```

- [ ] **Step 4: Add the `.chat-toolbar` style**

Append to `clients/word/src/styles.css`:

```css
.chat-toolbar {
  display: flex;
  justify-content: flex-end;
  padding: 4px 0;
}
```

- [ ] **Step 5: Typecheck**

Run (from `clients/word/`): `npm run typecheck`
Expected: no output (exit 0). No `.js` emitted into `src/`.

- [ ] **Step 6: Regression — run the full unit suite**

Run (from `clients/word/`), expecting only `PASS:` lines across every file:

```bash
for f in src/*.test.ts; do echo "== $f =="; npx tsx "$f"; done
```

Expected: every line is `PASS:`; no `FAIL:`. (Total ≈ 169 asserts: the prior 154 + 5 from Task 1 + 10 from Task 2.)

- [ ] **Step 7: Commit**

```bash
git add clients/word/src/App.tsx clients/word/src/components/ChatTab.tsx clients/word/src/components/FindingsTab.tsx clients/word/src/styles.css
git commit -m "feat(word): restore chat on pane reopen + Clear conversation control"
```

- [ ] **Step 8: Manual smoke checklist (required before merge — the human gate)**

Sideload in Word for Mac (`cd clients/word && npm run dev`, then load the manifest). Verify:
1. Open a contract, ask 2–3 chat questions (include one that proposes an edit). Close the pane. Reopen it on the **same** document → the bubbles **and** the edit card(s) are restored; a follow-up turn still has backend context.
2. A restored edit proposal still applies as a tracked change (or fails gracefully with the calm/red pill) against the current doc.
3. Open the pane on a **different** document → no stale conversation appears.
4. Click **Clear conversation** → the thread empties; close/reopen → it stays empty.
5. Sanity: run a contract review, then reopen — the review render is (as designed) not restored, but chat still recalls the review via backend grounding.

---

### Task 4: Update `docs/wiki.md`

**Files:**
- Modify: `docs/wiki.md` (header line 3; the "Shipped Since Last Update" table at line ~488; resolve the follow-up row at line ~551)

**Interfaces:**
- Consumes: nothing (documentation).
- Produces: nothing code-facing.

- [ ] **Step 1: Update the header line**

In `docs/wiki.md` line 3, change `Last updated: 2026-07-10` → `Last updated: 2026-07-13`, change `154 frontend asserts passing` → `169 frontend asserts passing`, and append to the end of the trailer: ` + chat persistence across pane reopen`.

- [ ] **Step 2: Add a "Shipped Since Last Update" row**

In the table under `## Shipped Since Last Update (2026-05-15)` (header `| Feature | Commit / Branch | Notes |` at line ~490), append a row:

```markdown
| **Word add-in: chat persistence across pane reopen** | `feat/chat-persistence-pane-reopen` | Frontend-only. The task pane restores the chat conversation (visible bubbles + backend `sessionId` thread) when closed and reopened on the same document — previously chat was per-pane-lifetime. New `docIdentity.ts` computes a stable per-document key as a TS port of `memory/document_id.py::resolve_document_id` (normalized-preamble SHA-256 over `readBody()`), so the client key matches the backend's identity (verified by a cross-language golden test). New fail-safe, versioned `chatSessionStore.ts` persists `{v,sessionId,messages,updatedAt}` to `localStorage` keyed by that id (LRU-capped at 20 docs; `rawResponse` stripped; storage errors are silent no-ops). `App.tsx` hydrates once on mount (before the pane's first turn) and persists on change; `ChatTab` gains a **Clear conversation** control. No backend/graph/prompt/LLM change → review outputs byte-identical. +15 frontend asserts (169 total). Spec: `docs/superpowers/specs/2026-07-13-chat-persistence-pane-reopen-design.md`; plan: `docs/superpowers/plans/2026-07-13-chat-persistence-pane-reopen.md`. **Sideload smoke-test required before merge.** |
```

- [ ] **Step 3: Resolve the follow-up row**

Find the follow-up row (line ~551) beginning `| Word add-in: chat history persistence across pane reopen | Medium |` and replace it with the resolved form:

```markdown
| ~~Word add-in: chat history persistence across pane reopen~~ | ~~Medium~~ | **DONE** — shipped in `feat/chat-persistence-pane-reopen`. Chat now persists to `localStorage` keyed by a client-side preamble-hash `document_id` (TS port of `memory/document_id.py`) and rehydrates (bubbles + `sessionId`) on pane reopen; adds a Clear-conversation control. See the Shipped row above. |
```

- [ ] **Step 4: Verify the edits landed**

Run: `grep -n "2026-07-13\|169 frontend asserts\|chat-persistence-pane-reopen\|~~Word add-in: chat history persistence" docs/wiki.md`
Expected: matches on the header, the shipped row, and the struck-through follow-up.

- [ ] **Step 5: Commit**

```bash
git add docs/wiki.md
git commit -m "docs(wiki): ship chat persistence across pane reopen; resolve follow-up"
```

---

## Self-Review

**1. Spec coverage** — every spec section maps to a task:
- Spec §1 `docIdentity.ts` → Task 1 (with the cross-language golden test the spec's Testing section requires).
- Spec §2 `chatSessionStore.ts` (versioning, `try/catch`, LRU, `rawResponse` strip, empty-docId no-op) → Task 2.
- Spec §3 `App.tsx` (settable `sessionId`, `docId` state, once-at-mount hydration gated by first-turn, persist-after-hydration) → Task 3 Steps 1–2 + the `onTurnStart` wiring in FindingsTab.
- Spec §4 `ChatTab.tsx` "Clear conversation" (clears state + `clearSession`, keeps `sessionId`) → Task 3 Step 3 + `clearConversation` in App.
- Spec Edge cases (empty text, storage unavailable, corrupt/old record, stale sessionId, preamble edit, restored edits) → covered by Task 1/2 logic + tests and the Task 3 smoke checklist.
- Spec Testing (typecheck, two unit files, smoke) → Task 1/2 unit steps, Task 3 typecheck + regression + smoke.
- Spec Non-goals (findings render, in-pane switch, UUID identity, backend) → not implemented, by design.

**2. Placeholder scan** — no `TBD`/`TODO`/"handle edge cases"/"similar to"; every code step shows complete code; every run step gives the exact command + expected output.

**3. Type consistency** — `resolveDocumentId(text: string): Promise<string>` is defined in Task 1 and consumed identically in Task 3. `StoredSession` / `loadSession` / `saveSession` / `clearSession` signatures are identical in Task 2's Interfaces, implementation, test, and Task 3's usage. `ChatMessage` is imported as a **type** in `chatSessionStore.ts` (erased — no React load at test time). New props `onTurnStart` / `onClear` are declared in the Props interfaces and passed from `App.tsx` with matching names and `() => void` types. `saveSession` strips `rawResponse` via a shallow copy + `delete` (no unused-variable lint issue).

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-13-chat-persistence-pane-reopen.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
