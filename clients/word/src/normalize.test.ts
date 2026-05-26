// Quick normalize sanity check. Run with: npx tsx src/normalize.test.ts
import { normalizeForSearch } from "./normalize";

const pass = (cond: boolean, label: string) =>
  console.log(cond ? `PASS: ${label}` : `FAIL: ${label}`);

pass(
  normalizeForSearch("Service Provider's “maximum liability”") ===
    `Service Provider's "maximum liability"`,
  "curly quotes + nbsp normalized",
);

pass(
  normalizeForSearch("  multi\n  line   \t whitespace  ") === "multi line whitespace",
  "whitespace collapsed",
);

pass(
  normalizeForSearch("café") === "café",
  "NFC unicode preserved",
);

pass(
  normalizeForSearch("”curly” ‘both’") === `"curly" 'both'`,
  "both quote types",
);
