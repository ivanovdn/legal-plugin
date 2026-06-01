// Parses the Trinetix contract-review output (team's required table format) into
// structured data the Findings tab renders. The format is defined in
// shared_operating_rules.md "Required final output format" and assembled into
// skills/contract_review/playbook/global/output_format.md.
//
// Expected H1 sections (the LLM may use # or ## — we match either):
//   Review Summary                 — key/value block (Overall status, Contract type, ...)
//   Key Findings                   — GFM table  | Issue ID | Clause | Rating | Issue | Required action | Owner |
//   Red and Missing Context Items  — GFM table  | Issue ID | Type | Clause | Why | Required action | Approver |
//   Approved Deviations            — GFM table  (rendered as info, no actions)
//   Suggested Redlines / Fallbacks — GFM table  | Clause | Action | Proposed wording | External comment |
//   Business Questions             — GFM table  | Question | Why it matters | Owner |
//   No Signature Checklist Result  — key/value block (Overall status, Blocking items, ...)
//
// "Current text" for Show-in-document / Accept-redline buttons is extracted
// from quotes inside the "Issue" cell (the AI Review Procedure §10.2 schema
// has "Current wording / issue:" so the model emits the quote naturally).
// Falls back to the Issue cell verbatim, then to the Clause name.

export type Risk = "RED" | "YELLOW" | "GREEN" | "MISSING_CONTEXT";

export interface Finding {
  issueId: string;
  clause: string;
  risk: Risk;
  issue: string;
  currentText: string;
  redline: string;
  rationale: string;        // legacy field; mapped from "Issue" if no separate field exists
  requiredAction: string;
  owner: string;
  externalComment: string;
}

export interface Blocker {
  issueId: string;
  type: string;             // "Red" | "Missing Context"
  clause: string;
  whyItBlocks: string;
  requiredAction: string;
  approverOwner: string;
}

export interface BusinessQuestion {
  question: string;
  whyItMatters: string;
  owner: string;
}

export interface NoSignatureGate {
  ready: boolean;
  overallStatus: string;
  blockingItems: string;
  missingContext: string;
  finalRecommendation: string;
}

export interface ReviewHeader {
  overallStatus: string;
  contractType: string;
  counterparty: string;
  trinetixRole: string;
  versionReviewed: string;
  mainBusinessContext: string;
}

export interface ReviewSummary {
  header: ReviewHeader;
  findings: Finding[];
  blockers: Blocker[];
  businessQuestions: BusinessQuestion[];
  gate: NoSignatureGate;
  counts: { red: number; yellow: number; green: number; missingContext: number };
  // Convenience legacy fields used by existing summary chips
  contractType?: string;
  reviewingAs?: string;
  overall?: string;
}

const RISK_ORDER: Record<Risk, number> = { RED: 0, MISSING_CONTEXT: 1, YELLOW: 2, GREEN: 3 };

/** Normalize a section heading for matching (case-insensitive, ignore decorations). */
function canonicalHeading(s: string): string {
  return s.replace(/[*_`#]/g, "").trim().toLowerCase();
}

/** Split markdown by H1/H2 headings; returns Map<canonicalHeading, sectionBody>. */
function splitSections(markdown: string): Map<string, string> {
  const sections = new Map<string, string>();
  const headingRe = /^(#{1,2})\s+(.+?)\s*$/gm;
  // Capture each heading's full-match index AND the index where its body starts
  // (right after the line). We end one section where the next heading match begins.
  const matches: Array<{ heading: string; bodyStart: number; lineStart: number }> = [];
  let m: RegExpExecArray | null;
  while ((m = headingRe.exec(markdown)) !== null) {
    matches.push({
      heading: m[2],
      lineStart: m.index,
      bodyStart: m.index + m[0].length,
    });
  }
  for (let i = 0; i < matches.length; i++) {
    const start = matches[i].bodyStart;
    const end = i + 1 < matches.length ? matches[i + 1].lineStart : markdown.length;
    sections.set(canonicalHeading(matches[i].heading), markdown.slice(start, end).trim());
  }
  return sections;
}

/** Parse a key:value block — one entry per line where the key ends with ':'. */
function parseKVBlock(body: string): Map<string, string> {
  const kv = new Map<string, string>();
  for (const line of body.split("\n")) {
    const idx = line.indexOf(":");
    if (idx <= 0) continue;
    const key = line.slice(0, idx).replace(/^[*_\-#\s]+/, "").trim().toLowerCase();
    const value = line.slice(idx + 1).trim();
    if (key) kv.set(key, value);
  }
  return kv;
}

/** Parse a GFM-style markdown table; returns array of {header: cell} dicts. */
function parseTable(body: string): Record<string, string>[] {
  const lines = body.split("\n").map((l) => l.trim()).filter((l) => l.startsWith("|"));
  if (lines.length < 2) return [];
  const cells = (line: string) =>
    line
      .replace(/^\||\|$/g, "")
      .split("|")
      .map((c) => c.trim().replace(/<br\s*\/?>/gi, "\n").replace(/\\\|/g, "|"));
  const headers = cells(lines[0]).map((h) => h.toLowerCase());
  // Skip the separator row (---|---|---).
  const dataLines = lines.slice(1).filter((l) => !/^\|[-:\s|]+\|$/.test(l));
  const rows: Record<string, string>[] = [];
  for (const dl of dataLines) {
    const vals = cells(dl);
    const row: Record<string, string> = {};
    for (let i = 0; i < headers.length; i++) {
      row[headers[i]] = vals[i] ?? "";
    }
    // Skip empty rows
    if (Object.values(row).some((v) => v)) rows.push(row);
  }
  return rows;
}

/** Map team risk vocabulary to our enum. Accepts "Red"/"red"/"RED" etc. */
function normalizeRisk(raw: string): Risk | null {
  const s = raw.replace(/[*_`]/g, "").trim().toLowerCase();
  if (s === "red") return "RED";
  if (s === "yellow") return "YELLOW";
  if (s === "green") return "GREEN";
  if (s === "missing context" || s === "missing-context" || s === "missing_context") return "MISSING_CONTEXT";
  return null;
}

/** Pull the first quoted substring (either curly or straight quotes) out of `text`. */
function extractQuoted(text: string): string {
  const m = text.match(/[“"]([^”"]{4,})[”"]/) ?? text.match(/[‘']([^’']{4,})[’']/);
  return m ? m[1].trim() : "";
}

/** Find a cell value across a list of acceptable header aliases. */
function pick(row: Record<string, string>, ...aliases: string[]): string {
  for (const a of aliases) {
    const lc = a.toLowerCase();
    if (row[lc]) return row[lc];
    // Substring fallback so "issue id" matches "issue id (if available)" etc.
    for (const key of Object.keys(row)) {
      if (key.includes(lc) && row[key]) return row[key];
    }
  }
  return "";
}

function parseHeader(body: string): ReviewHeader {
  const kv = parseKVBlock(body);
  return {
    overallStatus: kv.get("overall status") ?? "",
    contractType: kv.get("contract type") ?? "",
    counterparty: kv.get("counterparty") ?? "",
    trinetixRole: kv.get("trinetix role") ?? "",
    versionReviewed: kv.get("version reviewed") ?? "",
    mainBusinessContext: kv.get("main business context") ?? "",
  };
}

function parseGate(body: string): NoSignatureGate {
  const text = body;
  const kv = parseKVBlock(body);
  const overallStatus = kv.get("overall status") ?? "";
  // "Ready" if explicit signature-may-proceed language, "blocked" if DO-NOT-SEND.
  const ready =
    /signature may proceed/i.test(text) ||
    /ready for signature/i.test(overallStatus);
  return {
    ready: ready && !/do not send for signature/i.test(text),
    overallStatus,
    blockingItems: kv.get("blocking items") ?? "",
    missingContext: kv.get("missing context") ?? "",
    finalRecommendation: kv.get("final recommendation") ?? "",
  };
}

function parseFindings(body: string): Finding[] {
  return parseTable(body)
    .map((row): Finding | null => {
      const risk = normalizeRisk(pick(row, "rating", "risk"));
      if (!risk) return null;
      const issue = pick(row, "issue");
      const clause = pick(row, "clause / section", "clause", "section");
      const currentText = extractQuoted(issue) || issue || clause;
      return {
        issueId: pick(row, "issue id"),
        clause,
        risk,
        issue,
        currentText,
        redline: "", // filled in by mergeRedlines
        rationale: issue, // legacy field; team format folds rationale into Issue
        requiredAction: pick(row, "required action"),
        owner: pick(row, "owner"),
        externalComment: "",
      };
    })
    .filter((f): f is Finding => f !== null);
}

function mergeRedlines(findings: Finding[], body: string): void {
  const rows = parseTable(body);
  for (const row of rows) {
    const proposed = pick(row, "proposed wording or instruction", "proposed wording", "instruction");
    if (!proposed) continue;
    const clause = pick(row, "clause / section", "clause");
    const issueId = pick(row, "issue id");
    const externalComment = pick(row, "external comment");
    const action = pick(row, "action");
    // Match by Issue ID first (exact), then by clause substring (case-insensitive).
    const match =
      findings.find((f) => issueId && f.issueId && f.issueId === issueId) ??
      findings.find(
        (f) =>
          clause &&
          (f.clause.toLowerCase().includes(clause.toLowerCase()) ||
            clause.toLowerCase().includes(f.clause.toLowerCase())),
      );
    if (match) {
      // Build a single redline string: the proposed wording is the primary
      // payload for Accept-redline; quotes from inside it become the new text.
      // Prefer the quoted portion when the LLM phrases the cell as
      // 'Replace "X" with "Y"' or "Insert: 'Y'".
      const quoted = extractQuoted(proposed);
      match.redline = quoted || proposed;
      if (action && !match.requiredAction) match.requiredAction = action;
      if (externalComment) match.externalComment = externalComment;
    }
  }
}

function parseBlockers(body: string): Blocker[] {
  return parseTable(body).map((row) => ({
    issueId: pick(row, "issue id"),
    type: pick(row, "type"),
    clause: pick(row, "clause / section", "clause"),
    whyItBlocks: pick(row, "why it blocks signature", "why it blocks"),
    requiredAction: pick(row, "required action"),
    approverOwner: pick(row, "approver / owner", "approver"),
  }));
}

function parseQuestions(body: string): BusinessQuestion[] {
  return parseTable(body).map((row) => ({
    question: pick(row, "question"),
    whyItMatters: pick(row, "why it matters"),
    owner: pick(row, "owner"),
  }));
}

export function parseContractReview(markdown: string): ReviewSummary {
  const sections = splitSections(markdown);

  const header = parseHeader(sections.get("review summary") ?? "");
  const findings = parseFindings(sections.get("key findings") ?? "");
  const blockers = parseBlockers(sections.get("red and missing context items") ?? "");
  const businessQuestions = parseQuestions(sections.get("business questions") ?? "");
  const gate = parseGate(sections.get("no signature checklist result") ?? "");

  mergeRedlines(findings, sections.get("suggested redlines / fallbacks") ?? "");

  findings.sort((a, b) => RISK_ORDER[a.risk] - RISK_ORDER[b.risk]);

  const counts = {
    red: findings.filter((f) => f.risk === "RED").length,
    yellow: findings.filter((f) => f.risk === "YELLOW").length,
    green: findings.filter((f) => f.risk === "GREEN").length,
    missingContext: findings.filter((f) => f.risk === "MISSING_CONTEXT").length,
  };

  return {
    header,
    findings,
    blockers,
    businessQuestions,
    gate,
    counts,
    contractType: header.contractType || undefined,
    reviewingAs: header.trinetixRole || undefined,
    overall: header.overallStatus || undefined,
  };
}
