import { useEffect, useRef, useState } from "react";
import { sendFeedback, type FlagTarget } from "../feedback";

// In-pane, deliberately NOT Office.context.ui.displayDialogAsync: a dialog is a
// separate window with its own origin and message passing, and this codebase
// already records that the Mac webview is unreliable for that class of thing
// (see the window.confirm note on FinalizeBar).
type Status =
  | { kind: "idle" }
  | { kind: "sending" }
  | { kind: "sent" }
  | { kind: "error"; message: string };

const WHAT_IS_ATTACHED: Record<string, string> = {
  finding: "this finding",
  edit: "this proposed edit",
  reply: "this reply",
};

export default function FeedbackPanel({
  target,
  onClose,
}: {
  target: FlagTarget;
  onClose: () => void;
}) {
  const [comment, setComment] = useState("");
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  // Tracks the sent-state auto-close timer so it can be cancelled on
  // unmount. Without this, a completed send schedules onClose 1200ms out;
  // if the attorney then flags something else before it fires (new `key`,
  // this instance unmounts, a fresh one mounts), the stale timer still
  // calls the shared onClose and silently closes the NEW panel — and
  // whatever the attorney had started typing into it — out from under them.
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // True for as long as THIS instance is mounted. closeTimer's cleanup only
  // cancels a timer that already exists at unmount time; it does nothing
  // for a sendFeedback() still in flight at that moment (✕ clicked mid-send,
  // or a different card's flag swapping the key mid-send — nothing gates
  // other cards' flag buttons on this panel's status). The continuation
  // below resumes after unmount regardless, so it must check this itself
  // before touching state or scheduling closeTimer. Reset to true in the
  // effect body (not just the initial useRef(true)) so this stays correct
  // even if a future StrictMode double-invoke tears down and reruns the
  // effect once before the component's real unmount.
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (closeTimer.current !== null) clearTimeout(closeTimer.current);
    };
  }, []);

  const onSend = async () => {
    if (!comment.trim() || status.kind === "sending") return;
    setStatus({ kind: "sending" });
    try {
      await sendFeedback(target, comment.trim());
      if (!mounted.current) return; // unmounted while the request was in flight
      setStatus({ kind: "sent" });
      closeTimer.current = setTimeout(onClose, 1200);
    } catch (e) {
      if (!mounted.current) return; // unmounted while the request was in flight
      setStatus({ kind: "error", message: e instanceof Error ? e.message : String(e) });
    }
  };

  const item = WHAT_IS_ATTACHED[target.targetKind] ?? "this document";

  return (
    <div className="feedback-panel">
      <div className="feedback-header">
        <strong>What went wrong?</strong>
        <button className="link" onClick={onClose} aria-label="Close feedback">
          ✕
        </button>
      </div>

      <textarea
        className="feedback-input"
        rows={4}
        autoFocus
        value={comment}
        onChange={(e) => setComment(e.target.value)}
        placeholder={
          target.targetKind
            ? "e.g. this filled the counterparty's name into our signature block"
            : "e.g. it never flagged the assignment clause"
        }
        disabled={status.kind === "sending" || status.kind === "sent"}
      />

      {/* The attorney must never send something they didn't know they sent. */}
      <div className="feedback-attached">
        Sends your note plus {item}, the document text, and this turn's id so the
        developer can reproduce it.
      </div>

      <div className="feedback-actions">
        <button
          className="primary"
          onClick={onSend}
          disabled={!comment.trim() || status.kind === "sending" || status.kind === "sent"}
        >
          {status.kind === "sending" ? "Sending…" : status.kind === "sent" ? "Sent ✓" : "Send"}
        </button>
        <button className="secondary" onClick={onClose} disabled={status.kind === "sending"}>
          Cancel
        </button>
      </div>

      {status.kind === "error" && (
        <div className="status error">Didn't send: {status.message}</div>
      )}
    </div>
  );
}
