// The Word fake's eight semantic rules, each a documented CLAUDE.md gotcha.
// Run with: npx tsx src/wordFake.test.ts
//
// These are unit tests of OUR fake, not of word.ts. If one fails, either the
// fake is wrong or a gotcha we believed about Word is wrong — both need a human.
import { createFakeWord, type FakeParagraph } from "./eval/wordFake";
import { pass } from "./testAssert";

const DOC = ["1. GOVERNING LAW", "Signed by: [__]", "Title: Chief Executive", "entitled to notice"];

const search = (
  paragraphs: string[],
  query: string,
  opts: { matchCase?: boolean; matchWildcards?: boolean; matchWholeWord?: boolean } = {},
): string[] => {
  const fake = createFakeWord(paragraphs);
  fake.install();
  try {
    return fake.searchSync(query, {
      matchCase: opts.matchCase ?? false,
      matchWildcards: opts.matchWildcards ?? false,
      matchWholeWord: opts.matchWholeWord ?? false,
    });
  } finally {
    fake.uninstall();
  }
};

// --- Rule 1: over 255 chars throws (Word's real limit, not word.ts's 200) ---
let threw = false;
try {
  search(DOC, "x".repeat(256));
} catch {
  threw = true;
}
pass(threw, "rule1: a 256-char query throws");
// The paragraph is exactly the needle: `occurrences` advances by one character,
// so a needle of 255 y's inside a paragraph of 300 y's would match 46 times.
pass(search(["y".repeat(255)], "y".repeat(255)).length === 1, "rule1: 255 chars is allowed");
pass(search(["z".repeat(210)], "z".repeat(210)).length === 1, "rule1: 201-255 is allowed by Word even though word.ts filters at 200");

// --- Rule 2: never crosses a paragraph break ---
pass(search(DOC, "1. GOVERNING LAW\nSigned by:").length === 0, "rule2: a needle with \\n never matches");
pass(search(DOC, "GOVERNING LAW").length === 1, "rule2: within one paragraph matches");

// A case author can embed a newline inside a single paragraph string; the
// guard is what stops the fake matching across it. Without this assertion the
// rule is invisible, because paragraphs are separate array elements and no
// para.raw in the DOC fixture contains a newline at all.
pass(search(["Alpha\nBeta"], "Alpha\nBeta").length === 0, "rule2: never matches across an embedded paragraph mark");

// --- Rule 3: bracket metacharacters miss in literal mode ---
pass(search(DOC, "Signed by: [__]").length === 0, "rule3: literal mode misses a bracketed blank");
pass(search(DOC, "Signed by:").length === 1, "rule3: the clean leading run still matches");

// --- Rule 4: escaped metacharacters match literally in wildcard mode ---
pass(
  search(DOC, "Signed by: \\[__\\]", { matchWildcards: true }).length === 1,
  "rule4: escaped brackets match in wildcard mode",
);

// --- Rule 5: case-insensitive by default ---
pass(search(DOC, "governing law").length === 1, "rule5: matchCase false is insensitive");
pass(search(DOC, "governing law", { matchCase: true }).length === 0, "rule5: matchCase true is sensitive");

// --- Rule 6: whole-word bounds ---
pass(search(DOC, "title", { matchWholeWord: true }).length === 1, "rule6: whole word matches a standalone word");
pass(search(DOC, "title", { matchWholeWord: false }).length === 2, "rule6: substring mode also matches inside 'entitled'");
pass(search(DOC, "entitle", { matchWholeWord: true }).length === 0, "rule6: whole word rejects a prefix of a longer word");

// --- Rule 7: searches RAW text, including tracked deletions ---
const tracked = createFakeWord([{ raw: "Signed by: [__]Suzy Quatro", reviewed: "Signed by: Suzy Quatro" }]);
tracked.install();
pass(
  tracked.searchSync("\\[__\\]", { matchCase: false, matchWildcards: true, matchWholeWord: false }).length === 1,
  "rule7: a tracked deletion is still findable in the raw text",
);
pass(tracked.reviewedText() === "Signed by: Suzy Quatro", "rule7: reviewed text drops the deletion");
pass(tracked.rawText().includes("[__]"), "rule7: raw text keeps the deletion");
tracked.uninstall();

// --- Rule 8: a literal tab never matches ---
pass(
  search(["Signed by: [__]\tSigned by: Boris"], "Signed by: [__]\tSigned by: Boris").length === 0,
  "rule8: a needle containing a tab never matches",
);
pass(search(["Signed by: Ann\tSigned by: Boris"], "Signed by: Boris").length === 1, "rule8: a tab-free segment still matches");

// No wildcard metacharacters in this needle, so rule 3 cannot reject it first
// — the tab guard is the only thing that can, which is what isolates it.
pass(search(["Signed by: Ann\tSigned by: Boris"], "Ann\tSigned by").length === 0, "rule8: a tab-containing needle misses even with no metacharacters");

// --- Ranges, lazy loading, and tracked mutation ---
const withFake = async <T>(paras: FakeParagraph[], fn: (f: ReturnType<typeof createFakeWord>) => Promise<T>): Promise<T> => {
  const fake = createFakeWord(paras);
  fake.install();
  try {
    return await fn(fake);
  } finally {
    fake.uninstall();
  }
};

// items is empty until sync
await withFake(["Governing Law applies here"], async () => {
  await Word.run(async (context) => {
    const results = context.document.body.search("Governing Law", {
      matchCase: false, matchWildcards: false, matchWholeWord: false,
    });
    results.load("items");
    pass(results.items.length === 0, "lazy: items empty before sync");
    await context.sync();
    pass(results.items.length === 1, "lazy: items populated after sync");
  });
});

// text throws until loaded
await withFake(["Governing Law applies here"], async () => {
  await Word.run(async (context) => {
    const results = context.document.body.search("Governing Law", {
      matchCase: false, matchWildcards: false, matchWholeWord: false,
    });
    results.load("items");
    await context.sync();
    let threwOnText = false;
    try {
      void results.items[0].text;
    } catch {
      threwOnText = true;
    }
    pass(threwOnText, "lazy: range.text throws before load");
    results.items[0].load("text");
    await context.sync();
    pass(results.items[0].text === "Governing Law", "lazy: range.text readable after load");
  });
});

// expandTo spans head start -> tail end, by match boundary not paragraph
await withFake(["ALPHA middle text OMEGA tail"], async (fake) => {
  await Word.run(async (context) => {
    const body = context.document.body;
    const heads = body.search("ALPHA", { matchCase: false, matchWildcards: false, matchWholeWord: false });
    const tails = body.search("OMEGA", { matchCase: false, matchWildcards: false, matchWholeWord: false });
    heads.load("items");
    tails.load("items");
    await context.sync();
    const span = heads.items[0]
      .getRange(Word.RangeLocation.start)
      .expandTo(tails.items[0].getRange(Word.RangeLocation.end));
    span.load("text");
    await context.sync();
    pass(span.text === "ALPHA middle text OMEGA", "range: expandTo spans head start to tail end");
    pass(fake.rawText() === "ALPHA middle text OMEGA tail", "range: expandTo does not mutate");
  });
});

// tracked replacement: raw keeps the deletion, reviewed does not
await withFake(["Signed by: Boris"], async (fake) => {
  await Word.run(async (context) => {
    const doc = context.document;
    const results = doc.body.search("Boris", { matchCase: false, matchWildcards: false, matchWholeWord: false });
    results.load("items");
    await context.sync();
    doc.changeTrackingMode = Word.ChangeTrackingMode.trackAll;
    results.items[0].insertText("Suzy", Word.InsertLocation.replace);
    await context.sync();
    doc.changeTrackingMode = Word.ChangeTrackingMode.off;
    await context.sync();
  });
  pass(fake.reviewedText() === "Signed by: Suzy", "tracked: reviewed shows the replacement");
  pass(fake.rawText() === "Signed by: BorisSuzy", "tracked: raw keeps the struck original");
  pass(
    fake.trackingModeLog().join(",") === "TrackAll,Off",
    "tracked: every changeTrackingMode assignment is logged in order",
  );
});

// untracked replacement leaves no struck text
await withFake(["Signed by: Boris"], async (fake) => {
  await Word.run(async (context) => {
    const results = context.document.body.search("Boris", { matchCase: false, matchWildcards: false, matchWholeWord: false });
    results.load("items");
    await context.sync();
    results.items[0].insertText("Suzy", Word.InsertLocation.replace);
    await context.sync();
  });
  pass(fake.rawText() === "Signed by: Suzy", "untracked: raw has no struck original");
});

// a snapshot of ranges survives replacing earlier ones
await withFake(["fee here and fee there"], async (fake) => {
  await Word.run(async (context) => {
    const results = context.document.body.search("fee", { matchCase: false, matchWildcards: false, matchWholeWord: false });
    results.load("items");
    await context.sync();
    pass(results.items.length === 2, "snapshot: both matches collected upfront");
    for (const m of results.items) m.insertText("charge", Word.InsertLocation.replace);
    await context.sync();
  });
  pass(fake.rawText() === "charge here and charge there", "snapshot: both replacements land correctly");
});
