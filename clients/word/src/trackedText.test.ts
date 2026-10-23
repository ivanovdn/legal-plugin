// Finding text from the accepted-changes view in Word's raw text.
// Run with: npx tsx src/trackedText.test.ts
import { sameLiveText, trackedView } from "./trackedText";
import { pass } from "./testAssert";

const same = (a: unknown, b: unknown): boolean => JSON.stringify(a) === JSON.stringify(b);

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
  same(vm?.stretchesFor("Sony Corp, mailing address at [Address]", false), [
    "Sony Corp[Legal Name], mailing address at [Address]",
  ]),
  "VM: a quote across the struck run -> the raw stretch with it inside",
);
pass(same(vm?.stretchesFor("Trinetix Inc", false), []), "absent: not in the accepted text -> nothing to search");
pass(
  same(vm?.stretchesFor("[Legal Name]", false), []),
  "struck only: text that survives only as a deletion is not an occurrence",
);

// --- A struck run just before the quote stays out of its stretch ---
const edge = trackedView("between [Legal Name]Sony Corp, mailing", "between Sony Corp, mailing");
pass(same(edge?.stretchesFor("Sony Corp, mailing", false), ["Sony Corp, mailing"]), "edge: the struck run before the quote is left out");

// --- Ambiguous: the struck "big " shares letters with "bag" ---
const ambiguous = trackedView("a big bag", "a bag");
pass(
  same(ambiguous?.stretchesFor("bag", false), ["bag", "big bag"]),
  "ambiguous: the tight stretch first, the wide one as a fallback",
);

// --- Folding: case, quote style and spacing as Word's search compares them ---
const curly = trackedView("the “Company”[old] shall  pay", "the “Company” shall  pay");
pass(
  same(curly?.stretchesFor('the "company" shall pay', false), ["the “Company”[old] shall  pay"]),
  "folding: straight quotes, lower case and single spaces find the document's own characters",
);

// --- Whole word, for single-word anchors ---
const words = trackedView("He is entitled[x] to a Title", "He is entitled to a Title");
pass(same(words?.stretchesFor("Title", true), ["Title"]), "whole word: skips 'title' inside 'entitled'");
pass(same(words?.stretchesFor("Title", false), ["title"]), "not whole word: the first occurrence, inside 'entitled'");

// --- A stretch Word cannot search is not offered ---
const merged = trackedView("End of one.\rStart of two.", "End of one.Start of two.");
pass(
  same(merged?.stretchesFor("one.Start", false), []),
  "struck paragraph mark: the stretch would cross a paragraph -> not offered",
);

// --- Views that don't line up: search the text as written; matches are still checked ---
const unaligned = trackedView("Notice period: 30 days[old]", "Notice period: 30 days.");
pass(same(unaligned?.stretchesFor("30 days.", false), ["30 days."]), "unaligned views: the text as written");

// --- Every occurrence, for "fill every" ---
const fill = trackedView(
  "The Company: Sony Corp[Legal Name]\rfor and on behalf of [Legal Name]\rSigned for [Legal Name] by its director",
  "The Company: Sony Corp\rfor and on behalf of [Legal Name]\rSigned for [Legal Name] by its director",
);
pass(fill?.count("[Legal Name]") === 2, "count: the struck '[Legal Name]' is not an occurrence");
pass(same(fill?.stretchesForEvery("[Legal Name]"), ["[Legal Name]"]), "every: two live occurrences, one distinct stretch");
const inside = trackedView("Party: [Legal Full Name] and [Legal Name]", "Party: [Legal Name] and [Legal Name]");
pass(
  same(inside?.stretchesForEvery("[Legal Name]"), ["[Legal Full Name]", "[Legal Name]"]),
  "every: an occurrence across a struck run gets its own stretch, in document order",
);

// --- sameLiveText: is a match's accepted-changes text the text searched for? ---
pass(sameLiveText("Sony Corp, mailing  address", "sony corp, mailing address"), "live text: case and spacing ignored");
pass(sameLiveText("the “Company”", 'the "Company"'), "live text: curly and straight quotes alike");
pass(!sameLiveText("", "[Legal Name]"), "live text: a fully struck match has none");
pass(!sameLiveText("Sony Corp", "Sony Corp, mailing"), "live text: a partial match is not the text");
