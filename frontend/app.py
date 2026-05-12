# frontend/app.py
"""Chainlit frontend — chat interface for the legal plugin."""

import sys
from pathlib import Path

# Ensure project root is on path when Chainlit runs this file directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chainlit as cl

from frontend.api_client import submit_query, ingest_file, health_check


@cl.on_chat_start
async def on_chat_start():
    """Initialize chat session."""
    try:
        result = await health_check()
        if result.get("status") == "ok":
            await cl.Message(
                content="Legal Assistant ready. How can I help you today?\n\n"
                "You can:\n"
                "- Ask legal questions\n"
                "- Request contract generation or review\n"
                "- Upload documents (PDF/DOCX) for ingestion\n"
                "- Check compliance against policies",
            ).send()
        else:
            await cl.Message(content="Warning: Backend API is not fully healthy.").send()
    except Exception as e:
        await cl.Message(
            content=f"Error: Cannot reach backend API. Make sure FastAPI is running on port 8000.\n\n{e}",
        ).send()


@cl.on_message
async def on_message(message: cl.Message):
    """Handle incoming chat messages."""
    user = cl.user_session.get("user")
    user_id = user.identifier if user else "anonymous"

    # Check for file uploads
    if message.elements:
        await _handle_file_upload(message)
        return

    # Send query to backend
    thinking_msg = cl.Message(content="Processing your request...")
    await thinking_msg.send()

    try:
        result = await submit_query(
            request=message.content,
            user_id=user_id,
        )

        data = result.get("data", {})
        report = data.get("report", {})
        response_text = report.get("response", "No response generated.")
        task_type = data.get("task_type", "unknown")
        risk_level = data.get("risk_level", "unknown")
        awaiting_review = data.get("awaiting_review", False)
        sources = report.get("sources", [])

        # Build response message
        content_parts = []
        content_parts.append(response_text)
        content_parts.append(f"\n\n---\n**Task:** {task_type} | **Risk:** {risk_level}")

        if sources:
            source_lines = [f"- {s.get('doc_title', '?')} (`{s.get('doc_id', '?')}`)" for s in sources]
            content_parts.append("\n**Sources:**\n" + "\n".join(source_lines))

        thinking_msg.content = "\n".join(content_parts)
        await thinking_msg.update()

        # Human-in-the-loop for flagged responses
        if awaiting_review:
            await _handle_review(data)

    except Exception as e:
        thinking_msg.content = f"Error: {e}"
        await thinking_msg.update()


async def _handle_file_upload(message: cl.Message):
    """Process uploaded files — send to ingest endpoint."""
    for element in message.elements:
        if not hasattr(element, "path") or not element.path:
            continue

        filename = element.name or "unknown"
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

        if ext not in ("pdf", "docx"):
            await cl.Message(content=f"Unsupported file type: .{ext}. Please upload PDF or DOCX files.").send()
            continue

        status_msg = cl.Message(content=f"Ingesting **{filename}**...")
        await status_msg.send()

        try:
            result = await ingest_file(
                file_path=element.path,
                filename=filename,
            )
            data = result.get("data", {})
            chunks = data.get("chunks", 0)
            status_msg.content = f"Ingested **{filename}**: {chunks} chunks added to knowledge base."
            await status_msg.update()
        except Exception as e:
            status_msg.content = f"Failed to ingest **{filename}**: {e}"
            await status_msg.update()


async def _handle_review(data: dict):
    """Show review prompt for flagged responses."""
    risk_flags = data.get("report", {}).get("risk_flags", [])

    flag_text = ""
    if risk_flags:
        flag_lines = [f"- {f.get('reason', '?')}" for f in risk_flags]
        flag_text = "\n**Risk flags:**\n" + "\n".join(flag_lines)

    actions = [
        cl.Action(
            name="approve",
            label="Approve",
            description="Approve this response for delivery",
            payload={"action": "approve"},
        ),
        cl.Action(
            name="reject",
            label="Reject & Edit",
            description="Reject and provide feedback",
            payload={"action": "reject"},
        ),
    ]

    review_msg = await cl.AskActionMessage(
        content=f"This response requires attorney review.{flag_text}\n\nPlease review the response above and approve or reject.",
        actions=actions,
    ).send()

    if review_msg and review_msg.get("payload", {}).get("action") == "approve":
        await cl.Message(content="Response approved.").send()
    elif review_msg:
        await cl.Message(content="Response rejected. Please provide your revised response or feedback.").send()
    else:
        await cl.Message(content="Review timed out. Response held for later review.").send()
