// Parser sanity check for the team's required output format.
// Run with: npx tsx src/parser.test.ts
import { parseContractReview } from "./parser";

// ──────────────────────────────────────────────────────────────────────────
// Sample 1 — full happy-path NDA review in the team's required format
// (Review Summary, Key Findings, Red/Missing, Suggested Redlines, Business
//  Questions, No Signature Checklist Result with DO NOT SEND).
// ──────────────────────────────────────────────────────────────────────────

const sample1 = `# Review Summary
Overall status: Not ready
Contract type: Mutual NDA
Counterparty: ACME Inc
Trinetix role: Receiving + Disclosing
Version reviewed: v1.0 (2026-01-15)
Main business context: Pre-engagement diligence for SaaS rollout.

# Key Findings
| Issue ID | Clause / section | Rating | Issue | Required action | Owner |
| --- | --- | --- | --- | --- | --- |
| NDA-002 | Section 1 — Definition of CI | Yellow | Only marked information protected — definition says "any Confidential Information must be marked or identified in writing." | Broaden to include oral and unmarked. | Legal owner |
| NDA-006 | Section 5 — Term | Red | Term is 6 months — "this Agreement shall terminate six (6) months after the Effective Date." Trade secrets get no continuing protection. | Extend to 2 years; trade secrets indefinite. | CLCO |
| NDA-010 | Section 8 — Governing law | Green | Tennessee/AAA — matches template. | None. | Legal owner |

# Red and Missing Context Items
| Issue ID | Type | Clause / section | Why it blocks signature | Required action | Approver / owner |
| --- | --- | --- | --- | --- | --- |
| NDA-006 | Red | Section 5 — Term | Six-month term is below acceptable floor; trade secrets unprotected. | Extend to 2 years + indefinite trade-secret survival. | CLCO |
| NDA-MISSING-1 | Missing Context | Whole agreement | Counterparty's expected disclosures unknown — can't size the risk. | Confirm whether ACME will share source code or pricing. | Sales |

# Approved Deviations
| Issue ID | Deviation | Approver | Evidence | Final wording checked |
| --- | --- | --- | --- | --- |

# Suggested Redlines / Fallbacks
| Clause / section | Action | Proposed wording or instruction | External comment |
| --- | --- | --- | --- |
| Section 1 — Definition of CI | Replace | "Confidential Information means any non-public business, technical, financial, or commercial information disclosed in any form, whether or not marked." | We suggest aligning this clause with the agreed SOW structure. |
| Section 5 — Term | Replace | "This Agreement shall remain in effect for two (2) years; trade secrets shall remain protected for as long as they remain trade secrets." | This obligation should apply only to matters within Vendor's reasonable control. |

# Business Questions
| Question | Why it matters | Owner |
| --- | --- | --- |
| Will ACME share source code? | Determines whether source-code provisions are required. | Sales |
| Is portfolio-use needed? | Affects deliverable-examples clause. | Sales |

# No Signature Checklist Result
Overall status: Do not send for signature
Blocking items: NDA-006 (Term too short)
Missing context: Counterparty disclosure scope
Final recommendation: DO NOT SEND FOR SIGNATURE.
`;

const r1 = parseContractReview(sample1);
const pass = (cond: boolean, label: string) => console.log(cond ? `PASS: ${label}` : `FAIL: ${label}`);

console.log("=== Sample 1: team's required format (NDA) ===");
console.log("Header:", r1.header);
console.log("Findings:", r1.findings.length, r1.findings.map((f) => `${f.risk} ${f.clause}`));
console.log("Blockers:", r1.blockers.length);
console.log("Questions:", r1.businessQuestions.length);
console.log("Gate:", r1.gate);
console.log("Counts:", r1.counts);

pass(r1.header.contractType === "Mutual NDA", "header contractType");
pass(r1.header.overallStatus === "Not ready", "header overallStatus");
pass(r1.findings.length === 3, "3 findings parsed");
pass(r1.findings[0].risk === "RED", "RED first (sort order)");
pass(r1.findings[2].risk === "GREEN", "GREEN last (sort order)");
pass(r1.counts.red === 1 && r1.counts.yellow === 1 && r1.counts.green === 1, "counts");
pass(r1.findings[0].issueId === "NDA-006", "issueId carried");
pass(r1.findings[0].owner === "CLCO", "owner column");
pass(r1.findings[0].requiredAction.includes("Extend to 2 years"), "requiredAction populated");
pass(r1.blockers.length === 2, "2 blockers (Red + Missing Context)");
pass(r1.blockers[1].type === "Missing Context", "Missing Context type preserved");
pass(r1.businessQuestions.length === 2, "2 business questions");
pass(r1.gate.ready === false, "gate NOT ready (DO NOT SEND)");
pass(r1.gate.finalRecommendation.includes("DO NOT SEND"), "gate finalRecommendation");

// Quote extraction: NDA-006 issue text has quoted current text
const term = r1.findings.find((f) => f.issueId === "NDA-006");
pass(!!term && term.currentText.includes("six (6) months"), "currentText extracted from quoted Issue");

// Redline merge: the Suggested Redlines row "Section 5 — Term" should land on the Term finding
pass(!!term && term.redline.includes("two (2) years"), "redline merged from Suggested Redlines table");
pass(!!term && !!term.externalComment, "externalComment merged");

// ──────────────────────────────────────────────────────────────────────────
// Sample 2 — happy path with Signature may proceed
// ──────────────────────────────────────────────────────────────────────────

const sample2 = `# Review Summary
Overall status: Ready for legal approval
Contract type: NDA

# Key Findings
| Issue ID | Clause / section | Rating | Issue | Required action | Owner |
| --- | --- | --- | --- | --- | --- |
| NDA-001 | Section 1 — Type | Green | Mutual NDA — appropriate. | None. | Legal owner |

# No Signature Checklist Result
Overall status: Ready for signature
Blocking items: None
Missing context: None
Final recommendation: Signature may proceed, subject to normal internal signing authority and final business confirmation.
`;

const r2 = parseContractReview(sample2);
console.log("\n=== Sample 2: clean signature-may-proceed path ===");
console.log("Gate:", r2.gate);
pass(r2.gate.ready === true, "gate ready");
pass(r2.findings.length === 1 && r2.findings[0].risk === "GREEN", "single green finding");
pass(r2.counts.red === 0 && r2.counts.green === 1, "counts");

// ──────────────────────────────────────────────────────────────────────────
// Sample 3 — Missing Context rating
// ──────────────────────────────────────────────────────────────────────────

const sample3 = `# Key Findings
| Issue ID | Clause / section | Rating | Issue | Required action | Owner |
| --- | --- | --- | --- | --- | --- |
| MSA-X | Section 12 — Indemnity | Missing Context | Cap size not specified in the deal sheet. | Confirm deal size with Sales. | Finance |
`;

const r3 = parseContractReview(sample3);
console.log("\n=== Sample 3: Missing Context rating ===");
pass(r3.findings[0].risk === "MISSING_CONTEXT", "Missing Context risk parsed");
pass(r3.counts.missingContext === 1, "missingContext count");

// ──────────────────────────────────────────────────────────────────────────
// Sample 4 — graceful on empty / unrelated input
// ──────────────────────────────────────────────────────────────────────────

const r4 = parseContractReview("Some unrelated prose with no tables.");
console.log("\n=== Sample 4: empty input ===");
pass(r4.findings.length === 0, "no findings on unrelated prose");
pass(r4.gate.ready === false, "gate defaults to not-ready");
pass(r4.blockers.length === 0, "no blockers");
