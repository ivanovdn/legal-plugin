import { useEffect, useState } from "react";
import Tabs, { type TabKey } from "./components/Tabs";
import FindingsTab from "./components/FindingsTab";
import ChatTab, { type ChatMessage } from "./components/ChatTab";
import PreferencesTab from "./components/PreferencesTab";
import FinalizeBar from "./components/FinalizeBar";
import FeedbackPanel from "./components/FeedbackPanel";
import { buildSnapshot, onFlagRequested, requestFlag, EMPTY_TURN, type FlagTarget } from "./feedback";
import { readBody } from "./word";
import { isDocumentUnsaved, resolveDocumentId } from "./docIdentity";
import type { ReviewSummary } from "./parser";
import ContextMeter from "./components/ContextMeter";
import { type ContextBreakdown } from "./contextGauge";

// Debounce for the live document measurement. onParagraphChanged fires per
// keystroke; readBody() on a real contract is a full getReviewedText round trip.
const LIVE_DOC_DEBOUNCE_MS = 2000;

export default function App() {
  // session_id is generated once per pane lifetime so the contract_review
  // turn and any subsequent chat turns share chat_history on the backend.
  const [sessionId] = useState<string>(() =>
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `addin-${Date.now()}-${Math.random().toString(36).slice(2)}`,
  );
  const [tab, setTab] = useState<TabKey>("findings");
  // All persistent tab state is lifted here so toggling tabs doesn't reset it.
  const [findingsResult, setFindingsResult] = useState<ReviewSummary | null>(null);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [prefMarkdown, setPrefMarkdown] = useState<string>("");
  const [prefLoaded, setPrefLoaded] = useState<boolean>(false);
  const [flagTarget, setFlagTarget] = useState<FlagTarget | null>(null);
  const [unsaved, setUnsaved] = useState<boolean>(false);
  const [breakdown, setBreakdown] = useState<ContextBreakdown | null>(null);
  const [liveDocChars, setLiveDocChars] = useState<number | null>(null);
  // Whether the LAST MEASURED turn had its document cut. When it did, the live
  // readBody() size must not overwrite the document row: the row would then show
  // the full document while the truncation notice directly below says only part of
  // it was sent, and the counter would be quietly contradicting the warning.
  const [docTruncated, setDocTruncated] = useState(false);

  // The document id lives in Office settings, which persist only WITH the file.
  // On an unsaved document that id dies when the document closes, orphaning this
  // contract's review history, chat and feedback rows — permanently unjoinable
  // from any later work on the same contract. Nothing here prevents that; this
  // ends the silence, which was the actual complaint. Re-checked on focus so
  // saving the file in Word and coming back to the pane clears the notice.
  useEffect(() => {
    let alive = true;
    const check = () => {
      isDocumentUnsaved()
        .then((u) => { if (alive) setUnsaved(u); })
        .catch(() => { /* never let a probe break the pane */ });
    };
    check();
    window.addEventListener("focus", check);
    return () => {
      alive = false;
      window.removeEventListener("focus", check);
    };
  }, []);

  // ~2 s debounce: onParagraphChanged fires per keystroke, and readBody() on an
  // 85,000-char contract is not free.
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const measure = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        readBody()
          .then((t) => { if (alive) setLiveDocChars(t.length); })
          .catch(() => { /* never let a probe break the pane */ });
      }, LIVE_DOC_DEBOUNCE_MS);
    };
    measure();   // seed the line before the first edit
    // onParagraphChanged needs WordApi 1.5 and is absent from some office-js
    // typing releases, hence the narrow cast rather than a direct call. On a
    // host without the event, registration rejects and we fall back to
    // re-reading on focus — the same signal the unsaved check already uses.
    const useFocus = () => window.addEventListener("focus", measure);
    Word.run(async (context) => {
      const doc = context.document as unknown as {
        onParagraphChanged: { add: (h: () => Promise<void>) => void };
      };
      doc.onParagraphChanged.add(async () => { measure(); });
      await context.sync();
    }).catch(useFocus);
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
      // Harmless when it was never added, and the alternative is tracking a
      // flag that the async .catch() above may set after cleanup has run.
      window.removeEventListener("focus", measure);
    };
  }, []);

  // One panel for the whole pane; cards reach it through the feedback module's
  // one-slot channel rather than four levels of prop drilling.
  useEffect(() => {
    onFlagRequested(async (t) => {
      // Attach the document at flag time, not at card-render time — the doc may
      // have changed since the turn, and what we want is what they are looking at.
      const documentText = await readBody().catch(() => "");
      // Card flags inherit a documentId from the turn that produced them; the
      // header ("miss") flag has no turn, so resolve it here. Without this a
      // miss report — the entry point with no card, and the one class of
      // failure nothing else can catch — cannot be grouped with any other
      // feedback on the same contract.
      const documentId = t.turn.documentId || (await resolveDocumentId().catch(() => ""));
      setFlagTarget({
        ...t,
        turn: { ...t.turn, documentId },
        snapshot: { ...t.snapshot, ...buildSnapshot({ documentText }) },
      });
    });
    return () => onFlagRequested(null);
  }, []);

  return (
    <div className="app">
      <header>
        <h1>Legal Triage</h1>
        <p className="subtitle">Reviews the open document against the firm's standards.</p>
        <button
          className="secondary feedback-open"
          onClick={() =>
            // Route through requestFlag (not setFlagTarget directly) so this
            // shares the document-attach path registered below — otherwise
            // the panel's "sends the document text" promise is false for the
            // one entry point (the miss) that has no card to hang off.
            // Seed sessionId (already in scope) rather than a fully-empty
            // EMPTY_TURN, so a miss report still has something to correlate.
            requestFlag({
              turn: { ...EMPTY_TURN, sessionId },
              surface: "general",
              targetKind: "",
              targetRef: "",
              snapshot: {},
            })
          }
        >
          Send feedback
        </button>
        <p className="disclosure">
          During testing, your use of the assistant's suggestions is recorded so we
          can measure what it gets wrong.
        </p>
      </header>
      {/* Deliberately NOT the amber .status.warning used for memory_degraded.
          That one means "this turn wasn't remembered"; this means "nothing
          about this document will be remembered after you close it" — a
          document-level fact, which is also why it sits above the tabs rather
          than inside one. Two identical-looking banners would blur both. */}
      {unsaved && (
        <p className="unsaved-notice" role="status">
          <strong>This document isn't saved yet.</strong> Its review history, chat
          and any feedback you send stop being linked to this contract once you
          close it. Save the file first.
        </p>
      )}
      {/* The live-document adjustment moved INTO ContextMeter: withLiveDocument
          allocates a new object per call, and the auto-fire effect has to key on
          the raw per-turn breakdown or it fires on every render. */}
      <ContextMeter
        breakdown={breakdown}
        liveDocChars={liveDocChars}
        docTruncated={docTruncated}
      />
      <Tabs active={tab} onChange={setTab} />
      {/* Both tabs always mounted; visibility toggled via CSS so state persists. */}
      <div className={`tab-pane ${tab === "findings" ? "" : "hidden"}`}>
        <FindingsTab
          sessionId={sessionId}
          result={findingsResult}
          setResult={setFindingsResult}
          onBreakdown={(b, truncated) => { setBreakdown(b); setDocTruncated(truncated); }}
        />
      </div>
      <div className={`tab-pane ${tab === "chat" ? "" : "hidden"}`}>
        <ChatTab
          sessionId={sessionId}
          messages={chatMessages}
          setMessages={setChatMessages}
          onPreferenceAdded={() => setPrefLoaded(false)}
          onBreakdown={(b, truncated) => { setBreakdown(b); setDocTruncated(truncated); }}
        />
      </div>
      <div className={`tab-pane ${tab === "preferences" ? "" : "hidden"}`}>
        <PreferencesTab
          markdown={prefMarkdown}
          setMarkdown={setPrefMarkdown}
          loaded={prefLoaded}
          setLoaded={setPrefLoaded}
        />
      </div>
      {flagTarget && (
        // Keyed on target identity: switching from one card's flag to
        // another's without closing in between must remount the panel, not
        // reuse the instance — otherwise typed-but-unsent text (and status)
        // from the old target survives onto the new one.
        <FeedbackPanel
          key={`${flagTarget.surface}:${flagTarget.targetKind}:${flagTarget.targetRef}:${flagTarget.turn.turnId}`}
          target={flagTarget}
          onClose={() => setFlagTarget(null)}
        />
      )}
      {/* Document-level action, available regardless of the active tab. */}
      <FinalizeBar />
    </div>
  );
}
