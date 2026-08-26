import { useEffect, useRef, useState } from "react";
import { compactConversation } from "../api";
import { resolveDocumentId } from "../docIdentity";
import {
  NEVER_COMPACTED,
  PART_LABELS,
  gaugeLine,
  isWarning,
  reclaimTarget,
  withLiveDocument,
  type ContextBreakdown,
} from "../contextGauge";

interface Props {
  breakdown: ContextBreakdown | null;
  liveDocChars: number | null;
  docTruncated: boolean;
}

/**
 * The shared-header context counter.
 *
 * Three honesty constraints, all load-bearing:
 *  - the figures are the LAST TURN'S real measured values, labelled as such.
 *    Not a prediction: grounding is question-dependent and unknowable ahead of
 *    the question.
 *  - the manual Condense action appears only when the budget is under pressure
 *    AND there is compressible history. Offering it with nothing to condense
 *    would offer a no-op, and a control that cries wolf gets ignored.
 *  - condensing that happens WITHOUT a click still says so, before and after.
 *    Nothing is deleted either way, but the attorney should never discover
 *    after the fact that their history was summarised.
 *
 * The live-document adjustment lives here rather than in App.tsx's JSX because
 * withLiveDocument allocates a new object on every call. Keying the auto-fire
 * effect on that object would fire it on every render; keying it on the raw
 * per-turn breakdown fires it once per turn, which is the intent.
 */
export default function ContextMeter({ breakdown, liveDocChars, docTruncated }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [busyAuto, setBusyAuto] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Set when an AUTOMATIC run fails, stopping auto for the life of the pane.
  // The manual path can afford to surface an error on every attempt because a
  // human just pressed a button and is owed an answer; an unrequested error on
  // every turn against a down Ollama teaches attorneys to ignore the pane.
  // Deliberately not persisted: a transient blip must not disable the feature
  // permanently, and there is nowhere honest to persist a client-side judgement
  // about a transient condition. Reopening the pane re-arms it.
  const [disarmed, setDisarmed] = useState(false);
  // The raw per-turn breakdown this component has already auto-fired for.
  const firedFor = useRef<ContextBreakdown | null>(null);

  const shown =
    breakdown && liveDocChars !== null && !docTruncated
      ? withLiveDocument(breakdown, liveDocChars)
      : breakdown;

  const runCompaction = async (automatic: boolean) => {
    if (!shown) return;
    setBusy(true);
    setBusyAuto(automatic);
    setError(null);
    setNote(null);
    try {
      const documentId = await resolveDocumentId();
      // How much has to come back for the counter to fall below the warn line. The
      // backend cannot work this out — it has neither the document nor the grounding —
      // but the counter above already measured it.
      const res = await compactConversation(documentId, reclaimTarget(shown));
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
        const lead = automatic
          ? `Condensed automatically — ${n} earlier messages.`
          : `${n} earlier messages condensed.`;
        setNote(`${lead}${skipped}${short} The counter updates on your next message.`);
      } else {
        setNote(res.data?.reason || "Nothing earlier to condense yet.");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      // compactConversation throws on any non-2xx, and api/routes/compact.py raises
      // 500 for every `error` result — so catching here covers the whole failure
      // surface. A `reason` result is NOT a failure and never reaches this branch.
      if (automatic) setDisarmed(true);
    } finally {
      setBusy(false);
    }
  };

  // Fires at most once per turn: keyed on the RAW breakdown, which App.tsx replaces
  // exactly once per turn, and additionally guarded by a ref so a re-render caused
  // by our own setState cannot re-enter. Must sit above the early return below —
  // hooks cannot run conditionally.
  useEffect(() => {
    if (!breakdown || !shown?.auto_compact) return;
    if (busy || disarmed) return;
    if (firedFor.current === breakdown) return;
    firedFor.current = breakdown;
    void runCompaction(true);
  }, [breakdown, shown?.auto_compact, busy, disarmed]);

  const line = gaugeLine(shown);
  if (!shown || !line) return null;

  return (
    <div className={`context-meter ${isWarning(shown) ? "warn" : ""}`}>
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
              {shown.parts
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
      {busy && busyAuto && (
        <p className="context-meter-note" role="status">
          Condensing earlier turns automatically… (10–30 s)
        </p>
      )}
      {shown.can_compact && !(busy && busyAuto) && (
        <div className="context-meter-actions">
          <button className="secondary" onClick={() => runCompaction(false)} disabled={busy}>
            {busy ? "Condensing… (10–30 s)" : "Condense earlier turns"}
          </button>
          <span className="context-meter-note">
            Condenses your earlier messages into verbatim quotes. Your most recent
            messages stay word-for-word, and nothing is deleted.
          </span>
        </div>
      )}
      {note && <p className="context-meter-note" role="status">{note}</p>}
      {error && <p className="status error">Couldn't condense: {error}</p>}
    </div>
  );
}
