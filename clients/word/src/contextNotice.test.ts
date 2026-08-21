// Assertions for the truncation notice formatter.
// Run: npx tsx src/contextNotice.test.ts

// `declare const process` rather than @types/node: tsconfig pins
// "types": ["office-js", "vite/client"], and pulling in Node globals would
// change typing across the browser/Office.js sources for no benefit here.
// Same rationale as testAssert.ts / docIdentity.test.ts.
declare const process: { exit(code?: number): never };

import { truncationNotice } from "./contextNotice";

let passed = 0;
function assert(cond: boolean, name: string) {
  if (!cond) {
    console.error(`FAIL: ${name}`);
    process.exit(1);
  }
  console.log(`PASS: ${name}`);
  passed++;
}

// A healthy turn must produce NOTHING — the notice cannot cry wolf, or testers
// learn to ignore the one condition that means the answer may be unsound.
assert(truncationNotice(null) === null, "null -> no notice");
assert(truncationNotice(undefined) === null, "undefined -> no notice");

const notice = truncationNotice({ doc_chars: 84859, kept_chars: 49537, kept_pct: 58 });
assert(notice !== null, "truncation -> a notice");
assert(notice!.includes("58%"), "notice states the percentage seen");
assert(notice!.includes("35,322"), "notice states the missing char count, thousands-separated");

// The worst case: none of the document survived.
const zero = truncationNotice({ doc_chars: 5000, kept_chars: 0, kept_pct: 0 });
assert(zero !== null, "0% -> a notice");
assert(zero!.includes("none of this document"), "0% is described plainly, not as '0%'");

console.log(`\n${passed} assertions passed`);
