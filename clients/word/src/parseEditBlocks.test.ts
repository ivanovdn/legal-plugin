// Quick sanity check for parseEditBlocks. Run with: npx tsx src/parseEditBlocks.test.ts
import { extractEditBlocks, normalizeProposals, type EditProposal } from "./parseEditBlocks";

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

// 4b. replace_all action — accepted for multi-occurrence requests.
{
  const prose =
    '```json\n{"action": "replace_all", "target_text": "Signed by: [__]", ' +
    '"new_text": "Signed by: John Doe"}\n```';
  const { blocks } = extractEditBlocks(prose);
  pass(blocks.length === 1, "replace_all: accepted");
  pass(blocks[0].action === "replace_all", "replace_all: action preserved");
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
//    (Distinct edits so the collapse step below doesn't merge them — this test
//    is purely about array decoding.)
{
  const prose =
    "I will fill the two placeholders.\n\n" +
    "```json\n" +
    '[{"action": "replace", "target_text": "Signed by: [__]", "new_text": "Signed by: John Doe"}, ' +
    '{"action": "replace", "target_text": "Title: [__]", "new_text": "Title: CTO"}]\n' +
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

// 7c. Stacked objects in ONE block (traces cea50c6b / f15f8a9b). The local LLM
//     puts several edit objects in one fenced block separated by newlines, NOT
//     a JSON array. JSON.parse rejects multiple top-level objects, so the block
//     was dropped -> empty edits -> lossy JSON-retry -> destructive replace_all.
{
  const prose =
    "I will update the blank fields.\n\n" +
    "```json\n" +
    '{"action": "replace", "target_text": "Signed by: [__]", "new_text": "Signed by: Suzy Quatro"}\n' +
    '{"action": "replace", "target_text": "Title: [__]", "new_text": "Title: Chief"}\n' +
    '{"action": "replace", "target_text": "for and on behalf of [__]", "new_text": "for and on behalf of Acme"}\n' +
    "```";
  const { blocks } = extractEditBlocks(prose);
  pass(blocks.length === 3, "stacked-objects: all 3 edits extracted");
  pass(blocks[1].new_text === "Title: Chief", "stacked-objects: middle edit preserved");
  pass(
    blocks[2].new_text === "for and on behalf of Acme",
    "stacked-objects: last edit preserved",
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

// 9. Multi-line signature-block fill — the MSA/SOW regression (traces 02e41ead /
//    ce45b899). The LLM collapses a 3-field signature block into ONE multi-line
//    `replace` with all three lines differing. body.search can't span paragraph
//    breaks, so it was unmatchable. Split into one labeled replace_all per field.
{
  const prose =
    "I have updated the signature block.\n\n" +
    "```json\n" +
    '{"action": "replace", ' +
    '"target_text": "Signed by: [__]\\nTitle: [__]\\nfor and on behalf of [__]", ' +
    '"new_text": "Signed by: John Doe\\nTitle: CTO\\nfor and on behalf of Sony Company", ' +
    '"rationale": "Fills the Client signature block."}\n' +
    "```";
  const { blocks } = extractEditBlocks(prose);
  pass(blocks.length === 3, "multiline-fill: split into 3 per-field edits");
  pass(
    blocks.every((b) => b.action === "replace_all"),
    "multiline-fill: every field is replace_all",
  );
  pass(blocks[0].target_text === "Signed by: [__]", "multiline-fill: target line 1");
  pass(blocks[0].new_text === "Signed by: John Doe", "multiline-fill: new line 1");
  pass(blocks[1].target_text === "Title: [__]", "multiline-fill: target line 2");
  pass(
    blocks[2].target_text === "for and on behalf of [__]",
    "multiline-fill: target line 3",
  );
}

// 9b. TWO identical multi-line blocks (main agreement + appendix) — exactly what
//     the MSA/SOW traces emit. After splitting each into 3 fields we have 6 edits
//     that collapse to 3 fill-every replace_all (no double-fill on apply).
{
  const block =
    "```json\n" +
    '{"action": "replace", ' +
    '"target_text": "Signed by: [__]\\nTitle: [__]\\nfor and on behalf of [__]", ' +
    '"new_text": "Signed by: John Doe\\nTitle: CTO\\nfor and on behalf of Sony Company", ' +
    '"rationale": "%s"}\n' +
    "```";
  const prose =
    "Updating both blocks.\n\n" +
    block.replace("%s", "main agreement") +
    "\n" +
    block.replace("%s", "appendix");
  const { blocks } = extractEditBlocks(prose);
  pass(blocks.length === 3, "dup-blocks: collapse 2 cards into 3 unique fills");
  pass(
    blocks.every((b) => b.action === "replace_all"),
    "dup-blocks: all replace_all",
  );
}

// 9c. Multi-line PROSE rewrite (no blanks) is left untouched — not a fill.
{
  const prose =
    "```json\n" +
    '{"action": "replace", ' +
    '"target_text": "The fee is 100.\\nPayment is net 30.", ' +
    '"new_text": "The fee is 200.\\nPayment is net 60."}\n' +
    "```";
  const { blocks } = extractEditBlocks(prose);
  pass(blocks.length === 1, "multiline-prose: not split");
  pass(blocks[0].action === "replace", "multiline-prose: stays replace");
  pass(
    blocks[0].target_text === "The fee is 100.\nPayment is net 30.",
    "multiline-prose: target untouched",
  );
}

// 9d. Two identical SINGLE-LINE replaces collapse to one fill-every replace_all
//     (a duplicate replace can't fill a second location — body.search re-finds
//     the first match's struck original).
{
  const prose =
    "```json\n" +
    '[{"action": "replace", "target_text": "Signed by: [__]", "new_text": "Signed by: John Doe"}, ' +
    '{"action": "replace", "target_text": "Signed by: [__]", "new_text": "Signed by: John Doe"}]\n' +
    "```";
  const { blocks } = extractEditBlocks(prose);
  pass(blocks.length === 1, "dup-single: collapsed to one");
  pass(blocks[0].action === "replace_all", "dup-single: promoted to replace_all");
  pass(blocks[0].new_text === "Signed by: John Doe", "dup-single: new_text kept");
}

// 10. normalizeProposals on a BACKEND-style flat edit list (the actual MSA/SOW
//     bug — backend proposed_edits win over frontend extraction, so the cards
//     come from this list, not extractEditBlocks). Two identical multi-line
//     blocks → 3 per-field replace_all.
{
  const backendEdits: EditProposal[] = [
    {
      action: "replace",
      target_text: "Signed by: [__]\nTitle: [__]\nfor and on behalf of [__]",
      new_text: "Signed by: John Doe\nTitle: CTO\nfor and on behalf of Sony company",
      rationale: "main agreement",
    },
    {
      action: "replace",
      target_text: "Signed by: [__]\nTitle: [__]\nfor and on behalf of [__]",
      new_text: "Signed by: John Doe\nTitle: CTO\nfor and on behalf of Sony company",
      rationale: "appendix",
    },
  ];
  const out = normalizeProposals(backendEdits);
  pass(out.length === 3, "backend-list: normalized to 3 fields");
  pass(out.every((b) => b.action === "replace_all"), "backend-list: all replace_all");
  pass(out[0].target_text === "Signed by: [__]", "backend-list: field 1 target");
  pass(out[2].new_text === "for and on behalf of Sony company", "backend-list: field 3 new");

  // Idempotent: re-running yields the same shape (ChatTab may normalize a list
  // that extractEditBlocks already normalized).
  const again = normalizeProposals(out);
  pass(again.length === 3, "backend-list: idempotent length");
  pass(again.every((b) => b.action === "replace_all"), "backend-list: idempotent actions");
}

// 10b. A single, ordinary replace passes through normalizeProposals untouched —
//      we don't over-promote a lone edit to replace_all.
{
  const out = normalizeProposals([
    { action: "replace", target_text: "the fees paid", new_text: "2x the fees paid" },
  ]);
  pass(out.length === 1, "single-replace: unchanged count");
  pass(out[0].action === "replace", "single-replace: stays replace");
}

// 11. Tab-bundled signature line (trace 9e5b804c). The LLM prepends the dotted
//     signature line + a TAB to the first field ("…\tSigned by: [__]"). body.search
//     can't cross a tab, so that card failed. The tab reduction keeps only the
//     changed column, which then collapses with the plain "Signed by: [__]" fills.
{
  const dotted = "............................................";
  const edits: EditProposal[] = [
    {
      action: "replace",
      target_text: "Signed by: [__]\nTitle: [__]\nfor and on behalf of [__]",
      new_text: "Signed by: John Doe\nTitle: CTO\nfor and on behalf of Sony company",
    },
    {
      action: "replace",
      target_text: `${dotted}\tSigned by: [__]\nTitle: [__]\nfor and on behalf of [__]`,
      new_text: `${dotted}\tSigned by: John Doe\nTitle: CTO\nfor and on behalf of Sony company`,
    },
  ];
  const out = normalizeProposals(edits);
  pass(out.length === 3, "tab-bundled: collapsed to 3 clean fields");
  pass(out.every((b) => b.action === "replace_all"), "tab-bundled: all replace_all");
  pass(
    out.every((b) => !(b.target_text ?? "").includes("\t")),
    "tab-bundled: no tab survives in any target",
  );
  pass(
    out.some((b) => b.target_text === "Signed by: [__]" && b.new_text === "Signed by: John Doe"),
    "tab-bundled: dotted column dropped, field kept",
  );
}

// 12. Filled signature-block REWRITE (trace 32deb028). User explicitly asked to
//     change the Trinetix signatory (Boris → Suzy Quatro). It's a multi-line
//     replace of FILLED values (no blanks), one line unchanged. Split into
//     per-line `replace` (specific values, not replace_all) so each single line
//     is matchable; the unchanged "for and on behalf of" line is not emitted.
{
  const out = normalizeProposals([
    {
      action: "replace",
      target_text:
        "Signed by: Boris Bukengolts\nTitle: Chief Growth Officer\nfor and on behalf of Trinetix Inc.",
      new_text: "Signed by: Suzy Quatro\nTitle: CTO\nfor and on behalf of Trinetix Inc.",
    },
  ]);
  pass(out.length === 2, "filled-rewrite: split into the 2 changed fields");
  pass(out.every((b) => b.action === "replace"), "filled-rewrite: per-line replace, not replace_all");
  pass(
    out.some((b) => b.target_text === "Signed by: Boris Bukengolts" && b.new_text === "Signed by: Suzy Quatro"),
    "filled-rewrite: signatory line",
  );
  pass(
    out.some((b) => b.target_text === "Title: Chief Growth Officer" && b.new_text === "Title: CTO"),
    "filled-rewrite: title line",
  );
  pass(
    out.every((b) => !(b.target_text ?? "").includes("Trinetix Inc.")),
    "filled-rewrite: unchanged company line not emitted",
  );
}

// 12b. The model duplicated the filled-block rewrite card. Identical per-line
//      edits collapse to one replace_all per changed field (fills every matching
//      occurrence once — Boris appears once, so equivalent to replace).
{
  const edit = {
    action: "replace" as const,
    target_text:
      "Signed by: Boris Bukengolts\nTitle: Chief Growth Officer\nfor and on behalf of Trinetix Inc.",
    new_text: "Signed by: Suzy Quatro\nTitle: CTO\nfor and on behalf of Trinetix Inc.",
  };
  const out = normalizeProposals([edit, { ...edit }]);
  pass(out.length === 2, "filled-rewrite-dup: collapsed to 2 fields");
  pass(out.every((b) => b.action === "replace_all"), "filled-rewrite-dup: dup promoted to replace_all");
}

// 12c. Multi-paragraph PROSE rewrite — 2 changed lines, NO colon/blank field
//      markers — stays a single multi-line replace (word.ts head+tail span
//      matcher owns multi-paragraph clauses; splitting could mis-locate a line).
{
  const out = normalizeProposals([
    {
      action: "replace",
      target_text: "The fee was one hundred dollars.\nPayment was due in thirty days.",
      new_text: "The fee was two hundred dollars.\nPayment was due in sixty days.",
    },
  ]);
  pass(out.length === 1, "prose-multiline: not split");
  pass(out[0].action === "replace", "prose-multiline: stays replace");
}
