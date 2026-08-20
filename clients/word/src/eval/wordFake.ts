// A fake of the slice of Office.js the matcher touches.
//
// WRITTEN FROM CLAUDE.md's Word gotchas and the Office.js documentation —
// deliberately NOT from word.ts. Writing it from the caller would encode the
// caller's assumptions and the eval would then confirm itself.
//
// FIDELITY LIMIT: this does not implement Word.RequestContext structurally
// (that would be hundreds of members), so it is installed on globalThis and
// cast at the boundary. The compiler therefore does NOT verify shape
// conformance against @types/office-js. Nothing here references the Word
// namespace yet; once Task 3 sources the enum values from the real Word.*
// enums, tsc catches enum drift and that becomes the one genuine compiler
// check this file gets.
//
// BLIND SPOTS, stated rather than discovered later: revision rendering,
// expandTo across tables, list renumbering, content controls, and wildcard
// MATCHING (only escaped-literal wildcard queries are supported — see
// searchSync). A case asserts which characters, never how they look.

export type FakeParagraph = string | { raw: string; reviewed: string };

export type SearchOptions = {
  matchCase: boolean;
  matchWildcards: boolean;
  matchWholeWord: boolean;
};

// Word treats these as wildcards even with matchWildcards:false on Mac.
const WILDCARD_META = /[[\]{}()<>?*]/;
const WORD_CHAR = /[A-Za-z0-9_]/;

type Para = { raw: string; reviewed: string };

const toPara = (p: FakeParagraph): Para =>
  typeof p === "string" ? { raw: p, reviewed: p } : { raw: p.raw, reviewed: p.reviewed };

const isWholeWordAt = (haystack: string, index: number, length: number): boolean => {
  const before = index > 0 ? haystack[index - 1] : "";
  const after = index + length < haystack.length ? haystack[index + length] : "";
  return !WORD_CHAR.test(before) && !WORD_CHAR.test(after);
};

/** Every occurrence of `needle` in `haystack`, honouring case and whole-word. */
const occurrences = (
  haystack: string,
  needle: string,
  matchCase: boolean,
  matchWholeWord: boolean,
): number[] => {
  const hay = matchCase ? haystack : haystack.toLowerCase();
  const nee = matchCase ? needle : needle.toLowerCase();
  const hits: number[] = [];
  let from = 0;
  for (;;) {
    const at = hay.indexOf(nee, from);
    if (at === -1) return hits;
    if (!matchWholeWord || isWholeWordAt(haystack, at, needle.length)) hits.push(at);
    from = at + 1;
  }
};

export function createFakeWord(paragraphs: FakeParagraph[]) {
  const paras: Para[] = paragraphs.map(toPara);

  /**
   * The eight rules. Returns the matching substrings, per paragraph, in
   * document order.
   */
  const searchSync = (query: string, opts: SearchOptions): string[] => {
    // Rule 1 — Word's real limit is 255; word.ts filters at a conservative 200.
    if (query.length > 255) {
      throw new Error("SearchStringInvalidOrTooLong");
    }
    // Rule 2 — a search can never cross a paragraph mark.
    if (/[\n\r]/.test(query)) return [];
    // Rule 8 — a literal tab never matches, even where Word renders one.
    if (query.includes("\t")) return [];

    let needle = query;
    if (opts.matchWildcards) {
      // Rule 4 — only fully-escaped metacharacters are supported. An unescaped
      // metacharacter means real wildcard matching, which is a stated blind
      // spot: return no match rather than pretend.
      const unescaped = needle.replace(/\\(.)/g, "$1");
      const hadUnescapedMeta = WILDCARD_META.test(needle.replace(/\\./g, ""));
      if (hadUnescapedMeta) return [];
      needle = unescaped;
    } else if (WILDCARD_META.test(needle)) {
      // Rule 3 — Word for Mac mis-reads these as wildcards even in literal
      // mode, so the needle silently misses.
      return [];
    }
    if (!needle) return [];

    const found: string[] = [];
    for (const para of paras) {
      // Rule 7 — always the RAW text, tracked deletions included.
      for (const at of occurrences(para.raw, needle, opts.matchCase, opts.matchWholeWord)) {
        found.push(para.raw.slice(at, at + needle.length));
      }
    }
    return found;
  };

  return {
    searchSync,
    rawText: () => paras.map((p) => p.raw).join("\r"),
    reviewedText: () => paras.map((p) => p.reviewed).join("\r"),
    install: () => {
      /* Task 3 installs the globalThis.Word namespace here. */
    },
    uninstall: () => {
      /* Task 3 */
    },
  };
}
