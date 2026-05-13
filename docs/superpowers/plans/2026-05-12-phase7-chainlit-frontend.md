# Phase 7 — Chainlit Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Chainlit frontend that attorneys can use to chat with the legal assistant, upload documents, and review flagged outputs — all connecting to the FastAPI backend via HTTP.

**Architecture:** Chainlit app (`frontend/app.py`) is a thin client. It sends requests to FastAPI (`http://localhost:8000`), displays responses, and handles human-in-the-loop via `AskActionMessage`. File uploads go through Chainlit's built-in file handling, then POST to `/api/ingest`. No business logic in the frontend — everything goes through the API.

**Tech Stack:** Chainlit 2.11, httpx (async HTTP client to backend), FastAPI (backend, already built)

---

## File Structure

```
legal-plugin/
|-- frontend/
|   |-- app.py                # Chainlit entry point — chat handlers
|   +-- api_client.py         # HTTP client to FastAPI backend
|-- .chainlit/
|   +-- config.toml           # Chainlit configuration
+-- chainlit.md               # Welcome message (gitignored but needed for startup)
```

**Scoping note:** The spec lists admin/dashboard features (document browser, plugin manager, session viewer). These are complex UI components that go beyond Chainlit's core chat pattern. For Phase 7, we focus on:
- Chat interface (send query, see response)
- File upload (ingest documents)
- Human-in-the-loop (approve/reject flagged responses)
- Source citations in responses

Admin panels will be added incrementally in a later phase or when migrating to a custom frontend.

---

### Task 1: Create API client module

HTTP client that talks to the FastAPI backend. Used by all Chainlit handlers.

**Files:**
- Create: `frontend/api_client.py`

- [ ] **Step 1: Create the API client**

```python
# frontend/api_client.py
"""HTTP client for communicating with the FastAPI backend."""

import httpx

from config import get_settings


def _base_url() -> str:
    settings = get_settings()
    return f"http://localhost:{settings.api_port}"


async def submit_query(
    request: str,
    user_id: str,
    task_type: str = "",
    filters: dict | None = None,
    session_id: str = "",
) -> dict:
    """POST /api/query — submit a legal request."""
    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.post(
            f"{_base_url()}/api/query",
            json={
                "request": request,
                "task_type": task_type,
                "session_id": session_id,
                "filters": filters or {},
            },
            headers={"X-User-ID": user_id},
        )
        response.raise_for_status()
        return response.json()


async def ingest_file(
    file_path: str,
    filename: str,
    client_id: str = "internal",
    jurisdiction: str = "",
    doc_type: str = "contract",
    sensitivity: str = "internal",
    collection: str = "legal_docs",
) -> dict:
    """POST /api/ingest — upload and ingest a document."""
    async with httpx.AsyncClient(timeout=300.0) as client:
        with open(file_path, "rb") as f:
            response = await client.post(
                f"{_base_url()}/api/ingest",
                files={"file": (filename, f)},
                data={
                    "client_id": client_id,
                    "jurisdiction": jurisdiction,
                    "doc_type": doc_type,
                    "sensitivity": sensitivity,
                    "collection": collection,
                },
            )
        response.raise_for_status()
        return response.json()


async def health_check() -> dict:
    """GET /health — check backend status."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{_base_url()}/health")
        response.raise_for_status()
        return response.json()
```

- [ ] **Step 2: Commit**

```bash
mkdir -p frontend
git add frontend/api_client.py
git commit -m "feat: add API client for Chainlit-to-FastAPI communication"
```

---

### Task 2: Create Chainlit app with chat handler

The main Chainlit app — handles chat start, messages, and file uploads.

**Files:**
- Create: `frontend/app.py`

- [ ] **Step 1: Create the Chainlit app**

```python
# frontend/app.py
"""Chainlit frontend — chat interface for the legal plugin."""

import chainlit as cl

from frontend.api_client import submit_query, ingest_file, health_check


@cl.on_chat_start
async def on_chat_start():
    """Initialize chat session."""
    # Check backend health
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

        # Main response
        content_parts.append(response_text)

        # Metadata
        content_parts.append(f"\n\n---\n**Task:** {task_type} | **Risk:** {risk_level}")

        # Sources
        if sources:
            source_lines = [f"- {s.get('doc_title', '?')} (`{s.get('doc_id', '?')}`)" for s in sources]
            content_parts.append("\n**Sources:**\n" + "\n".join(source_lines))

        # Update the thinking message with the real response
        thinking_msg.content = "\n".join(content_parts)
        await thinking_msg.update()

        # Human-in-the-loop for flagged responses
        if awaiting_review:
            await _handle_review(data, thinking_msg)

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


async def _handle_review(data: dict, original_msg: cl.Message):
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
        await cl.Message(content="Response approved. Delivered to requester.").send()
    elif review_msg:
        await cl.Message(content="Response rejected. Please provide your revised response or feedback.").send()
    else:
        await cl.Message(content="Review timed out. Response held for later review.").send()
```

- [ ] **Step 2: Commit**

```bash
git add frontend/app.py
git commit -m "feat: add Chainlit app with chat, file upload, and human review"
```

---

### Task 3: Chainlit configuration

Create config files needed by Chainlit to run.

**Files:**
- Create: `.chainlit/config.toml`

- [ ] **Step 1: Create Chainlit config**

```toml
# .chainlit/config.toml

[project]
enable_telemetry = false

[features]
prompt_playground = false

[UI]
name = "Legal Assistant"
description = "AI-powered legal assistant for internal legal teams"
default_collapse_content = true
hide_cot = false

[UI.theme]
layout = "wide"
```

- [ ] **Step 2: Create welcome markdown**

Create `chainlit.md` (Chainlit requires this file to exist):

```markdown
# Legal Assistant

AI-powered legal assistant for your internal legal team.

## What you can do

- **Ask legal questions** — research case law, regulations, and policies
- **Generate contracts** — create new contracts from historical patterns
- **Review contracts** — analyze clauses and flag risks
- **Check compliance** — verify documents against regulations
- **Upload documents** — ingest PDF/DOCX files into the knowledge base
```

- [ ] **Step 3: Commit**

```bash
git add .chainlit/config.toml chainlit.md
git commit -m "feat: add Chainlit configuration and welcome page"
```

---

### Task 4: Startup script and verification

Create a script to start both backend and frontend, then verify the UI works.

**Files:**
- Create: `scripts/start.sh`

- [ ] **Step 1: Create startup script**

```bash
#!/bin/bash
# scripts/start.sh — start both FastAPI backend and Chainlit frontend
# Run from project root

set -e

echo "=== Starting Legal Plugin ==="

# Check Docker services
echo "Checking Docker services..."
docker compose ps --format "{{.Name}}: {{.Status}}" | head -7

# Start FastAPI backend
echo ""
echo "Starting FastAPI backend on port 8000..."
source .venv/bin/activate
uvicorn api.main:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!
echo "Backend PID: $BACKEND_PID"

# Wait for backend to be ready
sleep 3
curl -s http://localhost:8000/health > /dev/null 2>&1 || { echo "Backend failed to start"; kill $BACKEND_PID; exit 1; }
echo "Backend ready."

# Start Chainlit frontend
echo ""
echo "Starting Chainlit frontend on port 8080..."
chainlit run frontend/app.py --port 8080 --host 0.0.0.0 &
FRONTEND_PID=$!
echo "Frontend PID: $FRONTEND_PID"

echo ""
echo "=== Legal Plugin Running ==="
echo "  Backend:  http://localhost:8000 (API docs: http://localhost:8000/docs)"
echo "  Frontend: http://localhost:8080"
echo ""
echo "Press Ctrl+C to stop both services."

# Wait for either to exit
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
```

- [ ] **Step 2: Make executable**

```bash
chmod +x scripts/start.sh
```

- [ ] **Step 3: Test manually**

Start both services:
```bash
bash scripts/start.sh
```

Then open `http://localhost:8080` in your browser. Verify:
- Welcome message appears
- You can type a legal question and get a response
- Response shows task type, risk level, and sources
- Contract generation requests show the review prompt

- [ ] **Step 4: Commit**

```bash
git add scripts/start.sh
git commit -m "feat: add startup script for backend + frontend"
```

---

## Phase 7 Exit Criteria

- [ ] Chainlit UI loads at `http://localhost:8080`
- [ ] Typing a legal question returns a response from the backend
- [ ] Response shows task type, risk level, and source citations
- [ ] Contract generation request triggers review prompt (approve/reject)
- [ ] File upload ingests document and shows chunk count
- [ ] Backend API docs accessible at `http://localhost:8000/docs`
