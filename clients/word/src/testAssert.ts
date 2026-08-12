// Shared assertion helper for the hand-rolled *.test.ts scripts.
// Run a test file with: npx tsx src/<name>.test.ts
//
// Sets a non-zero exit code on failure so scripts/check.sh can actually fail
// the run. Deliberately does NOT throw or exit immediately — every assertion
// in a file still runs and reports, so one failure doesn't hide the rest.
//
// `declare const process` rather than @types/node: tsconfig pins
// "types": ["office-js", "vite/client"], and pulling in Node globals would
// change typing across the browser/Office.js sources for no benefit here.
declare const process: { exitCode?: number };

export const pass = (cond: boolean, label: string): void => {
  if (cond) {
    console.log(`PASS: ${label}`);
  } else {
    process.exitCode = 1;
    console.log(`FAIL: ${label}`);
  }
};
