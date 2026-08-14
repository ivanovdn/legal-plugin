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


class PreferencesUpdate(BaseModel):
    """Replace an attorney's USER.md."""
    markdown: str = Field("", description="Full markdown content of the attorney's USER.md")


class FeedbackSubmission(BaseModel):
    """An attorney's written report about one turn.

    attorney_id is deliberately absent — identity comes from the auth seam.
    """
    turn_id: str = Field("", description="The turn being reported on")
    trace_id: str = Field("", description="OTel trace id for that turn, when tracing is on")
    session_id: str = Field("", description="Pane session the turn belongs to")
    document_id: str = Field("", description="Stable document id")
    surface: str = Field("general", description="findings | chat | general")
    target_kind: str = Field("", description="finding | edit | reply; empty for unattached feedback")
    target_ref: str = Field("", description="Issue id, clause, or target-text excerpt")
    comment: str = Field(..., description="The attorney's own words")
    snapshot: dict | None = Field(None, description="Replayable input context; capped server-side")


class InteractionEvent(BaseModel):
    """One recorded interaction with an assistant suggestion."""
    turn_id: str = ""
    session_id: str = ""
    document_id: str = ""
    surface: str = ""
    action: str
    target_kind: str = ""
    target_ref: str = ""
    detail: str = Field("", description="Error text on failures; the count on per-turn counters")


class InteractionEventBatch(BaseModel):
    """A burst of interactions in one request."""
    events: list[InteractionEvent] = Field(default_factory=list)


class ApiResponse(BaseModel):
    """Standard response envelope."""
    status: str = "ok"
    data: dict | list | None = None
    errors: list[str] | None = None
