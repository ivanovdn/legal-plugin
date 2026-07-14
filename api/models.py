# api/models.py
"""Pydantic models for API request/response."""

from __future__ import annotations

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    """Submit a legal query for graph execution."""
    request: str = Field(..., description="The legal request text")
    task_type: str = Field("", description="Optional: pre-set task type to skip intent classification")
    session_id: str = Field("", description="Optional: resume an existing session")
    filters: dict = Field(default_factory=dict, description="Optional: additional retrieval filters (jurisdiction, doc_type)")
    uploaded_text: str = Field("", description="Optional: uploaded document text for review/analysis")
    document_uuid: str = Field("", description="Client-supplied stable document id (Office custom setting); falls back to the server-side preamble hash when empty")
    interactive_review: bool = Field(False, description="Caller can handle a human_review interrupt and resume it. Set True by clients with a review loop (Chainlit); leave False for clients without a resume UI (Word) so a contract-review blocker is reported, not interrupted.")


class ResumeRequest(BaseModel):
    """Resume a graph execution after human review interrupt."""
    approved: bool = Field(True, description="Whether the attorney approves the output")
    notes: str = Field("", description="Attorney review notes")
    revised_response: str = Field("", description="Optional: revised response text if not approved")


class ApiResponse(BaseModel):
    """Standard response envelope."""
    status: str = "ok"
    data: dict | list | None = None
    errors: list[str] | None = None
