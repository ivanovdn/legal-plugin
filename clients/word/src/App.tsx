import { useState } from "react";
import { submitReview } from "./api";
import { parseContractReview, type ReviewSummary } from "./parser";
import FindingCard from "./components/FindingCard";

type Status =
  | { kind: "idle" }
  | { kind: "reading" }
  | { kind: "reviewing"; charCount: number }
  | { kind: "ready"; result: ReviewSummary }
  | { kind: "error"; message: string };

async function readDocumentBody(): Promise<string> {
  if (typeof Word === "undefined") {
    // Browser dev preview — return a stub so the UI still renders.
    return "Open this add-in inside Word to read the active document.";
  }
  return Word.run(async (context) => {
    const body = context.document.body;
    body.load("text");
    await context.sync();
    return body.text;
  });
}

export default function App() {
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  const onReview = async () => {
    try {
      setStatus({ kind: "reading" });
      const text = await readDocumentBody();
      if (!text || text.trim().length < 20) {
        setStatus({ kind: "error", message: "Document is empty or too short to review." });
        return;
      }
      setStatus({ kind: "reviewing", charCount: text.length });
      const res = await submitReview(text);
      if (res.status === "error") {
        setStatus({ kind: "error", message: (res.errors ?? ["unknown error"])[0] });
        return;
      }
      const reportText = res.data?.report?.response ?? res.data?.interrupt_payload?.llm_response ?? "";
      if (!reportText) {
        setStatus({ kind: "error", message: "Backend returned no review text." });
        return;
      }
      const parsed = parseContractReview(reportText);
      setStatus({ kind: "ready", result: parsed });
    } catch (e) {
      setStatus({ kind: "error", message: e instanceof Error ? e.message : String(e) });
    }
  };

  const busy = status.kind === "reading" || status.kind === "reviewing";

  return (
    <div className="app">
      <h1>Legal Triage</h1>
      <p className="subtitle">Reviews the open document against the firm's standards.</p>

      <button className="primary" onClick={onReview} disabled={busy}>
        {busy ? "Reviewing…" : "Review this contract"}
      </button>

      {status.kind === "reading" && <div className="status">Reading document…</div>}
      {status.kind === "reviewing" && (
        <div className="status">
          Sending {status.charCount.toLocaleString()} characters to the backend… this may take 30–60 seconds.
        </div>
      )}
      {status.kind === "error" && <div className="status error">Error: {status.message}</div>}

      {status.kind === "ready" && <Results result={status.result} />}
    </div>
  );
}

function Results({ result }: { result: ReviewSummary }) {
  const { findings, missing, counts, overall, contractType, reviewingAs } = result;

  return (
    <>
      <div className="summary">
        {contractType && (
          <span>
            <strong>Type:</strong> {contractType}
          </span>
        )}
        {reviewingAs && (
          <span>
            <strong>Side:</strong> {reviewingAs}
          </span>
        )}
        {overall && (
          <span>
            <strong>Overall:</strong> {overall}
          </span>
        )}
        <span className="badge red">{counts.red} RED</span>
        <span className="badge yellow">{counts.yellow} YELLOW</span>
        <span className="badge green">{counts.green} GREEN</span>
      </div>

      {findings.length === 0 && <div className="status">No clause findings parsed from the response.</div>}

      <div className="findings">
        {findings.map((f, i) => (
          <FindingCard key={i} finding={f} />
        ))}
        {missing.length > 0 && (
          <div className="card">
            <div className="card-header">
              <span className="badge yellow">MISSING</span>
              <div className="card-title">Missing clauses ({missing.length})</div>
            </div>
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {missing.map((m, i) => (
                <li key={i}>{m}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </>
  );
}
