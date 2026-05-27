import { useEffect, useRef, useState } from "react";
import { chatQuery } from "../api";
import { extractEditBlocks, type EditProposal } from "../parseEditBlocks";
import { readBody } from "../word";
import EditProposalCard from "./EditProposalCard";

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  proposedEdits?: EditProposal[];
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
      const proposedEdits = res.data?.report?.proposed_edits ?? blocks;
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          content: cleanedProse || rawAnswer,
          proposedEdits: proposedEdits.length > 0 ? proposedEdits : undefined,
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
