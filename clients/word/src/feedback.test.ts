// Feedback client contracts. Run with: npx tsx src/feedback.test.ts
import {
  APPLIED_TEXT_CAP,
  appliedFromProposal,
  buildSnapshot,
  capApplied,
  onFlagRequested,
  recordEvent,
  requestFlag,
  sendFeedback,
} from "./feedback";
import { pass } from "./testAssert";

// Minimal ambient type for the one Node API this file needs (detecting an
// unhandled promise rejection) — not @types/node, same rationale as
// testAssert.ts's own `declare const process`: tsconfig pins
// "types": ["office-js", "vite/client"], and pulling in full Node globals
// would change typing project-wide for no benefit here.
declare const process: {
  once(event: "unhandledRejection", listener: (reason: unknown) => void): void;
  removeListener(event: "unhandledRejection", listener: (reason: unknown) => void): void;
};

(globalThis as { localStorage?: unknown }).localStorage = {
  getItem: () => null,
  setItem: () => {},
};

const TURN = { turnId: "t-1", traceId: "abc", sessionId: "s-1", documentId: "d-1" };
const TARGET = { turn: TURN, surface: "chat", targetKind: "edit", targetRef: "[__]", snapshot: {} };

// --- recordEvent must never break the pane -------------------------------
(globalThis as { fetch?: unknown }).fetch = () => Promise.reject(new Error("offline"));
let threw = false;
// A rejected fetch promise never throws SYNCHRONOUSLY — whether or not
// recordEvent attaches an internal .catch(), so the try/catch alone can't
// tell the two cases apart (this is exactly what made the original version
// of this assertion vacuous). The only observable difference is whether the
// rejection goes UNHANDLED, and Node only reports that asynchronously via
// process's "unhandledRejection" event. Listen for it, give the microtask
// queue a turn to raise it, then always remove the listener so it can't leak
// into a later assertion in this file.
let unhandledRejectionSeen = false;
const onUnhandledRejection = () => {
  unhandledRejectionSeen = true;
};
process.once("unhandledRejection", onUnhandledRejection);
try {
  recordEvent(TURN, "chat", "edit_applied");
} catch {
  threw = true;
}
await new Promise((resolve) => setTimeout(resolve, 0));
process.removeListener("unhandledRejection", onUnhandledRejection);
pass(!threw && !unhandledRejectionSeen, "recordEvent swallows a rejected fetch");

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

// --- SP4: the applied text -----------------------------------------------
let lastEvent: Record<string, unknown> = {};
(globalThis as { fetch?: unknown }).fetch = (_url: string, init: { body: string }) => {
  lastEvent = JSON.parse(init.body).events[0];
  return Promise.resolve({ ok: true });
};
recordEvent(TURN, "chat", "edit_applied", {
  targetKind: "replace",
  targetRef: "the Seller",
  applied: { action: "replace", target_text: "the Seller", new_text: "Acme Ltd" },
});
pass(
  JSON.stringify(lastEvent.applied) ===
    JSON.stringify({ action: "replace", target_text: "the Seller", new_text: "Acme Ltd" }),
  "recordEvent sends the applied text with the event",
);

recordEvent(TURN, "chat", "edit_discarded");
pass(!("applied" in lastEvent), "an event with no applied text sends none");

const huge = "x".repeat(APPLIED_TEXT_CAP + 10);
const capped = capApplied({ action: "insert", target_text: huge, new_text: huge, proposed_text: huge, position: "after" });
pass(
  capped.target_text.length === APPLIED_TEXT_CAP && capped.new_text.length === APPLIED_TEXT_CAP &&
    capped.proposed_text?.length === APPLIED_TEXT_CAP && capped.position === "after",
  "each applied field is capped at 20,000 characters",
);

const proposal = { action: "replace" as const, target_text: "1x fees", new_text: "2x fees" };
pass(
  JSON.stringify(appliedFromProposal(proposal, "3x fees")) ===
    JSON.stringify({ action: "replace", target_text: "1x fees", new_text: "3x fees", proposed_text: "2x fees" }),
  "an Apply records the text the attorney applied, and the model's when they differ",
);
pass(appliedFromProposal(proposal, "2x fees").proposed_text === undefined,
  "an Apply of the model's text as proposed records no separate proposal");
pass(
  JSON.stringify(appliedFromProposal({ action: "insert", anchor_text: "4. Term", new_text: "4.1 Renewal." }, "4.1 Renewal.")) ===
    JSON.stringify({ action: "insert", target_text: "4. Term", new_text: "4.1 Renewal.", position: "after" }),
  "an insert records its anchor as the target, and where it went",
);
pass(
  JSON.stringify(appliedFromProposal({ action: "delete", target_text: "Non-solicit." }, "")) ===
    JSON.stringify({ action: "delete", target_text: "Non-solicit.", new_text: "" }),
  "a delete records what it took out",
);
