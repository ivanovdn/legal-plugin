// API client for the legal-plugin FastAPI backend.
// All paths are relative — Vite's dev-server proxy rewrites /api/* to http://localhost:8000.

export interface QueryResponse {
  status: "ok" | "error";
  data?: {
    session_id?: string;
    task_type?: string;
    risk_level?: string;
    awaiting_review?: boolean;
    report?: { response?: string; sources?: unknown[]; notes_unincorporated?: string };
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

export async function submitReview(docText: string): Promise<QueryResponse> {
  const body = {
    request: "Review this contract.",
    task_type: "contract_review",
    filters: { client_id: "internal" },
    uploaded_text: docText,
  };
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
