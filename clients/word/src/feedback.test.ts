// Feedback client contracts. Run with: npx tsx src/feedback.test.ts
import { buildSnapshot, onFlagRequested, recordEvent, requestFlag, sendFeedback } from "./feedback";
import { pass } from "./testAssert";

(globalThis as { localStorage?: unknown }).localStorage = {
  getItem: () => null,
  setItem: () => {},
};

const TURN = { turnId: "t-1", traceId: "abc", sessionId: "s-1", documentId: "d-1" };
const TARGET = { turn: TURN, surface: "chat", targetKind: "edit", targetRef: "[__]", snapshot: {} };

// --- recordEvent must never break the pane -------------------------------
(globalThis as { fetch?: unknown }).fetch = () => Promise.reject(new Error("offline"));
let threw = false;
try {
  recordEvent(TURN, "chat", "edit_applied");
} catch {
  threw = true;
}
pass(!threw, "recordEvent swallows a rejected fetch");

(globalThis as { fetch?: unknown }).fetch = () => {
  throw new Error("fetch itself exploded");
};
threw = false;
try {
  recordEvent(TURN, "chat", "edit_applied");
} catch {
  threw = true;
}
pass(!threw, "recordEvent swallows a synchronous fetch throw");

// --- sendFeedback must surface failure ------------------------------------
(globalThis as { fetch?: unknown }).fetch = async () => ({
  ok: false,
  status: 500,
  statusText: "Server Error",
});
let caught = false;
await sendFeedback(TARGET, "wrong field").catch(() => {
  caught = true;
});
pass(caught, "sendFeedback propagates a non-ok response");

let sentBody: Record<string, unknown> = {};
(globalThis as { fetch?: unknown }).fetch = async (_u: string, init: { body: string }) => {
  sentBody = JSON.parse(init.body);
  return { ok: true, status: 200, statusText: "OK", json: async () => ({}) };
};
await sendFeedback(TARGET, "wrong field");
pass(sentBody.turn_id === "t-1" && sentBody.comment === "wrong field", "sendFeedback posts the turn id and comment");

// --- buildSnapshot ---------------------------------------------------------
const snap = buildSnapshot({ documentText: "NDA", request: "who signs?" });
pass(snap.document_text === "NDA" && snap.request === "who signs?", "buildSnapshot uses snake_case keys");
pass(!("assistant_output" in snap), "buildSnapshot omits absent parts");

// --- the flag channel ------------------------------------------------------
let received: string | null = null;
onFlagRequested((t) => {
  received = t.targetRef;
});
requestFlag(TARGET);
pass(received === "[__]", "requestFlag reaches the registered handler");

onFlagRequested(null);
received = null;
requestFlag(TARGET);
pass(received === null, "requestFlag is a no-op with no handler registered");
