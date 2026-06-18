import { useState } from "react";
import { finalizeDocument } from "../word";

// Document-level "produce a clean copy" action. Accepting tracked changes is
// destructive and not limited to the assistant's edits, so it's gated behind
// an explicit in-pane confirm (window.confirm is unreliable in Word for Mac's
// webview). After finalizing, the user uses Word's File → Save As to name the
// clean deliverable — Office.js can't reliably save-as a new file cross-OS.
type Status =
  | { kind: "idle" }
  | { kind: "confirming" }
  | { kind: "running" }
  | { kind: "done"; count: number }
  | { kind: "error"; message: string };

export default function FinalizeBar() {
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  const onFinalize = async () => {
    setStatus({ kind: "running" });
    const res = await finalizeDocument();
    if (res.ok) setStatus({ kind: "done", count: res.value });
    else setStatus({ kind: "error", message: res.error });
  };

  return (
    <div className="finalize-bar">
      {status.kind === "idle" && (
        <div className="finalize-row">
          <div className="finalize-label">
            Done reviewing?
            <span className="finalize-sub">
              Accept all tracked changes and turn Track Changes off.
            </span>
          </div>
          <button className="secondary" onClick={() => setStatus({ kind: "confirming" })}>
            Finalize → clean copy
          </button>
        </div>
      )}

      {status.kind === "confirming" && (
        <div className="finalize-confirm">
          <div className="finalize-warning">
            This accepts <strong>all</strong> tracked changes in the document
            (not only the assistant's) and turns Track Changes off. Save a copy
            first if you want to keep the redlined version.
          </div>
          <div className="finalize-actions">
            <button className="primary" onClick={onFinalize}>
              Accept all &amp; finalize
            </button>
            <button className="secondary" onClick={() => setStatus({ kind: "idle" })}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {status.kind === "running" && <div className="finalize-status">Finalizing…</div>}

      {status.kind === "done" && (
        <div className="finalize-status success">
          {status.count > 0
            ? `Finalized — ${status.count} change${status.count === 1 ? "" : "s"} accepted, Track Changes off.`
            : "Document was already clean. Track Changes off."}
          <span className="finalize-sub">
            Use Word's File → Save As to save your clean copy.
          </span>
          <button className="link-button" onClick={() => setStatus({ kind: "idle" })}>
            Done
          </button>
        </div>
      )}

      {status.kind === "error" && (
        <div className="finalize-status error">
          {status.message}
          <button className="link-button" onClick={() => setStatus({ kind: "idle" })}>
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}
