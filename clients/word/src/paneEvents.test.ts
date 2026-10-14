// Pane lifecycle events (SP3). Run with: npx tsx src/paneEvents.test.ts
import {
  installErrorHandlers,
  recordPaneEvent,
  reportClientError,
  setPaneSession,
  startWait,
} from "./paneEvents";
import { pass } from "./testAssert";

(globalThis as { localStorage?: unknown }).localStorage = { getItem: () => null, setItem: () => {} };
(globalThis as { Office?: unknown }).Office = {
  context: { document: { settings: { get: () => "d-1", set: () => {}, saveAsync: () => {} } } },
};

type Sent = Record<string, string>;
let sent: Sent[] = [];
let onSend: () => void = () => {};
(globalThis as { fetch?: unknown }).fetch = (_url: string, init: { body: string }) => {
  sent.push(JSON.parse(init.body).events[0]);
  onSend();
  return Promise.resolve({ ok: true });
};
const last = (): Sent => sent[sent.length - 1];

setPaneSession("s-1");

recordPaneEvent("pane_opened", { detail: "saved" });
pass(
  last().surface === "pane" && last().action === "pane_opened" && last().detail === "saved" &&
    last().session_id === "s-1" && last().document_id === "d-1" && last().turn_id === "",
  "recordPaneEvent sends surface pane with the pane's session and document, and no turn",
);

recordPaneEvent("tab_viewed", { targetRef: "chat" });
pass(last().action === "tab_viewed" && last().target_ref === "chat",
  "tab_viewed carries the tab in target_ref");

recordPaneEvent("turn_waited", { turnId: "t-9", targetRef: "review", detail: "96100" });
pass(last().turn_id === "t-9" && last().target_ref === "review" && last().detail === "96100",
  "turn_waited carries its turn, its kind and the milliseconds");

reportClientError("chat", new Error("Backend returned 502 Bad Gateway"), "t-3");
pass(
  last().action === "client_error" && last().target_ref === "chat" &&
    last().detail === "Backend returned 502 Bad Gateway" && last().turn_id === "t-3",
  "reportClientError sends the message, where it happened and the turn",
);

reportClientError("preferences", "plain string");
pass(last().detail === "plain string" && last().turn_id === "",
  "a non-Error is sent as its string, with no turn");

// Uncaught errors and rejections, on a stand-in for window.
const win = new EventTarget();
installErrorHandlers(win);
installErrorHandlers(win); // a second install must not double every report
sent = [];
win.dispatchEvent(Object.assign(new Event("error"), { error: new Error("boom"), message: "boom" }));
pass(
  sent.length === 1 && last().action === "client_error" && last().target_ref === "window" &&
    last().detail === "boom",
  "an uncaught error is reported once, from window, with its message",
);

sent = [];
win.dispatchEvent(Object.assign(new Event("unhandledrejection"), { reason: new Error("lost promise") }));
pass(sent.length === 1 && last().detail === "lost promise",
  "an unhandled rejection is reported with its reason");

// A report that itself raises an error event must not loop. Without the guard
// each send dispatches another error (capped at 5 so a broken guard fails fast
// instead of hanging the gate).
sent = [];
let depth = 0;
onSend = () => {
  if (depth++ < 5) {
    win.dispatchEvent(Object.assign(new Event("error"), { error: new Error("again") }));
  }
};
win.dispatchEvent(Object.assign(new Event("error"), { error: new Error("first") }));
onSend = () => {};
pass(sent.length === 1 && sent[0].detail === "first",
  "an error raised while reporting an error is dropped, not re-reported");

let clock = 1000;
const elapsed = startWait(() => clock);
clock = 1250.6;
pass(elapsed() === 251, "startWait gives whole milliseconds since it started");
clock = 900;
pass(elapsed() === 0, "a clock that steps backwards never gives a negative wait");

// Window noise: the same uncaught error over and over is one report, and there is a ceiling.
sent = [];
for (let i = 0; i < 5; i++) {
  win.dispatchEvent(Object.assign(new Event("error"), { error: new Error("Script error.") }));
}
pass(sent.length === 1, "a repeated identical window error is reported once");

sent = [];
for (let i = 0; i < 40; i++) {
  win.dispatchEvent(Object.assign(new Event("error"), { error: new Error(`distinct ${i}`) }));
}
pass(sent.length > 0 && sent.length <= 20, "window errors stop being reported at the per-load ceiling");
