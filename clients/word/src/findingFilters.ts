// Pure filter/sort logic for the Findings tab. Frontend-only, no Office.js.
import { RISK_ORDER, type Finding, type Risk } from "./parser";

/** Canonical severity order (matches RISK_ORDER); used to seed the UI chips. */
export const ALL_RISKS: Risk[] = ["RED", "MISSING_CONTEXT", "YELLOW", "GREEN"];

export interface FindingFilters {
  /** Only findings whose risk is in this set are kept. */
  severities: Set<Risk>;
  /** "all" | an owner name | "Unassigned" (matches findings with a blank owner). */
  owner: string;
  sortBy: "severity" | "clause";
}

/** Normalize a finding's owner for grouping/filtering. */
export function ownerKey(f: Finding): string {
  return f.owner.trim() || "Unassigned";
}

export function applyFindingFilters(findings: Finding[], filters: FindingFilters): Finding[] {
  const filtered = findings.filter((f) => {
    if (!filters.severities.has(f.risk)) return false;
    if (filters.owner !== "all" && ownerKey(f) !== filters.owner) return false;
    return true;
  });
  // Copy before sort — never mutate the caller's array.
  return [...filtered].sort((a, b) =>
    filters.sortBy === "clause"
      ? a.clause.localeCompare(b.clause)
      : RISK_ORDER[a.risk] - RISK_ORDER[b.risk],
  );
}
