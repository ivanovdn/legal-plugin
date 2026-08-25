// The attorney-facing shape of the context budget.
//
// Kept as pure functions, separate from JSX, so the arithmetic can be asserted
// on. The backend sends the breakdown; only this module decides how to say it.
//
// Why the breakdown is backend-fed: the client knows the document text from
// readBody() and nothing else — not the playbook size, not whether grounding
// attached, not the prior-review block, not the injected history. All of that
// is assembled in _run_doc_chat. The ONE line the client can watch change
// between turns is the document, which is what withLiveDocument is for.

export interface ContextPart {
  key: string;
  chars: number;
  tokens: number;
  pct: number;
  compactable: boolean;
}

export interface ContextBreakdown {
  budget_chars: number;
  budget_tokens: number;
  chars_per_token: number;
  total_chars: number;
  total_tokens: number;
  pct: number;
  warn_pct: number;
  can_compact: boolean;
  compressible_messages: number;
  parts: ContextPart[];
}

export const PART_LABELS: Record<string, string> = {
  document: "document",
  playbook: "playbook",
  msa: "governing MSA",
  review: "prior review",
  history: "history",
  system: "system & question",
};

/** Never compacted — said in the expanded view so the trade-off is legible. */
export const NEVER_COMPACTED = new Set(["document", "playbook", "msa", "review"]);

export function formatTokens(n: number): string {
  return n >= 1000 ? `${Math.round(n / 1000)}k` : `${n}`;
}

/** The collapsed one-liner, or null when no turn has been measured yet. */
export function gaugeLine(b: ContextBreakdown | null | undefined): string | null {
  if (!b) return null;
  const history = b.parts.find((p) => p.compactable);
  const share = history ? ` · history ${history.pct}%` : "";
  return `context  ${formatTokens(b.total_tokens)} / ${formatTokens(b.budget_tokens)} tokens${share}`;
}

export function isWarning(b: ContextBreakdown | null | undefined): boolean {
  return Boolean(b && b.pct >= b.warn_pct);
}

/**
 * Recompute the document line (and only it) from a live readBody() length.
 *
 * `can_compact` recomputes its threshold half here — editing the document
 * genuinely changes whether the budget is under pressure — but takes
 * `compressible_messages` as backend truth, since the client cannot know what
 * is in the store. The backend reports 0 when compaction is disabled, so the
 * master switch survives this path.
 */
export function withLiveDocument(b: ContextBreakdown, docChars: number): ContextBreakdown {
  const parts = b.parts.map((p) =>
    p.key === "document"
      ? {
          ...p,
          chars: docChars,
          tokens: Math.trunc(docChars / b.chars_per_token),
          pct: b.budget_chars ? Math.trunc((docChars * 100) / b.budget_chars) : 0,
        }
      : p,
  );
  const total = parts.reduce((sum, p) => sum + p.chars, 0);
  const pct = b.budget_chars ? Math.trunc((total * 100) / b.budget_chars) : 0;
  return {
    ...b,
    parts,
    total_chars: total,
    total_tokens: Math.trunc(total / b.chars_per_token),
    pct,
    can_compact: b.compressible_messages > 0 && pct >= b.warn_pct,
  };
}
