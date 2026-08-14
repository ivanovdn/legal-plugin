import { useEffect, useState } from "react";
import Tabs, { type TabKey } from "./components/Tabs";
import FindingsTab from "./components/FindingsTab";
import ChatTab, { type ChatMessage } from "./components/ChatTab";
import PreferencesTab from "./components/PreferencesTab";
import FinalizeBar from "./components/FinalizeBar";
import FeedbackPanel from "./components/FeedbackPanel";
import { buildSnapshot, onFlagRequested, EMPTY_TURN, type FlagTarget } from "./feedback";
import { readBody } from "./word";
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
      setFlagTarget({ ...t, snapshot: { ...t.snapshot, ...buildSnapshot({ documentText }) } });
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
            setFlagTarget({
              turn: EMPTY_TURN,
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
        <FeedbackPanel target={flagTarget} onClose={() => setFlagTarget(null)} />
      )}
      {/* Document-level action, available regardless of the active tab. */}
      <FinalizeBar />
    </div>
  );
}
