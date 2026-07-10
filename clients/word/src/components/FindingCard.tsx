import { useState } from "react";
import RiskBadge from "./RiskBadge";
import type { Finding } from "../parser";
import { acceptRedline, goToClause, showInDocument } from "../word";

type ActionState =
  | { kind: "idle" }
  | { kind: "running" }
  | { kind: "done"; message: string }
  | { kind: "error"; message: string }
  | { kind: "notfound"; message: string };

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
  const [jump, setJump] = useState<ActionState>({ kind: "idle" });

  const onJump = async () => {
    if (jump.kind === "running") return;
    setJump({ kind: "running" });
    const res = await goToClause(anchors);
    // Success is silent — the Word selection is the feedback. Only surface errors.
    setJump(res.ok ? { kind: "idle" } : { kind: res.notFound ? "notfound" : "error", message: res.error });
  };

  const anchors = finding.anchors.length > 0 ? finding.anchors : [finding.currentText];

  const onShow = async () => {
    if (comment.kind === "running") return;
    setComment({ kind: "running" });
    const res = await showInDocument(anchors, buildCommentBody(finding));
    if (res.ok) setComment({ kind: "done", message: "Commented ✓" });
    else setComment({ kind: res.notFound ? "notfound" : "error", message: res.error });
  };

  const onAccept = async () => {
    if (redline.kind === "running") return;
    if (!finding.redline) {
      setRedline({ kind: "error", message: "No suggested redline for this finding." });
      return;
    }
    setRedline({ kind: "running" });
    const res = await acceptRedline(anchors, finding.redline);
    if (res.ok) setRedline({ kind: "done", message: "Applied ✓ — see Track Changes" });
    else setRedline({ kind: res.notFound ? "notfound" : "error", message: res.error });
  };

  return (
    <div className="card">
      <div className="card-header">
        <RiskBadge risk={finding.risk} />
        {finding.issueId && <span className="issue-id">{finding.issueId}</span>}
        <div
          className="card-title card-title-clickable"
          role="button"
          tabIndex={0}
          title="Go to this clause in the document"
          onClick={onJump}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              onJump();
            }
          }}
        >
          {finding.clause}
        </div>
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
        <>
          <div className="card-actions">
            <button className="secondary" onClick={onShow} disabled={comment.kind === "running"}>
              {comment.kind === "running" ? "Commenting…" : "Comment in doc"}
            </button>
            {/*
              Accept redline only when the Issue cell quoted the literal current
              wording. For Missing-Context findings (placeholders, missing
              clauses), the redline is an instruction not a replacement, and
              applying it would strike out the section heading and insert
              "Insert [...]" instructional text — nonsense outcome. Lawyer fills
              the placeholder by hand instead.
            */}
            {finding.redline && finding.hasQuotedText && (
              <button className="secondary" onClick={onAccept} disabled={redline.kind === "running"}>
                {redline.kind === "running" ? "Applying…" : "Accept redline"}
              </button>
            )}
          </div>
          {finding.redline && !finding.hasQuotedText && (
            <div className="card-hint">
              No literal current wording to replace — fill the placeholder
              manually using the suggested wording above as a guide.
            </div>
          )}
        </>
      )}
      {comment.kind === "done" && <div className="card-status success">{comment.message}</div>}
      {comment.kind === "error" && <div className="card-status error">{comment.message}</div>}
      {comment.kind === "notfound" && <div className="card-status info">{comment.message}</div>}
      {redline.kind === "done" && <div className="card-status success">{redline.message}</div>}
      {redline.kind === "error" && <div className="card-status error">{redline.message}</div>}
      {redline.kind === "notfound" && <div className="card-status info">{redline.message}</div>}
      {jump.kind === "error" && <div className="card-status error">{jump.message}</div>}
      {jump.kind === "notfound" && <div className="card-status info">{jump.message}</div>}
    </div>
  );
}
