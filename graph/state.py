# graph/state.py
from __future__ import annotations

from typing import Annotated, TypedDict

from config import get_settings
from ingest.chunk_models import LegalChunk


def _history_reducer(old: list[dict] | None, new: list[dict]) -> list[dict]:
    """Concatenate old + new, then cap to the last 2*N entries.

    N = chat_history_n_turns from settings. Each turn contributes 2 entries
    (one user message, one assistant message), so 2*N is the message cap.
    """
    n = get_settings().chat_history_n_turns
    old = old or []
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
