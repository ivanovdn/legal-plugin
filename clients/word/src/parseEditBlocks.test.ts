// Quick sanity check for parseEditBlocks. Run with: npx tsx src/parseEditBlocks.test.ts
import { extractEditBlocks } from "./parseEditBlocks";

const pass = (cond: boolean, label: string) =>
  console.log(cond ? `PASS: ${label}` : `FAIL: ${label}`);

// 1. Well-formed single block
{
  const prose =
    "Here's a tighter cap for Section 4.\n\n" +
    "```json\n" +
    '{"action": "replace", "target_text": "the fees paid", ' +
    '"new_text": "2x the fees paid", "rationale": "Aligns with playbook"}\n' +
    "```";
  const { cleanedProse, blocks } = extractEditBlocks(prose);
  pass(blocks.length === 1, "well-formed: 1 block parsed");
  pass(blocks[0].action === "replace", "well-formed: action=replace");
  pass(blocks[0].new_text === "2x the fees paid", "well-formed: new_text correct");
  pass(!cleanedProse.includes("```"), "well-formed: cleanedProse strips fences");
  pass(cleanedProse.includes("tighter cap"), "well-formed: cleanedProse keeps prose");
}

// 2. Multiple blocks
{
  const prose =
    "Two options:\n" +
    '```json\n{"action": "replace", "target_text": "X", "new_text": "Y"}\n```\n' +
    "Or:\n" +
    '```json\n{"action": "insert", "anchor_text": "Sec 7", "position": "after", ' +
    '"new_text": "Force majeure..."}\n```';
  const { blocks } = extractEditBlocks(prose);
  pass(blocks.length === 2, "multiple: 2 blocks parsed");
  pass(blocks[0].action === "replace", "multiple: first is replace");
  pass(blocks[1].action === "insert", "multiple: second is insert");
  pass(blocks[1].position === "after", "multiple: position preserved");
}

// 3. Malformed JSON — skipped
{
  const prose =
    '```json\n{"action": "replace", target_text: missing-quotes}\n```\n' +
    '```json\n{"action": "delete", "target_text": "auto-renew"}\n```';
  const { blocks } = extractEditBlocks(prose);
  pass(blocks.length === 1, "malformed: only valid block kept");
  pass(blocks[0].action === "delete", "malformed: valid action=delete");
}

// 4. Unknown action — skipped
{
  const prose = '```json\n{"action": "moonwalk", "target_text": "X"}\n```';
  const { blocks } = extractEditBlocks(prose);
  pass(blocks.length === 0, "unknown-action: skipped");
}

// 5. No blocks — empty array, prose unchanged
{
  const prose = "Why is the IP clause risky? It's the assignment direction.";
  const { cleanedProse, blocks } = extractEditBlocks(prose);
  pass(blocks.length === 0, "no-blocks: empty array");
  pass(cleanedProse === prose, "no-blocks: prose unchanged");
}

// 6. Empty input
{
  const { cleanedProse, blocks } = extractEditBlocks("");
  pass(blocks.length === 0, "empty: no blocks");
  pass(cleanedProse === "", "empty: empty prose");
}

// 7. Array inside ONE block (the regression that broke "fill every Signed by")
//    The local LLM consolidated two edits into a single fenced block holding
//    an array. The old parser dropped both because it expected a single dict.
{
  const prose =
    "I will replace the placeholder in two locations.\n\n" +
    "```json\n" +
    '[{"action": "replace", "target_text": "Signed by: [__]", "new_text": "Signed by: John Doe"}, ' +
    '{"action": "replace", "target_text": "Signed by: [__]", "new_text": "Signed by: John Doe"}]\n' +
    "```";
  const { blocks } = extractEditBlocks(prose);
  pass(blocks.length === 2, "array-in-block: both edits extracted");
  pass(
    blocks[0].action === "replace" && blocks[1].action === "replace",
    "array-in-block: both are replace",
  );
  pass(
    blocks[0].new_text === "Signed by: John Doe",
    "array-in-block: new_text preserved",
  );
}

// 7b. Block whose string value contains a LITERAL newline (LLM line-wrapped
//     the value mid-content). Spec-invalid JSON but recoverable.
{
  const prose =
    "```json\n" +
    '{"action": "replace", "target_text": "long-dots-line\nSigned by:\n[__]\\tSigned by: Boris", ' +
    '"new_text": "long-dots-line\nSigned by: John Doe\\tSigned by: Boris"}\n' +
    "```";
  const { blocks } = extractEditBlocks(prose);
  pass(blocks.length === 1, "broken-string: tolerant parser recovers");
  pass(
    blocks[0]?.new_text?.includes("John Doe") ?? false,
    "broken-string: new_text preserved",
  );
}

// 8. Array with mixed-validity entries — valid ones kept, invalid dropped.
{
  const prose =
    "```json\n" +
    '[{"action": "replace", "target_text": "X", "new_text": "Y"}, ' +
    '{"action": "moonwalk", "target_text": "Z"}, ' +
    '{"action": "insert", "anchor_text": "Section 7", "position": "after", "new_text": "..."}]\n' +
    "```";
  const { blocks } = extractEditBlocks(prose);
  pass(blocks.length === 2, "array-mixed: 2 valid kept, 1 dropped");
  pass(blocks[0].action === "replace", "array-mixed: replace kept");
  pass(blocks[1].action === "insert", "array-mixed: insert kept");
}
