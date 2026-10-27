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
// TRACKED REPLACE, measured in Word for Mac 16.114 (VM 2026-10-23; sideload
// 2026-10-27): the new text goes BEFORE the struck original, and a tracked
// deletion wholly inside the replaced range merges into the new deletion. (Real
// Word also drops the author's own earlier insertion instead of striking it;
// the fake keeps no insertions, so it strikes it — raw differs there, the
// accepted-changes text does not.) An edit that only PARTLY overlaps a deletion
// is not observed, so it throws (see applyMutation) rather than guess.
//
// MODEL NOTE: `reviewed` is never stored. A paragraph is `raw` plus a list of
// deletion spans (raw coordinates); `reviewed` is always `raw` with those
// spans removed, computed on demand (see `reviewedOf`). Storing two strings
// that mutation code must keep in agreement by hand was tried in an earlier
// revision of this file and broke: whichever side wasn't the offset of
// record drifted the moment a tracked deletion and a later untracked edit
// coexisted in the same paragraph. Deriving `reviewed` structurally makes
// that class of bug impossible instead of merely fixed. A `{raw, reviewed}`
// fixture pair must differ by exactly one contiguous span (longest-common-
// prefix/suffix locates it) — anything else throws at construction time,
// naming the paragraph, rather than silently producing wrong ground truth.
//
// VIEWS: `body.text` is the raw text, tracked deletions still in it (CLAUDE.md:
// "body.text includes tracked-change deletions"); `getReviewedText("Current")`
// drops them (CLAUDE.md: the text as if every change were accepted). The Range
// form does NOT: Office.js describes it the same way, but in Word for Mac
// 16.114 a searched range's "current" text kept its struck run (measured
// 2026-10-27), so the fake returns the raw slice. "Original" is refused: this fake does not track
// insertions, so it cannot know the original text. Both views join paragraphs
// with "\r", and Body.getReviewedText adds one more "\r" at the end — the
// one difference measured in real Word (Mac 16.114, 2026-10-27), which broke
// the first version of trackedText.ts. How else real Word writes breaks is not
// established, so word.ts must not depend on the two views agreeing about them.

export type FakeParagraph = string | { raw: string; reviewed: string };

export type SearchOptions = {
  matchCase: boolean;
  matchWildcards: boolean;
  matchWholeWord: boolean;
};

// Word treats these as wildcards even with matchWildcards:false on Mac.
const WILDCARD_META = /[[\]{}()<>?*]/;
const WORD_CHAR = /[A-Za-z0-9_]/;

type Deletion = { start: number; end: number };
type Para = { raw: string; deletions: Deletion[] };

/**
 * Derive the single contiguous deletion span implied by a `{raw, reviewed}`
 * fixture pair via longest-common-prefix + longest-common-suffix: whatever
 * `raw` has in the unmatched middle is the deleted span. Throws if `reviewed`
 * cannot be reached from `raw` by removing exactly one contiguous span — a
 * multi-span or non-deletion difference isn't representable by this model, so
 * a case author finds out immediately instead of quietly getting wrong ground
 * truth. A multi-span fixture is not needed by this corpus.
 */
const deriveDeletions = (raw: string, reviewed: string, label: string): Deletion[] => {
  if (raw === reviewed) return [];
  const minLen = Math.min(raw.length, reviewed.length);
  let prefixLen = 0;
  while (prefixLen < minLen && raw[prefixLen] === reviewed[prefixLen]) prefixLen++;
  const maxSuffix = minLen - prefixLen;
  let suffixLen = 0;
  while (suffixLen < maxSuffix && raw[raw.length - 1 - suffixLen] === reviewed[reviewed.length - 1 - suffixLen]) {
    suffixLen++;
  }
  const rawMiddleStart = prefixLen;
  const rawMiddleEnd = raw.length - suffixLen;
  const reviewedMiddleStart = prefixLen;
  const reviewedMiddleEnd = reviewed.length - suffixLen;
  if (reviewedMiddleStart !== reviewedMiddleEnd) {
    throw new Error(
      `FakeParagraph ${label}: reviewed cannot be derived from raw by removing exactly one contiguous ` +
        `span (raw=${JSON.stringify(raw)}, reviewed=${JSON.stringify(reviewed)})`,
    );
  }
  return [{ start: rawMiddleStart, end: rawMiddleEnd }];
};

const toPara = (p: FakeParagraph, label: string): Para =>
  typeof p === "string" ? { raw: p, deletions: [] } : { raw: p.raw, deletions: deriveDeletions(p.raw, p.reviewed, label) };

/** `raw` with every deletion span removed — the reviewed view of one paragraph. */
const reviewedOf = (para: Para): string => {
  let out = "";
  let cursor = 0;
  for (const d of para.deletions) {
    out += para.raw.slice(cursor, d.start);
    cursor = d.end;
  }
  out += para.raw.slice(cursor);
  return out;
};

/**
 * Belt-and-braces: confirms a paragraph's deletions are still sorted and
 * non-overlapping after a mutation batch. Should be unreachable once
 * applyMutation's overlap check (which refuses an edit that re-touches an
 * existing deletion) is doing its job — if this ever fires, that check has a
 * gap, not just this one paragraph.
 */
const assertDeletionsWellFormed = (para: Para, label: string): void => {
  for (let i = 1; i < para.deletions.length; i++) {
    if (para.deletions[i].start < para.deletions[i - 1].end) {
      throw new Error(
        `FakeParagraph ${label}: deletions invariant violated after sync() — spans ` +
          `${JSON.stringify(para.deletions)} are not sorted and non-overlapping.`,
      );
    }
  }
};

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
  const paras: Para[] = paragraphs.map((p, i) => toPara(p, `#${i}`));

  type Hit = { start: number; end: number; text: string };

  /**
   * The eight rules, resolved ONCE to hits carrying an absolute raw-document
   * offset alongside the matched text. `searchSync` and `searchRanges` both
   * derive from this single pass so a match decided here (in particular a
   * matchWholeWord accept/reject) can never be re-decided differently
   * downstream.
   *
   * An earlier revision had `searchRanges` re-locate positions with a SECOND,
   * whole-word-UNAWARE, case-insensitive substring scan of the text
   * `searchSync` returned. That silently mis-located a match: found by Task 4's
   * "Title" vs "entitled" case (CLAUDE.md's `shouldMatchWholeWord` gotcha) —
   * `occurrences()` correctly rejected "title" inside "entitled" and accepted
   * standalone "Title", but the old `searchRanges` then re-searched for the
   * literal text "Title" case-insensitively from the start of the document and
   * landed on the earlier, whole-word-REJECTED "entitled" substring instead.
   * Not a fidelity gap worth stating (see file header) — an actual bug, since
   * it made the fake disagree with its own `occurrences()` computation.
   */
  const locate = (query: string, opts: SearchOptions): Hit[] => {
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

    const hits: Hit[] = [];
    const offsets = paraOffsets();
    paras.forEach((para, i) => {
      // Rule 7 — always the RAW text, tracked deletions included.
      for (const at of occurrences(para.raw, needle, opts.matchCase, opts.matchWholeWord)) {
        hits.push({
          start: offsets[i] + at,
          end: offsets[i] + at + needle.length,
          text: para.raw.slice(at, at + needle.length),
        });
      }
    });
    return hits;
  };

  /** The eight rules. Returns the matching substrings, per paragraph, in document order. */
  const searchSync = (query: string, opts: SearchOptions): string[] =>
    locate(query, opts).map((hit) => hit.text);

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
  const ChangeTrackingVersion = { original: "Original", current: "Current" } as const satisfies Record<
    string,
    `${Word.ChangeTrackingVersion}`
  >;

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


  /** A ClientResult: its value is computed at the next sync() and throws if read before. */
  const clientResult = (compute: () => string): { readonly value: string } => {
    let loaded = false;
    let value = "";
    pendingLoads.push(() => {
      value = compute();
      loaded = true;
    });
    return {
      get value(): string {
        if (!loaded) throw new Error("ValueNotLoaded: call context.sync() before reading value");
        return value;
      },
    };
  };

  const reviewedText = (version: string, compute: () => string): { readonly value: string } => {
    if (version !== ChangeTrackingVersion.current) {
      throw new Error(`fake models only ChangeTrackingVersion.current (it does not track insertions), got ${version}`);
    }
    return clientResult(compute);
  };

  type FakeRange = {
    start: number;
    end: number;
    _text?: string;
    load(prop: string): void;
    readonly text: string;
    getRange(loc: string): FakeRange;
    expandTo(other: FakeRange): FakeRange;
    insertText(text: string, loc: string): void;
    getReviewedText(version: string): { readonly value: string };
    select(): void;
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
        if (loc === RangeLocation.start) return makeRange(range.start, range.start);
        if (loc === RangeLocation.end) return makeRange(range.end, range.end);
        // Refuse rather than guess (this file's own stated design principle):
        // `whole` is declared in RangeLocation for shape-fidelity against the
        // real enum, but no caller collapses a range to itself today, and
        // guessing which point it should resolve to would be inventing
        // behavior nobody has validated against real Word.
        throw new Error(`fake supports only RangeLocation.start/end, got ${loc}`);
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
      getReviewedText(version: string) {
        // Measured, Word for Mac 16.114 (2026-10-27): a range's accepted-changes
        // text still holds the struck runs inside it — unlike the body's.
        return reviewedText(version, () => rawSlice(range.start, range.end));
      },
      select() {
        /* navigation only: the fake does not model the selection */
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
    const s = local;
    const e = local + length;
    // Refuse rather than guess: an edit that cuts INTO an existing deletion
    // (one end inside the struck run), or an untracked edit over one, is not
    // observed in real Word (see TRACKED REPLACE). A deletion wholly inside a
    // tracked edit is (it merges).
    const straddling = para.deletions.find(
      (d) => d.start < e && d.end > s && (d.start < s || d.end > e || !m.tracked),
    );
    if (straddling) {
      throw new Error(
        `FakeParagraph #${index}: edit at raw [${s}, ${e}) cuts into an existing tracked deletion at ` +
          `[${straddling.start}, ${straddling.end}). What real Word does with an edit that ends inside a ` +
          `struck run, or an untracked edit over one, is not observed — this needs a real-Word observation before it can be modelled, ` +
          `not a fix to this fake.`,
      );
    }
    if (m.tracked) {
      // Tracked: the new text goes in BEFORE the original span, which stays in
      // RAW (struck) — measured. Deletions inside the span merge into its new
      // deletion; any deletion at or after `s` shifts right by the inserted length.
      para.raw = para.raw.slice(0, s) + m.text + para.raw.slice(s);
      para.deletions = para.deletions.filter((d) => !(d.start >= s && d.end <= e));
      for (const d of para.deletions) {
        if (d.start >= s) {
          d.start += m.text.length;
          d.end += m.text.length;
        }
      }
      para.deletions.push({ start: s + m.text.length, end: e + m.text.length });
      para.deletions.sort((a, b) => a.start - b.start);
    } else {
      // Untracked: the span is genuinely removed from RAW. Any deletion at or
      // after the edit shifts by the net length delta (new text vs. removed
      // span) so its offsets stay correct in the new, shorter-or-longer raw.
      para.raw = para.raw.slice(0, s) + m.text + para.raw.slice(e);
      const delta = m.text.length - (e - s);
      if (delta !== 0) {
        for (const d of para.deletions) {
          if (d.start >= e) {
            d.start += delta;
            d.end += delta;
          }
        }
      }
    }
  };

  const sync = async (): Promise<void> => {
    // Descending start offset, so a snapshot of ranges taken before any
    // mutation stays valid — the guarantee replaceAll depends on.
    pendingMutations.sort((a, b) => b.start - a.start);
    for (const m of pendingMutations) applyMutation(m);
    pendingMutations.length = 0;
    paras.forEach((para, i) => assertDeletionsWellFormed(para, `#${i}`));
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

  /** locate(), but returning ranges in raw-document coordinates rather than text. */
  const searchRanges = (query: string, opts: SearchOptions): FakeRange[] =>
    locate(query, opts).map((hit) => makeRange(hit.start, hit.end));

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
      load(prop: string) {
        if (prop === "text") {
          pendingLoads.push(() => {
            bodyText = rawTextOf();
          });
        }
      },
      get text(): string {
        if (bodyText === undefined) {
          throw new Error("PropertyNotLoaded: call load('text') then context.sync()");
        }
        return bodyText;
      },
      // Measured, Word for Mac 16.114 (2026-10-27): the body's accepted-changes
      // text ends with one paragraph mark body.text does not have.
      getReviewedText: (version: string) => reviewedText(version, () => paras.map(reviewedOf).join("\r") + "\r"),
    },
  };
  let bodyText: string | undefined;

  const namespace = {
    run: async <T>(cb: (context: { document: typeof document; sync: () => Promise<void> }) => Promise<T>): Promise<T> =>
      cb({ document, sync }),
    ChangeTrackingMode,
    ChangeTrackingVersion,
    RangeLocation,
    InsertLocation,
  };

  return {
    searchSync,
    rawText: rawTextOf,
    reviewedText: () => paras.map(reviewedOf).join("\r"),
    trackingModeLog: () => [...modeLog],
    install: () => {
      (globalThis as Record<string, unknown>).Word = namespace;
    },
    uninstall: () => {
      delete (globalThis as Record<string, unknown>).Word;
    },
  };
}
