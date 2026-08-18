// Tester feedback client: a written report, and silent interaction telemetry.
//
// recordEvent returns void rather than a Promise on purpose — a caller cannot
// accidentally await it, and a telemetry outage can never surface in the pane
// or break an Apply. sendFeedback is the opposite: it throws, because the
// attorney is watching for confirmation that their report sent.
import { userHeaders } from "./attorneyIdentity";

export interface TurnRef {
  turnId: string;
  traceId: string;
  sessionId: string;
  documentId: string;
}

export interface FlagTarget {
  turn: TurnRef;
  /** "findings" | "chat" | "general" */
  surface: string;
  /** "finding" | "edit" | "reply" | "" */
  targetKind: string;
  targetRef: string;
  snapshot: Record<string, unknown>;
}

export const EMPTY_TURN: TurnRef = { turnId: "", traceId: "", sessionId: "", documentId: "" };

/** Assemble the replayable context. Absent parts are omitted, not sent empty. */
export function buildSnapshot(parts: {
  documentText?: string;
  assistantOutput?: string;
  request?: string;
  contractType?: string;
  target?: unknown;
}): Record<string, unknown> {
  const snap: Record<string, unknown> = {};
  if (parts.documentText) snap.document_text = parts.documentText;
  if (parts.assistantOutput) snap.assistant_output = parts.assistantOutput;
  if (parts.request) snap.request = parts.request;
  if (parts.contractType) snap.contract_type_detected = parts.contractType;
  if (parts.target !== undefined) snap.target = parts.target;
  return snap;
}

export async function sendFeedback(target: FlagTarget, comment: string): Promise<void> {
  const res = await fetch("/api/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...userHeaders() },
    body: JSON.stringify({
      turn_id: target.turn.turnId,
      trace_id: target.turn.traceId,
      session_id: target.turn.sessionId,
      document_id: target.turn.documentId,
      surface: target.surface,
      target_kind: target.targetKind,
      target_ref: target.targetRef,
      comment,
      snapshot: target.snapshot,
    }),
  });
  if (!res.ok) {
    if (res.status === 403) throw new Error("Feedback is currently disabled on the server.");
    throw new Error(`Backend returned ${res.status} ${res.statusText}`);
  }
}

export function recordEvent(
  turn: TurnRef,
  surface: string,
  action: string,
  extra: { targetKind?: string; targetRef?: string; detail?: string; request?: string } = {},
): void {
  try {
    void fetch("/api/events", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...userHeaders() },
      body: JSON.stringify({
        events: [
          {
            turn_id: turn.turnId,
            session_id: turn.sessionId,
            document_id: turn.documentId,
            surface,
            action,
            target_kind: extra.targetKind ?? "",
            target_ref: (extra.targetRef ?? "").slice(0, 200),
            detail: (extra.detail ?? "").slice(0, 500),
            request: (extra.request ?? "").slice(0, 500),
          },
        ],
      }),
    }).catch(() => {
      /* telemetry must never surface */
    });
  } catch {
    /* fetch itself unavailable — still never surface */
  }
}

// A one-slot channel from any card to the single FeedbackPanel that App owns.
// Deliberately a module singleton rather than React context or prop drilling:
// there is exactly one pane and one panel, the cards sit four levels down, and
// this codebase already uses module-level singletons for identity.
let flagHandler: ((t: FlagTarget) => void) | null = null;

export function onFlagRequested(fn: ((t: FlagTarget) => void) | null): void {
  flagHandler = fn;
}

export function requestFlag(target: FlagTarget): void {
  flagHandler?.(target);
}
