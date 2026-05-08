# graph/state.py
from __future__ import annotations

from typing import TypedDict

from ingest.chunk_models import LegalChunk


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
    checkpoint_id: str
    trace_id: str
