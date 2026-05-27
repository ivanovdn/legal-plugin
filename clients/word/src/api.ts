// API client for the legal-plugin FastAPI backend.
// All paths are relative — Vite's dev-server proxy rewrites /api/* to http://localhost:8000.

import type { EditProposal } from "./parseEditBlocks";

export interface QueryResponse {
  status: "ok" | "error";
  data?: {
    session_id?: string;
    task_type?: string;
    risk_level?: string;
    awaiting_review?: boolean;
    report?: {
      response?: string;
      sources?: unknown[];
      notes_unincorporated?: string;
      proposed_edits?: EditProposal[];
    };
    interrupt_payload?: {
      task_type?: string;
      risk_level?: string;
      llm_response?: string;
      risk_flags?: unknown[];
      review_iterations?: number;
    };
  };
  errors?: string[];
}

async function postQuery(body: Record<string, unknown>): Promise<QueryResponse> {
  const res = await fetch("/api/query", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-User-ID": "word-addin" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(`Backend returned ${res.status} ${res.statusText}`);
  }
  return res.json();
}

/** Run the contract_review skill on the doc text. */
export async function submitReview(docText: string, sessionId: string): Promise<QueryResponse> {
  return postQuery({
    request: "Review this contract.",
    task_type: "contract_review",
    session_id: sessionId,
    filters: { client_id: "internal" },
    uploaded_text: docText,
  });
}

/**
 * Ask a follow-up question about the open document.
 *
 * Explicit task_type="research" routes to legal_research (the ReAct agent
 * for Q&A). legal_research was patched to read uploaded_docs from state,
 * so the agent sees the open document as primary context. Skipping
 * intent_router saves a classifier LLM call per turn.
 *
 * Sharing sessionId with submitReview means chat_history carries the prior
 * contract_review output forward into the chat turns automatically.
 */
export async function chatQuery(
  question: string,
  docText: string,
  sessionId: string,
): Promise<QueryResponse> {
  return postQuery({
    request: question,
    task_type: "research",
    session_id: sessionId,
    filters: { client_id: "internal" },
    uploaded_text: docText,
  });
}
