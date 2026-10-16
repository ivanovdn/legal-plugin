// SP4: copies of the document as the attorney works on it — when the pane
// opens, after each pause in editing, and just before Finalize — so what the
// assistant applied can be compared with what the attorney kept.
// Spec: docs/superpowers/specs/2026-10-15-document-changes-design.md
//
// Quiet like recordEvent: nothing here can reach the attorney or stop what they
// are doing. Only documents the assistant has been used on are captured — the
// id is read with readDocumentId(), never created: resolveDocumentId() writes
// to the file and marks it modified.
import { readDocumentId } from "./docIdentity";
import { requestHeaders } from "./requestHeaders";
import { readDocumentForCapture } from "./word";

export type Moment = "pane_open" | "idle" | "finalize";

export interface TrackedChange {
  type: string;
  author: string;
  date: string;
  text: string;
  cut?: boolean;
}

/** tracked === null: Word could not read the tracked changes (sent as tracked_total -1). */
export interface DocumentReading {
  current: string;
  original: string;
  tracked: TrackedChange[] | null;
}

export interface SnapshotBody {
  session_id: string;
  document_id: string;
  moment: Moment;
  current_text: string;
  original_text: string;
  tracked_changes: TrackedChange[];
  tracked_total: number;
}

export interface SnapshotReply {
  recorded: number;
  enabled: boolean;
}

export const IDLE_MS = 30_000;
export const FINALIZE_WAIT_MS = 3_000;
export const POLL_MS = 120_000;
export const TRACKED_CAP = 500;
export const TRACKED_TEXT_CAP = 2_000;

type Schedule = (fn: () => void, ms: number) => unknown;
type Cancel = (handle: unknown) => void;

export interface CaptureDeps {
  readDocumentId: () => string;
  readDocument: () => Promise<DocumentReading>;
  send: (body: SnapshotBody) => Promise<SnapshotReply>;
  sessionId: string;
  schedule?: Schedule;
  cancel?: Cancel;
}

export interface DocumentCapture {
  /** Read and send the document. Never throws. */
  capture(moment: Moment): Promise<void>;
  /** The document may have changed: (re)start the idle timer. */
  noteChange(): void;
}

/** The body for one capture: tracked changes capped, their count kept from before the cap. */
export function buildSnapshotBody(
  documentId: string,
  sessionId: string,
  moment: Moment,
  reading: DocumentReading,
): SnapshotBody {
  const all = reading.tracked;
  return {
    session_id: sessionId,
    document_id: documentId,
    moment,
    current_text: reading.current,
    original_text: reading.original,
    tracked_changes: (all ?? []).slice(0, TRACKED_CAP).map((c) =>
      c.text.length > TRACKED_TEXT_CAP ? { ...c, text: c.text.slice(0, TRACKED_TEXT_CAP), cut: true } : c,
    ),
    tracked_total: all === null ? -1 : all.length,
  };
}

const contentOf = (body: SnapshotBody): string =>
  JSON.stringify([body.current_text, body.original_text, body.tracked_changes]);

export function createDocumentCapture(deps: CaptureDeps): DocumentCapture {
  const schedule: Schedule = deps.schedule ?? ((fn, ms) => setTimeout(fn, ms));
  const cancel: Cancel = deps.cancel ?? ((h) => clearTimeout(h as ReturnType<typeof setTimeout>));
  let running: Promise<void> | null = null;
  let stopped = false;
  let lastSent = "";
  let timer: unknown = undefined;

  async function run(moment: Moment): Promise<void> {
    try {
      const documentId = deps.readDocumentId();
      if (!documentId) return;
      const body = buildSnapshotBody(documentId, deps.sessionId, moment, await deps.readDocument());
      const content = contentOf(body);
      if (moment === "idle" && content === lastSent) return;
      const reply = await deps.send(body);
      lastSent = content;
      if (!reply.enabled) stopped = true;
    } catch {
      /* a capture must never reach the attorney */
    }
  }

  async function capture(moment: Moment): Promise<void> {
    if (stopped) return;
    if (running) {
      // One read at a time; Finalize alone waits, so its moment is not lost.
      if (moment !== "finalize") return;
      await running;
      if (stopped) return;
    }
    const mine = run(moment);
    running = mine;
    await mine;
    if (running === mine) running = null;
  }

  function noteChange(): void {
    if (stopped) return;
    try {
      if (timer !== undefined) cancel(timer);
      timer = schedule(() => {
        timer = undefined;
        void capture("idle");
      }, IDLE_MS);
    } catch {
      /* never let a timer break typing */
    }
  }

  return { capture, noteChange };
}

/** Resolves when `work` settles or after `ms`, whichever is first. Never rejects. */
export function withinTime(
  work: Promise<void>,
  ms: number,
  schedule: Schedule = (fn, t) => setTimeout(fn, t),
): Promise<void> {
  return new Promise<void>((resolve) => {
    work.then(resolve, resolve);
    schedule(resolve, ms);
  });
}

let installed: DocumentCapture | null = null;

/** Finalize's own capture, given at most FINALIZE_WAIT_MS: Finalize goes ahead either way. */
export function captureBeforeFinalize(
  capture: DocumentCapture | null = installed,
  ms: number = FINALIZE_WAIT_MS,
  schedule?: Schedule,
): Promise<void> {
  if (!capture) return Promise.resolve();
  let work: Promise<void>;
  try {
    work = capture.capture("finalize");
  } catch {
    return Promise.resolve();
  }
  return withinTime(work, ms, schedule);
}

export async function postDocumentSnapshot(body: SnapshotBody): Promise<SnapshotReply> {
  const res = await fetch("/api/document-snapshot", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...requestHeaders() },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`document snapshot: ${res.status}`);
  const json = (await res.json()) as { data?: { recorded?: number; enabled?: boolean } };
  return { recorded: Number(json.data?.recorded ?? 0), enabled: json.data?.enabled !== false };
}

/** Both change events (spec D3) feed the idle timer; true if either registered. */
async function listenForChanges(onChange: () => void): Promise<boolean> {
  if (typeof Word === "undefined" || typeof Office === "undefined") return false;
  const paragraphs = Word.run(async (context) => {
    // WordApi 1.5, absent from some office-js typing releases — the same narrow
    // cast App.tsx uses for the live document size.
    const doc = context.document as unknown as {
      onParagraphChanged: { add: (h: () => Promise<void>) => void };
    };
    doc.onParagraphChanged.add(async () => {
      onChange();
    });
    await context.sync();
    return true;
  }).catch(() => false);
  const selection = new Promise<boolean>((resolve) => {
    try {
      Office.context.document.addHandlerAsync(
        Office.EventType.DocumentSelectionChanged,
        () => onChange(),
        (result: Office.AsyncResult<void>) => resolve(result.status === Office.AsyncResultStatus.Succeeded),
      );
    } catch {
      resolve(false);
    }
  });
  const results = await Promise.all([paragraphs, selection]);
  return results.some(Boolean);
}

/** Once per pane load, from App: capture now, then after every pause in editing. */
export function installDocumentCapture(sessionId: string): void {
  if (installed) return;
  const capture = createDocumentCapture({
    readDocumentId,
    readDocument: readDocumentForCapture,
    send: postDocumentSnapshot,
    sessionId,
  });
  installed = capture;
  void capture.capture("pane_open");
  listenForChanges(() => capture.noteChange())
    .then((listening) => {
      // Neither event registered: a slow poll stands in, still sending only on change.
      if (!listening) {
        setInterval(() => {
          if (document.visibilityState === "visible") void capture.capture("idle");
        }, POLL_MS);
      }
    })
    .catch(() => {
      /* never let capture wiring break the pane */
    });
}
