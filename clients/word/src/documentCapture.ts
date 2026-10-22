// SP4: copies of the document as the attorney works on it — when the pane
// opens, about a second after each Apply, after each pause in editing, and just
// before Finalize — so what the assistant applied can be compared with what the
// attorney kept.
// Spec: docs/superpowers/specs/2026-10-15-document-changes-design.md
//
// Quiet like recordEvent: nothing here can reach the attorney or stop what they
// are doing. Only documents the assistant has been used on are captured — the
// id is read with readDocumentId(), never created: resolveDocumentId() writes
// to the file and marks it modified.
import { readDocumentId } from "./docIdentity";
import { requestHeaders } from "./requestHeaders";
import { readDocumentForCapture } from "./word";

export type Moment = "pane_open" | "apply" | "idle" | "finalize";

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
/** How long after the last Apply its copy is taken; Applies closer together than this make one copy.
 *  Short on purpose: an attorney who applies, then saves and closes, closes the pane with the document,
 *  and a copy still waiting goes with it. The copy is missed only when the pane closes within this long
 *  of the last Apply; a longer wait missed two such runs in Word. */
export const APPLY_SETTLE_MS = 1_000;
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
  /** An Apply just went into the document: (re)start the APPLY_SETTLE_MS timer, a timer of its own. */
  noteApply(): void;
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

/** idle and apply send only when the document is not what this pane last sent; pane_open and finalize
 *  always do, the moment itself being information. */
const sendsOnlyOnChange = (moment: Moment): boolean => moment === "idle" || moment === "apply";

export function createDocumentCapture(deps: CaptureDeps): DocumentCapture {
  const schedule: Schedule = deps.schedule ?? ((fn, ms) => setTimeout(fn, ms));
  const cancel: Cancel = deps.cancel ?? ((h) => clearTimeout(h as ReturnType<typeof setTimeout>));
  let running: Promise<void> | null = null;
  let stopped = false;
  let lastSent = "";
  let timer: unknown = undefined;
  let applyTimer: unknown = undefined;

  async function run(moment: Moment): Promise<void> {
    try {
      const documentId = deps.readDocumentId();
      if (!documentId) return;
      const body = buildSnapshotBody(documentId, deps.sessionId, moment, await deps.readDocument());
      const content = contentOf(body);
      if (sendsOnlyOnChange(moment) && content === lastSent) return;
      const reply = await deps.send(body);
      lastSent = content;
      if (!reply.enabled) stopped = true;
    } catch {
      /* a capture must never reach the attorney */
    }
  }

  async function capture(moment: Moment): Promise<void> {
    if (stopped) return;
    // One capture at a time, and what a capture meets when one is in flight depends on its moment.
    // pane_open and idle give way: they return at once. apply waits for it, then reads: what is in flight
    // may have read the document before the Apply went in, so the copy this Apply is owed has to be read
    // after it — never dropped, never read alongside it. finalize never queues behind one: its read has to
    // be issued before acceptAll() runs, and a capture still in flight can outlast Finalize's wait. A
    // Finalize read queued behind it would be issued after the accept and record the accepted
    // document, still labelled "finalize". Overlapping reads are harmless: they only read.
    if (moment === "apply") {
      // A loop, because another waiter or Finalize may take the slot as this one frees it; the slot is
      // taken below with no await in between. A reply that turned capture off meanwhile ends the wait too.
      while (running) await running;
      if (stopped) return;
    } else if (running && moment !== "finalize") {
      return;
    }
    const mine = run(moment);
    running = mine;
    await mine;
    // Only the latest run clears the slot: an older capture finishing after Finalize took it
    // must not free it.
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

  // Its own timer, so typing (noteChange) can neither cancel nor postpone the copy an Apply is owed — the
  // attorney who applies and then types or closes the document is the case this exists for — and an Apply
  // leaves the idle timer alone. Debounced against itself: Applies in a row make one copy.
  function noteApply(): void {
    if (stopped) return;
    try {
      if (applyTimer !== undefined) cancel(applyTimer);
      applyTimer = schedule(() => {
        applyTimer = undefined;
        void capture("apply");
      }, APPLY_SETTLE_MS);
    } catch {
      /* never let a timer break an Apply */
    }
  }

  return { capture, noteChange, noteApply };
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

/** From the Apply cards, once an edit is in the document. Nothing if the capture is not installed; never throws. */
export function noteApplied(capture: DocumentCapture | null = installed): void {
  try {
    capture?.noteApply();
  } catch {
    /* an Apply must never be undone by its own telemetry */
  }
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
