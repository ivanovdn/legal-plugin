import RiskBadge from "./RiskBadge";
import type { Finding } from "../parser";

export default function FindingCard({ finding }: { finding: Finding }) {
  return (
    <div className="card">
      <div className="card-header">
        <RiskBadge risk={finding.risk} />
        <div className="card-title">{finding.clause}</div>
      </div>
      {finding.issue && (
        <>
          <div className="card-section-label">Issue</div>
          <div>{finding.issue}</div>
        </>
      )}
      {finding.currentText && (
        <>
          <div className="card-section-label">Current text</div>
          <div className="card-quote">{finding.currentText}</div>
        </>
      )}
      {finding.redline && (
        <>
          <div className="card-section-label">Suggested redline</div>
          <div className="card-quote card-redline">{finding.redline}</div>
        </>
      )}
      {finding.rationale && (
        <>
          <div className="card-section-label">Rationale</div>
          <div className="card-rationale">{finding.rationale}</div>
        </>
      )}
    </div>
  );
}
