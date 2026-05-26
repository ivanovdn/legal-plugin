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
function searchCandidates(needle: string): string[] {
  const normalized = normalizeForSearch(needle);
  const candidates: string[] = [];
  const add = (s: string) => {
    const t = s.trim();
    if (t && t.length >= 12 && !candidates.includes(t)) candidates.push(t);
  };

  if (normalized.length <= 200 && !/\n/.test(needle)) add(normalized);

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

  if (candidates.length && /['"]/.test(candidates[candidates.length - 1])) {
    const curly = candidates[candidates.length - 1].replace(/'/g, "’").replace(/"/g, "“");
    add(curly);
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
  const results = context.document.body.search(trial, { matchCase: false });
  results.load("items");
  await context.sync();
  return results.items.length > 0 ? results.items[0] : null;
}

/**
 * Locate the full range of the clause matching `currentText`.
 *
 * Office.js body.search() has a 255-char limit and cannot cross paragraph
 * boundaries, so we:
 *   1. find the START via a short head snippet
 *   2. find the END via a short tail snippet (if currentText is long enough)
 *   3. extend a range from the start-paragraph's start to the end-paragraph's end
 *
 * Falls back to single-paragraph match if the tail can't be found.
 * Returns a Range covering the full clause (possibly spanning multiple paragraphs).
 */
async function findClauseRange(
  context: Word.RequestContext,
  currentText: string,
): Promise<Word.Range | null> {
  // Step 1: find first match via progressively shorter head candidates
  let startMatch: Word.Range | null = null;
  for (const trial of searchCandidates(currentText)) {
    startMatch = await searchFirst(context, trial);
    if (startMatch) break;
  }
  if (!startMatch) return null;

  startMatch.paragraphs.load("items");
  await context.sync();
  if (startMatch.paragraphs.items.length === 0) return startMatch;
  const startParagraph = startMatch.paragraphs.items[0];
  const startRange = startParagraph.getRange(Word.RangeLocation.start);

  // Step 2: try to find the end via progressively shorter tail snippets.
  // The shortest candidates (last 2–3 words) reliably fit in a single paragraph
  // even when the doc has mid-clause hard breaks.
  let endMatch: Word.Range | null = null;
  for (const trial of tailCandidates(currentText)) {
    endMatch = await searchFirst(context, trial);
    if (endMatch) break;
  }
  if (!endMatch) {
    return startParagraph.getRange(Word.RangeLocation.whole);
  }
  endMatch.paragraphs.load("items");
  await context.sync();
  if (endMatch.paragraphs.items.length === 0) {
    return startParagraph.getRange(Word.RangeLocation.whole);
  }
  const endParagraph = endMatch.paragraphs.items[0];
  const endRange = endParagraph.getRange(Word.RangeLocation.end);

  // Step 3: build a range from start of first paragraph to end of last paragraph
  return startRange.expandTo(endRange);
}

/**
 * Scroll Word to the clause matching `currentText`, select its full range
 * (spanning all paragraphs the original quote covered), and attach a Word
 * Comment containing the supplied text.
 */
export async function showInDocument(currentText: string, commentBody: string): Promise<Result<string>> {
  if (!isWordAvailable()) return fail("Word is not available (open the add-in inside Word).");
  if (!currentText.trim()) return fail("Empty clause text — nothing to locate.");
  try {
    return await Word.run(async (context) => {
      const range = await findClauseRange(context, currentText);
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
export async function acceptRedline(currentText: string, newText: string): Promise<Result<void>> {
  if (!isWordAvailable()) return fail("Word is not available (open the add-in inside Word).");
  if (!currentText.trim()) return fail("Empty clause text — nothing to replace.");
  if (!newText.trim()) return fail("No redline provided.");
  try {
    return await Word.run(async (context) => {
      const range = await findClauseRange(context, currentText);
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
      const range = await findClauseRange(context, anchorText);
      if (!range) return fail("Couldn't locate the anchor in the document.");

      const doc = context.document;
      doc.load("changeTrackingMode");
      await context.sync();
      const originalMode = doc.changeTrackingMode;

      doc.changeTrackingMode = Word.ChangeTrackingMode.trackAll;
      try {
        const loc = position === "after" ? Word.InsertLocation.after : Word.InsertLocation.before;
        range.insertText(newText, loc);
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
