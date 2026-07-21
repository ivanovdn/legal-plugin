// Run with: npx tsx src/parsePreferenceBlocks.test.ts
import { extractPreferenceBlocks } from "./parsePreferenceBlocks";

const pass = (cond: boolean, label: string) =>
  console.log(cond ? `PASS: ${label}` : `FAIL: ${label}`);

{
  const { cleanedProse, preferences } = extractPreferenceBlocks(
    "Noted.\n```preference\nAlways flag uncapped indemnity.\n```",
  );
  pass(preferences.length === 1 && preferences[0] === "Always flag uncapped indemnity.", "single");
  pass(!cleanedProse.includes("```"), "block stripped from prose");
}
{
  const { preferences } = extractPreferenceBlocks(
    "```preference\n- Delaware fallback.\n- Surface auto-renewal.\n```",
  );
  pass(preferences.length === 2 && preferences[1] === "Surface auto-renewal.", "multi + bullets");
}
{
  const { preferences } = extractPreferenceBlocks("no block here");
  pass(preferences.length === 0, "none");
}
