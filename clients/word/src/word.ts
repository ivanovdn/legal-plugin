// Office.js helpers for the task pane. Each public function is async and
// returns a Result so callers can branch on success/failure without try/catch.
//
// All Word manipulation happens inside Word.run so the context object is
// scoped correctly and context.sync() is awaited before returning data.

import { normalizeForSearch } from "./normalize";
import type { EditProposal } from "./parseEditBlocks";

export type Result<T = void> = { ok: true; value: T } | { ok: false; error: string };

const ok = <T>(value: T): Result<T> => ({ ok: true, value });
const fail = (error: string): Result<never> => ({ ok: false, error });

const isWordAvailable = (): boolean => typeof Word !== "undefined";

/** Read the active document body as a single string. Stub when not in Word. */
export async function readBody(): Promise<string> {
  if (!isWordAvailable()) {
    return "Open this add-in inside Word to read the active document.";
  }
  return Word.run(async (context) => {
    const body = context.document.body;
    body.load("text");
    await context.sync();
    return body.text;
  });
}

/**
 * Build a list of search candidates from a clause quote.
 *
 * Office.js body.search() has a 255-char hard limit and cannot cross
 * paragraph boundaries. Full clause quotes routinely violate both. We try
 * progressively shorter prefixes — first sentence, ~200-char head, ~100-char
 * head — to find at least the START of the clause.
 */
// Characters Word's body.search treats as wildcard-special — even with
// matchWildcards off on Word for Mac it won't match them literally, so a needle
// containing them (e.g. a heading annotated "[Source: …]") silently returns no
// match. We fall back to the leading run before the first such character.
const SEARCH_SPECIAL = /[[\](){}<>?*@^~\\]/;

// Office.js body.search rejects strings over 255 chars with
// SearchStringInvalidOrTooLong. 200 leaves a safety margin and matches the
// existing direct-add filter below.
const SEARCH_MAX_LEN = 200;

function searchCandidates(needle: string): string[] {
  const normalized = normalizeForSearch(needle);
  const candidates: string[] = [];
  const push = (s: string) => {
    const t = s.trim();
    if (t && t.length >= 12 && t.length <= SEARCH_MAX_LEN && !candidates.includes(t)) candidates.push(t);
  };
  const add = (s: string) => {
    push(s);
    // Also try the clean leading run before the first wildcard-special char,
    // so "7. GOVERNING LAW [Source: …]" still matches via "7. GOVERNING LAW".
    const idx = s.search(SEARCH_SPECIAL);
    if (idx > 0) push(s.slice(0, idx));
  };

  if (normalized.length <= 200 && !/\n/.test(needle)) add(normalized);

  // First non-empty line of the original needle. Paragraph breaks (\n) survive
  // in the needle, so the first line is guaranteed to sit inside a single
  // paragraph — body.search can't cross breaks, so for multi-paragraph anchors
  // (e.g. a "Heading\nbody…" insert anchor) this matches the opening paragraph
  // where the longer head snippets straddle the break and always miss.
  const firstLine = needle.split(/\r?\n/).map((s) => s.trim()).find(Boolean);
  if (firstLine) add(normalizeForSearch(firstLine));

  const sentenceMatch = normalized.match(/^.+?[.!?](?:\s|$)/);
  if (sentenceMatch) add(sentenceMatch[0]);

  if (normalized.length > 200) {
    const head = normalized.slice(0, 200);
    const lastSpace = head.lastIndexOf(" ");
    add(lastSpace > 100 ? head.slice(0, lastSpace) : head);
  }

  if (normalized.length > 100) {
    const head = normalized.slice(0, 100);
    const lastSpace = head.lastIndexOf(" ");
    add(lastSpace > 50 ? head.slice(0, lastSpace) : head);
  }

  // Progressively shorter word-aligned prefixes. body.search runs against the
  // RAW document while our needle is normalized; when they differ mid-phrase
  // (a soft line break, an odd space) the full string silently fails to match
  // even though the text is present. A shorter leading run is far likelier to
  // land inside one clean run — findClauseRange then bridges to the tail.
  const words = normalized.split(/\s+/).filter(Boolean);
  for (const n of [12, 8, 5]) {
    if (words.length > n) add(words.slice(0, n).join(" "));
  }

  if (candidates.length && /['"]/.test(candidates[candidates.length - 1])) {
    const curly = candidates[candidates.length - 1].replace(/'/g, "’").replace(/"/g, "“");
    add(curly);
  }

  // Fallback: if the >= 12-char length filter rejected every variant (which
  // happens for parser-supplied anchors like "Parties", "Effective Date" —
  // intentional short clause-name anchors, not stray common words), include
  // the cleaned needle anyway. The parser chose this anchor on purpose; trust it.
  // Length must still be within SEARCH_MAX_LEN or body.search will throw.
  if (candidates.length === 0) {
    const trimmed = normalized.trim();
    if (trimmed && trimmed.length <= SEARCH_MAX_LEN) candidates.push(trimmed);
    const idx = trimmed.search(SEARCH_SPECIAL);
    if (idx > 0) {
      const clean = trimmed.slice(0, idx).trim();
      if (clean && clean !== trimmed && clean.length <= SEARCH_MAX_LEN) candidates.push(clean);
    }
  }

  return candidates;
}

/**
 * Build progressively shorter tail-snippet candidates so we can locate the END
 * of a clause even when the doc has aggressive mid-clause paragraph breaks
 * (body.search can't cross them).
 */
function tailCandidates(needle: string): string[] {
  const normalized = normalizeForSearch(needle);
  if (normalized.length < 40) return [];
  const candidates: string[] = [];
  const add = (s: string) => {
    const t = s.trim();
    if (t && t.length >= 6 && t.length <= 200 && !candidates.includes(t)) candidates.push(t);
  };

  // Last sentence first (most natural unit)
  const sentences = normalized.match(/[^.!?]+[.!?](?:\s|$)/g);
  if (sentences && sentences.length > 1) {
    add(sentences[sentences.length - 1]);
  }

  // Progressively shorter tails — first one to fit in a paragraph wins
  for (const N of [150, 100, 60, 40, 25, 15]) {
    if (normalized.length > N) {
      const tail = normalized.slice(-N);
      const firstSpace = tail.indexOf(" ");
      add(firstSpace > 0 ? tail.slice(firstSpace + 1) : tail);
    }
  }

  // Last 2–3 words as final fallback
  const words = normalized.split(/\s+/).filter(Boolean);
  if (words.length >= 3) add(words.slice(-3).join(" "));
  if (words.length >= 2) add(words.slice(-2).join(" "));

  return candidates;
}

async function searchFirst(
  context: Word.RequestContext,
  trial: string,
): Promise<Word.Range | null> {
  try {
    const results = context.document.body.search(trial, {
      matchCase: false,
      matchWildcards: false,
    });
    results.load("items");
    await context.sync();
    return results.items.length > 0 ? results.items[0] : null;
  } catch (e) {
    // body.search itself throws on malformed candidates (>255 chars,
    // certain wildcard-special char combinations the API rejects post-queue,
    // etc.). Treat as "no match" so the search loop moves on to the next
    // candidate rather than aborting the whole find/delete/redline flow.
    // Logged at warn so it's visible in DevTools without surfacing to the UI.
    console.warn("[word.ts] body.search rejected candidate:", trial.slice(0, 80), e);
    return null;
  }
}

/**
 * Locate the range of the text matching `currentText`.
 *
 * Office.js body.search() has a 255-char limit, can't cross paragraph breaks,
 * and runs against the raw document — so the full quote often fails to match
 * even when the text is present (soft breaks, normalization differences). So we:
 *   1. find the START via the first head candidate that matches (the full
 *      string, then progressively shorter word-aligned prefixes)
 *   2. find the END via the first tail candidate that matches
 *   3. return a range spanning the START match's start → END match's end
 *
 * Using the MATCH boundaries (not whole paragraphs) means a fragment quote
 * replaces exactly the fragment, while a full-clause quote still spans the
 * whole clause. If no tail is found, the head match alone is returned.
 */
async function findClauseRange(
  context: Word.RequestContext,
  currentText: string,
): Promise<Word.Range | null> {
  // Step 1: find the start via the first matching head candidate.
  let startMatch: Word.Range | null = null;
  for (const trial of searchCandidates(currentText)) {
    startMatch = await searchFirst(context, trial);
    if (startMatch) break;
  }
  if (!startMatch) return null;

  // Step 2: find the end via the first matching tail candidate.
  let endMatch: Word.Range | null = null;
  for (const trial of tailCandidates(currentText)) {
    endMatch = await searchFirst(context, trial);
    if (endMatch) break;
  }
  // No tail (short quote, or only the full string matched) → the head match
  // already covers the whole quote.
  if (!endMatch) return startMatch;

  // Step 3: span from the start of the head match to the end of the tail match.
  return startMatch
    .getRange(Word.RangeLocation.start)
    .expandTo(endMatch.getRange(Word.RangeLocation.end));
}

/**
 * Try each anchor in order; return the first range that matches.
 *
 * Used for findings where the parser can't pin down one definitive quote — for
 * example a Missing Context item whose Issue cell describes the gap ("Effective
 * date is a placeholder.") rather than quoting current wording. The parser
 * stacks several candidates (quoted text → clause-name segments → full clause →
 * issue text), strongest first; we walk that list until something lands.
 */
async function findClauseRangeFromAnchors(
  context: Word.RequestContext,
  anchors: string[],
): Promise<Word.Range | null> {
  for (const candidate of anchors) {
    if (!candidate.trim()) continue;
    const range = await findClauseRange(context, candidate);
    if (range) return range;
  }
  return null;
}

/** Normalize string|string[] into the ordered candidate list the helpers use. */
function toAnchors(input: string | string[]): string[] {
  return Array.isArray(input) ? input : [input];
}

/**
 * Scroll Word to the clause matching `currentText`, select its full range
 * (spanning all paragraphs the original quote covered), and attach a Word
 * Comment containing the supplied text.
 */
export async function showInDocument(
  target: string | string[],
  commentBody: string,
): Promise<Result<string>> {
  if (!isWordAvailable()) return fail("Word is not available (open the add-in inside Word).");
  const anchors = toAnchors(target).filter((s) => s.trim());
  if (anchors.length === 0) return fail("Empty clause text — nothing to locate.");
  try {
    return await Word.run(async (context) => {
      const range = await findClauseRangeFromAnchors(context, anchors);
      if (!range) return fail("Couldn't locate this clause in the document.");
      range.select();
      range.insertComment(commentBody);
      range.load("text");
      await context.sync();
      return ok(range.text);
    });
  } catch (e) {
    return fail(e instanceof Error ? e.message : String(e));
  }
}

/**
 * Replace the full clause matching `currentText` with `newText` as a tracked
 * change. Spans multiple paragraphs if needed (start of first match's
 * paragraph → end of tail match's paragraph). Saves and restores the
 * document's prior change-tracking mode.
 */
export async function acceptRedline(
  target: string | string[],
  newText: string,
): Promise<Result<void>> {
  if (!isWordAvailable()) return fail("Word is not available (open the add-in inside Word).");
  const anchors = toAnchors(target).filter((s) => s.trim());
  if (anchors.length === 0) return fail("Empty clause text — nothing to replace.");
  if (!newText.trim()) return fail("No redline provided.");
  try {
    return await Word.run(async (context) => {
      const range = await findClauseRangeFromAnchors(context, anchors);
      if (!range) return fail("Couldn't locate this clause in the document.");

      const doc = context.document;
      doc.load("changeTrackingMode");
      await context.sync();
      const originalMode = doc.changeTrackingMode;

      doc.changeTrackingMode = Word.ChangeTrackingMode.trackAll;
      try {
        range.insertText(newText, Word.InsertLocation.replace);
        await context.sync();
      } finally {
        doc.changeTrackingMode = originalMode;
        await context.sync();
      }
      return ok(undefined);
    });
  } catch (e) {
    return fail(e instanceof Error ? e.message : String(e));
  }
}

/**
 * Insert `newText` immediately before or after the range matching `anchorText`,
 * as a tracked change. Used for chat-driven additions like "add a force majeure
 * clause after Section 7." The agent's `new_text` should include any leading
 * newline or paragraph break it wants visible in the document.
 */
export async function insertNear(
  anchorText: string,
  position: "after" | "before",
  newText: string,
): Promise<Result<void>> {
  if (!isWordAvailable()) return fail("Word is not available (open the add-in inside Word).");
  if (!anchorText.trim()) return fail("Empty anchor text — nothing to insert near.");
  if (!newText.trim()) return fail("No insertion text provided.");
  try {
    return await Word.run(async (context) => {
      // Anchor on a SINGLE line/paragraph, not the whole multi-paragraph anchor:
      // for "after" use the anchor's LAST line (end of the section), for
      // "before" its FIRST line. Each line sits in one paragraph, so body.search
      // matches reliably — and we insert a whole new paragraph relative to it,
      // which keeps the new clause from splitting the section or gluing on.
      const lines = anchorText.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
      const targetLine = position === "after" ? lines[lines.length - 1] : lines[0];

      let match: Word.Range | null = null;
      for (const trial of searchCandidates(targetLine)) {
        match = await searchFirst(context, trial);
        if (match) break;
      }
      if (!match) return fail("Couldn't locate the anchor in the document.");
      match.paragraphs.load("items");
      await context.sync();
      if (match.paragraphs.items.length === 0) {
        return fail("Couldn't resolve the anchor paragraph.");
      }
      const anchorParagraph = match.paragraphs.items[0];

      const doc = context.document;
      doc.load("changeTrackingMode");
      await context.sync();
      const originalMode = doc.changeTrackingMode;

      // Split the new clause into individual paragraphs so embedded newlines
      // become real paragraph breaks (insertParagraph would otherwise render a
      // raw "\n" as literal text). Insert each as its own paragraph, in order.
      const newParas = newText.split(/\r?\n/).map((p) => p.trim()).filter(Boolean);

      doc.changeTrackingMode = Word.ChangeTrackingMode.trackAll;
      try {
        if (position === "after") {
          // Chain after the anchor so paragraphs keep their order.
          let ref = anchorParagraph;
          for (const p of newParas) {
            ref = ref.insertParagraph(p, Word.InsertLocation.after);
          }
        } else {
          // Insert each before the anchor, in order.
          for (const p of newParas) {
            anchorParagraph.insertParagraph(p, Word.InsertLocation.before);
          }
        }
        await context.sync();
      } finally {
        doc.changeTrackingMode = originalMode;
        await context.sync();
      }
      return ok(undefined);
    });
  } catch (e) {
    return fail(e instanceof Error ? e.message : String(e));
  }
}

/**
 * Delete the range matching `targetText` as a tracked change (strikethrough).
 * Used for chat-driven removals like "delete the auto-renewal language."
 */
async function deleteClause(targetText: string): Promise<Result<void>> {
  if (!isWordAvailable()) return fail("Word is not available (open the add-in inside Word).");
  if (!targetText.trim()) return fail("Empty target text — nothing to delete.");
  try {
    return await Word.run(async (context) => {
      const range = await findClauseRange(context, targetText);
      if (!range) return fail("Couldn't locate this text in the document.");

      const doc = context.document;
      doc.load("changeTrackingMode");
      await context.sync();
      const originalMode = doc.changeTrackingMode;

      doc.changeTrackingMode = Word.ChangeTrackingMode.trackAll;
      try {
        range.delete();
        await context.sync();
      } finally {
        doc.changeTrackingMode = originalMode;
        await context.sync();
      }
      return ok(undefined);
    });
  } catch (e) {
    return fail(e instanceof Error ? e.message : String(e));
  }
}

/**
 * Apply a chat-proposed edit by dispatching to the right Office.js helper
 * based on `proposal.action`. All variants run inside Word.run and produce a
 * tracked change the lawyer reviews via Word's Review ribbon.
 */
export async function applyEdit(proposal: EditProposal): Promise<Result<void>> {
  if (proposal.action === "replace") {
    if (!proposal.target_text || !proposal.new_text) {
      return fail("Replace proposal missing target_text or new_text.");
    }
    return acceptRedline(proposal.target_text, proposal.new_text);
  }
  if (proposal.action === "insert") {
    if (!proposal.anchor_text || !proposal.new_text || !proposal.position) {
      return fail("Insert proposal missing anchor_text, position, or new_text.");
    }
    return insertNear(proposal.anchor_text, proposal.position, proposal.new_text);
  }
  if (proposal.action === "delete") {
    if (!proposal.target_text) return fail("Delete proposal missing target_text.");
    return deleteClause(proposal.target_text);
  }
  return fail(`Unknown action: ${String((proposal as { action: unknown }).action)}`);
}
