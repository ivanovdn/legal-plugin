// Text normalization for Office.js body.search().
//
// contract_review quotes from body.text (LF separators, straight quotes),
// but the document's actual content may contain curly quotes, non-breaking
// spaces, or wide-whitespace. We normalize both the search needle and the
// haystack to a common form so the search matches.

const CURLY_SINGLE = /[‘’]/g; // ‘ ’
const CURLY_DOUBLE = /[“”]/g; // “ ”
const NBSP = / /g;                 // non-breaking space

export function normalizeForSearch(s: string): string {
  return s
    .normalize("NFC")
    .replace(CURLY_SINGLE, "'")
    .replace(CURLY_DOUBLE, '"')
    .replace(NBSP, " ")
    .replace(/\s+/g, " ")
    .trim();
}
