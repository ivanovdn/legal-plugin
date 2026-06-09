import { useEffect, useRef, useState } from "react";
import { chatQuery } from "../api";
import { extractEditBlocks, type EditProposal } from "../parseEditBlocks";
import { readBody } from "../word";
import EditProposalCard from "./EditProposalCard";

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  proposedEdits?: EditProposal[];
  /** True when the response sounded like an edit promise but no block came through. */
  promisedEditMissing?: boolean;
  /** Original LLM output before edit-block stripping — for the "show raw" toggle. */
  rawResponse?: string;
}

// Phrases the LLM uses when it claims it's about to make an edit OR has just
// made one. If we see any of these in the response AND no JSON block was
// emitted, surface a warning — the user otherwise sees a confident "I will
// replace X" / "I have replaced X" with no actual change.
//
// Verb-stem trick: stems are shortened so the `\w{0,3}\b` tail matches both
// present (replace, replaces, replacing) AND past (replaced) tenses. Mirrors
// the _EDIT_PROMISE_RE pattern in skills/legal_research.py.
const PROMISE_PATTERNS = [
  /\bi['’]?(?:ll|ve| will| have| am going to)\b[^.?!\n]*\b(?:replac|insert|delet|fill|add|remov|chang|rewrit|tighten|loosen|updat|edit|modif|set)\w{0,3}\b/i,
  /\bi['’]?(?:ll|ve| will| have| am going to)\b[^.?!\n]*\b(?:made|appli\w{0,3}|perform\w{0,3})\b[^.?!\n]*\b(?:edit|change|update|replacement)\w?\b/i,
];

function looksLikeEditPromise(prose: string): boolean {
  return PROMISE_PATTERNS.some((p) => p.test(prose));
}

interface Props {
  sessionId: string;
  messages: ChatMessage[];
  setMessages: React.Dispatch<React.SetStateAction<ChatMessage[]>>;
}

export default function ChatTab({ sessionId, messages, setMessages }: Props) {
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom on new messages.
  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

  const send = async () => {
    const question = input.trim();
    if (!question || busy) return;
    setInput("");
    setError(null);
    setMessages((m) => [...m, { role: "user", content: question }]);
    setBusy(true);
    try {
      const docText = await readBody();
      const res = await chatQuery(question, docText, sessionId);
      if (res.status === "error") {
        setError((res.errors ?? ["unknown error"])[0]);
        return;
      }
      const rawAnswer =
        res.data?.report?.response ?? res.data?.interrupt_payload?.llm_response ?? "(no response)";
      // Strip fenced JSON blocks for display; prefer the backend's authoritative
      // parsed proposed_edits, falling back to client-side extraction.
      const { cleanedProse, blocks } = extractEditBlocks(rawAnswer);
      // Use the backend's parsed edits when present, otherwise the frontend's
      // own extraction. Note: a plain `??` would only fall back on null/undefined
      // — an empty array would short-circuit to the empty backend value and
      // ignore the frontend's correctly-parsed blocks. Prefer NON-EMPTY instead.
      const backendEdits = res.data?.report?.proposed_edits ?? [];
      const proposedEdits = backendEdits.length > 0 ? backendEdits : blocks;
      const finalProse = cleanedProse || rawAnswer;
      const promisedEditMissing =
        proposedEdits.length === 0 && looksLikeEditPromise(finalProse);
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          content: finalProse,
          proposedEdits: proposedEdits.length > 0 ? proposedEdits : undefined,
          promisedEditMissing,
          rawResponse: rawAnswer,
        },
      ]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <div className="tab-content chat">
      <div className="chat-list" ref={listRef}>
        {messages.length === 0 && (
          <div className="status">
            Ask a follow-up about the open document. Try: "Why is the IP clause risky?" or "Show me a stricter cap."
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`chat-msg chat-${m.role}`}>
            <div className="chat-role">{m.role === "user" ? "You" : "Assistant"}</div>
            <div className="chat-content">{m.content}</div>
            {m.proposedEdits?.map((proposal, j) => (
              <EditProposalCard key={`${i}-${j}`} proposal={proposal} />
            ))}
            {m.promisedEditMissing && (
              <div className="chat-warning">
                The assistant described an edit but didn't emit the required edit block, so
                nothing was changed in the document. Try rephrasing — e.g., quote the exact
                text to replace, or split the request into one location at a time.
              </div>
            )}
            {m.role === "assistant" && m.rawResponse && (
              <details className="raw-response">
                <summary>Show raw LLM response ({m.rawResponse.length.toLocaleString()} chars)</summary>
                <pre>{m.rawResponse}</pre>
              </details>
            )}
          </div>
        ))}
        {busy && (
          <div className="chat-msg chat-assistant">
            <div className="chat-role">Assistant</div>
            <div className="chat-content chat-thinking">Thinking… (30–60 s)</div>
          </div>
        )}
      </div>

      {error && <div className="status error">Error: {error}</div>}

      <div className="chat-input-row">
        <textarea
          className="chat-input"
          rows={2}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Ask about the open document…"
          disabled={busy}
        />
        <button className="primary" onClick={send} disabled={busy || !input.trim()}>
          Send
        </button>
      </div>
    </div>
  );
}
