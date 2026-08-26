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
  auto_compact: boolean;
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

/**
 * How many characters must come back for the counter to fall below the WARN line.
 *
 * Deliberately measured against the warn line, not the budget. The Condense control
 * appears at warn_pct, but a target of (total - budget) is zero anywhere below 100% —
 * so through the whole amber band compaction ran with no target at all, skipped its cap
 * derivation, and produced a segment LARGER than the history it replaced. Observed
 * live: 1,293 chars became 1,825.
 */
export function reclaimTarget(b: ContextBreakdown): number {
  const warnLine = Math.floor((b.budget_chars * b.warn_pct) / 100);
  return Math.max(0, b.total_chars - warnLine);
}

/**
 * Recompute the counter for a compaction that just ran against THIS breakdown.
 *
 * The gauge's honesty rule is "measured last turn, never predicted" — the pane
 * cannot know what the NEXT turn's grounding will cost, so it never forecasts.
 * Compaction is not an exception to that rule so much as a case where the
 * measurement already exists: the backend returns exactly how many characters
 * came out of history, history is the only part compaction can touch, and the
 * compressible pool is empty afterwards by construction (compact_conversation
 * condenses every row past the boundary except the keep-recent floor). Three
 * measurements, no forecast.
 *
 * Without it the pane told the attorney "the counter updates on your next
 * message" — true, and useless. They had just watched something happen on their
 * behalf and the number did not move, which reads as nothing having happened.
 *
 * Same shape as withLiveDocument: recompute only the one line that genuinely
 * changed, leave every other part exactly as the backend measured it.
 */
export function withReclaimedHistory(
  b: ContextBreakdown,
  reclaimedChars: number,
): ContextBreakdown {
  // A refusal reclaims nothing, and the identity return keeps the caller from
  // having to special-case it.
  if (reclaimedChars <= 0) return b;
  const parts = b.parts.map((p) => {
    if (!p.compactable) return p;
    const chars = Math.max(0, p.chars - reclaimedChars);
    return {
      ...p,
      chars,
      tokens: Math.trunc(chars / b.chars_per_token),
      pct: b.budget_chars ? Math.trunc((chars * 100) / b.budget_chars) : 0,
    };
  });
  const total = parts.reduce((sum, p) => sum + p.chars, 0);
  return {
    ...b,
    parts,
    total_chars: total,
    total_tokens: Math.trunc(total / b.chars_per_token),
    pct: b.budget_chars ? Math.trunc((total * 100) / b.budget_chars) : 0,
    // Condensed rows stop being compressible: compaction takes every row past
    // the boundary except the keep-recent floor, so the pool is empty until the
    // next turns refill it. can_compact and auto_compact both follow from that,
    // and both erring OFF is the safe direction — it can only withhold an offer,
    // never fire something nobody asked for.
    compressible_messages: 0,
    can_compact: false,
    auto_compact: false,
  };
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
 *
 * `auto_compact` is narrowed rather than recomputed: it can go false here but
 * never true. See the comment on it below.
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
    // Narrowed here, NEVER widened — note this reads b.auto_compact rather than
    // recomputing from scratch the way can_compact does. The backend already folded
    // compaction_auto and compaction_auto_min_messages into that field and the
    // client knows neither, so a live document edit may switch auto OFF (the
    // pressure is genuinely relieved) but must never switch it ON. The asymmetry
    // with can_compact is deliberate: making a button appear on the client's own
    // estimate of a document the backend has not seen is cheap, and firing an
    // unrequested LLM call on the same estimate is not.
    auto_compact: b.auto_compact && pct >= b.warn_pct,
  };
}
