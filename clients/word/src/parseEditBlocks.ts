// Parse fenced ```json``` blocks out of chat prose into structured edit proposals.
// The backend's legal_research skill optionally appends one or more blocks when
// the lawyer asks for a document change. Display the cleanedProse to the user;
// render each EditProposal as a preview card with [Apply] / [Discard] buttons.

export type EditAction = "replace" | "replace_all" | "insert" | "delete";

export type EditProposal = {
  action: EditAction;
  target_text?: string;
  new_text?: string;
  anchor_text?: string;
  position?: "after" | "before";
  rationale?: string;
};

const JSON_BLOCK_RE = /```json\s*\n([\s\S]*?)```/g;
const VALID_ACTIONS = new Set<EditAction>(["replace", "replace_all", "insert", "delete"]);

/** Escape literal LF/CR/TAB characters that sit INSIDE JSON string values.
 *  Local LLMs occasionally line-wrap a long string value mid-content, which
 *  leaves a raw newline inside a quoted string (JSON spec: invalid). We walk
 *  the text, track whether we're inside a quoted string, and replace raw
 *  whitespace with proper backslash-escape sequences. */
function escapeUnescapedWhitespaceInStrings(raw: string): string {
  let out = "";
  let inString = false;
  let escapeNext = false;
  for (const ch of raw) {
    if (escapeNext) {
      out += ch;
      escapeNext = false;
      continue;
    }
    if (inString && ch === "\\") {
      out += ch;
      escapeNext = true;
      continue;
    }
    if (ch === '"') {
      inString = !inString;
      out += ch;
      continue;
    }
    if (inString && (ch === "\n" || ch === "\r" || ch === "\t")) {
      out += ch === "\n" ? "\\n" : ch === "\r" ? "\\r" : "\\t";
    } else {
      out += ch;
    }
  }
  return out;
}

/** JSON.parse with a best-effort fallback for raw whitespace inside strings. */
function tolerantParse(raw: string): unknown | undefined {
  try {
    return JSON.parse(raw);
  } catch {
    // fall through
  }
  try {
    return JSON.parse(escapeUnescapedWhitespaceInStrings(raw));
  } catch {
    return undefined;
  }
}

/** Decode one or more concatenated top-level JSON values from `raw`.
 *  Mirrors the backend `_iter_json_values`. The local LLM frequently stacks
 *  several edit objects in ONE fenced block, separated only by newlines
 *  ({...}\n{...}) instead of a JSON array — which JSON.parse rejects as "extra
 *  data", so the whole block used to be dropped (traces cea50c6b / f15f8a9b).
 *  We first try to parse the block as a single value (object / array / wrapper);
 *  failing that we split on balanced brace/bracket boundaries (tracking string
 *  state) and parse each piece. Returns [] when nothing parses. */
function iterJsonValues(raw: string): unknown[] {
  const whole = tolerantParse(raw);
  if (whole !== undefined) return [whole];

  const s = escapeUnescapedWhitespaceInStrings(raw);
  const values: unknown[] = [];
  let depth = 0;
  let inString = false;
  let escapeNext = false;
  let start = -1;
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (escapeNext) {
      escapeNext = false;
      continue;
    }
    if (inString) {
      if (ch === "\\") escapeNext = true;
      else if (ch === '"') inString = false;
      continue;
    }
    if (ch === '"') {
      inString = true;
    } else if (ch === "{" || ch === "[") {
      if (depth === 0) start = i;
      depth++;
    } else if (ch === "}" || ch === "]") {
      if (depth > 0) {
        depth--;
        if (depth === 0 && start >= 0) {
          try {
            values.push(JSON.parse(s.slice(start, i + 1)));
          } catch {
            // skip an unparseable fragment, keep scanning
          }
          start = -1;
        }
      }
    }
  }
  return values;
}

/** Normalize decoded JSON values into a flat list of edit-object candidates.
 *  A value may be a bare edit object, an array of edits, or a {"edits": [...]}
 *  wrapper. */
function flattenEditValues(values: unknown[]): unknown[] {
  const out: unknown[] = [];
  for (const v of values) {
    const edits = (v as { edits?: unknown })?.edits;
    if (v && typeof v === "object" && !Array.isArray(v) && Array.isArray(edits)) {
      out.push(...edits);
    } else if (Array.isArray(v)) {
      out.push(...v);
    } else {
      out.push(v);
    }
  }
  return out;
}

// A "blank to fill" token: a bracketed blank ([__], [ ], [...], [---]) or a run
// of 3+ underscores — the label-less fill markers in signature blocks
// ("Signed by: [__]"). A NAMED placeholder like [Year] is deliberately NOT a
// blank here; we only intervene on the bare-blank pattern that fails to apply.
const BLANK_PLACEHOLDER_RE = /\[[\s_.\-]*\]|_{3,}/;

/**
 * Reduce a tab-bundled line to its single changed segment. The LLM sometimes
 * prepends a two-column neighbour to a field — e.g. the dotted signature LINE +
 * a tab: "………………\tSigned by: [__]" (trace 9e5b804c). body.search can't reach
 * across a tab, so the bundled target never matches. When target and new_text
 * have the same tab-segment count and differ on exactly ONE segment, keep only
 * that segment ("Signed by: [__]" → "Signed by: John Doe"); the unchanged
 * column (the dotted line / the counterparty's filled cell) is dropped. If zero
 * or 2+ segments differ, or there's no tab, leave the pair untouched.
 */
function reduceTabSegment(
  target: string,
  newText: string,
): { target: string; newText: string } {
  if (!target.includes("\t") || !newText.includes("\t")) return { target, newText };
  const tSegs = target.split("\t");
  const nSegs = newText.split("\t");
  if (tSegs.length !== nSegs.length) return { target, newText };
  const diffIdx: number[] = [];
  for (let i = 0; i < tSegs.length; i++) {
    if (tSegs[i].trim() !== nSegs[i].trim()) diffIdx.push(i);
  }
  if (diffIdx.length === 1) {
    const i = diffIdx[0];
    return { target: tSegs[i].trim(), newText: nSegs[i].trim() };
  }
  return { target, newText };
}

// A LABELED blank ("Signed by: [__]"): carries a blank token AND text beyond it.
// A bare "[__]" line is NOT labeled — it can't safely become a replace_all
// (replaceAll refuses bare blanks anyway).
function isLabeledBlank(line: string): boolean {
  return (
    BLANK_PLACEHOLDER_RE.test(line) &&
    line.replace(BLANK_PLACEHOLDER_RE, "").trim().length > 0
  );
}

// A structured "field" line — "Label: value" (has a colon) or one carrying a
// blank placeholder ("for and on behalf of [__]"). Signature / execution blocks
// are made of these, each on its own paragraph. PROSE clauses are not, so they
// stay multi-line for word.ts's head+tail span matcher (which CAN cross breaks).
function isFieldLine(line: string): boolean {
  return /:/.test(line) || BLANK_PLACEHOLDER_RE.test(line);
}

/**
 * A local LLM often collapses a whole signature block into ONE multi-line
 * `replace` — several "Label: value" lines whose new_text changes each (traces
 * 02e41ead / ce45b899 fill blanks; 32deb028 rewrites a filled block; 9e5b804c
 * bundles the dotted signature line). That target can't be applied: body.search
 * can't cross paragraph breaks, so only the first line matches and the
 * 85%-completeness guard rejects the partial match ("Couldn't find the exact
 * target text") — and it would only ever touch the FIRST block.
 *
 * When EVERY changed line is a structured field, split into one edit per changed
 * line. A labeled blank ("Signed by: [__]") becomes a `replace_all` — blanks
 * recur across blocks (main + appendix), replaceAll snapshots every match in one
 * pass (no struck-text re-find), and the labeled target hits one field, never
 * every blank. A specific value ("Signed by: Boris Bukengolts") becomes a
 * `replace` — one occurrence, not every match. (collapseDuplicateFills then folds
 * the LLM's duplicated per-block cards into a single fill-every edit.)
 *
 * Multi-paragraph PROSE (no per-line colon/blank), single-line targets, and
 * single-diff blocks are left untouched — the apply-time simplifyMultilineReplace
 * / head+tail matcher owns those. Exported for unit testing.
 */
export function splitMultilineFieldEdits(p: EditProposal): EditProposal[] {
  if (p.action !== "replace" || !p.target_text || !p.new_text) return [p];
  const tLines = p.target_text.split(/\r?\n/);
  const nLines = p.new_text.split(/\r?\n/);
  if (tLines.length < 2 || tLines.length !== nLines.length) return [p];

  const diffs: Array<{ target: string; newText: string }> = [];
  for (let i = 0; i < tLines.length; i++) {
    const t = tLines[i].trim();
    const n = nLines[i].trim();
    if (t === n) continue;
    // A bundled "…dotted…\tSigned by: [__]" line reduces to just the changed
    // column so body.search (which can't cross a tab) can match it.
    diffs.push(reduceTabSegment(t, n));
  }
  if (diffs.length < 2) return [p];

  // Only split a block of structured field lines; leave prose to the span matcher.
  if (!diffs.every((d) => isFieldLine(d.target))) return [p];

  return diffs.map((d) => ({
    action: (isLabeledBlank(d.target) ? "replace_all" : "replace") as EditAction,
    target_text: d.target,
    new_text: d.newText,
    rationale: p.rationale,
  }));
}

/**
 * Collapse duplicate fill edits. The LLM enumerates one card per signature block
 * (main agreement + appendix) with IDENTICAL target/new_text. Applying both
 * would double-fill: a `replace` only hits the first match, and the second card
 * re-finds the first block's struck-out original (Office.js Track Changes leaves
 * deleted text visible to body.search) and stacks another change on it.
 *
 * Group by (target, new_text, anchor, position). A group seen 2+ times is a
 * "fill every occurrence" request → emit ONE `replace_all` so all blocks fill in
 * a single snapshot pass. Singletons pass through unchanged. Exported for
 * unit testing.
 */
export function collapseDuplicateFills(blocks: EditProposal[]): EditProposal[] {
  const order: string[] = [];
  const groups = new Map<string, EditProposal[]>();
  for (const b of blocks) {
    const key = JSON.stringify([
      b.target_text ?? "",
      b.new_text ?? "",
      b.anchor_text ?? "",
      b.position ?? "",
    ]);
    if (!groups.has(key)) {
      groups.set(key, []);
      order.push(key);
    }
    groups.get(key)!.push(b);
  }

  const out: EditProposal[] = [];
  for (const key of order) {
    const group = groups.get(key)!;
    const first = group[0];
    if (group.length === 1) {
      out.push(first);
      continue;
    }
    // Duplicated fill. A text replacement is promoted to replace_all so every
    // occurrence fills in one pass; other actions just keep a single copy.
    if (first.action === "replace" || first.action === "replace_all") {
      out.push({ ...first, action: "replace_all" });
    } else {
      out.push(first);
    }
  }
  return out;
}

/**
 * Make a list of edit proposals applicable: split unmatchable multi-line
 * signature-block fills into per-field replace_all, then collapse the LLM's
 * duplicate per-block cards into one fill-every edit. Idempotent (safe to run
 * more than once) so it can be applied to whichever edit list ends up used —
 * the backend's authoritative proposed_edits OR the frontend's own extraction.
 * Both sources carry the same multi-line-block failure mode, so normalize at the
 * point of use, not only inside extractEditBlocks.
 */
export function normalizeProposals(blocks: EditProposal[]): EditProposal[] {
  return collapseDuplicateFills(blocks.flatMap(splitMultilineFieldEdits));
}

export function extractEditBlocks(prose: string): {
  cleanedProse: string;
  blocks: EditProposal[];
} {
  const blocks: EditProposal[] = [];
  if (!prose) return { cleanedProse: "", blocks };

  for (const match of prose.matchAll(JSON_BLOCK_RE)) {
    const raw = match[1].trim();
    // A block may hold a single edit object, an array of edits, OR several edit
    // objects stacked one per line ({...}\n{...}) — the local LLM uses all three
    // interchangeably. iterJsonValues decodes whichever shape is present.
    const values = iterJsonValues(raw);
    if (values.length === 0) {
      // Tolerant: skip malformed blocks, keep others.
      continue;
    }
    for (const c of flattenEditValues(values)) {
      if (
        c &&
        typeof c === "object" &&
        !Array.isArray(c) &&
        VALID_ACTIONS.has((c as { action: EditAction }).action)
      ) {
        blocks.push(c as EditProposal);
      }
    }
  }

  // Post-process: split unmatchable multi-line signature-block fills into
  // per-field replace_all (the apply-time matcher can't span paragraph breaks),
  // then collapse the LLM's duplicate per-block cards into one fill-every edit.
  const finalBlocks = normalizeProposals(blocks);

  const cleanedProse = prose
    .replace(JSON_BLOCK_RE, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();

  return { cleanedProse, blocks: finalBlocks };
}
