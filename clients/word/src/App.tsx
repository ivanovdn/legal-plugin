import { useEffect, useState } from "react";
import Tabs, { type TabKey } from "./components/Tabs";
import FindingsTab from "./components/FindingsTab";
import ChatTab, { type ChatMessage } from "./components/ChatTab";
import PreferencesTab from "./components/PreferencesTab";
import FinalizeBar from "./components/FinalizeBar";
import FeedbackPanel from "./components/FeedbackPanel";
import { buildSnapshot, onFlagRequested, requestFlag, EMPTY_TURN, type FlagTarget } from "./feedback";
import { readBody } from "./word";
import { resolveDocumentId } from "./docIdentity";
import type { ReviewSummary } from "./parser";

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
      <Tabs active={tab} onChange={setTab} />
      {/* Both tabs always mounted; visibility toggled via CSS so state persists. */}
      <div className={`tab-pane ${tab === "findings" ? "" : "hidden"}`}>
        <FindingsTab
          sessionId={sessionId}
          result={findingsResult}
          setResult={setFindingsResult}
        />
      </div>
      <div className={`tab-pane ${tab === "chat" ? "" : "hidden"}`}>
        <ChatTab
          sessionId={sessionId}
          messages={chatMessages}
          setMessages={setChatMessages}
          onPreferenceAdded={() => setPrefLoaded(false)}
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
