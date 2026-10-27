// Finding text from the accepted-changes view in Word's raw text.
// Run with: npx tsx src/trackedText.test.ts
import { trackedView, type Stretch } from "./trackedText";
import { pass } from "./testAssert";

const same = (a: unknown, b: unknown): boolean => JSON.stringify(a) === JSON.stringify(b);
const texts = (xs: Stretch[] | undefined): string[] | undefined => xs?.map((x) => x.text);

// --- No tracked deletions: the caller keeps today's path ---
pass(trackedView("Clause one.\rClause two.", "Clause one.\rClause two.") === null, "no deletions: same text -> null");
pass(
  trackedView("Clause one.\rClause two.", "Clause one.\nClause two.") === null,
  "no deletions: line breaks written differently -> null",
);

// --- The VM failure (2026-10-23): "[Legal Name]" struck, "Sony Corp" inserted before it ---
const vm = trackedView(
  'between Sony Corp[Legal Name], mailing address at [Address] (the "Company")',
  'between Sony Corp, mailing address at [Address] (the "Company")',
);
pass(vm !== null, "VM: a deletion is present -> a view");
pass(
  same(texts(vm?.stretchesFor("Sony Corp, mailing address at [Address]", false)), [
    "Sony Corp[Legal Name], mailing address at [Address]",
  ]),
  "VM: a quote across the struck run -> the raw stretch with it inside",
);
pass(same(texts(vm?.stretchesFor("Trinetix Inc", false)), []), "absent: not in the accepted text -> nothing to search");
pass(
  same(texts(vm?.stretchesFor("[Legal Name]", false)), []),
  "struck only: text that survives only as a deletion is not an occurrence",
);

// --- Real Word (Mac 16.114, 2026-10-27): the accepted-changes text ends with a
// paragraph mark body.text lacks; the views still line up ---
const measured = trackedView(
  'between Sony Corp[Legal Name], mailing address at [Address]\rfor and on behalf of Trinetix Inc.\r\n',
  'between Sony Corp, mailing address at [Address]\rfor and on behalf of Trinetix Inc.\r\n\r',
);
pass(
  same(texts(measured?.stretchesFor("Sony Corp, mailing address at [Address]", false)), [
    "Sony Corp[Legal Name], mailing address at [Address]",
  ]),
  "measured: an extra trailing paragraph mark does not stop the quote mapping across the struck run",
);
pass(
  trackedView("Clause one.\rClause two.", "Clause one.\rClause two.\r") === null,
  "measured: no deletions, only the extra trailing paragraph mark -> null",
);
pass(
  same(texts(trackedView("one[x] two\rthree", "one two\r\rthree")?.stretchesFor("one two", false)), ["one[x] two"]),
  "a stray break the raw text lacks, mid-document, does not break the mapping",
);

// --- A struck run just before the quote stays out of its stretch ---
const edge = trackedView("between [Legal Name]Sony Corp, mailing", "between Sony Corp, mailing");
pass(same(texts(edge?.stretchesFor("Sony Corp, mailing", false)), ["Sony Corp, mailing"]), "edge: the struck run before the quote is left out");

// --- Ambiguous: the struck "big " shares letters with "bag" ---
const ambiguous = trackedView("a big bag", "a bag");
pass(
  same(texts(ambiguous?.stretchesFor("bag", false)), ["bag", "big bag"]),
  "ambiguous: the tight stretch first, the wide one as a fallback",
);

// --- Folding: case, quote style and spacing as Word's search compares them ---
const curly = trackedView("the “Company”[old] shall  pay", "the “Company” shall  pay");
pass(
  same(texts(curly?.stretchesFor('the "company" shall pay', false)), ["the “Company”[old] shall  pay"]),
  "folding: straight quotes, lower case and single spaces find the document's own characters",
);

// --- Whole word, for single-word anchors ---
const words = trackedView("He is entitled[x] to a Title", "He is entitled to a Title");
pass(same(texts(words?.stretchesFor("Title", true)), ["Title"]), "whole word: skips 'title' inside 'entitled'");
pass(same(texts(words?.stretchesFor("Title", false)), ["title"]), "not whole word: the first occurrence, inside 'entitled'");

// --- A stretch Word cannot search is not offered ---
const merged = trackedView("End of one.\rStart of two.", "End of one.Start of two.");
pass(
  merged === null,
  "struck paragraph mark only: breaks are not compared, so no view -> today's path (its quote can't be searched either way)",
);
const mergedAndStruck = trackedView("End of one[x].\rStart of two.", "End of one.Start of two.");
pass(
  same(texts(mergedAndStruck?.stretchesFor("one.Start", false)), []),
  "struck paragraph mark: the stretch would cross a paragraph -> not offered",
);

// --- Views that don't line up: search the text as written; matches are still checked ---
const unaligned = trackedView("Notice period: 30 days[old]", "Notice period: 30 days.");
pass(same(texts(unaligned?.stretchesFor("30 days", false)), ["30 days"]), "unaligned views: the text as written");
pass(
  same(texts(unaligned?.stretchesFor("30 days.", false)), []),
  "unaligned views: text the raw document lacks is not searched (no way to tell which match is live)",
);

// --- Every occurrence, for "fill every" ---
const fill = trackedView(
  "The Company: Sony Corp[Legal Name]\rfor and on behalf of [Legal Name]\rSigned for [Legal Name] by its director",
  "The Company: Sony Corp\rfor and on behalf of [Legal Name]\rSigned for [Legal Name] by its director",
);
pass(fill?.count("[Legal Name]") === 2, "count: the struck '[Legal Name]' is not an occurrence");
pass(
  same(fill?.stretchesForEvery("[Legal Name]"), [
    { text: "[Legal Name]", nth: 1, total: 3 },
    { text: "[Legal Name]", nth: 2, total: 3 },
  ]),
  "every: one stretch per live occurrence, each pointing past the struck copy",
);
const inside = trackedView("Party: [Legal Full Name] and [Legal Name]", "Party: [Legal Name] and [Legal Name]");
pass(
  same(texts(inside?.stretchesForEvery("[Legal Name]")), ["[Legal Full Name]", "[Legal Name]"]),
  "every: an occurrence across a struck run gets its own stretch, in document order",
);

// --- Which raw occurrence: Word's matches come in document order, and Range.getReviewedText
// keeps struck runs (measured, Word for Mac 16.114), so the caller picks a match by index ---
pass(
  same(vm?.stretchesFor("Sony Corp, mailing address at [Address]", false), [
    { text: "Sony Corp[Legal Name], mailing address at [Address]", nth: 0, total: 1 },
  ]),
  "index: the VM stretch is the only raw occurrence",
);
const struckFirst = trackedView("Signed: [Legal Name]Sony\rFor [Legal Name] only", "Signed: Sony\rFor [Legal Name] only");
pass(
  same(struckFirst?.stretchesFor("[Legal Name]", false), [{ text: "[Legal Name]", nth: 1, total: 2 }]),
  "index: a struck copy earlier in the document is counted and skipped",
);
pass(
  same(trackedView("x[y] a b\ra\rb end", "x a b\ra\rb end")?.stretchesFor("a b", false), [
    { text: "a b", nth: 0, total: 1 },
  ]),
  "index: a raw occurrence across a paragraph mark is not counted (Word's search can't match it)",
);
pass(
  same(words?.stretchesFor("Title", true), [{ text: "Title", nth: 0, total: 1 }]),
  "index: whole-word counting matches whole-word search",
);
pass(
  same(words?.stretchesFor("Title", false), [{ text: "title", nth: 0, total: 2 }]),
  "index: substring counting includes 'entitled'",
);
