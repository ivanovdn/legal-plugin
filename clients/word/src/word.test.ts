// Pure-helper checks for word.ts. Run with: npx tsx src/word.test.ts
// (The Office.js-dependent functions are smoke-tested by sideloading in Word.)
import {
  ambiguousBlankRejection,
  escapeWordWildcards,
  isAmbiguousBlankPlaceholder,
  shouldMatchWholeWord,
} from "./word";
import { pass } from "./testAssert";

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

// --- shouldMatchWholeWord: single-word clause anchors search whole-word-only ---
// so "Title" can't match mid-word inside "entitled". Only 1-word trials qualify:
// matchWholeWord is unverified/harmful on space-containing (multi-word) queries.
pass(shouldMatchWholeWord("Title"), "wholeword: 1-word anchor -> true");
pass(shouldMatchWholeWord("  Confidentiality  "), "wholeword: 1-word w/ padding -> true");
pass(!shouldMatchWholeWord("Effective Date"), "wholeword: 2-word anchor -> false (substring-tolerant)");
pass(!shouldMatchWholeWord("Limitation of Liability"), "wholeword: 3-word phrase -> false");
pass(!shouldMatchWholeWord("The Receiving Party shall not"), "wholeword: 5-word phrase -> false");
pass(!shouldMatchWholeWord(""), "wholeword: empty -> false");
pass(!shouldMatchWholeWord("   "), "wholeword: whitespace-only -> false");

// --- ambiguousBlankRejection: BOTH value-writing actions refuse a bare blank ---
// replace_all was already guarded; replace was not, so the model's
// replace "[__]" -> "Blizzard Corp" would have written a company name into the
// signature block's first blank (trace cc81804f).
pass(ambiguousBlankRejection("replace", "[__]") !== null, "reject: replace on bare [__]");
pass(ambiguousBlankRejection("replace_all", "[__]") !== null, "reject: replace_all on bare [__]");
pass(ambiguousBlankRejection("replace", "___") !== null, "reject: replace on bare underscores");
pass(ambiguousBlankRejection("replace", "  [__]  ") !== null, "reject: padded bare blank");
// Labelled and specific targets stay applyable on both actions.
pass(ambiguousBlankRejection("replace", "Signed by: [__]") === null, "allow: replace 'Signed by: [__]'");
pass(ambiguousBlankRejection("replace_all", "[Year]") === null, "allow: replace_all '[Year]'");
pass(ambiguousBlankRejection("replace", "Blizzard Corp") === null, "allow: replace real text");
// The message names the field-by-line remedy so the model can self-correct.
pass(
  (ambiguousBlankRejection("replace", "[__]") ?? "").includes("Signed by: [__]"),
  "reject message suggests targeting the field's own line",
);
