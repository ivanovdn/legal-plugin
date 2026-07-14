# Chat persistence across pane reopen — design

> **⚠️ SUPERSEDED (2026-07-14).** This localStorage approach was built and then reverted after
> sideload smoke testing revealed (a) the preamble-hash identity drifts when the review workflow
> fills fields in the opening block, and (b) the real deployment is multi-attorney on cloud docs
> with a shared VM backend — for which per-machine localStorage is the wrong store. Replaced by a
> server-side design keyed by a document-embedded UUID; see
> `docs/superpowers/specs/2026-07-14-canonical-document-uuid-design.md` (foundation slice). Kept
> for design history.

> **Status:** approved design, ready for an implementation plan.
> **Scope:** frontend-only, in the Word add-in. **No backend, graph, prompt, parser, or
> LLM change** — review outputs stay byte-identical.
> **Source:** the `docs/wiki.md` follow-up "Word add-in: chat history persistence across
> pane reopen" (Medium) + the `docs/context_and_memory_audit.md` finding that chat is
> per-pane-lifetime.

## Problem

`App.tsx` mints `sessionId` **once per pane lifetime** (a `crypto.randomUUID()` in a
`useState` initializer) and holds `chatMessages` in React state. Closing the task pane
unmounts the app; reopening it mints a **fresh** `sessionId` and an **empty** message list,
so the conversation is gone.

Two distinct things are lost, and they live in different places:

1. **The rendered bubbles** (`ChatMessage[]`) — role, prose, `proposedEdits`,
   `promisedEditMissing`, `rawResponse`. These are held **only** in React state; they are
   **never** persisted anywhere (the backend keeps just 300-char assistant stubs in Redis).
   On reopen they are lost entirely.
2. **The backend thread.** The Redis `chat_history` for the old `sessionId` actually
   **survives** (24 h TTL, refreshed each call), but the client has discarded the key
   (`sessionId`) that points at it, so the next turn starts a brand-new thread.

## Guiding principle

Persist a small per-document record in the browser's `localStorage`, keyed by a **stable
client-side document id**. On reopen, resolve the id from the current document and rehydrate
both the bubbles **and** the `sessionId` (so the backend thread continues too). Additive and
fail-safe: if storage is unavailable the feature silently degrades to today's behavior.

**Document identity = a TS port of the backend's `resolve_document_id`** (normalized-preamble
SHA-256). The localStorage key only needs to be stable per-document on the client, but reusing
the backend's exact identity means the restored chat, the Redis thread, and the SQLite review
store all agree on what "the same document" is. `readBody()` on the client returns the exact
`uploaded_text` the backend hashes, so the two ids match by construction.

> **Alternative considered (documented upgrade path):** an Office.js **custom document
> property UUID** written into the file on first open (the deferred
> "Office.js custom-document-property `document_id`" follow-up). More durable — survives
> preamble edits, rename, and move — but more code (WordApi 1.3 read-or-create handshake,
> async race on mount) and copies of a file share the UUID. `resolveDocumentId` is a single
> swappable function, so this is a later drop-in, exactly as `memory/document_id.py` is on the
> backend. Not built in this slice.

---

## 1. `docIdentity.ts` — stable client-side document id (NEW)

A faithful TS port of [`memory/document_id.py`](../../../memory/document_id.py), so the client
and backend agree on identity.

```ts
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

- Mirrors the Python steps in order: `[:800]` prefix → cut at the first `\n<digit>.` →
  `NFC` → lowercase → collapse whitespace (`\s+` → single space) → trim → SHA-256 hex.
- Empty/whitespace input returns `""` (same as Python) → treated as "no key" by the store
  (no persist, no restore).
- `crypto.subtle` requires a secure context; the add-in is HTTPS-only (mandatory per
  CLAUDE.md), and Node ≥ 18 provides `globalThis.crypto.subtle` for the test runner.

**Known cross-language caveat (documented, not fixed):** Python's `re` `\s` / `str.lower()`
and JS's `\s` / `String.toLowerCase()` can differ on **exotic Unicode** whitespace and casing
(e.g. non-breaking space, Turkish `i`, `ß`). A title/parties preamble is ordinary text, so this
does not bite in practice; the golden test below uses a realistic legal preamble. If a real doc
ever hashes differently across the two, the only effect is an orphaned prior conversation on
that doc — never a crash or cross-doc mixup.

## 2. `chatSessionStore.ts` — versioned localStorage wrapper (NEW)

A pure module over `localStorage`. All access goes through a safe accessor so a blocked/absent
store (or a thrown quota error) is a **silent no-op**, never a crash.

```ts
import type { ChatMessage } from "./components/ChatTab";

const KEY_PREFIX = "legal-triage:chat:";       // per-doc record key: KEY_PREFIX + docId
const INDEX_KEY = "legal-triage:chat-index";   // JSON string[] of docIds, most-recent first
const SCHEMA_VERSION = 1;
const MAX_DOCS = 20;                             // LRU cap

export interface StoredSession {
  v: number;
  sessionId: string;
  messages: ChatMessage[];   // rawResponse stripped (see below)
  updatedAt: string;         // ISO 8601
}

export function loadSession(docId: string): StoredSession | null;
export function saveSession(docId: string, sessionId: string, messages: ChatMessage[]): void;
export function clearSession(docId: string): void;
```

- **`loadSession`** — `docId === ""` → `null`. Read `KEY_PREFIX+docId`, `JSON.parse` inside
  `try/catch`, validate `v === SCHEMA_VERSION` and the basic shape (`sessionId` string,
  `messages` array). Any miss / parse error / version mismatch → return `null` (and remove the
  bad key). Touch the LRU index (move this docId to front) on a successful load.
- **`saveSession`** — `docId === ""` → no-op. **Strip `rawResponse`** from each message before
  storing (debug-only "show raw" payload; full LLM outputs × many turns × 20 docs would bloat
  the quota). Build `{ v, sessionId, messages, updatedAt }`, `JSON.stringify`, `setItem` inside
  `try/catch`. On a quota error: evict the oldest indexed doc(s) and retry once; if it still
  throws, give up silently. Update the LRU index (move docId to front, trim to `MAX_DOCS`,
  `removeItem` the evicted docs' keys).
- **`clearSession`** — `removeItem` the key and drop the docId from the index.
- **Type source:** `ChatMessage` stays defined in `ChatTab.tsx`; the store imports it as a
  type (no runtime coupling).

**Testability:** the module reads `globalThis.localStorage` via the safe accessor. The unit
test installs a minimal in-memory `localStorage` shim on `globalThis` before importing the
module.

## 3. `App.tsx` — hydrate on mount, persist on change (MODIFY)

- Make `sessionId` settable: `const [sessionId, setSessionId] = useState(...)` (initializer
  unchanged — a fresh uuid is the fallback when there's no stored record).
- Add `const [docId, setDocId] = useState("")` and `const hydratedRef = useRef(false)`.
- **Hydration effect (runs once at mount, after Office is ready):** `readBody()` →
  `resolveDocumentId(text)` → `setDocId(id)`. If `id` is non-empty **and no turn has yet
  fired this pane-lifetime**, `loadSession(id)` → on a hit, `setSessionId(record.sessionId)`
  and `setChatMessages(record.messages)`. Set `hydratedRef.current = true` at the end
  (whether or not a record was found).
- **Persist effect** on `[docId, sessionId, chatMessages]`: only after `hydratedRef.current`
  is true (so we never overwrite a stored record with the empty pre-hydration state) and only
  when `docId` is non-empty and `chatMessages.length > 0` → `saveSession(docId, sessionId, chatMessages)`.
- **First-turn guard for the sessionId swap:** hydration must not replace `sessionId` after an
  outbound request has already gone out this pane-lifetime (a rare pane-open → immediate
  "Review" race would otherwise split the review thread from the chat thread). Track with a
  single ref set to true at the top of both send paths, passed to the tabs as one
  `onTurnStart` callback; hydration skips the `setSessionId` (and message restore) if it's
  already set. Bubbles-only restore is also skipped in that case, since a turn is already in
  progress. (Mechanism is the plan's to finalize; the behavior is: hydrate once, only before
  the pane's first turn.)

## 4. `ChatTab.tsx` — "Clear conversation" control (MODIFY)

- A small secondary control (e.g. a "Clear conversation" link/button in the chat header or
  above the input) that clears the in-memory `chatMessages` and calls a passed-in `onClear`.
- `onClear` (in `App.tsx`) resets `chatMessages` to `[]` and calls `clearSession(docId)` so the
  cleared state survives the next reopen. It does **not** mint a new `sessionId` (the thread
  continues; only the visible/stored bubbles are cleared) — keeping it simple; revisit if a
  full "new conversation" reset is wanted later.

---

## Edge cases

- **Empty document text** → `resolveDocumentId` returns `""` → no persist, no restore
  (behaves exactly like today).
- **`localStorage` unavailable / blocked / quota exceeded** → every store op is a silent
  no-op; the feature degrades to today's in-memory-only behavior. No banner, no crash (this is
  an enhancement, not the loud-degradation path that `memory_degraded` covers for Redis).
- **Corrupt or old-schema stored record** → `loadSession` discards it and returns `null`;
  the bad key is removed. Fresh start.
- **Stored `sessionId` older than the backend's 24 h Redis TTL** → restoring it is harmless:
  the Redis thread is simply empty, the bubbles still restore from localStorage, and the next
  turn starts a fresh backend thread under that id. `memory_degraded` is unaffected.
- **Preamble edited between sessions** → new `docId` → the prior conversation is orphaned (not
  restored). Same tradeoff the backend review store already accepts for preamble-hash identity.
- **Restored edit proposals** apply against the **current** document; if it has moved on, the
  existing locator handles a miss gracefully (the calm/red `card-status` pill from the
  clause-locator and calm-notfound branches). No special handling needed.
- **Two docs sharing a preamble** (same template) → same `docId` → shared conversation. Same
  known limitation as the backend review store; acceptable for the demo.

## Non-goals / Out of scope

- **Findings-tab render persistence.** The review render also vanishes on reopen, but it is
  server-persisted (SQLite review store) and one click to re-derive. Kept out; the store shape
  is generic enough to hold it in a fast-follow.
- **In-pane document switch** (swapping the open document without closing the pane). The
  mount-resolved `docId` won't update — a **pre-existing** quirk (audit §7.5), unchanged here.
- **Office.js custom-property UUID identity.** Documented above as the durable upgrade path;
  not built in this slice.
- **Any backend / graph / prompt / LLM / chat_history-cap change.** None.

## Testing

- **`tsc --noEmit`** clean (`npm run typecheck`).
- **Unit — `docIdentity.test.ts`:** empty/whitespace → `""`; a preamble-cut case (text with a
  `\n1.` numbered section hashes the same as the text truncated there); and a **cross-language
  golden vector** — a realistic legal preamble whose expected hex is produced by running
  `memory/document_id.py::resolve_document_id` on the identical string, asserting the TS output
  equals it (guards against normalization drift).
- **Unit — `chatSessionStore.test.ts`** (in-memory `localStorage` shim): round-trip
  save→load; `rawResponse` is stripped on save; `docId === ""` is a no-op for save/load;
  version-mismatch and corrupt-JSON records are discarded; LRU eviction past `MAX_DOCS`
  removes the oldest keys; a storage accessor that throws makes save/load safe no-ops.
- **Smoke (required before merge, per repo rule — Office.js/React has no automated coverage):**
  sideload in Word for Mac — hold a chat, close the pane, reopen on the **same** document →
  the conversation (bubbles + edit cards) is restored and a follow-up turn still has backend
  context; reopen on a **different** document → no stale conversation; "Clear conversation"
  empties it and it stays empty after reopen; a restored edit proposal still applies (or fails
  gracefully) against the current doc.

## Risks / rollback

- **Risk: low.** Two new isolated frontend modules + two effects and one small control in
  existing components. No backend/graph/prompt/model surface touched → review outputs
  byte-identical. Storage failures are non-fatal by construction.
- **Main drift risk** is `docIdentity.ts` diverging from `memory/document_id.py`; the
  cross-language golden test is the guard, and the caveat above bounds the blast radius (an
  orphaned prior conversation, never a crash or cross-doc mixup).
- **Rollback:** remove the hydration + persist effects (and the "Clear conversation" control)
  → today's behavior. Nothing else depends on the new modules.
