// Pure-helper checks for word.ts. Run with: npx tsx src/word.test.ts
// (The Office.js-dependent functions are smoke-tested by sideloading in Word.)
import { escapeWordWildcards, isAmbiguousBlankPlaceholder, shouldMatchWholeWord } from "./word";

const pass = (cond: boolean, label: string) =>
  console.log(cond ? `PASS: ${label}` : `FAIL: ${label}`);

// --- escapeWordWildcards: backslash-escape Word's wildcard metacharacters ---
pass(escapeWordWildcards("Signed by: [__]") === "Signed by: \\[__\\]", "escape: brackets escaped");
pass(escapeWordWildcards("plain text, no specials") === "plain text, no specials", "escape: plain untouched");
pass(escapeWordWildcards("a (b) {c}") === "a \\(b\\) \\{c\\}", "escape: parens + braces escaped");
pass(escapeWordWildcards("for and on behalf of [__]") === "for and on behalf of \\[__\\]", "escape: label + bracket");

// --- isAmbiguousBlankPlaceholder: bare blanks (replace_all would corrupt) ---
pass(isAmbiguousBlankPlaceholder("[__]"), "bare: [__] is ambiguous");
pass(isAmbiguousBlankPlaceholder("___"), "bare: underscores ambiguous");
pass(isAmbiguousBlankPlaceholder("[ ]"), "bare: empty bracket ambiguous");
pass(isAmbiguousBlankPlaceholder("[...]"), "bare: dotted bracket ambiguous");
pass(isAmbiguousBlankPlaceholder("  [__]  "), "bare: whitespace-padded still ambiguous");

// --- labeled / specific placeholders are NOT ambiguous (allowed) ---
pass(!isAmbiguousBlankPlaceholder("Signed by: [__]"), "labeled: 'Signed by: [__]' allowed");
pass(!isAmbiguousBlankPlaceholder("Title: [__]"), "labeled: 'Title: [__]' allowed");
pass(!isAmbiguousBlankPlaceholder("[Year]"), "specific: [Year] allowed");
pass(!isAmbiguousBlankPlaceholder("[Legal Name]"), "specific: [Legal Name] allowed");

// --- shouldMatchWholeWord: short clause-name anchors search whole-word-only ---
// so "Title" can't match mid-word inside "entitled". Short = <= 2 words.
pass(shouldMatchWholeWord("Title"), "wholeword: 1-word anchor -> true");
pass(shouldMatchWholeWord("Effective Date"), "wholeword: 2-word anchor -> true");
pass(shouldMatchWholeWord("Execution   Block"), "wholeword: collapses whitespace -> 2 words true");
pass(!shouldMatchWholeWord("Limitation of Liability"), "wholeword: 3-word phrase -> false");
pass(!shouldMatchWholeWord("The Receiving Party shall not"), "wholeword: 5-word phrase -> false");
pass(!shouldMatchWholeWord(""), "wholeword: empty -> false");
pass(!shouldMatchWholeWord("   "), "wholeword: whitespace-only -> false");
