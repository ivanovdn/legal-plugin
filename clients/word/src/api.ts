// API client for the legal-plugin FastAPI backend.
// All paths are relative — Vite's dev-server proxy rewrites /api/* to http://localhost:8000.

import type { EditProposal } from "./parseEditBlocks";
import { resolveDocumentId } from "./docIdentity";
import { userHeaders } from "./attorneyIdentity";
import type { ContextTruncated, TokenUsage } from "./contextNotice";
import type { ContextBreakdown } from "./contextGauge";

export interface QueryResponse {
  status: "ok" | "error";
  data?: {
    session_id?: string;
    turn_id?: string;
    trace_id?: string;
    task_type?: string;
    risk_level?: string;
    awaiting_review?: boolean;
    memory_degraded?: boolean;
    report?: {
      response?: string;
      sources?: unknown[];
      notes_unincorporated?: string;
      proposed_edits?: EditProposal[];
      proposed_preferences?: string[];
      contract_type_detected?: string;
      review_persist_error?: string;
      context_truncated?: ContextTruncated | null;
      tokens?: TokenUsage | null;
      context_breakdown?: ContextBreakdown | null;
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
    headers: { "Content-Type": "application/json", ...userHeaders() },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(`Backend returned ${res.status} ${res.statusText}`);
  }
  return res.json();
}

/** Run the contract_review skill on the doc text. */
export async function submitReview(docText: string, sessionId: string): Promise<QueryResponse> {
  const document_uuid = await resolveDocumentId();
  return postQuery({
    request: "Review this contract.",
    task_type: "contract_review",
    session_id: sessionId,
    filters: { client_id: "internal" },
    uploaded_text: docText,
    document_uuid,
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
  const document_uuid = await resolveDocumentId();
  return postQuery({
    request: question,
    task_type: "research",
    session_id: sessionId,
    filters: { client_id: "internal" },
    uploaded_text: docText,
    document_uuid,
  });
}

export interface CompactResponse {
  status: "ok" | "error";
  data?: {
    compacted?: boolean;
    from_id?: number;
    to_id?: number;
    messages?: number;
    quotes?: number;
    /** Quotes that could not be verified against the row they cited, and were left
     *  out. Surfaced to the attorney rather than swallowed: a rising count is the
     *  signal that the model has drifted. */
    dropped?: number;
    segment_id?: number;
    reason?: string;
    error?: string;
  };
  errors?: string[];
}

/**
 * Condense the earlier part of this document's conversation.
 *
 * Its own endpoint, not a flag on /api/query: compaction produces no answer and
 * carries its own latency. A failure comes back as a non-2xx and is thrown —
 * the attorney clicked, so a silent failure would be a lie.
 */
export async function compactConversation(documentId: string): Promise<CompactResponse> {
  const res = await fetch("/api/compact", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...userHeaders() },
    body: JSON.stringify({ document_id: documentId }),
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* a non-JSON error body is still an error — keep the status line */
    }
    throw new Error(detail);
  }
  return res.json();
}
