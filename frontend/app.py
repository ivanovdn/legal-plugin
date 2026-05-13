# frontend/app.py
"""Chainlit frontend — chat interface for the legal plugin."""

import sys
from pathlib import Path

# Ensure project root is on path when Chainlit runs this file directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chainlit as cl

import tempfile

from fpdf import FPDF

from frontend.api_client import submit_query, ingest_file, health_check


def _generate_pdf(text: str) -> str:
    """Generate a PDF from contract text. Returns path to temp file."""
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_left_margin(15)
    pdf.set_right_margin(15)

    def _clean(s: str) -> str:
        """Replace Unicode chars that Helvetica can't render."""
        return (s
            .replace("\u2014", "--")   # em dash
            .replace("\u2013", "-")    # en dash
            .replace("\u2018", "'")    # left single quote
            .replace("\u2019", "'")    # right single quote
            .replace("\u201c", '"')    # left double quote
            .replace("\u201d", '"')    # right double quote
            .replace("\u2026", "...")   # ellipsis
            .replace("\u26a0", "[!]")  # warning sign
            .replace("\u2022", "-")    # bullet
            .replace("**", "")
        )

    for line in text.split("\n"):
        stripped = line.strip()

        if stripped.startswith("**") and stripped.endswith("**"):
            pdf.set_font("Helvetica", "B", 12)
            pdf.multi_cell(0, 7, _clean(stripped), new_x="LMARGIN", new_y="NEXT")
        elif stripped.startswith("DRAFT") or stripped.startswith("SERVICE AGREEMENT") or stripped.startswith("DEVIATION"):
            pdf.set_font("Helvetica", "B", 14)
            pdf.multi_cell(0, 8, _clean(stripped), new_x="LMARGIN", new_y="NEXT")
        elif stripped.startswith("===") or stripped.startswith("---"):
            pdf.ln(3)
        elif not stripped:
            pdf.ln(4)
        else:
            pdf.set_font("Helvetica", "", 10)
            pdf.multi_cell(0, 5, _clean(stripped), new_x="LMARGIN", new_y="NEXT")

    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    pdf.output(tmp.name)
    return tmp.name


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

    # File upload: if user wrote a message with it, treat as review/analysis
    # If no message text, treat as ingestion
    uploaded_text = ""
    if message.elements:
        has_text = message.content and message.content.strip()
        if not has_text:
            await _handle_file_upload(message)
            return
        # Extract text from uploaded file for review
        uploaded_text = await _extract_file_text(message.elements)
        if not uploaded_text:
            await cl.Message(content="Could not extract text from the uploaded file.").send()
            return

    # Send query to backend
    thinking_msg = cl.Message(content="Processing your request...")
    await thinking_msg.send()

    try:
        result = await submit_query(
            request=message.content,
            user_id=user_id,
            uploaded_text=uploaded_text,
        )

        data = result.get("data", {})
        report = data.get("report", {})
        response_text = report.get("response", "No response generated.")
        task_type = data.get("task_type", "unknown")
        risk_level = data.get("risk_level", "unknown")
        awaiting_review = data.get("awaiting_review", False)
        sources = report.get("sources", [])

        # Contract generation / drafting — show draft in side panel
        if task_type in ("contract_generation", "drafting") and len(response_text) > 500:
            await _handle_contract_response(
                thinking_msg, response_text, task_type, risk_level, sources, awaiting_review,
            )
            return

        # Regular response — show inline
        content_parts = [response_text]
        content_parts.append(f"\n\n---\n**Task:** {task_type} | **Risk:** {risk_level}")

        if sources:
            source_lines = [f"- {s.get('doc_title', '?')} (`{s.get('doc_id', '?')}`)" for s in sources]
            content_parts.append("\n**Sources:**\n" + "\n".join(source_lines))

        thinking_msg.content = "\n".join(content_parts)
        await thinking_msg.update()

        if awaiting_review:
            await _handle_review(report)

    except Exception as e:
        thinking_msg.content = f"Error: {e}"
        await thinking_msg.update()


async def _handle_contract_response(
    thinking_msg: cl.Message,
    response_text: str,
    task_type: str,
    risk_level: str,
    sources: list,
    awaiting_review: bool,
):
    """Handle contract generation / drafting — summary in chat, full draft as text element."""

    # Extract deviation report if present
    summary = ""
    draft = response_text
    if "DEVIATION REPORT" in response_text:
        parts = response_text.split("DEVIATION REPORT", 1)
        draft = parts[0].strip()
        deviation_report = "DEVIATION REPORT" + parts[1]
        summary = deviation_report
    else:
        summary = f"Generated {task_type.replace('_', ' ')} draft ({len(response_text)} chars)"

    # Summary message in chat with deviation report
    chat_content = f"**Contract draft generated** — click the attachment to review the full text.\n\n"
    chat_content += f"**Task:** {task_type} | **Risk:** {risk_level}\n\n"
    if summary:
        chat_content += f"```\n{summary}\n```"

    # Full contract as a text element (shows in side panel on click)
    text_element = cl.Text(
        name="contract_draft.md",
        content=draft,
        display="side",
    )

    thinking_msg.content = chat_content
    thinking_msg.elements = [text_element]
    await thinking_msg.update()

    # Human review
    if awaiting_review:
        actions = [
            cl.Action(
                name="approve",
                label="Approve Draft",
                description="Approve this contract draft for delivery",
                payload={"action": "approve"},
            ),
            cl.Action(
                name="request_changes",
                label="Request Changes",
                description="Send back with feedback",
                payload={"action": "request_changes"},
            ),
            cl.Action(
                name="reject",
                label="Reject",
                description="Reject this draft entirely",
                payload={"action": "reject"},
            ),
        ]

        review_msg = await cl.AskActionMessage(
            content="This draft requires your review before delivery. Please review the contract in the side panel.",
            actions=actions,
            timeout=86400,
        ).send()

        if review_msg:
            action = review_msg.get("payload", {}).get("action", "")
            if action == "approve":
                pdf_path = _generate_pdf(draft)
                pdf_element = cl.File(
                    name="contract_draft.pdf",
                    path=pdf_path,
                    display="inline",
                )
                await cl.Message(
                    content="Draft approved. Download the PDF below.",
                    elements=[pdf_element],
                ).send()
            elif action == "request_changes":
                await cl.Message(content="Please describe the changes you'd like made to this draft.").send()
            elif action == "reject":
                await cl.Message(content="Draft rejected.").send()
        else:
            await cl.Message(content="Review timed out. Draft held for later review.").send()


async def _extract_file_text(elements) -> str:
    """Extract text from uploaded PDF/DOCX files for inline review."""
    from ingest.parsers.pdf_parser import parse_pdf
    from ingest.parsers.docx_parser import parse_docx

    parts = []
    for element in elements:
        if not hasattr(element, "path") or not element.path:
            continue
        filepath = Path(element.path)
        ext = filepath.suffix.lower()
        try:
            if ext == ".pdf":
                chunks = parse_pdf(
                    filepath=filepath,
                    client_id="internal", jurisdiction="", doc_type="contract",
                    sensitivity="internal",
                )
            elif ext == ".docx":
                chunks = parse_docx(
                    filepath=filepath,
                    client_id="internal", jurisdiction="", doc_type="contract",
                    sensitivity="internal",
                )
            else:
                continue
            parts.extend(c.text for c in chunks)
        except Exception as e:
            parts.append(f"[Error extracting {element.name}: {e}]")
    return "\n\n".join(parts)


async def _handle_file_upload(message: cl.Message):
    """Process uploaded files — send to ingest endpoint (no message text = ingest)."""
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


async def _handle_review(report: dict):
    """Show review prompt for non-contract flagged responses."""
    risk_flags = report.get("risk_flags", [])

    flag_text = ""
    if risk_flags:
        flag_lines = [f"- {f.get('reason', '?')}" for f in risk_flags]
        flag_text = "\n**Risk flags:**\n" + "\n".join(flag_lines)

    actions = [
        cl.Action(
            name="approve",
            label="Approve",
            description="Approve this response",
            payload={"action": "approve"},
        ),
        cl.Action(
            name="reject",
            label="Reject",
            description="Reject this response",
            payload={"action": "reject"},
        ),
    ]

    review_msg = await cl.AskActionMessage(
        content=f"This response requires attorney review.{flag_text}",
        actions=actions,
        timeout=86400,
    ).send()

    if review_msg and review_msg.get("payload", {}).get("action") == "approve":
        await cl.Message(content="Response approved.").send()
    elif review_msg:
        await cl.Message(content="Response rejected.").send()
    else:
        await cl.Message(content="Review timed out.").send()
