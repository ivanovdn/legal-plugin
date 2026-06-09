import { useState } from "react";
import { submitReview } from "../api";
import {
  parseContractReview,
  type Blocker,
  type BusinessQuestion,
  type NoSignatureGate,
  type ReviewSummary,
} from "../parser";
import { readBody } from "../word";
import FindingCard from "./FindingCard";

type Status =
  | { kind: "idle" }
  | { kind: "reading" }
  | { kind: "reviewing"; charCount: number }
  | { kind: "error"; message: string };

interface Props {
  sessionId: string;
  result: ReviewSummary | null;
  setResult: React.Dispatch<React.SetStateAction<ReviewSummary | null>>;
}

export default function FindingsTab({ sessionId, result, setResult }: Props) {
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [rawResponse, setRawResponse] = useState<string>("");

  const onReview = async () => {
    try {
      setStatus({ kind: "reading" });
      const text = await readBody();
      if (!text || text.trim().length < 20) {
        setStatus({ kind: "error", message: "Document is empty or too short to review." });
        return;
      }
      setStatus({ kind: "reviewing", charCount: text.length });
      const res = await submitReview(text, sessionId);
      if (res.status === "error") {
        setStatus({ kind: "error", message: (res.errors ?? ["unknown error"])[0] });
        return;
      }
      const reportText =
        res.data?.report?.response ?? res.data?.interrupt_payload?.llm_response ?? "";
      if (!reportText) {
        setStatus({ kind: "error", message: "Backend returned no review text." });
        return;
      }
      setRawResponse(reportText);
      const parsed = parseContractReview(reportText);
      // Surface contract_type_detected from the backend (Phase 4) into the
      // header so the lawyer sees what bundle was applied — overrides any
      // value the LLM emitted inside Review Summary.
      const detected = res.data?.report?.contract_type_detected;
      if (detected && !parsed.header.contractType) {
        parsed.header.contractType = detected.toUpperCase();
        parsed.contractType = detected.toUpperCase();
      }
      setResult(parsed);
      setStatus({ kind: "idle" });
    } catch (e) {
      setStatus({ kind: "error", message: e instanceof Error ? e.message : String(e) });
    }
  };

  const busy = status.kind === "reading" || status.kind === "reviewing";

  return (
    <div className="tab-content">
      <button className="primary" onClick={onReview} disabled={busy}>
        {busy ? "Reviewing…" : result ? "Re-review this contract" : "Review this contract"}
      </button>

      {status.kind === "reading" && <div className="status">Reading document…</div>}
      {status.kind === "reviewing" && (
        <div className="status">
          Sending {status.charCount.toLocaleString()} characters to the backend… this may take 30–60
          seconds.
        </div>
      )}
      {status.kind === "error" && <div className="status error">Error: {status.message}</div>}

      {result && (
        <div className="findings-scroll">
          <Results result={result} />
          {rawResponse && <RawResponse markdown={rawResponse} />}
        </div>
      )}
    </div>
  );
}

function RawResponse({ markdown }: { markdown: string }) {
  return (
    <details className="raw-response">
      <summary>Show raw LLM response ({markdown.length.toLocaleString()} chars)</summary>
      <pre>{markdown}</pre>
    </details>
  );
}

function GateBanner({ gate }: { gate: NoSignatureGate }) {
  if (!gate.overallStatus && !gate.finalRecommendation) return null;
  const cls = gate.ready ? "gate-banner ready" : "gate-banner blocked";
  return (
    <div className={cls}>
      <div className="gate-status">
        {gate.ready ? "Signature may proceed" : "DO NOT SEND FOR SIGNATURE"}
      </div>
      {gate.blockingItems && (
        <div className="gate-line">
          <strong>Blocking items:</strong> {gate.blockingItems}
        </div>
      )}
      {gate.missingContext && (
        <div className="gate-line">
          <strong>Missing context:</strong> {gate.missingContext}
        </div>
      )}
      {gate.finalRecommendation && !gate.ready && (
        <div className="gate-line">{gate.finalRecommendation}</div>
      )}
    </div>
  );
}

function BlockerList({ blockers }: { blockers: Blocker[] }) {
  if (blockers.length === 0) return null;
  return (
    <div className="card">
      <div className="card-header">
        <span className="badge red">BLOCKERS ({blockers.length})</span>
        <div className="card-title">Red and Missing Context</div>
      </div>
      <ul className="blocker-list">
        {blockers.map((b, i) => (
          <li key={i}>
            <div className="blocker-head">
              <span className={`badge ${b.type.toLowerCase().includes("missing") ? "missing_context" : "red"}`}>
                {b.type || "Red"}
              </span>
              {b.issueId && <span className="issue-id">{b.issueId}</span>}
              <span className="clause-name">{b.clause}</span>
            </div>
            {b.whyItBlocks && <div className="blocker-why">{b.whyItBlocks}</div>}
            <div className="blocker-meta">
              {b.requiredAction && (
                <span>
                  <strong>Action:</strong> {b.requiredAction}
                </span>
              )}
              {b.approverOwner && (
                <span>
                  <strong>Approver:</strong> {b.approverOwner}
                </span>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function BusinessQuestions({ questions }: { questions: BusinessQuestion[] }) {
  const [open, setOpen] = useState(false);
  if (questions.length === 0) return null;
  return (
    <div className="card">
      <button className="collapsible-header" onClick={() => setOpen((v) => !v)}>
        <span className="badge yellow">QUESTIONS ({questions.length})</span>
        <div className="card-title">Business Questions</div>
        <span className="caret">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <ul className="question-list">
          {questions.map((q, i) => (
            <li key={i}>
              <div className="question-text">{q.question}</div>
              {q.whyItMatters && <div className="question-why">{q.whyItMatters}</div>}
              {q.owner && <div className="question-owner">Owner: {q.owner}</div>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Results({ result }: { result: ReviewSummary }) {
  const { findings, blockers, businessQuestions, gate, counts, header } = result;

  return (
    <>
      <GateBanner gate={gate} />

      <div className="summary">
        {header.contractType && (
          <span>
            <strong>Type:</strong> {header.contractType}
          </span>
        )}
        {header.trinetixRole && (
          <span>
            <strong>Role:</strong> {header.trinetixRole}
          </span>
        )}
        {header.counterparty && (
          <span>
            <strong>Counterparty:</strong> {header.counterparty}
          </span>
        )}
        {header.overallStatus && (
          <span>
            <strong>Status:</strong> {header.overallStatus}
          </span>
        )}
        <span className="badge red">{counts.red} RED</span>
        <span className="badge yellow">{counts.yellow} YELLOW</span>
        <span className="badge green">{counts.green} GREEN</span>
        {counts.missingContext > 0 && (
          <span className="badge missing_context">{counts.missingContext} MISSING</span>
        )}
      </div>

      <BlockerList blockers={blockers} />

      {findings.length === 0 && <div className="status">No clause findings parsed from the response.</div>}

      <div className="findings">
        {findings.map((f, i) => (
          <FindingCard key={i} finding={f} />
        ))}
      </div>

      <BusinessQuestions questions={businessQuestions} />
    </>
  );
}
