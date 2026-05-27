// Parse fenced ```json``` blocks out of chat prose into structured edit proposals.
// The backend's legal_research skill optionally appends one or more blocks when
// the lawyer asks for a document change. Display the cleanedProse to the user;
// render each EditProposal as a preview card with [Apply] / [Discard] buttons.

export type EditAction = "replace" | "insert" | "delete";

export type EditProposal = {
  action: EditAction;
  target_text?: string;
  new_text?: string;
  anchor_text?: string;
  position?: "after" | "before";
  rationale?: string;
};

const JSON_BLOCK_RE = /```json\s*\n([\s\S]*?)```/g;
const VALID_ACTIONS = new Set<EditAction>(["replace", "insert", "delete"]);

export function extractEditBlocks(prose: string): {
  cleanedProse: string;
  blocks: EditProposal[];
} {
  const blocks: EditProposal[] = [];
  if (!prose) return { cleanedProse: "", blocks };

  for (const match of prose.matchAll(JSON_BLOCK_RE)) {
    const raw = match[1].trim();
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      // Tolerant: skip malformed blocks, keep others.
      continue;
    }
    if (
      parsed &&
      typeof parsed === "object" &&
      !Array.isArray(parsed) &&
      VALID_ACTIONS.has((parsed as { action: EditAction }).action)
    ) {
      blocks.push(parsed as EditProposal);
    }
  }

  const cleanedProse = prose
    .replace(JSON_BLOCK_RE, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();

  return { cleanedProse, blocks };
}
