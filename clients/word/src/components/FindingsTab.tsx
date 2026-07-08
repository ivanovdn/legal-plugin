import { useEffect, useState } from "react";
import { submitReview } from "../api";
import {
  parseContractReview,
  type Blocker,
  type BusinessQuestion,
  type NoSignatureGate,
  type ReviewSummary,
} from "../parser";
import type { Risk } from "../parser";
import { applyFindingFilters, ALL_RISKS, ownerKey, type FindingFilters } from "../findingFilters";
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
  const [persistError, setPersistError] = useState<string | null>(null);

  const onReview = async () => {
    setPersistError(null);
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
      const rpe = res.data?.report?.review_persist_error;
      if (rpe) setPersistError(rpe);
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
      {persistError && (
        <div className="status error">
          ⚠ This review could not be saved ({persistError}) — it won't be recalled in chat. Re-run the review.
        </div>
      )}

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

const RISK_LABEL: Record<Risk, string> = {
  RED: "RED",
  MISSING_CONTEXT: "MISSING",
  YELLOW: "YELLOW",
  GREEN: "GREEN",
};

function FilterBar({
  filters,
  setFilters,
  owners,
  shown,
  total,
}: {
  filters: FindingFilters;
  setFilters: React.Dispatch<React.SetStateAction<FindingFilters>>;
  owners: string[];
  shown: number;
  total: number;
}) {
  const toggleSeverity = (r: Risk) =>
    setFilters((f) => {
      const severities = new Set(f.severities);
      severities.has(r) ? severities.delete(r) : severities.add(r);
      return { ...f, severities };
    });

  return (
    <div className="filter-bar">
      {ALL_RISKS.map((r) => (
        <button
          key={r}
          className={`filter-chip ${filters.severities.has(r) ? "active" : ""}`}
          onClick={() => toggleSeverity(r)}
        >
          {RISK_LABEL[r]}
        </button>
      ))}
      <button
        className="filter-chip"
        onClick={() => setFilters((f) => ({ ...f, severities: new Set<Risk>(["RED", "MISSING_CONTEXT"]) }))}
      >
        Blockers only
      </button>
      <button
        className="filter-chip"
        onClick={() => setFilters((f) => ({ ...f, severities: new Set<Risk>(ALL_RISKS) }))}
      >
        All
      </button>
      {owners.some((o) => o !== "Unassigned") && (
        <select
          value={filters.owner}
          onChange={(e) => setFilters((f) => ({ ...f, owner: e.target.value }))}
        >
          <option value="all">All owners</option>
          {owners.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      )}
      <select
        value={filters.sortBy}
        onChange={(e) => setFilters((f) => ({ ...f, sortBy: e.target.value as FindingFilters["sortBy"] }))}
      >
        <option value="severity">Sort: severity</option>
        <option value="clause">Sort: clause name</option>
      </select>
      <span className="filter-count">
        showing {shown} of {total}
      </span>
    </div>
  );
}

function Results({ result }: { result: ReviewSummary }) {
  const { findings, blockers, businessQuestions, gate, counts, header } = result;

  const [filters, setFilters] = useState<FindingFilters>({
    severities: new Set<Risk>(ALL_RISKS),
    owner: "all",
    sortBy: "severity",
  });

  // Reset filters whenever a new review arrives, so a stale filter can't hide
  // fresh findings. `result` is a new object on every parse.
  useEffect(() => {
    setFilters({ severities: new Set<Risk>(ALL_RISKS), owner: "all", sortBy: "severity" });
  }, [result]);

  const owners = Array.from(new Set(findings.map(ownerKey))).sort();
  const visible = applyFindingFilters(findings, filters);
  const keyOf = new Map(findings.map((f, i) => [f, `${f.issueId}-${f.clause}-${i}`]));

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

      {findings.length === 0 && (
        <div className="status">No clause findings parsed from the response.</div>
      )}

      {findings.length > 0 && (
        <FilterBar
          filters={filters}
          setFilters={setFilters}
          owners={owners}
          shown={visible.length}
          total={findings.length}
        />
      )}

      {findings.length > 0 && visible.length === 0 && (
        <div className="status">No findings match the current filters.</div>
      )}

      <div className="findings">
        {visible.map((f) => (
          <FindingCard key={keyOf.get(f)} finding={f} />
        ))}
      </div>

      <BusinessQuestions questions={businessQuestions} />
    </>
  );
}
