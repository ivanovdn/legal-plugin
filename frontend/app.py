# frontend/app.py
"""Chainlit frontend — chat interface for the legal plugin."""

import datetime
import sys
import tempfile
from pathlib import Path

# Ensure project root is on path when Chainlit runs this file directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chainlit as cl
from fpdf import FPDF

from frontend.api_client import submit_query, ingest_file, health_check
from ingest.parsers.pdf_parser import parse_pdf
from ingest.parsers.docx_parser import parse_docx


# ---------------------------------------------------------------------------
# PDF generation
# ---------------------------------------------------------------------------

def _generate_pdf(text: str) -> str:
    """Generate a PDF from contract text. Returns path to temp file."""
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_left_margin(15)
    pdf.set_right_margin(15)

    def _clean(s: str) -> str:
        s = s.replace("**", "")
        return s.encode("latin-1", errors="replace").decode("latin-1")

    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("DRAFT") or stripped.startswith("SERVICE AGREEMENT") or stripped.startswith("DEVIATION"):
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


# ---------------------------------------------------------------------------
# File text extraction
# ---------------------------------------------------------------------------

async def _extract_file_text(elements) -> str:
    """Extract text from uploaded PDF/DOCX/TXT files for inline review."""
    parts = []
    for element in elements:
        if not hasattr(element, "path") or not element.path:
            continue
        filepath = Path(element.path)
        original_name = getattr(element, "name", "") or filepath.name
        ext = Path(original_name).suffix.lower() or filepath.suffix.lower()
        try:
            if ext == ".pdf":
                chunks = parse_pdf(
                    filepath=filepath, client_id="internal",
                    jurisdiction="", doc_type="contract", sensitivity="internal",
                )
                parts.extend(c.text for c in chunks)
            elif ext == ".docx":
                chunks = parse_docx(
                    filepath=filepath, client_id="internal",
                    jurisdiction="", doc_type="contract", sensitivity="internal",
                )
                parts.extend(c.text for c in chunks)
            elif ext == ".txt":
                parts.append(filepath.read_text(encoding="utf-8"))
            else:
                parts.append(f"[Unsupported file type: {ext}]")
        except Exception as e:
            parts.append(f"[Error extracting {element.name}: {e}]")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Action callbacks (buttons)
# ---------------------------------------------------------------------------

@cl.action_callback("approve")
async def on_approve(action: cl.Action):
    full_text = cl.user_session.get("pending_review_text", "")
    if full_text:
        pdf_path = _generate_pdf(full_text)
        pdf_element = cl.File(name="approved_contract.pdf", path=pdf_path, display="inline")
        await cl.Message(content="Draft approved. Download the PDF below.", elements=[pdf_element]).send()
    else:
        await cl.Message(content="Draft approved.").send()
    cl.user_session.set("pending_review_text", "")


@cl.action_callback("request_changes")
async def on_request_changes(action: cl.Action):
    await cl.Message(content="Please describe the changes you'd like made to this draft.").send()


@cl.action_callback("reject")
async def on_reject(action: cl.Action):
    await cl.Message(content="Draft rejected.").send()
    cl.user_session.set("pending_review_text", "")


# ---------------------------------------------------------------------------
# Chat handlers
# ---------------------------------------------------------------------------

@cl.on_chat_start
async def on_chat_start():
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
        await cl.Message(content=f"Error: Cannot reach backend API at port 8000.\n\n{e}").send()


@cl.on_message
async def on_message(message: cl.Message):
    user = cl.user_session.get("user")
    user_id = user.identifier if user else "anonymous"

    # File with message text = review/analyze; file without text = ingest
    uploaded_text = ""
    if message.elements:
        has_text = message.content and message.content.strip()
        if not has_text:
            await _handle_file_upload(message)
            return
        try:
            uploaded_text = await _extract_file_text(message.elements)
        except Exception as e:
            await cl.Message(content=f"Could not extract text: {e}").send()
            return
        if not uploaded_text:
            await cl.Message(content="Could not extract text from the file. Supported: PDF, DOCX, TXT.").send()
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

        # Contract skills — always show in side panel
        if task_type in ("contract_generation", "drafting", "contract_review"):
            await _show_in_side_panel(
                thinking_msg, response_text, task_type, risk_level, sources, awaiting_review,
            )
            return

        # Short response — show inline
        content_parts = [response_text]
        content_parts.append(f"\n\n---\n**Task:** {task_type} | **Risk:** {risk_level}")
        if sources:
            source_lines = [f"- {s.get('doc_title', '?')} (`{s.get('doc_id', '?')}`)" for s in sources]
            content_parts.append("\n**Sources:**\n" + "\n".join(source_lines))

        thinking_msg.content = "\n".join(content_parts)
        await thinking_msg.update()

    except Exception as e:
        thinking_msg.content = f"Error: {e}"
        await thinking_msg.update()


# ---------------------------------------------------------------------------
# Side panel display
# ---------------------------------------------------------------------------

async def _show_in_side_panel(
    thinking_msg: cl.Message,
    response_text: str,
    task_type: str,
    risk_level: str,
    sources: list,
    awaiting_review: bool,
):
    """Show response in side panel with unique clickable attachment."""
    ts = datetime.datetime.now().strftime("%H%M%S")

    # Split off deviation report if present
    summary = ""
    full_text = response_text
    if "DEVIATION REPORT" in response_text:
        parts = response_text.split("DEVIATION REPORT", 1)
        full_text = parts[0].strip()
        summary = "DEVIATION REPORT" + parts[1]

    # Chat message with summary
    if task_type == "contract_review":
        label = "Contract review complete"
        filename = f"review_{ts}.md"
    elif task_type in ("contract_generation", "drafting"):
        label = "Contract draft generated"
        filename = f"draft_{ts}.md"
    else:
        label = "Analysis complete"
        filename = f"analysis_{ts}.md"

    chat_content = f"**{label}** — click **{filename}** to view in side panel.\n\n"
    chat_content += f"**Task:** {task_type} | **Risk:** {risk_level}\n"
    if summary:
        chat_content += f"\n```\n{summary}\n```"

    # Clickable text element — opens in side panel
    text_element = cl.Text(name=filename, content=full_text, display="side")

    thinking_msg.content = chat_content
    thinking_msg.elements = [text_element]
    await thinking_msg.update()

    # Human review with action buttons
    if awaiting_review:
        cl.user_session.set("pending_review_text", full_text)

        actions = [
            cl.Action(name="approve", label="Approve Draft", payload={"action": "approve"}),
            cl.Action(name="request_changes", label="Request Changes", payload={"action": "request_changes"}),
            cl.Action(name="reject", label="Reject", payload={"action": "reject"}),
        ]

        await cl.Message(
            content="This requires your review. Please review in the side panel, then choose an action.",
            actions=actions,
        ).send()


# ---------------------------------------------------------------------------
# File ingestion (no message text = ingest to Qdrant)
# ---------------------------------------------------------------------------

async def _handle_file_upload(message: cl.Message):
    for element in message.elements:
        if not hasattr(element, "path") or not element.path:
            continue
        filename = element.name or "unknown"
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in ("pdf", "docx"):
            await cl.Message(content=f"Unsupported file type: .{ext}. Upload PDF or DOCX.").send()
            continue

        status_msg = cl.Message(content=f"Ingesting **{filename}**...")
        await status_msg.send()
        try:
            result = await ingest_file(file_path=element.path, filename=filename)
            chunks = result.get("data", {}).get("chunks", 0)
            status_msg.content = f"Ingested **{filename}**: {chunks} chunks added."
            await status_msg.update()
        except Exception as e:
            status_msg.content = f"Failed to ingest **{filename}**: {e}"
            await status_msg.update()
