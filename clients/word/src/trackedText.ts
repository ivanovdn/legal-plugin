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
// one first.
//
// Which match is ours is settled by counting, not by asking Word: in Word for
// Mac 16.114 a range's getReviewedText("current") still holds its struck runs
// (measured 2026-10-27), so it can't tell a live match from a struck copy. We
// hold the raw text, so each stretch carries its place among the stretch's raw
// occurrences, counted the way Word's search counts; Word returns matches in
// document order, and the caller takes match `nth` only when Word found exactly
// `total` — any disagreement and nothing is applied.

import { normalizeForSearch } from "./normalize";

/** Raw text to search for, and which of its `total` raw occurrences is the one meant. */
export type Stretch = { text: string; nth: number; total: number };

export type TrackedView = {
  /** Raw stretches that may hold the first occurrence of `needle` in the
   *  accepted-changes text, best first; empty when it isn't there. Search each
   *  with the same `wholeWord`. */
  stretchesFor(needle: string, wholeWord: boolean): Stretch[];
  /** One raw stretch per occurrence of `needle`, in document order. */
  stretchesForEvery(needle: string): Stretch[];
  /** How many times `needle` occurs in the accepted-changes text. */
  count(needle: string): number;
};

// The views do not agree about line breaks: in Word for Mac 16.114 the
// accepted-changes text ends with a paragraph mark body.text lacks (measured
// 2026-10-27), and nothing else about how either writes breaks is established.
// So breaks are left out of the comparison on both sides. That costs nothing:
// a stretch with a break in it can't be searched anyway.
const isBreak = (c: string): boolean => "\r\n\v\f".includes(c);
const withoutBreaks = (s: string): string => s.replace(/[\r\n\v\f]/g, "");

// body.search never matches across a paragraph mark or a line break, nor a
// literal tab, and rejects a string over 255 characters.
const UNSEARCHABLE = /[\r\n\v\f\t]/;
const WORD_SEARCH_LIMIT = 255;
const searchable = (s: string): boolean => s.length > 0 && s.length <= WORD_SEARCH_LIMIT && !UNSEARCHABLE.test(s);

const WORD_CHAR = /[\p{L}\p{N}_]/u;

/** One character as normalizeForSearch and Word's case-insensitive search see it. */
const fold = (c: string): string => {
  if (isBreak(c)) return " ";
  if (c === "‘" || c === "’") return "'";
  if (c === "“" || c === "”") return '"';
  if (/\s/.test(c)) return " ";
  const lower = c.toLowerCase();
  return lower.length === 1 ? lower : c;
};

/** `text` folded, whitespace runs collapsed; `at[k]` is the source index of folded character k. */
type Folded = { text: string; at: number[] };

/** `keepBreaks` folds breaks to a character no needle holds, so an occurrence
 *  can't span one — as Word's search can't. */
const foldText = (s: string, keepBreaks = false): Folded => {
  const chars: string[] = [];
  const at: number[] = [];
  for (let i = 0; i < s.length; i++) {
    const f = keepBreaks && isBreak(s[i]) ? "\n" : fold(s[i]);
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
 *  index it can sit at; -1 for a break, which is not placed. Null when current,
 *  breaks aside, is not raw with stretches taken out. */
type Embedding = { left: Int32Array; right: Int32Array };

const embed = (raw: string, current: string): Embedding | null => {
  const n = current.length;
  const left = new Int32Array(n);
  let i = 0;
  for (let j = 0; j < n; j++) {
    if (isBreak(current[j])) {
      left[j] = -1;
      continue;
    }
    while (i < raw.length && raw[i] !== current[j]) i++;
    if (i === raw.length) return null;
    left[j] = i++;
  }
  // A left embedding exists, so the right one does too.
  const right = new Int32Array(n);
  i = raw.length - 1;
  for (let j = n - 1; j >= 0; j--) {
    if (isBreak(current[j])) {
      right[j] = -1;
      continue;
    }
    while (raw[i] !== current[j]) i--;
    right[j] = i--;
  }
  return { left, right };
};

/** The raw stretches for current [start, end): the tight one (struck runs only
 *  inside), then the wide one (struck runs at the edges too), searchable only,
 *  each placed among its raw occurrences. An occurrence starts and ends on a
 *  non-break: its needle is trimmed. */
const stretchesAt = (
  raw: string,
  rawFolded: Folded,
  e: Embedding,
  start: number,
  end: number,
  wholeWord: boolean,
): Stretch[] => {
  const out: Stretch[] = [];
  if (e.left[start] < 0 || e.right[end - 1] < 0) return out;
  const add = (from: number, to: number) => {
    const text = raw.slice(from, to);
    if (!searchable(text) || out.some((s) => s.text === text)) return;
    const all = occurrences(rawFolded, text, wholeWord);
    const nth = all.findIndex(([at]) => at === from);
    if (nth >= 0) out.push({ text, nth, total: all.length });
  };
  if (e.right[start] <= e.left[end - 1]) add(e.right[start], e.left[end - 1] + 1);
  add(e.left[start], e.right[end - 1] + 1);
  return out;
};

/** Null when the document holds no tracked deletions — the two views differ by
 *  nothing but how they write line breaks — so the caller keeps today's path. */
export function trackedView(raw: string, current: string): TrackedView | null {
  if (withoutBreaks(raw) === withoutBreaks(current)) return null;
  const folded = foldText(current);
  const rawFolded = foldText(raw, true);
  const e = embed(raw, current);
  // Views that don't line up (a character the raw text lacks): search the text
  // as written, and only when every raw occurrence of it is live — then the
  // k-th live occurrence is the k-th raw one.
  const asWritten = (needle: string, wholeWord: boolean, live: number): Stretch[] => {
    const total = occurrences(rawFolded, needle, wholeWord).length;
    if (!searchable(needle) || total !== live) return [];
    return Array.from({ length: live }, (_, nth) => ({ text: needle, nth, total }));
  };
  return {
    stretchesFor(needle, wholeWord) {
      const live = occurrences(folded, needle, wholeWord);
      if (live.length === 0) return [];
      if (!e) return asWritten(needle, wholeWord, live.length).slice(0, 1);
      return stretchesAt(raw, rawFolded, e, live[0][0], live[0][1], wholeWord);
    },
    stretchesForEvery(needle) {
      const live = occurrences(folded, needle, false);
      if (live.length === 0) return [];
      if (!e) return asWritten(needle, false, live.length);
      const out: Stretch[] = [];
      for (const [start, end] of live) {
        const best = stretchesAt(raw, rawFolded, e, start, end, false)[0];
        if (best !== undefined) out.push(best);
      }
      return out;
    },
    count: (needle) => occurrences(folded, needle, false).length,
  };
}
