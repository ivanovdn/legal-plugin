// Quick parser sanity check. Run with: npx tsx src/parser.test.ts
import { parseContractReview } from "./parser";

const sample = `CONTRACT REVIEW
===============
Contract type: Master Services Agreement
Reviewing as: Customer

CLAUSE: 5. Limitation of Liability
RISK: RED
ISSUE: Cap is 12 months of fees — too high for our position.
CURRENT TEXT: "Service Provider's maximum liability... shall not exceed the fees paid by Client in the 12 months preceding the relevant claim."
SUGGESTED REDLINE: "Service Provider's maximum liability... shall not exceed the fees paid by Client in the 6 months preceding the relevant claim."
RATIONALE: Our standard cap is 6 months.

CLAUSE: 3. Intellectual Property
RISK: YELLOW
ISSUE: IP assignment direction is split.
CURRENT TEXT: "All intellectual property created by Service Provider in the course of performing services is hereby assigned to Client."
SUGGESTED REDLINE: "Each party retains its pre-existing IP; deliverables assigned to Client on payment."
RATIONALE: Split-pattern clauses cause downstream disputes.

CLAUSE: 6. Termination
RISK: GREEN
ISSUE: Acceptable
CURRENT TEXT: "Either party may terminate for convenience upon 30 days written notice."
RATIONALE: Matches our standard 30-day notice.

MISSING CLAUSES:
- Force majeure: protects against unforeseen disruptions.
- Data protection / GDPR rider: required for EU counterparties.

SUMMARY:
GREEN: 1
YELLOW: 1
RED: 1
Overall risk: HIGH
`;

const result = parseContractReview(sample);
console.log("Contract type:", result.contractType);
console.log("Side:", result.reviewingAs);
console.log("Overall:", result.overall);
console.log("Counts:", result.counts);
console.log("Findings:", result.findings.length);
for (const f of result.findings) {
  console.log(`  [${f.risk}] ${f.clause} :: redline ${f.redline ? "present" : "absent"}`);
}
console.log("Missing:", result.missing);

// Assertions
const pass = (cond: boolean, label: string) => console.log(cond ? `PASS: ${label}` : `FAIL: ${label}`);
pass(result.findings.length === 3, "3 findings parsed");
pass(result.findings[0].risk === "RED", "RED first (sort order)");
pass(result.findings[2].risk === "GREEN", "GREEN last (sort order)");
pass(result.counts.red === 1 && result.counts.yellow === 1 && result.counts.green === 1, "counts");
pass(result.overall === "HIGH", "overall risk parsed");
pass(result.missing.length === 2, "2 missing clauses");
pass(result.contractType === "Master Services Agreement", "contract type");
pass(result.findings[0].redline.length > 0, "RED has redline");
pass(result.findings[2].redline === "", "GREEN has no redline");
