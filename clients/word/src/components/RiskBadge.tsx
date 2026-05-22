import type { Risk } from "../parser";

export default function RiskBadge({ risk }: { risk: Risk }) {
  return <span className={`badge ${risk.toLowerCase()}`}>{risk}</span>;
}
