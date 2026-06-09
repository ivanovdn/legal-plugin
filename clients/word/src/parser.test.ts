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
| NDA-MISSING-1 | Whole agreement | Missing Context | Counterparty's expected disclosures unknown. | Confirm whether ACME will share source code or pricing. | Sales |

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
pass(r1.findings.length === 4, "4 findings parsed");
pass(r1.findings[0].risk === "RED", "RED first (sort order)");
pass(r1.findings[3].risk === "GREEN", "GREEN last (sort order)");
pass(
  r1.counts.red === 1 && r1.counts.yellow === 1 && r1.counts.green === 1 && r1.counts.missingContext === 1,
  "counts",
);
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
// Sample 3 — Missing Context rating + anchor candidates
// ──────────────────────────────────────────────────────────────────────────

const sample3 = `# Key Findings
| Issue ID | Clause / section | Rating | Issue | Required action | Owner |
| --- | --- | --- | --- | --- | --- |
| MSA-X | Section 12 — Indemnity | Missing Context | Cap size not specified in the deal sheet. | Confirm deal size with Sales. | Finance |
| NDA-Y | Preamble / Effective Date | Missing Context | Effective date is a placeholder. | Fill date. | Sales |
`;

const r3 = parseContractReview(sample3);
console.log("\n=== Sample 3: Missing Context rating + anchors ===");
const indem = r3.findings.find((f) => f.issueId === "MSA-X")!;
const effDate = r3.findings.find((f) => f.issueId === "NDA-Y")!;
console.log("MSA-X anchors:", indem.anchors);
console.log("NDA-Y anchors:", effDate.anchors);

pass(indem.risk === "MISSING_CONTEXT", "Missing Context risk parsed");
pass(r3.counts.missingContext === 2, "missingContext count");

// Anchor strategy: for meta-textual Issue cells, the clause-name segments
// must lead so the Word add-in can locate the section in the doc.
pass(effDate.anchors.includes("Effective Date"), "anchors include last clause segment");
pass(effDate.anchors.includes("Preamble"), "anchors include first clause segment");
pass(
  effDate.anchors.indexOf("Effective Date") < effDate.anchors.indexOf("Effective date is a placeholder."),
  "clause-segment anchor ranks BEFORE meta-textual issue verbatim",
);
pass(effDate.hasQuotedText === false, "hasQuotedText=false for meta-textual Issue (no quotes)");
pass(indem.hasQuotedText === false, "hasQuotedText=false when no quotes in Issue");

// Sample 1's NDA-006 Issue DOES include quoted text — hasQuotedText should be true
pass(term!.hasQuotedText === true, "hasQuotedText=true when Issue cell quotes current wording");

// ──────────────────────────────────────────────────────────────────────────
// Sample 4 — graceful on empty / unrelated input
// ──────────────────────────────────────────────────────────────────────────

const r4 = parseContractReview("Some unrelated prose with no tables.");
console.log("\n=== Sample 4: empty input ===");
pass(r4.findings.length === 0, "no findings on unrelated prose");
pass(r4.gate.ready === false, "gate defaults to not-ready");
pass(r4.blockers.length === 0, "no blockers");

// ──────────────────────────────────────────────────────────────────────────
// Sample 5 — cross-card redline contamination (regression test)
// Two findings under the same "Preamble" parent. The Suggested Redlines table
// names each child specifically. The old substring-only matcher would attach
// the FIRST row to the FIRST finding regardless of specificity; the scored
// matcher must pair them correctly.
// ──────────────────────────────────────────────────────────────────────────

const sample5 = `# Key Findings
| Issue ID | Clause / section | Rating | Issue | Required action | Owner |
| --- | --- | --- | --- | --- | --- |
| F1 | Preamble / Effective Date | Missing Context | Effective date is a placeholder. | Fill date. | Sales |
| F2 | Preamble / Parties | Missing Context | Counterparty name and address are placeholders. | Fill counterparty details. | Sales |

# Suggested Redlines / Fallbacks
| Clause / section | Action | Proposed wording or instruction | External comment |
| --- | --- | --- | --- |
| Preamble / Parties | Insert | Insert [Legal Name], [Address] | — |
| Preamble / Effective Date | Insert | Insert [Month] [Date], [Year] | — |
`;

const r5 = parseContractReview(sample5);
const effDateF = r5.findings.find((f) => f.issueId === "F1")!;
const partiesF = r5.findings.find((f) => f.issueId === "F2")!;
console.log("\n=== Sample 5: cross-card contamination regression ===");
console.log("Effective Date redline:", effDateF.redline);
console.log("Parties redline:        ", partiesF.redline);

pass(
  effDateF.redline.includes("Month") && !effDateF.redline.includes("Legal Name"),
  "Effective Date gets its OWN redline, not the Parties one",
);
pass(
  partiesF.redline.includes("Legal Name") && !partiesF.redline.includes("Month"),
  "Parties gets its OWN redline, not the Effective Date one",
);

// ──────────────────────────────────────────────────────────────────────────
// Sample 5b — Blockers card is spec-conformant (drops Yellow, fills omissions).
//
// Real failure mode from user testing: the LLM put a Yellow row in the
// "Red and Missing Context Items" table (spec says only Red + Missing
// Context belong there), and ALSO omitted one Missing Context entry that
// existed in Key Findings. The derived Blockers list must:
//   - exclude the rogue Yellow row
//   - include the omitted Missing Context row (enriched from Key Findings)
//   - take "why it blocks" / "approver" from the raw table when present
// ──────────────────────────────────────────────────────────────────────────

const sample5b = `# Key Findings
| Issue ID | Clause / section | Rating | Issue | Required action | Owner |
| --- | --- | --- | --- | --- | --- |
| 1 | Preamble / Effective Date | Missing Context | Date is a placeholder. | Fill date. | Sales |
| 2 | Structure of the Agreement | Yellow | SOW precedence undermines MSA. | Revise. | Legal |
| 3 | Indemnity Exceptions | Red | Uncapped indemnity. | Apply cap. | CLCO |
| 4 | Information Security & Privacy | Missing Context | No DPA attached. | Request data flow. | Privacy |

# Red and Missing Context Items
| Issue ID | Type | Clause / section | Why it blocks signature | Required action | Approver / owner |
| --- | --- | --- | --- | --- | --- |
| 1 | Missing Context | Preamble / Effective Date | Unfilled legal names, dates. | Complete all placeholders. | Sales / Legal |
| 2 | Yellow | Structure of the Agreement | SOW precedence undermines IP. | Revise to MSA-prevailing. | Legal |
| 3 | Red | Indemnity Exceptions | Uncapped liability. | Apply cap or escalate. | CLCO |
`;

const r5b = parseContractReview(sample5b);
console.log("\n=== Sample 5b: Blockers card spec-conformance ===");
console.log("Blockers:", r5b.blockers.map((b) => `${b.type} :: ${b.clause}`));

pass(r5b.blockers.length === 3, "blockers: 3 entries (1 Red + 2 Missing Context — Yellow dropped)");
pass(
  r5b.blockers.every((b) => b.type === "Red" || b.type === "Missing Context"),
  "blockers: no Yellow leakage",
);
pass(
  !r5b.blockers.some((b) => b.clause === "Structure of the Agreement"),
  "blockers: Yellow row excluded",
);
pass(
  r5b.blockers.some((b) => b.clause === "Information Security & Privacy"),
  "blockers: omitted Missing Context entry filled in from Key Findings",
);
// The Effective Date entry should pick up the richer "why it blocks" from the
// raw blockers table (vs falling back to the shorter Key Findings issue text).
const effDateBlocker = r5b.blockers.find((b) => b.clause === "Preamble / Effective Date");
pass(
  !!effDateBlocker && effDateBlocker.whyItBlocks.includes("Unfilled legal names"),
  "blockers: 'why it blocks' enriched from raw table when present",
);
// The InfoSec entry has no raw row → fall back to Key Findings' issue text.
const infoSecBlocker = r5b.blockers.find((b) => b.clause === "Information Security & Privacy");
pass(
  !!infoSecBlocker && infoSecBlocker.whyItBlocks === "No DPA attached.",
  "blockers: 'why it blocks' falls back to Key Findings issue when no raw row",
);

// ──────────────────────────────────────────────────────────────────────────
// Sample 6 — LLM emits a parent-level redline covering multiple children.
// Real failure mode observed on the local LLM: instead of two rows
// ("Preamble / Effective Date" and "Preamble / Parties"), it emits ONE row
// with clause "Preamble" whose wording covers both blanks. Both child
// findings should still get the redline rendered on their card.
// ──────────────────────────────────────────────────────────────────────────

const sample6 = `# Key Findings
| Issue ID | Clause / section | Rating | Issue | Required action | Owner |
| --- | --- | --- | --- | --- | --- |
| 1 | Preamble / Effective Date | Missing Context | Effective date is a placeholder. | Fill date. | Sales |
| 2 | Preamble / Parties | Missing Context | Counterparty name and address are placeholders. | Fill counterparty details. | Sales |
| 3 | Execution | Missing Context | Signatory name/title placeholders. | Fill signatory details. | Sales |

# Suggested Redlines / Fallbacks
| Clause / section | Action | Proposed wording or instruction | External comment |
| --- | --- | --- | --- |
| Preamble | Fill placeholders | Insert [Legal Name], [Address], [Month] [Date], [Year]. | N/A |
| Execution | Fill placeholders | Insert signatory name and title. | N/A |
`;

const r6 = parseContractReview(sample6);
const f1 = r6.findings.find((f) => f.issueId === "1")!;
const f2 = r6.findings.find((f) => f.issueId === "2")!;
const f3 = r6.findings.find((f) => f.issueId === "3")!;
console.log("\n=== Sample 6: parent-level redline propagation ===");
console.log("F1 redline:", f1.redline);
console.log("F2 redline:", f2.redline);
console.log("F3 redline:", f3.redline);
pass(!!f1.redline, "F1 (Effective Date) inherits the Preamble redline");
pass(!!f2.redline, "F2 (Parties) inherits the Preamble redline");
pass(f3.redline.includes("signatory"), "F3 (Execution) keeps its own redline");
pass(f1.redline === f2.redline, "F1 and F2 share the same parent-level wording");
