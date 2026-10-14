// Pane lifecycle events (SP3): what the attorney does in the pane between
// turns, the errors it shows them, and how long they really waited — sent as
// interaction_event rows with surface "pane". Everything goes through
// recordEvent, so none of it can surface in the pane or break what the
// attorney is doing. Spec: docs/superpowers/specs/2026-10-14-pane-activity-design.md
import { recordEvent } from "./feedback";
import { readDocumentId } from "./docIdentity";

let paneSessionId = "";
let installedOn: EventTarget | null = null;
let reporting = false;

/** The pane's session (App mints it once per pane load); every pane event carries it. */
export function setPaneSession(sessionId: string): void {
  paneSessionId = sessionId;
}

/** Send one pane event. The document id is read at send time and never created. */
export function recordPaneEvent(
  action: string,
  extra: { turnId?: string; targetRef?: string; detail?: string } = {},
): void {
  recordEvent(
    { turnId: extra.turnId ?? "", traceId: "", sessionId: paneSessionId, documentId: readDocumentId() },
    "pane",
    action,
    { targetRef: extra.targetRef, detail: extra.detail },
  );
}

/** An error the pane showed the attorney — or an uncaught one, where = "window". */
export function reportClientError(where: string, error: unknown, turnId = ""): void {
  // If reporting raises another error event synchronously, drop it rather than loop.
  if (reporting) return;
  reporting = true;
  try {
    const detail = error instanceof Error ? error.message : String(error);
    recordPaneEvent("client_error", { turnId, targetRef: where, detail });
  } finally {
    reporting = false;
  }
}

/** Uncaught errors and unhandled rejections → client_error. Once per target. */
export function installErrorHandlers(target: EventTarget): void {
  if (installedOn === target) return;
  installedOn = target;
  target.addEventListener("error", (e) => {
    const ev = e as ErrorEvent;
    reportClientError("window", ev.error ?? ev.message ?? "unknown error");
  });
  target.addEventListener("unhandledrejection", (e) => {
    reportClientError("window", (e as PromiseRejectionEvent).reason ?? "unhandled rejection");
  });
}

/** Start a wait; the returned function gives the whole milliseconds since. */
export function startWait(now: () => number = () => Date.now()): () => number {
  const started = now();
  return () => Math.max(0, Math.round(now() - started));
}
