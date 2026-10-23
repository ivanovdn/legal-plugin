// Finding text from the ACCEPTED-CHANGES view in Word's RAW text.
//
// The model reads the document as body.getReviewedText("current") — tracked
// deletions dropped (see readBody) — but body.search matches the raw document,
// struck deletions included. So a quote that runs across a struck run never
// matches as written: to Word, "Sony Corp, mailing address at [Address]" is
// "Sony Corp[Legal Name], mailing address at [Address]" when "[Legal Name]" is
// a tracked deletion (VM, 2026-10-23).
//
// The two views differ only by the struck runs: the current text is the raw
// text with some stretches taken out. So a quote's place in the current text
// maps onto one stretch of the raw text — struck runs inside it included — and
// that stretch is what Word can find. Where the mapping is ambiguous (a struck
// run that repeats the text beside it), two stretches are offered, the tight
// one first. Either way the caller accepts a match only after Word's own
// reading of it (Range.getReviewedText) is the quote, so a wrong guess applies
// nothing.

import { normalizeForSearch } from "./normalize";

export type TrackedView = {
  /** Raw stretches that may hold the first occurrence of `needle` in the
   *  accepted-changes text, best first; empty when it isn't there. */
  stretchesFor(needle: string, wholeWord: boolean): string[];
  /** One raw stretch per occurrence of `needle`, distinct, in document order. */
  stretchesForEvery(needle: string): string[];
  /** How many times `needle` occurs in the accepted-changes text. */
  count(needle: string): number;
};

// How body.text and getReviewedText write line breaks is not established, so
// any of these counts as the same character.
const BREAKS = "\r\n\v\f";
const isBreak = (c: string): boolean => BREAKS.includes(c);
const sameChar = (a: string, b: string): boolean => a === b || (isBreak(a) && isBreak(b));
const breaksAsOne = (s: string): string => s.replace(/\r\n|[\r\n\v\f]/g, "\n");

// body.search never matches across a paragraph mark or a line break, nor a
// literal tab, and rejects a string over 255 characters.
const UNSEARCHABLE = /[\r\n\v\f\t]/;
const WORD_SEARCH_LIMIT = 255;
const searchable = (s: string): boolean => s.length > 0 && s.length <= WORD_SEARCH_LIMIT && !UNSEARCHABLE.test(s);

const WORD_CHAR = /[\p{L}\p{N}_]/u;

/** One character as normalizeForSearch and Word's case-insensitive search see it. */
const fold = (c: string): string => {
  if (c === "‘" || c === "’") return "'";
  if (c === "“" || c === "”") return '"';
  if (/\s/.test(c)) return " ";
  const lower = c.toLowerCase();
  return lower.length === 1 ? lower : c;
};

/** `text` folded, whitespace runs collapsed; `at[k]` is the source index of folded character k. */
type Folded = { text: string; at: number[] };

const foldText = (s: string): Folded => {
  const chars: string[] = [];
  const at: number[] = [];
  for (let i = 0; i < s.length; i++) {
    const f = fold(s[i]);
    if (f === " " && chars[chars.length - 1] === " ") continue;
    chars.push(f);
    at.push(i);
  }
  return { text: chars.join(""), at };
};

const foldNeedle = (needle: string): string => foldText(normalizeForSearch(needle)).text;

/** Every non-overlapping occurrence of `needle` in the folded text, as [start, end) in the source. */
const occurrences = (folded: Folded, needle: string, wholeWord: boolean): Array<[number, number]> => {
  const key = foldNeedle(needle);
  if (!key) return [];
  const found: Array<[number, number]> = [];
  let k = folded.text.indexOf(key);
  while (k !== -1) {
    const before = k > 0 ? folded.text[k - 1] : "";
    const after = folded.text[k + key.length] ?? "";
    if (wholeWord && (WORD_CHAR.test(before) || WORD_CHAR.test(after))) {
      k = folded.text.indexOf(key, k + 1);
      continue;
    }
    found.push([folded.at[k], folded.at[k + key.length - 1] + 1]);
    k = folded.text.indexOf(key, k + key.length);
  }
  return found;
};

/** For each current character, the earliest (`left`) and latest (`right`) raw
 *  index it can sit at. Null when current is not raw with stretches taken out. */
type Embedding = { left: Int32Array; right: Int32Array };

const embed = (raw: string, current: string): Embedding | null => {
  const n = current.length;
  const left = new Int32Array(n);
  let i = 0;
  for (let j = 0; j < n; j++) {
    while (i < raw.length && !sameChar(raw[i], current[j])) i++;
    if (i === raw.length) return null;
    left[j] = i++;
  }
  // A left embedding exists, so the right one does too.
  const right = new Int32Array(n);
  i = raw.length - 1;
  for (let j = n - 1; j >= 0; j--) {
    while (!sameChar(raw[i], current[j])) i--;
    right[j] = i--;
  }
  return { left, right };
};

/** The raw stretches for current [start, end): the tight one (struck runs only
 *  inside), then the wide one (struck runs at the edges too), searchable only. */
const stretchesAt = (raw: string, e: Embedding, start: number, end: number): string[] => {
  const out: string[] = [];
  const add = (s: string) => {
    if (searchable(s) && !out.includes(s)) out.push(s);
  };
  if (e.right[start] <= e.left[end - 1]) add(raw.slice(e.right[start], e.left[end - 1] + 1));
  add(raw.slice(e.left[start], e.right[end - 1] + 1));
  return out;
};

/** Null when the document holds no tracked deletions — the two views differ by
 *  nothing but how they write line breaks — so the caller keeps today's path. */
export function trackedView(raw: string, current: string): TrackedView | null {
  if (breaksAsOne(raw) === breaksAsOne(current)) return null;
  const folded = foldText(current);
  const e = embed(raw, current);
  // Views that don't line up (a character the raw text lacks): search the
  // text as written. The caller still checks every match's accepted-changes text.
  const asWritten = (needle: string): string[] => (searchable(needle) ? [needle] : []);
  return {
    stretchesFor(needle, wholeWord) {
      const first = occurrences(folded, needle, wholeWord)[0];
      if (!first) return [];
      return e ? stretchesAt(raw, e, first[0], first[1]) : asWritten(needle);
    },
    stretchesForEvery(needle) {
      const all = occurrences(folded, needle, false);
      if (all.length === 0) return [];
      if (!e) return asWritten(needle);
      const out: string[] = [];
      for (const [start, end] of all) {
        const best = stretchesAt(raw, e, start, end)[0];
        if (best !== undefined && !out.includes(best)) out.push(best);
      }
      return out;
    },
    count: (needle) => occurrences(folded, needle, false).length,
  };
}

/** Is a match's accepted-changes text the text searched for, compared the way
 *  Word's case-insensitive search and normalizeForSearch compare? */
export function sameLiveText(live: string, expected: string): boolean {
  return foldNeedle(live) === foldNeedle(expected);
}
