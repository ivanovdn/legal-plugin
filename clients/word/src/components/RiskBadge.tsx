import type { Risk } from "../parser";

const LABEL: Record<Risk, string> = {
  RED: "RED",
  YELLOW: "YELLOW",
  GREEN: "GREEN",
  MISSING_CONTEXT: "MISSING",
};

export default function RiskBadge({ risk }: { risk: Risk }) {
  return <span className={`badge ${risk.toLowerCase()}`}>{LABEL[risk]}</span>;
}
