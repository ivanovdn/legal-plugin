# api/turn_record.py
"""The one exit path every request handler goes through (spec 2026-10-09, D3).

finish_turn stamps app.outcome AND builds the turn_log row from that same
value, so the row and the trace cannot disagree about how a turn went. The row
is written after the response (BackgroundTasks) — the attorney waits for
nothing — except on exits that raise, which skip background tasks and so
write it now.
"""
from __future__ import annotations

import contextvars
import logging
import time
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import BackgroundTasks

from api.client_info import ClientInfo
from api.runtime_facts import ollama_facts, settings_snapshot
from config import get_settings
from memory.document_store import save_version_quietly
from memory.turn_log import build_turn_row, write_turn_quietly
from observability.log_context import set_log_turn
from observability.spans import (
    current_trace_id, degradations, set_outcome, set_trace_attributes, turn_facts,
)

logger = logging.getLogger(__name__)


def start_turn(
    *,
    kind: str,
    turn_id: str,
    client: ClientInfo,
    attorney_id: str = "",
    user_name: str = "",
    session_id: str = "",
    document_id: str = "",
    task_type: str = "",
    request: str = "",
    doc_text: str = "",
) -> dict:
    """What is known when the request arrives. Call first thing inside the
    handler's root span; it also stamps the client facts on the trace."""
    set_log_turn(turn_id)
    settings = get_settings()
    backend_build = settings.build_sha
    set_trace_attributes(metadata={
        "turn_id": turn_id,
        "office.platform": client.office_platform,
        "office.version": client.office_version,
        "pane.build": client.pane_build,
        "backend.build": backend_build,
    })
    return {
        "kind": kind, "turn_id": turn_id, "trace_id": current_trace_id(),
        "arrived_at": datetime.now(timezone.utc).isoformat(),
        "started": time.monotonic(),
        "client": asdict(client), "backend_build": backend_build,
        "attorney_id": attorney_id, "user_name": user_name,
        "session_id": session_id, "document_id": document_id,
        "task_type": task_type, "request": request, "doc_text": doc_text,
        # What this turn runs with beyond the code, read as it arrives. Cached
        # for a minute (api/runtime_facts.py), so a model pulled on the server
        # shows up on turns up to a minute later.
        "runtime": {"settings": settings_snapshot(settings),
                    **ollama_facts(settings.ollama_base_url, settings.llm_model)},
    }


def _turn_document(turn: dict) -> dict | None:
    """SP4: the text a pane turn was sent, keyed by the request's document UUID.
    Only /api/query submits qualify: Chainlit sends no UUID, a resume (Chainlit
    only) carries the checkpoint's resolved id rather than one the client sent,
    and compaction carries no text."""
    if not (turn["kind"] == "query" and turn["document_id"] and turn["doc_text"]
            and get_settings().document_capture_enabled):
        return None
    return {"document_id": turn["document_id"], "text": turn["doc_text"],
            "attorney_id": turn["attorney_id"], "source": "turn"}


def _record_quietly(row: dict, document: dict | None) -> None:
    """Everything a turn leaves behind after the response: its turn_log row and,
    for a pane turn, its document's text. Neither can raise."""
    write_turn_quietly(row)
    if document is not None:
        save_version_quietly(**document)


def finish_turn(
    turn: dict,
    *,
    outcome: str,
    status: str,
    api_error: str = "",
    awaiting_review: bool = False,
    result: dict | None = None,
    compaction: dict | None = None,
    background_tasks: BackgroundTasks | None = None,
) -> None:
    """Stamp app.outcome and record the turn. Never raises.

    Must run inside the root span: degradations() and turn_facts() are reset
    when it closes. With background_tasks the write happens after the
    response; without (an exit about to raise) it happens now.
    """
    set_outcome(outcome)
    try:
        row = build_turn_row(
            kind=turn["kind"], turn_id=turn["turn_id"], trace_id=turn["trace_id"],
            arrived_at=turn["arrived_at"],
            duration_ms=round((time.monotonic() - turn["started"]) * 1000),
            attorney_id=turn["attorney_id"], user_name=turn["user_name"],
            session_id=turn["session_id"], document_id=turn["document_id"],
            task_type=turn["task_type"], request=turn["request"], doc_text=turn["doc_text"],
            status=status, outcome=outcome, degradations=degradations(),
            api_error=api_error, awaiting_review=awaiting_review,
            facts=turn_facts(), result=result, compaction=compaction,
            client=turn["client"], backend_build=turn["backend_build"],
            runtime=turn["runtime"],
        )
        document = _turn_document(turn)
    except Exception:
        logger.exception(
            "[turn_log] could not build the row for turn %s — the response is unaffected",
            turn.get("turn_id"),
        )
        return
    if background_tasks is not None:
        # Background tasks run in the request task's context, not this
        # thread's: carry this one so a failed write still logs [turn=<id>].
        background_tasks.add_task(contextvars.copy_context().run, _record_quietly, row, document)
    else:
        _record_quietly(row, document)
