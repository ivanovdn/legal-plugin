# graph/state.py
from __future__ import annotations

from typing import Annotated, TypedDict

from config import get_settings
from ingest.chunk_models import LegalChunk


def _history_reducer(old: list[dict] | None, new: list[dict]) -> list[dict]:
    """Concatenate old + new, then cap to the last 2*N entries (FIFO eviction).

    N = chat_history_n_turns from settings. Each turn contributes 2 entries
    (one user message, one assistant message), so 2*N is the message cap.

    Idempotent for "node forwarding state unchanged": when `new == old`, return
    `old` without concatenating. Most graph nodes return the FULL state dict,
    which makes LangGraph fire this reducer with `old == new` once per node per
    turn — without the idempotency guard, chat_history would double on every
    node and explode to the cap. history_appender is the one node that returns
    a partial dict with genuinely-new messages; its append goes through the
    normal concatenate-and-cap path.
    """
    n = get_settings().chat_history_n_turns
    old = old or []
    new = new or []
    if new == old:
        return old
    return (old + new)[-(2 * n):]


# The book passages a conversation keeps as retrieved (spec D8): enough for any real session, and a
# bound on what the checkpointer carries.
BOOK_REFS_CAP = 500


def _book_refs_reducer(old: list[str] | None, new: list[str] | None) -> list[str]:
    """The conversation's retrieved book ids, oldest first: old, then new, an id retrieved again moving
    to the end, capped to the last BOOK_REFS_CAP (ruling 19).

    Idempotent when `new == old`, for the same reason as `_history_reducer`: nodes hand back the whole
    state, so this fires with the current value once per node. An empty `new` (the [] query.py seeds
    every turn with, or a turn that retrieved nothing) keeps `old`.
    """
    old = old or []
    new = list(dict.fromkeys(new or []))
    if new == old:
        return old
    again = set(new)
    return ([ref_id for ref_id in old if ref_id not in again] + new)[-BOOK_REFS_CAP:]


class LegalAgentState(TypedDict):
    request: str
    user_id: str
    uploaded_docs: list[LegalChunk]
    task_type: str          # contract_generation | contract_review | compliance | research | drafting | multi
    skill_plan: list[str]
    retrieval_query: str
    retrieved_chunks: list[LegalChunk]
    filters: dict           # client_id, jurisdiction, doc_type
    messages: list[dict]
    llm_response: str
    risk_level: str         # low | medium | high
    risk_flags: list[dict]
    awaiting_review: bool
    attorney_notes: str
    report: dict
    session_id: str
    checkpoint_ref: str
    trace_id: str
    chat_history: Annotated[list[dict], _history_reducer]
    review_iterations: int                 # NEW — counts loop-backs; capped at max_review_iterations
    report_notes_unincorporated: str       # NEW — attorney notes the loop couldn't incorporate (set on cap)
    previous_draft: str                    # NEW — preserved across loop-back so skill revises rather than regenerates
    proposed_edits: list[dict]             # NEW — structured edit proposals parsed from chat skill output
    proposed_preferences: list[str]        # NEW — plain-text preference suggestions parsed from chat output
    contract_type_detected: str            # NEW — nda | msa | sow | baa (set by contract_review when uploaded_text present)
    requires_attorney: bool                # NEW — contract_review verdict says the doc needs attorney sign-off (blocker/yellow); surfaced in report
    interactive_review: bool               # NEW — caller can handle a human_review interrupt + resume (Chainlit True; Word False)
    document_id: str                       # NEW — stable id for the open document (review-store key)
    memory_degraded: bool                  # NEW — True when a memory read/store was unavailable this turn
    user_name: str                         # NEW — display-only name (self-entered now; SSO `name` claim later); NEVER a key
    context_truncated: dict | None         # NEW — set when the document was cut to fit the budget; None on a healthy turn
    token_usage: dict | None               # NEW — real prompt/completion counts from the LLM response (observability.tracing shape)
    context_breakdown: dict | None          # NEW — what this turn's context actually spent, for the pane's counter
    turn_started_at: float                 # NEW — epoch secs the request began (submit or resume); audit duration_ms derives from it
    turn_id: str                           # NEW — the request's turn id (query.py mints it; a resume re-stamps it); audit_log carries it so its row joins turn_log
    book_refs_retrieved: Annotated[list[str], _book_refs_reducer]  # NEW — book ref_ids the conversation retrieved (book tools' results), recency order, capped
    book_refs_turn: list[str]              # NEW — book ref_ids THIS turn retrieved, sorted (per turn; legal_research resets it)
    book_citations: list[dict]             # NEW — each book ref_id the answer cites, checked once by risk_assessor: {ref_id, valid, citation, reason} (per turn)


def has_attached_document(state: LegalAgentState | dict) -> bool:
    """A turn has a document attached when `uploaded_docs` holds one, whatever its text: the Word pane's
    research turns always attach theirs, a blank document's "" included (api/routes/query.py). The one
    test of it: legal_research takes doc-chat on it, and risk_assessor then checks no book citation."""
    return bool(state.get("uploaded_docs"))
