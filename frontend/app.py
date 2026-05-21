# frontend/app.py
"""Chainlit frontend — chat interface for the legal plugin."""

import datetime
import logging
import sys
import tempfile
import traceback
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# Ensure project root is on path when Chainlit runs this file directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chainlit as cl
from fpdf import FPDF

from frontend.api_client import submit_query, ingest_file, health_check, resume_query
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
# Review loop — uses AskActionMessage so buttons stay live across iterations
# ---------------------------------------------------------------------------

async def _ask_review_action(review_iter: int) -> str | None:
    """Block until the attorney clicks an action button. Returns the action name or None on timeout."""
    actions = [
        cl.Action(name="approve", label="Approve Draft", payload={"action": "approve"}),
        cl.Action(name="request_changes", label="Request Changes", payload={"action": "request_changes"}),
        cl.Action(name="reject", label="Reject", payload={"action": "reject"}),
    ]
    res = await cl.AskActionMessage(
        content=f"Review iteration {review_iter}: choose an action.",
        actions=actions,
        timeout=3600,
    ).send()
    if res is None:
        return None
    return res.get("name") or (res.get("payload") or {}).get("action")


async def _review_loop(session_id: str):
    """Drive the review loop. Renders new drafts and asks for verdicts until terminal."""
    review_iter = 0
    while True:
        chosen = await _ask_review_action(review_iter)
        if chosen is None:
            await cl.Message(content="Review timed out — no action taken.").send()
            return

        if chosen == "approve":
            await cl.Message(content="Finalizing report...").send()
            try:
                result = await resume_query(session_id=session_id, approved=True)
            except Exception as e:
                logger.error("[review_loop] approve resume failed: %r", e)
                traceback.print_exc()
                await cl.Message(content=f"Resume failed: {type(e).__name__}: {e}").send()
                return
        elif chosen == "request_changes":
            notes_msg = await cl.AskUserMessage(
                content="Describe the changes you'd like made to this draft.",
                timeout=600,
            ).send()
            if not notes_msg:
                await cl.Message(content="No notes provided — cancelling.").send()
                return
            notes_text = (notes_msg.get("output") or notes_msg.get("content") or "").strip()
            if not notes_text:
                await cl.Message(content="Empty notes — cancelling.").send()
                return
            await cl.Message(content="Regenerating draft with your changes... (this may take a minute)").send()
            try:
                result = await resume_query(session_id=session_id, approved=False, notes=notes_text)
            except Exception as e:
                logger.error("[review_loop] request_changes resume failed: %r", e)
                traceback.print_exc()
                await cl.Message(content=f"Resume failed: {type(e).__name__}: {e}").send()
                return
        elif chosen == "reject":
            await cl.Message(content="Closing without changes...").send()
            try:
                result = await resume_query(session_id=session_id, approved=False)
            except Exception as e:
                logger.error("[review_loop] reject resume failed: %r", e)
                traceback.print_exc()
                await cl.Message(content=f"Resume failed: {type(e).__name__}: {e}").send()
                return
        else:
            await cl.Message(content=f"Unknown action: {chosen}").send()
            return

        if result.get("status") == "error":
            errs = result.get("errors") or ["unknown error"]
            await cl.Message(content=f"Resume failed: {errs[0]}").send()
            return

        data = result.get("data", {})
        if data.get("awaiting_review"):
            payload = data.get("interrupt_payload", {})
            response_text = payload.get("llm_response", "")
            task_type = payload.get("task_type", "unknown")
            risk_level = payload.get("risk_level", "unknown")
            review_iter = payload.get("review_iterations", review_iter + 1)
            placeholder = cl.Message(content="")
            await placeholder.send()
            await _show_in_side_panel(placeholder, response_text, task_type, risk_level, [])
            continue

        report = data.get("report", {})
        response_text = report.get("response", "(no response)")
        notes_unincorp = report.get("notes_unincorporated", "")

        chat_content = "**Final report — ready for download.**"
        if notes_unincorp:
            chat_content += (
                f"\n\n---\n**Notes not incorporated** "
                f"(iteration cap reached):\n{notes_unincorp}"
            )

        elements = []
        try:
            pdf_path = _generate_pdf(response_text)
            ts = datetime.datetime.now().strftime("%H%M%S")
            elements.append(cl.File(name=f"contract_{ts}.pdf", path=pdf_path, display="inline"))
        except Exception as e:
            logger.error("[review_loop] PDF generation failed: %r", e)
            chat_content += f"\n\n_Note: PDF generation failed — {e}. Full text below._\n\n{response_text}"

        await cl.Message(content=chat_content, elements=elements).send()
        return


# ---------------------------------------------------------------------------
# Chat handlers
# ---------------------------------------------------------------------------

@cl.on_chat_start
async def on_chat_start():
    cl.user_session.set("session_id", str(uuid.uuid4()))
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

    session_id = cl.user_session.get("session_id", "")
    thinking_msg = cl.Message(content="Processing your request...")
    await thinking_msg.send()

    try:
        result = await submit_query(
            request=message.content,
            user_id=user_id,
            uploaded_text=uploaded_text,
            session_id=session_id,
        )

        data = result.get("data", {})
        awaiting_review = data.get("awaiting_review", False)
        if awaiting_review:
            payload = data.get("interrupt_payload", {})
            response_text = payload.get("llm_response", "No response generated.")
            task_type = payload.get("task_type", "unknown")
            risk_level = payload.get("risk_level", "unknown")
            sources = []
        else:
            report = data.get("report", {})
            response_text = report.get("response", "No response generated.")
            task_type = data.get("task_type", "unknown")
            risk_level = data.get("risk_level", "unknown")
            sources = report.get("sources", [])

        # Contract skills — always show in side panel
        if task_type in ("contract_generation", "drafting", "contract_review"):
            await _show_in_side_panel(
                thinking_msg, response_text, task_type, risk_level, sources,
            )
            if awaiting_review:
                await _review_loop(session_id)
            return

        # Short response — show inline
        content_parts = [response_text]
        content_parts.append(f"\n\n---\n**Task:** {task_type} | **Risk:** {risk_level}")
        if sources:
            source_lines = [f"- {s.get('doc_title', '?')} (`{s.get('doc_id', '?')}`)" for s in sources]
            content_parts.append("\n**Sources:**\n" + "\n".join(source_lines))

        thinking_msg.content = "\n".join(content_parts)
        await thinking_msg.update()

        if awaiting_review:
            await _review_loop(session_id)

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
