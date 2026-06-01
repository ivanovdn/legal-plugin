import { useState } from "react";
import RiskBadge from "./RiskBadge";
import type { Finding } from "../parser";
import { acceptRedline, showInDocument } from "../word";

type ActionState =
  | { kind: "idle" }
  | { kind: "running" }
  | { kind: "done"; message: string }
  | { kind: "error"; message: string };

function buildCommentBody(f: Finding): string {
  const lines = [`[${f.risk.replace("_", " ")}] ${f.clause}`];
  if (f.issue) lines.push(`Issue: ${f.issue}`);
  if (f.requiredAction) lines.push(`Required action: ${f.requiredAction}`);
  if (f.redline) lines.push(`Suggested redline: ${f.redline}`);
  if (f.owner) lines.push(`Owner: ${f.owner}`);
  return lines.join("\n");
}

export default function FindingCard({ finding }: { finding: Finding }) {
  const [comment, setComment] = useState<ActionState>({ kind: "idle" });
  const [redline, setRedline] = useState<ActionState>({ kind: "idle" });

  const onShow = async () => {
    if (comment.kind === "running") return;
    setComment({ kind: "running" });
    const res = await showInDocument(finding.currentText, buildCommentBody(finding));
    if (res.ok) setComment({ kind: "done", message: "Commented ✓" });
    else setComment({ kind: "error", message: res.error });
  };

  const onAccept = async () => {
    if (redline.kind === "running") return;
    if (!finding.redline) {
      setRedline({ kind: "error", message: "No suggested redline for this finding." });
      return;
    }
    setRedline({ kind: "running" });
    const res = await acceptRedline(finding.currentText, finding.redline);
    if (res.ok) setRedline({ kind: "done", message: "Applied ✓ — see Track Changes" });
    else setRedline({ kind: "error", message: res.error });
  };

  return (
    <div className="card">
      <div className="card-header">
        <RiskBadge risk={finding.risk} />
        {finding.issueId && <span className="issue-id">{finding.issueId}</span>}
        <div className="card-title">{finding.clause}</div>
      </div>
      {finding.issue && (
        <>
          <div className="card-section-label">Issue</div>
          <div>{finding.issue}</div>
        </>
      )}
      {finding.currentText && finding.currentText !== finding.issue && (
        <>
          <div className="card-section-label">Current text</div>
          <div className="card-quote">{finding.currentText}</div>
        </>
      )}
      {finding.requiredAction && (
        <>
          <div className="card-section-label">Required action</div>
          <div>{finding.requiredAction}</div>
        </>
      )}
      {finding.redline && (
        <>
          <div className="card-section-label">Suggested redline</div>
          <div className="card-quote card-redline">{finding.redline}</div>
        </>
      )}
      {finding.owner && (
        <div className="card-meta">
          <strong>Owner:</strong> {finding.owner}
        </div>
      )}
      {finding.externalComment && (
        <>
          <div className="card-section-label">External comment</div>
          <div className="card-rationale">{finding.externalComment}</div>
        </>
      )}

      {finding.currentText && (
        <div className="card-actions">
          <button className="secondary" onClick={onShow} disabled={comment.kind === "running"}>
            {comment.kind === "running" ? "Locating…" : "Show in document"}
          </button>
          {finding.redline && (
            <button className="secondary" onClick={onAccept} disabled={redline.kind === "running"}>
              {redline.kind === "running" ? "Applying…" : "Accept redline"}
            </button>
          )}
        </div>
      )}
      {comment.kind === "done" && <div className="card-status success">{comment.message}</div>}
      {comment.kind === "error" && <div className="card-status error">{comment.message}</div>}
      {redline.kind === "done" && <div className="card-status success">{redline.message}</div>}
      {redline.kind === "error" && <div className="card-status error">{redline.message}</div>}
    </div>
  );
}
