// Parses the contract_review skill's free-form output into structured findings.
// Format is defined in skills/contract_review/SKILL.md:
//
//   CLAUSE: [name]
//   RISK: GREEN | YELLOW | RED
//   ISSUE: [...]
//   CURRENT TEXT: "[...]"
//   SUGGESTED REDLINE: "[...]"     (YELLOW/RED only)
//   RATIONALE: [...]
//
// Trailing blocks: MISSING CLAUSES, SUMMARY.

export type Risk = "RED" | "YELLOW" | "GREEN";

export interface Finding {
  clause: string;
  risk: Risk;
  issue: string;
  currentText: string;
  redline: string;
  rationale: string;
}

export interface ReviewSummary {
  findings: Finding[];
  missing: string[];
  counts: { red: number; yellow: number; green: number };
  overall?: string;
  contractType?: string;
  reviewingAs?: string;
}

const RISK_ORDER: Record<Risk, number> = { RED: 0, YELLOW: 1, GREEN: 2 };

const stripQuotes = (s: string): string => s.trim().replace(/^"|"$/g, "");

const extractField = (block: string, label: string): string => {
  const re = new RegExp(`${label}\\s*:\\s*(.*?)(?=\\n[A-Z][A-Z\\s]+:|\\n*$)`, "is");
  const m = block.match(re);
  return m ? m[1].trim() : "";
};

const isRisk = (s: string): s is Risk => s === "RED" || s === "YELLOW" || s === "GREEN";

export function parseContractReview(markdown: string): ReviewSummary {
  const findings: Finding[] = [];

  // Header fields (optional)
  const contractType = markdown.match(/Contract type:\s*(.+)/i)?.[1].trim();
  const reviewingAs = markdown.match(/Reviewing as:\s*(.+)/i)?.[1].trim();

  // Cut off the trailing sections so they don't get parsed as findings
  const missingIdx = markdown.search(/^MISSING CLAUSES:/im);
  const summaryIdx = markdown.search(/^SUMMARY:/im);
  const findingsEnd = [missingIdx, summaryIdx].filter((i) => i >= 0).sort((a, b) => a - b)[0] ?? markdown.length;
  const findingsBlock = markdown.slice(0, findingsEnd);

  // Split into per-clause blocks
  const parts = findingsBlock.split(/^CLAUSE:\s*/m).slice(1);
  for (const part of parts) {
    const clauseLineEnd = part.indexOf("\n");
    const clause = (clauseLineEnd === -1 ? part : part.slice(0, clauseLineEnd)).trim();
    const body = clauseLineEnd === -1 ? "" : part.slice(clauseLineEnd + 1);

    const riskRaw = extractField(body, "RISK").toUpperCase();
    if (!isRisk(riskRaw)) continue;

    findings.push({
      clause,
      risk: riskRaw,
      issue: extractField(body, "ISSUE"),
      currentText: stripQuotes(extractField(body, "CURRENT TEXT")),
      redline: stripQuotes(extractField(body, "SUGGESTED REDLINE")),
      rationale: extractField(body, "RATIONALE"),
    });
  }

  findings.sort((a, b) => RISK_ORDER[a.risk] - RISK_ORDER[b.risk]);

  // Missing clauses
  const missing: string[] = [];
  if (missingIdx >= 0) {
    const missingBlock = markdown.slice(missingIdx, summaryIdx >= 0 ? summaryIdx : markdown.length);
    for (const line of missingBlock.split("\n").slice(1)) {
      const m = line.match(/^\s*-\s*(.+)/);
      if (m) missing.push(m[1].trim());
    }
  }

  const counts = {
    red: findings.filter((f) => f.risk === "RED").length,
    yellow: findings.filter((f) => f.risk === "YELLOW").length,
    green: findings.filter((f) => f.risk === "GREEN").length,
  };

  let overall: string | undefined;
  if (summaryIdx >= 0) {
    const summaryBlock = markdown.slice(summaryIdx);
    overall = summaryBlock.match(/Overall risk:\s*(.+)/i)?.[1].trim();
  }

  return { findings, missing, counts, overall, contractType, reviewingAs };
}
