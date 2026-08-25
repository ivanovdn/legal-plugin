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
      const res = await compactConversation(documentId);
      if (res.data?.compacted) {
        const n = res.data.messages ?? 0;
        setNote(`${n} earlier messages condensed. The counter updates on your next message.`);
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
