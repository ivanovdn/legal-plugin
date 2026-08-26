// Assertions for the context gauge's formatting and threshold logic.
// Run: npx tsx src/contextGauge.test.ts

// `declare const process` rather than @types/node: tsconfig pins
// "types": ["office-js", "vite/client"], and pulling in Node globals would
// change typing across the browser/Office.js sources for no benefit here.
declare const process: { exit(code?: number): never };

import {
  PART_LABELS,
  reclaimTarget,
  formatTokens,
  gaugeLine,
  isWarning,
  withLiveDocument,
  withReclaimedHistory,
  type ContextBreakdown,
} from "./contextGauge";

function assert(cond: boolean, name: string) {
  if (!cond) {
    console.error(`FAIL: ${name}`);
    process.exit(1);
  }
  console.log(`PASS: ${name}`);
}

// The measured grounded-MSA case: 131,446 fixed chars of a 150,000 budget.
const MSA: ContextBreakdown = {
  budget_chars: 150000,
  budget_tokens: 30674,
  chars_per_token: 4.89,
  total_chars: 150000,
  total_tokens: 30674,
  pct: 100,
  warn_pct: 90,
  can_compact: true,
  auto_compact: true,
  compressible_messages: 14,
  parts: [
    { key: "document", chars: 84859, tokens: 17353, pct: 56, compactable: false },
    { key: "playbook", chars: 38587, tokens: 7890, pct: 25, compactable: false },
    { key: "msa", chars: 0, tokens: 0, pct: 0, compactable: false },
    { key: "review", chars: 5000, tokens: 1022, pct: 3, compactable: false },
    { key: "history", chars: 18554, tokens: 3794, pct: 12, compactable: true },
    { key: "system", chars: 3000, tokens: 613, pct: 2, compactable: false },
  ],
};

assert(gaugeLine(null) === null, "no breakdown -> no gauge line");
assert(
  gaugeLine(MSA) === "context  31k / 31k tokens · history 12%",
  "gauge line reads total / budget and the compactable share",
);
assert(formatTokens(613) === "613", "sub-1k token counts are not abbreviated");
assert(formatTokens(17353) === "17k", "large token counts round to k");

assert(isWarning(null) === false, "no breakdown -> not warning");
assert(isWarning(MSA) === true, "at or above warn_pct -> warning");
assert(
  isWarning({ ...MSA, pct: 89 }) === false,
  "below warn_pct -> not warning",
);

// Every backend part key must have a label, or the expanded view renders a
// raw key at an attorney.
assert(
  MSA.parts.every((p) => typeof PART_LABELS[p.key] === "string"),
  "every part key has a display label",
);

// The document is the ONE line the client can watch change between turns —
// readBody() is all it knows. Every other line holds its last measured value.
const live = withLiveDocument(MSA, 42000);
assert(live.parts[0].chars === 42000, "live document char count replaces the measured one");
assert(live.total_chars === 150000 - 84859 + 42000, "total follows the live document");
assert(live.parts[1].chars === 38587, "non-document parts are untouched");
assert(live.pct === 71, "pct recomputes from the live total");
assert(
  withLiveDocument(MSA, 42000).can_compact === false,
  "a shrunken document drops below the threshold, so no action is offered",
);
assert(
  withLiveDocument({ ...MSA, compressible_messages: 0 }, 200000).can_compact === false,
  "no compressible history -> no action even when far over budget",
);

// auto_compact is NARROWED by a live document edit and never widened. A shrunk
// document genuinely relieves the pressure, so auto should stop firing.
assert(
  withLiveDocument(MSA, 42000).auto_compact === false,
  "a shrunk document relieves the pressure -> auto stops firing",
);
// The other direction is the one that matters. The backend folded compaction_auto
// and the message floor into this field; the client can see neither, so a live
// document edit must never turn auto ON. Showing a button on the client's own
// estimate is one thing; firing an unrequested LLM call on it is another.
assert(
  withLiveDocument({ ...MSA, auto_compact: false }, 200000).auto_compact === false,
  "a grown document never turns auto on — the client cannot see the flag or the floor",
);
assert(
  withLiveDocument(MSA, 84859).auto_compact === true,
  "an unchanged document leaves auto armed",
);

// withReclaimedHistory — the counter's answer to "I just watched that happen and the
// number did not move". Every figure below is a MEASUREMENT the backend already
// returned, not a forecast: only the compactable line moves, and it moves by exactly
// the chars compaction reported freeing.
const reclaimed = withReclaimedHistory(MSA, 5000);
assert(
  reclaimed.parts.find((p) => p.compactable)?.chars === 18554 - 5000,
  "the history line drops by exactly what was reclaimed",
);
assert(
  reclaimed.total_chars === 145000 && reclaimed.pct === 96,
  "the total and the percentage follow the history line down",
);
assert(
  reclaimed.parts.filter((p) => !p.compactable).every((p) => {
    const before = MSA.parts.find((q) => q.key === p.key);
    return before !== undefined && before.chars === p.chars;
  }),
  "document, playbook, MSA and review are untouched — compaction cannot reach them",
);
// The pool is empty by construction: compaction takes every row past the boundary
// except the keep-recent floor. Both flags follow, and both erring OFF is the safe
// direction — it can only withhold an offer, never fire something unasked.
assert(
  reclaimed.compressible_messages === 0 &&
    reclaimed.can_compact === false &&
    reclaimed.auto_compact === false,
  "condensed rows stop being compressible, so neither control stays armed",
);
assert(
  withReclaimedHistory(MSA, 0) === MSA,
  "a refusal reclaims nothing and returns the breakdown untouched",
);
assert(
  withReclaimedHistory(MSA, 999_999).parts.find((p) => p.compactable)?.chars === 0 &&
    withReclaimedHistory(MSA, 999_999).total_chars === 150000 - 18554,
  "over-reclaiming floors history at zero rather than going negative",
);

// The Condense control appears at warn_pct, so the target must be positive across the
// whole amber band. Measured against the BUDGET it is zero below 100%, which is how
// compaction came to run with no target and write a segment larger than its source.
assert(reclaimTarget(MSA) === 150000 - 135000, "target is measured to the warn line");
assert(
  reclaimTarget({ ...MSA, total_chars: 140000, pct: 93 }) === 5000,
  "inside the amber band the target is positive, not zero",
);
assert(
  reclaimTarget({ ...MSA, total_chars: 100000, pct: 66 }) === 0,
  "comfortably under the warn line -> nothing to reclaim",
);

console.log("contextGauge: all assertions passed");
