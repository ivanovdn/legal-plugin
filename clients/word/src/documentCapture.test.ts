// SP4 document capture. Run with: npx tsx src/documentCapture.test.ts
import {
  buildSnapshotBody,
  captureBeforeFinalize,
  createDocumentCapture,
  IDLE_MS,
  TRACKED_CAP,
  TRACKED_TEXT_CAP,
  withinTime,
  type DocumentReading,
  type SnapshotBody,
  type SnapshotReply,
} from "./documentCapture";
import { pass } from "./testAssert";

const READING: DocumentReading = {
  current: "NDA with Acme Ltd",
  original: "NDA with [__]",
  tracked: [{ type: "Added", author: "Jane Smith", date: "2026-10-16T09:00:00.000Z", text: "Acme Ltd" }],
};

/** Timers the test fires by hand. */
function fakeTimers() {
  const pending = new Map<number, () => void>();
  let next = 0;
  return {
    schedule: (fn: () => void) => {
      pending.set(++next, fn);
      return next;
    },
    cancel: (handle: unknown) => {
      pending.delete(handle as number);
    },
    fire: () => {
      const fns = [...pending.values()];
      pending.clear();
      fns.forEach((fn) => fn());
    },
    count: () => pending.size,
  };
}

function harness(opts: {
  id?: string;
  read?: () => Promise<DocumentReading>;
  reply?: () => Promise<SnapshotReply>;
} = {}) {
  const sent: SnapshotBody[] = [];
  let reads = 0;
  const timers = fakeTimers();
  const capture = createDocumentCapture({
    readDocumentId: () => opts.id ?? "d-1",
    readDocument: () => {
      reads++;
      return opts.read ? opts.read() : Promise.resolve(READING);
    },
    send: (body) => {
      sent.push(body);
      return opts.reply ? opts.reply() : Promise.resolve({ recorded: 1, enabled: true });
    },
    sessionId: "s-1",
    schedule: timers.schedule,
    cancel: timers.cancel,
  });
  return { capture, sent, reads: () => reads, timers };
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

{
  const h = harness();
  await h.capture.capture("pane_open");
  const b = h.sent[0];
  pass(
    h.sent.length === 1 && b.moment === "pane_open" && b.session_id === "s-1" && b.document_id === "d-1" &&
      b.current_text === READING.current && b.original_text === READING.original &&
      b.tracked_changes.length === 1 && b.tracked_total === 1,
    "a capture sends both views, the tracked changes, the moment, the session and the document",
  );
}

{
  const h = harness({ id: "" });
  await h.capture.capture("pane_open");
  pass(h.reads() === 0 && h.sent.length === 0, "a document the assistant has no id for is neither read nor sent");
}

{
  let reading = READING;
  const h = harness({ read: () => Promise.resolve(reading) });
  await h.capture.capture("pane_open");
  await h.capture.capture("idle");
  pass(h.sent.length === 1, "an idle capture of an unchanged document sends nothing");
  reading = { ...READING, current: "NDA with Acme Limited" };
  await h.capture.capture("idle");
  pass(h.sent.length === 2 && h.sent[1].current_text === "NDA with Acme Limited",
    "an idle capture of a changed document sends it");
  await h.capture.capture("finalize");
  pass(h.sent.length === 3 && h.sent[2].moment === "finalize", "a finalize capture sends even when nothing changed");
}

{
  let release: (r: DocumentReading) => void = () => {};
  const h = harness({ read: () => new Promise<DocumentReading>((resolve) => { release = resolve; }) });
  const first = h.capture.capture("idle");
  const second = h.capture.capture("idle");
  release(READING);
  await first;
  await second;
  pass(h.reads() === 1 && h.sent.length === 1, "a capture asked for while one is reading is skipped");
}

{
  let release: (r: DocumentReading) => void = () => {};
  let calls = 0;
  const h = harness({
    read: () =>
      ++calls === 1
        ? new Promise<DocumentReading>((resolve) => { release = resolve; })
        : Promise.resolve({ ...READING, current: "finalized" }),
  });
  const idle = h.capture.capture("idle");
  const fin = h.capture.capture("finalize");
  release(READING);
  await idle;
  await fin;
  pass(h.sent.map((b) => b.moment).join(",") === "idle,finalize",
    "Finalize waits for a capture already reading, then sends its own");
}

{
  const h = harness({ reply: () => Promise.resolve({ recorded: 0, enabled: false }) });
  await h.capture.capture("pane_open");
  await h.capture.capture("finalize");
  h.capture.noteChange();
  pass(h.sent.length === 1 && h.timers.count() === 0, "a server that says capture is off stops every later capture");
}

{
  let failing = true;
  const h = harness({
    read: () => (failing ? Promise.reject(new Error("Word busy")) : Promise.resolve(READING)),
  });
  let threw = false;
  try {
    await h.capture.capture("pane_open");
  } catch {
    threw = true;
  }
  failing = false;
  await h.capture.capture("pane_open");
  pass(!threw && h.sent.length === 1, "a failed read is swallowed and the next capture still works");
}

{
  let down = true;
  const h = harness({
    reply: () => (down ? Promise.reject(new Error("offline")) : Promise.resolve({ recorded: 1, enabled: true })),
  });
  await h.capture.capture("idle");
  down = false;
  await h.capture.capture("idle");
  pass(h.sent.length === 2, "a capture the server never got is sent again at the next pause");
}

{
  const h = harness();
  h.capture.noteChange();
  h.capture.noteChange();
  h.capture.noteChange();
  const waiting = h.timers.count();
  h.timers.fire();
  await tick();
  pass(waiting === 1 && h.sent.length === 1 && h.sent[0].moment === "idle",
    `changes within ${IDLE_MS / 1000} s of each other make one idle capture`);
}

{
  const tracked = [
    { type: "Added", author: "A", date: "", text: "x".repeat(TRACKED_TEXT_CAP + 1) },
    ...Array.from({ length: TRACKED_CAP }, () => ({ type: "Deleted", author: "B", date: "", text: "y" })),
  ];
  const b = buildSnapshotBody("d-1", "s-1", "idle", { ...READING, tracked });
  pass(
    b.tracked_changes.length === TRACKED_CAP && b.tracked_total === TRACKED_CAP + 1 &&
      b.tracked_changes[0].text.length === TRACKED_TEXT_CAP && b.tracked_changes[0].cut === true &&
      b.tracked_changes[1].cut === undefined,
    "tracked changes are capped at 500, 2,000 characters each, keeping the count from before the cap",
  );
}

{
  const h = harness({ read: () => Promise.resolve({ ...READING, tracked: null }) });
  await h.capture.capture("pane_open");
  const b = h.sent[0];
  pass(b.tracked_total === -1 && b.tracked_changes.length === 0 && b.current_text === READING.current,
    "a Word that cannot read tracked changes still sends both texts, with tracked_total -1");
}

{
  const timers = fakeTimers();
  let done = false;
  const p = withinTime(new Promise<void>(() => {}), 3000, timers.schedule).then(() => { done = true; });
  timers.fire();
  await p;
  pass(done, "withinTime gives up on work that never settles when its time is up");
  let settled = false;
  await withinTime(Promise.reject(new Error("boom")), 3000, fakeTimers().schedule).then(() => {
    settled = true;
  });
  pass(settled, "withinTime resolves, never rejects, when the work fails");
}

{
  const timers = fakeTimers();
  const h = harness({ read: () => new Promise<DocumentReading>(() => {}) });
  let through = false;
  const p = captureBeforeFinalize(h.capture, 3000, timers.schedule).then(() => { through = true; });
  timers.fire();
  await p;
  pass(through, "Finalize goes ahead when its capture hangs");
}
