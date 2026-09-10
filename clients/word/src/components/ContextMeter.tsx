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
  withReclaimedHistory,
  type ContextBreakdown,
} from "../contextGauge";

interface Props {
  breakdown: ContextBreakdown | null;
  liveDocChars: number | null;
  docTruncated: boolean;
}

interface Outcome {
  /** The breakdown the run was MEASURED against. Scopes the reclaim adjustment
   *  and the button's suppression: a newer breakdown is fresh backend truth and
   *  neither should still apply to it. */
  forBreakdown: ContextBreakdown | null;
  /** The breakdown that was current when the run FINISHED. Scopes DISPLAY, and
   *  is deliberately a different thing. Keying display off forBreakdown hides a
   *  run that completed after a newer breakdown had already landed — the attorney
   *  never sees work they did not ask for. Not scoping it at all leaves the notice
   *  on screen for the rest of the session, which is what shipped and was wrong.
   *  Tagging the turn the result actually ARRIVED on gives it exactly one turn. */
  shownFor: ContextBreakdown | null;
  /** One line. Always shown while visible. */
  headline: string;
  /** Quality detail — lives in the expanded view, not the collapsed header.
   *  Empty when there is nothing to report. */
  caveat: string;
  /** Characters taken out of history. 0 for a refusal. */
  reclaimed: number;
}

interface Failure {
  /** The breakdown that was current when the run FAILED. Scopes DISPLAY exactly
   *  as Outcome.shownFor does, and for the same reason: unscoped, the notice
   *  stays on screen for the rest of the session. That is not hypothetical —
   *  measured 2026-09-10, an automatic failure at 16:04 was still on screen at
   *  16:08 through a completed turn, because the ONLY thing that cleared the
   *  error was the start of the next run, and an automatic failure disarms auto
   *  so there is no next run. The two behaviours were individually correct and
   *  lethal together. */
  shownFor: ContextBreakdown | null;
  message: string;
  /** Whether the run that failed was AUTOMATIC, captured at failure time rather
   *  than read from busyAuto at render time. busyAuto describes the most recent
   *  run, so a later manual run would silently relabel this failure as one the
   *  attorney asked for. */
  automatic: boolean;
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
  // The result of the last run, tagged with the breakdown it describes.
  //
  // A bare string could not do this job. Three things have to expire, and they
  // expire at DIFFERENT moments: the text must outlive its own turn (a run
  // finishing while a newer breakdown is already pending must still be readable —
  // the attorney may not have asked for it), while the reclaim adjustment and the
  // button's suppression must NOT, because a newer breakdown is the backend's own
  // fresh measurement and we do not second-guess it.
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
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
  // Mirrors the current breakdown so a run finishing LATER can tag its result
  // with the turn it actually arrived on rather than the one it started against.
  const latestBreakdown = useRef<ContextBreakdown | null>(breakdown);

  // Scoped to THIS turn: a new breakdown means the backend has re-measured, so
  // our own adjustment and the button's suppression both stop applying. The note
  // itself is deliberately not scoped — see the Outcome comment above.
  const outcomeIsForThisTurn = outcome !== null && outcome.forBreakdown === breakdown;
  // Display scope — see the Outcome type. One turn, then gone.
  const outcomeVisible = outcome !== null && outcome.shownFor === breakdown;
  // A failure expires the same way a success does. It used to be unscoped,
  // which made it permanent after an automatic failure.
  const failureVisible = failure !== null && failure.shownFor === breakdown;

  let shown =
    breakdown && liveDocChars !== null && !docTruncated
      ? withLiveDocument(breakdown, liveDocChars)
      : breakdown;
  if (shown && outcomeIsForThisTurn && outcome.reclaimed > 0) {
    shown = withReclaimedHistory(shown, outcome.reclaimed);
  }

  const runCompaction = async (automatic: boolean) => {
    if (!shown) return;
    setBusy(true);
    setBusyAuto(automatic);
    setFailure(null);
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
        // Detail, not headline: a drop count is a quality signal about the summary,
        // not something an attorney must act on. It reads as alarming inline and it
        // is routine — a dense markdown table offers almost no quotable sentence
        // boundaries, so a table-heavy answer drops most of what the model tried.
        const skipped =
          dropped > 0
            ? `${dropped} quote${dropped === 1 ? "" : "s"} couldn't be matched to the message ` +
              `${dropped === 1 ? "it" : "they"} came from and ${dropped === 1 ? "was" : "were"} ` +
              `left out. Nothing was deleted — the original messages are still stored.`
            : "";
        // When the target was missed, say so and name the reason: the attorney is
        // about to see the document truncated again and should know compaction was
        // not the thing that could have prevented it.
        const reclaimed = res.data.reclaimed ?? 0;
        const requested = res.data.requested ?? 0;
        // This one DOES belong in the headline: it only appears when the document is
        // about to be truncated anyway, and the attorney should know compaction was
        // not the thing that could have prevented it.
        const missedTarget = requested > 0 && reclaimed < requested;
        const short = missedTarget
          ? ` Short of the ${requested.toLocaleString("en-US")} needed to clear the warning.`
          : "";
        const why = missedTarget
          ? " The rest of the context is document, playbook and MSA, which are never condensed."
          : "";
        const freed = reclaimed.toLocaleString("en-US");
        const lead = automatic
          ? `Condensed automatically — ${n} messages, ${freed} characters freed.`
          : `Condensed ${n} messages, ${freed} characters freed.`;
        // No "the counter updates on your next message" any more: it updates now,
        // from the reclaimed figure carried on the outcome.
        setOutcome({
          forBreakdown: breakdown,
          shownFor: latestBreakdown.current,
          headline: `${lead}${short}`,
          caveat: `${skipped}${why}`.trim(),
          reclaimed,
        });
        // A SUCCESSFUL run disproves whatever disarmed us, so re-arm. Both causes
        // are settled by it: a transport failure means the model is reachable
        // again, and a net-benefit refusal means the pool is now large enough to
        // be worth condensing. Without this the latch outlives its own reason —
        // measured 2026-09-10, one transient Ollama blip at 16:04 disabled
        // automatic compaction for the rest of the pane's life even though a
        // manual run at 16:10 proved the model was back, and the only cure was
        // reloading a task pane, which no attorney would know to do.
        //
        // Not a churn risk: a successful run empties the compressible pool, so
        // can_compact is false on the next turn and nothing can re-fire until
        // real pressure returns. Automatic successes reach this line too, where
        // it is a no-op — auto cannot have been armed while disarmed.
        setDisarmed(false);
      } else {
        setOutcome({
          forBreakdown: breakdown,
          shownFor: latestBreakdown.current,
          headline: res.data?.reason || "Nothing earlier to condense yet.",
          caveat: "",
          reclaimed: 0,
        });
        // A refusal is not a failure — it renders its `reason` here, the same
        // quiet note the manual path shows. But an AUTOMATIC refusal disarms:
        // compact_conversation generates the segment before it checks net
        // benefit (compaction.py:545 vs :600), so every refusal already cost a
        // full 10-30s call. Nothing on the backend remembers a turn was
        // refused, and auto_compact is recomputed from pressure and the size of
        // the compressible pool alone, so a stable refusal would otherwise fire
        // again, unasked, on every subsequent turn for zero benefit. Disarm on
        // this FIRST refusal, not a second one: while auto_compact is true,
        // compaction_auto_min_chars guarantees there's always something
        // compressible, so "nothing earlier to condense yet" can't be the
        // reason here — the only refusal automatic firing can reach already IS
        // the net-benefit one. The attorney keeps the manual button either way.
        // This holds only because the effect below now records `firedFor`
        // BEFORE its `busy` check — a breakdown superseded by an in-flight run
        // is skipped, not deferred into a late fire against an already-advanced
        // boundary that would otherwise reach this branch for real.
        if (automatic) setDisarmed(true);
      }
    } catch (e) {
      setFailure({
        shownFor: latestBreakdown.current,
        message: e instanceof Error ? e.message : String(e),
        automatic,
      });
      // Cleared here, not at the function's head (that would wipe a
      // just-finished run's success note within one frame) — a fresh failure
      // must not render beneath a stale "Condensed…" note either.
      setOutcome(null);
      // This try also wraps resolveDocumentId(), not just compactConversation —
      // an Office.js settings failure lands here too, and correctly disarms
      // auto: it is as much a dead end for an automatic run as a failed
      // compaction call. compactConversation itself throws on any non-2xx, and
      // api/routes/compact.py raises 500 for every `error` result, so together
      // this covers the whole failure surface. A `reason` result is NOT a
      // failure and never reaches this branch — see the disarm call in the
      // else branch above for that case.
      if (automatic) setDisarmed(true);
    } finally {
      setBusy(false);
    }
  };

  // Kept in step with the prop so a run that finishes on a LATER turn tags its
  // result with the turn it arrived on. Written in an effect rather than during
  // render: a ref mutated mid-render is not a value React can reason about.
  useEffect(() => {
    latestBreakdown.current = breakdown;
  }, [breakdown]);

  // Fires at most once per turn: keyed on the RAW breakdown, which App.tsx replaces
  // exactly once per turn, and additionally guarded by a ref so a re-render caused
  // by our own setState cannot re-enter. Must sit above the early return below —
  // hooks cannot run conditionally. runCompaction is deliberately absent from the
  // dependency list below: it is redefined every render, so satisfying
  // exhaustive-deps by adding it would make this effect body run on every render
  // instead of once per turn — the ref guard above, not the dependency list, is
  // what keeps re-entrancy safe.
  useEffect(() => {
    if (!breakdown || !shown?.auto_compact) return;
    if (firedFor.current === breakdown) return;
    // Recorded BEFORE the busy/disarmed check below: a breakdown that arrives
    // while a run is already in flight must be marked seen now, so once `busy`
    // clears the next render treats it as superseded and SKIPS it — instead of
    // finding an unrecorded ref and firing it late against a boundary that has
    // already moved (see the refusal-branch comment in runCompaction above).
    firedFor.current = breakdown;
    if (busy || disarmed) return;
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
          {outcome !== null && outcome.caveat !== "" && (
            <p className="context-meter-note">Last condense: {outcome.caveat}</p>
          )}
        </>
      )}
      {busy && busyAuto && (
        <p className="context-meter-note" role="status">
          Condensing earlier turns automatically… (10–30 s)
        </p>
      )}
      {shown.can_compact && !(busy && busyAuto) && !outcomeIsForThisTurn && (
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
      {outcomeVisible && outcome && (
        <p className="context-meter-outcome" role="status">
          {outcome.headline}
        </p>
      )}
      {/* An automatic failure owes the same "you didn't ask for this" disclosure
          a successful automatic run gets, so the wording names which kind of run
          produced it — from the failure itself, not from busyAuto. */}
      {failureVisible && failure && (
        <p className="status error">
          {failure.automatic ? "Couldn't condense automatically: " : "Couldn't condense: "}
          {failure.message}
        </p>
      )}
    </div>
  );
}
