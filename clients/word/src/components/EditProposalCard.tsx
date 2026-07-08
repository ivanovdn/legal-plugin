import { useState } from "react";
import type { EditProposal } from "../parseEditBlocks";
import { applyEdit, goToClause } from "../word";

type Status =
  | { kind: "idle" }
  | { kind: "running" }
  | { kind: "applied" }
  | { kind: "discarded" }
  | { kind: "error"; message: string };

const ACTION_LABEL: Record<EditProposal["action"], string> = {
  replace: "REWRITE",
  replace_all: "REPLACE ALL",
  insert: "INSERT",
  delete: "DELETE",
};

const ACTION_CLASS: Record<EditProposal["action"], string> = {
  replace: "edit-action-replace",
  replace_all: "edit-action-replace",
  insert: "edit-action-insert",
  delete: "edit-action-delete",
};

export default function EditProposalCard({ proposal }: { proposal: EditProposal }) {
  // The lawyer can tweak new_text before applying — e.g. "2x" → "3x".
  const [draftText, setDraftText] = useState(proposal.new_text ?? "");
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [jumpError, setJumpError] = useState<string | null>(null);

  // For inserts, the doc location is the anchor; for replace/replace_all/delete
  // it's the target text.
  const jumpTarget = proposal.action === "insert" ? proposal.anchor_text : proposal.target_text;

  const onJump = async () => {
    if (!jumpTarget) return;
    setJumpError(null);
    const res = await goToClause(jumpTarget);
    if (!res.ok) setJumpError(res.error);
  };

  const onApply = async () => {
    if (status.kind === "running" || status.kind === "applied") return;
    setStatus({ kind: "running" });
    const effective: EditProposal =
      proposal.action === "delete"
        ? proposal
        : { ...proposal, new_text: draftText };
    const res = await applyEdit(effective);
    if (res.ok) setStatus({ kind: "applied" });
    else setStatus({ kind: "error", message: res.error });
  };

  const onDiscard = () => {
    if (status.kind === "running") return;
    setStatus({ kind: "discarded" });
  };

  if (status.kind === "discarded") return null;

  const showBefore =
    proposal.action === "replace" ||
    proposal.action === "replace_all" ||
    proposal.action === "delete";
  const showAfter =
    proposal.action === "replace" ||
    proposal.action === "replace_all" ||
    proposal.action === "insert";
  const showAnchor = proposal.action === "insert";

  return (
    <div className="edit-card">
      <div className="edit-card-header">
        <span className={`edit-action ${ACTION_CLASS[proposal.action]}`}>
          {ACTION_LABEL[proposal.action]}
        </span>
        <span className="edit-card-title">Proposed edit</span>
      </div>

      {showAnchor && proposal.anchor_text && (
        <>
          <div className="card-section-label">
            Anchor ({proposal.position === "before" ? "insert before" : "insert after"})
          </div>
          <div className="card-quote">{proposal.anchor_text}</div>
        </>
      )}

      {showBefore && proposal.target_text && (
        <>
          <div className="card-section-label">
            {proposal.action === "replace_all" ? "Find (every occurrence)" : "Before"}
          </div>
          <div className="card-quote">{proposal.target_text}</div>
        </>
      )}

      {showAfter && (
        <>
          <div className="card-section-label">
            After {proposal.action === "replace" ? "(editable)" : "(editable, new text)"}
          </div>
          <textarea
            className="edit-textarea"
            value={draftText}
            onChange={(e) => setDraftText(e.target.value)}
            rows={Math.min(8, Math.max(2, draftText.split("\n").length))}
            disabled={status.kind === "running" || status.kind === "applied"}
          />
        </>
      )}

      {proposal.rationale && (
        <>
          <div className="card-section-label">Rationale</div>
          <div className="card-rationale">{proposal.rationale}</div>
        </>
      )}

      <div className="card-actions">
        <button
          className="primary"
          onClick={onApply}
          disabled={status.kind === "running" || status.kind === "applied"}
        >
          {status.kind === "running"
            ? "Applying…"
            : status.kind === "applied"
            ? "Applied ✓"
            : "Apply with Track Changes"}
        </button>
        {jumpTarget && (
          <button className="secondary" onClick={onJump} disabled={status.kind === "running"}>
            Go to
          </button>
        )}
        {status.kind !== "applied" && (
          <button
            className="secondary"
            onClick={onDiscard}
            disabled={status.kind === "running"}
          >
            Discard
          </button>
        )}
      </div>

      {status.kind === "applied" && (
        <>
          <div className="card-status success">Applied as a tracked change ✓</div>
          <div className="card-hint">
            Not final yet — it stays in the document (including after you Save)
            until you Accept or Reject it in Word's Review tab.
          </div>
        </>
      )}
      {status.kind === "error" && (
        <div className="card-status error">{status.message}</div>
      )}
      {jumpError && <div className="card-status error">{jumpError}</div>}
    </div>
  );
}
