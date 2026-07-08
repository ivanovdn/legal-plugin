// Pure-helper checks for findingFilters.ts. Run with: npx tsx src/findingFilters.test.ts
import { applyFindingFilters, ALL_RISKS, type FindingFilters } from "./findingFilters";
import type { Finding, Risk } from "./parser";

const pass = (cond: boolean, label: string) =>
  console.log(cond ? `PASS: ${label}` : `FAIL: ${label}`);

const makeFinding = (over: Partial<Finding>): Finding => ({
  issueId: "",
  clause: "Clause",
  risk: "GREEN",
  issue: "",
  currentText: "",
  anchors: [],
  hasQuotedText: false,
  redline: "",
  rationale: "",
  requiredAction: "",
  owner: "",
  externalComment: "",
  ...over,
});

const findings: Finding[] = [
  makeFinding({ clause: "Term", risk: "RED", owner: "CLCO" }),
  makeFinding({ clause: "Indemnity", risk: "YELLOW", owner: "Legal owner" }),
  makeFinding({ clause: "Definitions", risk: "MISSING_CONTEXT", owner: "" }),
  makeFinding({ clause: "Governing law", risk: "GREEN", owner: "Legal owner" }),
];

const all: FindingFilters = { severities: new Set(ALL_RISKS), owner: "all", sortBy: "severity" };

// severity subset
const redsOnly = applyFindingFilters(findings, { ...all, severities: new Set<Risk>(["RED"]) });
pass(redsOnly.length === 1 && redsOnly[0].clause === "Term", "severity subset keeps only RED");

// blockers-only (RED + MISSING_CONTEXT)
const blockers = applyFindingFilters(findings, { ...all, severities: new Set<Risk>(["RED", "MISSING_CONTEXT"]) });
pass(blockers.length === 2, "blockers-only keeps RED + MISSING_CONTEXT");

// empty severity set → empty result
const none = applyFindingFilters(findings, { ...all, severities: new Set<Risk>() });
pass(none.length === 0, "empty severity set yields no findings");

// owner filter (specific)
const legal = applyFindingFilters(findings, { ...all, owner: "Legal owner" });
pass(legal.length === 2 && legal.every((f) => f.owner === "Legal owner"), "owner filter keeps matching owner");

// owner filter (Unassigned = empty owner)
const unassigned = applyFindingFilters(findings, { ...all, owner: "Unassigned" });
pass(unassigned.length === 1 && unassigned[0].clause === "Definitions", "owner 'Unassigned' matches empty owner");

// owner 'all' keeps everything
pass(applyFindingFilters(findings, all).length === 4, "owner 'all' keeps every finding");

// sort by severity: RED, MISSING_CONTEXT, YELLOW, GREEN
const bySeverity = applyFindingFilters(findings, all).map((f) => f.risk);
pass(
  JSON.stringify(bySeverity) === JSON.stringify(["RED", "MISSING_CONTEXT", "YELLOW", "GREEN"]),
  "sort by severity orders RED<MISSING<YELLOW<GREEN",
);

// sort by clause name A–Z
const byClause = applyFindingFilters(findings, { ...all, sortBy: "clause" }).map((f) => f.clause);
pass(
  JSON.stringify(byClause) === JSON.stringify(["Definitions", "Governing law", "Indemnity", "Term"]),
  "sort by clause is alphabetical",
);

// purity: input array not mutated
const before = findings.map((f) => f.clause);
applyFindingFilters(findings, { ...all, sortBy: "clause" });
pass(JSON.stringify(findings.map((f) => f.clause)) === JSON.stringify(before), "does not mutate input array");
