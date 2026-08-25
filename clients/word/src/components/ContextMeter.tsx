import { useState } from "react";
import { compactConversation } from "../api";
import { resolveDocumentId } from "../docIdentity";
import {
  NEVER_COMPACTED,
  PART_LABELS,
  gaugeLine,
  isWarning,
  type ContextBreakdown,
} from "../contextGauge";

interface Props {
  breakdown: ContextBreakdown | null;
}

/**
 * The shared-header context counter.
 *
 * Two honesty constraints, both load-bearing:
 *  - the figures are the LAST TURN'S real measured values, labelled as such.
 *    Not a prediction: grounding is question-dependent and unknowable ahead of
 *    the question.
 *  - the Condense action appears only when the budget is under pressure AND
 *    there is compressible history. Offering it with nothing to condense would
 *    offer a no-op, and a control that cries wolf gets ignored.
 */
export default function ContextMeter({ breakdown }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const line = gaugeLine(breakdown);
  if (!breakdown || !line) return null;

  const condense = async () => {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const documentId = await resolveDocumentId();
      // How much has to come back for the document to stop being cut. The backend
      // cannot work this out — it has neither the document nor the grounding — but the
      // counter above already measured it.
      const over = Math.max(0, breakdown.total_chars - breakdown.budget_chars);
      const res = await compactConversation(documentId, over);
      if (res.data?.compacted) {
        const n = res.data.messages ?? 0;
        const dropped = res.data.dropped ?? 0;
        // The drop count is stated, never swallowed. Each dropped line failed to
        // match the message it cited, so leaving it out is the safe outcome — but
        // the attorney should know the summary is thinner than the model intended.
        const skipped =
          dropped > 0
            ? ` ${dropped} quote${dropped === 1 ? "" : "s"} couldn't be checked against the message ` +
              `${dropped === 1 ? "it" : "they"} came from and ${dropped === 1 ? "was" : "were"} left out.`
            : "";
        // When the target was missed, say so and name the reason: the attorney is
        // about to see the document truncated again and should know compaction was
        // not the thing that could have prevented it.
        const reclaimed = res.data.reclaimed ?? 0;
        const requested = res.data.requested ?? 0;
        const short =
          requested > 0 && reclaimed < requested
            ? ` Freed ${reclaimed.toLocaleString("en-US")} of the ` +
              `${requested.toLocaleString("en-US")} characters needed — the rest of the ` +
              `context is document, playbook and MSA, which are never condensed.`
            : "";
        setNote(
          `${n} earlier messages condensed.${skipped}${short} The counter updates on your next message.`,
        );
      } else {
        setNote(res.data?.reason || "Nothing earlier to condense yet.");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={`context-meter ${isWarning(breakdown) ? "warn" : ""}`}>
      <button
        className="context-meter-line"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
      >
        <span>{line}</span>
        <span className="context-meter-caret">{expanded ? "▾" : "▸"}</span>
      </button>
      {expanded && (
        <>
          <p className="context-meter-caption">Measured on your last message.</p>
          <table className="context-meter-table">
            <tbody>
              {breakdown.parts
                .filter((p) => p.chars > 0)
                .map((p) => (
                  <tr key={p.key}>
                    <td>{PART_LABELS[p.key] ?? p.key}</td>
                    <td className="num">{p.chars.toLocaleString("en-US")}</td>
                    <td className="num">{p.pct}%</td>
                    <td className="context-meter-note">
                      {NEVER_COMPACTED.has(p.key)
                        ? "never condensed"
                        : p.compactable
                          ? "what condensing reclaims"
                          : ""}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </>
      )}
      {breakdown.can_compact && (
        <div className="context-meter-actions">
          <button className="secondary" onClick={condense} disabled={busy}>
            {busy ? "Condensing… (10–30 s)" : "Condense earlier turns"}
          </button>
          <span className="context-meter-note">
            Condenses {breakdown.compressible_messages} earlier messages into verbatim
            quotes. Your most recent messages stay word-for-word, and nothing is deleted.
          </span>
        </div>
      )}
      {note && <p className="context-meter-note" role="status">{note}</p>}
      {error && <p className="status error">Couldn't condense: {error}</p>}
    </div>
  );
}
