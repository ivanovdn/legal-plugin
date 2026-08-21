// The attorney-facing wording for a truncated context.
//
// Kept as a pure function, separate from JSX, so it can be asserted on. The
// backend sends {doc_chars, kept_chars, kept_pct}; only this module decides how
// to say it.
//
// Why this exists: before 2026-08-21 a truncated document produced only a
// logger.warning in the backend. The attorney asked "is the liability cap
// acceptable?" and got a confident answer from a model that had never seen the
// cap — the document is cut from the TAIL, so liability, indemnity,
// term/termination, governing law and the signature blocks are what go missing.

export interface ContextTruncated {
  doc_chars: number;
  kept_chars: number;
  kept_pct: number;
}

export interface TokenUsage {
  input: number | null;
  output: number | null;
  total: number | null;
  unit: string;
}

/** The sentence to show, or null when the whole document was sent. */
export function truncationNotice(ct: ContextTruncated | null | undefined): string | null {
  if (!ct) return null;
  const missing = Math.max(0, ct.doc_chars - ct.kept_chars);
  // Locale pinned deliberately. Bare toLocaleString() takes the host's ICU
  // locale, so the same code yields "35,322" here and "35 322" or "35.322" on a
  // differently-configured box — which would break the assertion below for a
  // reason that has nothing to do with this code.
  const missingText = missing.toLocaleString("en-US");
  const tail =
    " Clauses near the end of the contract — liability, indemnity, termination," +
    " governing law, signature blocks — may be missing from this answer.";
  if (ct.kept_pct <= 0) {
    return `I could read none of this document (${missingText} characters were not sent).${tail}`;
  }
  return (
    `I could only read ${ct.kept_pct}% of this document ` +
    `(${missingText} characters were not sent).${tail}`
  );
}
