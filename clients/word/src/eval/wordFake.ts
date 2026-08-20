// A fake of the slice of Office.js the matcher touches.
//
// WRITTEN FROM CLAUDE.md's Word gotchas and the Office.js documentation —
// deliberately NOT from word.ts. Writing it from the caller would encode the
// caller's assumptions and the eval would then confirm itself.
//
// FIDELITY LIMIT: this does not implement Word.RequestContext structurally
// (that would be hundreds of members), so it is installed on globalThis and
// cast at the boundary. The compiler therefore does NOT verify shape
// conformance against @types/office-js. What it DOES verify: the enum
// value objects below are declared `as const satisfies Record<string,
// `${Word.Enum}`>` — the literals are hand-written (the real enums have no
// runtime presence here; this file is what installs `Word`), but `satisfies`
// makes tsc reject any value the real enum doesn't declare, so a typo or a
// renamed enum member is a compile error (TS2820), not a silent runtime miss.
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

  // Values are hand-written literals, checked against the real Word.* enums
  // via `satisfies` — see the FIDELITY LIMIT note in the file header.
  const ChangeTrackingMode = {
    off: "Off",
    trackAll: "TrackAll",
    trackMineOnly: "TrackMineOnly",
  } as const satisfies Record<string, `${Word.ChangeTrackingMode}`>;
  const RangeLocation = { start: "Start", end: "End", whole: "Whole" } as const satisfies Record<
    string,
    `${Word.RangeLocation}`
  >;
  const InsertLocation = { replace: "Replace" } as const satisfies Record<string, `${Word.InsertLocation}`>;

  type Mutation = { start: number; end: number; text: string; tracked: boolean };

  let trackingMode: string = ChangeTrackingMode.off;
  const modeLog: string[] = [];
  const pendingLoads: Array<() => void> = [];
  const pendingMutations: Mutation[] = [];

  // Offsets are into the RAW document, paragraphs joined by "\r".
  const paraOffsets = (): number[] => {
    const offs: number[] = [];
    let at = 0;
    for (const p of paras) {
      offs.push(at);
      at += p.raw.length + 1; // +1 for the paragraph mark
    }
    return offs;
  };

  const rawTextOf = (): string => paras.map((p) => p.raw).join("\r");
  const rawSlice = (start: number, end: number): string => rawTextOf().slice(start, end);

  type FakeRange = {
    start: number;
    end: number;
    _text?: string;
    load(prop: string): void;
    readonly text: string;
    getRange(loc: string): FakeRange;
    expandTo(other: FakeRange): FakeRange;
    insertText(text: string, loc: string): void;
  };

  const makeRange = (start: number, end: number): FakeRange => {
    const range: FakeRange = {
      start,
      end,
      load(prop: string) {
        if (prop === "text") {
          pendingLoads.push(() => {
            range._text = rawSlice(range.start, range.end);
          });
        }
      },
      get text(): string {
        if (range._text === undefined) {
          throw new Error("PropertyNotLoaded: call load('text') then context.sync()");
        }
        return range._text;
      },
      getRange(loc: string) {
        return loc === RangeLocation.end ? makeRange(range.end, range.end) : makeRange(range.start, range.start);
      },
      expandTo(other: FakeRange) {
        return makeRange(Math.min(range.start, other.start), Math.max(range.end, other.end));
      },
      insertText(text: string, loc: string) {
        if (loc !== InsertLocation.replace) {
          throw new Error(`fake supports only InsertLocation.replace, got ${loc}`);
        }
        pendingMutations.push({
          start: range.start,
          end: range.end,
          text,
          tracked: trackingMode === ChangeTrackingMode.trackAll,
        });
      },
    };
    return range;
  };

  /** Locate the paragraph containing a raw document offset. */
  const paraAt = (offset: number): { index: number; local: number } => {
    const offs = paraOffsets();
    for (let i = offs.length - 1; i >= 0; i--) {
      if (offset >= offs[i]) return { index: i, local: offset - offs[i] };
    }
    return { index: 0, local: offset };
  };

  const applyMutation = (m: Mutation): void => {
    const { index, local } = paraAt(m.start);
    const para = paras[index];
    const length = m.end - m.start;
    const original = para.raw.slice(local, local + length);
    // Tracked: the original stays in RAW (struck) and is dropped from REVIEWED.
    // Untracked: it is gone from both.
    para.raw = para.raw.slice(0, local) + (m.tracked ? original : "") + m.text + para.raw.slice(local + length);
    const reviewedAt = para.reviewed.indexOf(original);
    if (reviewedAt !== -1) {
      para.reviewed =
        para.reviewed.slice(0, reviewedAt) + m.text + para.reviewed.slice(reviewedAt + original.length);
    }
  };

  const sync = async (): Promise<void> => {
    // Descending start offset, so a snapshot of ranges taken before any
    // mutation stays valid — the guarantee replaceAll depends on.
    pendingMutations.sort((a, b) => b.start - a.start);
    for (const m of pendingMutations) applyMutation(m);
    pendingMutations.length = 0;
    for (const load of pendingLoads) load();
    pendingLoads.length = 0;
  };

  const makeCollection = (query: string, opts: SearchOptions) => {
    const collection = {
      items: [] as FakeRange[],
      load(_prop: string) {
        pendingLoads.push(() => {
          collection.items = searchRanges(query, opts);
        });
      },
    };
    return collection;
  };

  /** searchSync, but returning ranges in raw-document coordinates. */
  const searchRanges = (query: string, opts: SearchOptions): FakeRange[] => {
    const hits = searchSync(query, opts);
    if (hits.length === 0) return [];
    const raw = rawTextOf();
    const ranges: FakeRange[] = [];
    let from = 0;
    for (const hit of hits) {
      const at = opts.matchCase
        ? raw.indexOf(hit, from)
        : raw.toLowerCase().indexOf(hit.toLowerCase(), from);
      if (at === -1) continue;
      ranges.push(makeRange(at, at + hit.length));
      from = at + 1;
    }
    return ranges;
  };

  const document = {
    get changeTrackingMode(): string {
      return trackingMode;
    },
    set changeTrackingMode(value: string) {
      trackingMode = value;
      modeLog.push(value);
    },
    load(_prop: string) {
      /* changeTrackingMode is readable immediately in the fake */
    },
    body: {
      search: (query: string, opts: SearchOptions) => makeCollection(query, opts),
    },
  };

  const namespace = {
    run: async <T>(cb: (context: { document: typeof document; sync: () => Promise<void> }) => Promise<T>): Promise<T> =>
      cb({ document, sync }),
    ChangeTrackingMode,
    RangeLocation,
    InsertLocation,
  };

  return {
    searchSync,
    rawText: rawTextOf,
    reviewedText: () => paras.map((p) => p.reviewed).join("\r"),
    trackingModeLog: () => [...modeLog],
    install: () => {
      (globalThis as Record<string, unknown>).Word = namespace;
    },
    uninstall: () => {
      delete (globalThis as Record<string, unknown>).Word;
    },
  };
}
