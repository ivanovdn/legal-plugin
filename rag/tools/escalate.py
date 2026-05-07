# rag/tools/escalate.py
"""Escalation tool — flags items for attorney review."""

from datetime import datetime, timezone

from langchain_core.tools import tool

from config import get_settings

_escalation_store: list[dict] = []


@tool
def escalate(
    reason: str,
    context: str = "",
    severity: str = "medium",
) -> str:
    """Escalate an issue for attorney review.

    Use when: retrieval confidence is too low, no citations found,
    conflicting information, or high-risk determination needed.

    Args:
        reason: Why this is being escalated.
        context: Relevant context (query, partial results, etc.).
        severity: low, medium, or high.

    Returns:
        Escalation ticket ID and confirmation.
    """
    settings = get_settings()
    prefix = settings.escalation_ticket_prefix
    year = datetime.now(timezone.utc).strftime("%Y")
    seq = len(_escalation_store) + 1
    ticket_id = f"{prefix}-{year}-{seq:04d}"

    ticket = {
        "ticket_id": ticket_id,
        "reason": reason,
        "context": context,
        "severity": severity,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _escalation_store.append(ticket)

    return (
        f"Escalation ticket created: {ticket_id}\n"
        f"Severity: {severity}\n"
        f"Reason: {reason}\n"
        f"This will be routed to attorney review."
    )
