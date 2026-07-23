// Extract ```preference fenced blocks from assistant prose into individual
// preference suggestions. Plain text (one preference per line, leading bullet
// stripped), NOT JSON — mirrors the backend's _extract_proposed_preferences.

const PREFERENCE_BLOCK_RE = /```preference\s*\n([\s\S]*?)```/gi;

export function extractPreferenceBlocks(
  prose: string,
): { cleanedProse: string; preferences: string[] } {
  const preferences: string[] = [];
  const cleanedProse = (prose || "")
    .replace(PREFERENCE_BLOCK_RE, (_m, body: string) => {
      for (const line of body.split("\n")) {
        const t = line.trim().replace(/^[-*]\s+/, "");
        if (t) preferences.push(t);
      }
      return "";
    })
    .trim();
  return { cleanedProse, preferences };
}
