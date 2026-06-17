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

  const cleanedProse = prose
    .replace(JSON_BLOCK_RE, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();

  return { cleanedProse, blocks };
}
