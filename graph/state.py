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
    contract_type_detected: str            # NEW — nda | msa | sow | baa (set by contract_review when uploaded_text present)
    requires_attorney: bool                # NEW — contract_review verdict says the doc needs attorney sign-off (blocker/yellow); surfaced in report
    interactive_review: bool               # NEW — caller can handle a human_review interrupt + resume (Chainlit True; Word False)
    document_id: str                       # NEW — stable id for the open document (review-store key)
    memory_degraded: bool                  # NEW — True when a memory read/store was unavailable this turn
