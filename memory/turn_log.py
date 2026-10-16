"""turn_log — one durable row per accepted request (spec 2026-10-09).

Who asked, when, on which document; did it work; what grounded it; where the
time went; what sent it. Phoenix keeps the full prompt and the span tree for
drill-down; this table is the system of record.

The write is QUIET, like interaction_event: it runs after the response has
gone out (api/turn_record.py), so there is nobody left to tell, and telemetry
must never break a turn. A failed write puts the whole row in the log instead.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from psycopg.types.json import Jsonb

from memory.db import get_pool
from memory.feedback_store import _filters
from memory.fingerprint import fingerprint, storable

logger = logging.getLogger(__name__)

# INSERT order. build_turn_row returns exactly these keys — tests pin that, so
# the two cannot drift apart.
COLUMNS: tuple[str, ...] = (
    "timestamp", "turn_id", "trace_id", "kind", "session_id", "document_id",
    "attorney_id", "user_name",
    "task_type", "request", "doc_chars", "doc_sha256",
    "status", "outcome", "degradations", "error", "awaiting_review",
    "contract_type", "contract_type_defaulted", "msa_title", "preferences_chars",
    "doc_kept_pct", "context",
    "duration_ms", "llm_calls", "llm_load_ms", "llm_prompt_ms", "llm_gen_ms",
    "tokens_in", "tokens_out", "llm_detail",
    "response_chars", "edits_proposed", "preferences_proposed", "compaction",
    "office_platform", "office_version", "pane_build", "backend_build", "user_agent",
    "settings", "model_digest", "ollama_version",
)
_JSON_COLUMNS = frozenset({"degradations", "context", "llm_detail", "compaction", "settings"})

_REQUEST_CAP = 2000
_ERROR_CAP = 2000


def _total(calls: list[dict], key: str) -> int:
    return sum(int(call.get(key) or 0) for call in calls)


def build_turn_row(
    *,
    kind: str,
    turn_id: str,
    trace_id: str,
    arrived_at: str,
    duration_ms: int,
    attorney_id: str,
    user_name: str,
    session_id: str,
    document_id: str,
    task_type: str,
    request: str,
    doc_text: str,
    status: str,
    outcome: str,
    degradations: list[str],
    api_error: str,
    awaiting_review: bool,
    facts: dict,
    result: dict | None,
    compaction: dict | None,
    client: dict,
    backend_build: str,
    runtime: dict,
) -> dict[str, Any]:
    """One turn_log row. Pure: no I/O, no settings, no clock.

    `result` is the graph's final state, or None when nothing came back (the
    graph raised, a resume found no session, a compaction). Where it exists it
    wins over the request for document_id (intake resolves the preamble-hash
    fallback) and task_type (what actually ran). `facts` is
    observability.spans.turn_facts(); `client` holds ClientInfo's four fields;
    `runtime` is what api/runtime_facts.py read when the request arrived —
    `settings`, `model_digest`, `ollama_version` (any may be absent).
    """
    state = result or {}
    calls = [dict(call) for call in facts.get("llm_detail") or []]
    doc_chars, doc_sha256 = fingerprint(doc_text)
    truncated = state.get("context_truncated")
    return {
        "timestamp": arrived_at,
        "turn_id": turn_id,
        "trace_id": trace_id,
        "kind": kind,
        "session_id": session_id,
        "document_id": state.get("document_id") or document_id,
        "attorney_id": attorney_id,
        "user_name": user_name,
        "task_type": state.get("task_type") or task_type,
        "request": storable(request)[:_REQUEST_CAP],
        "doc_chars": doc_chars,
        "doc_sha256": doc_sha256,
        "status": status,
        "outcome": outcome,
        "degradations": list(degradations),
        # The first failure the turn recorded says what broke; the API's own
        # error text is the fallback for exits that never called mark_failed
        # (a resume whose session had expired).
        "error": storable(facts.get("error") or api_error)[:_ERROR_CAP],
        "awaiting_review": bool(awaiting_review),
        "contract_type": facts.get("contract_type", ""),
        "contract_type_defaulted": bool(facts.get("contract_type_defaulted", False)),
        "msa_title": facts.get("msa_title") or "",
        "preferences_chars": int(facts.get("preferences_chars", 0)),
        "doc_kept_pct": truncated.get("kept_pct") if isinstance(truncated, dict) else None,
        "context": state.get("context_breakdown") or None,
        "duration_ms": max(0, int(duration_ms)),
        "llm_calls": len(calls),
        "llm_load_ms": _total(calls, "load_ms"),
        "llm_prompt_ms": _total(calls, "prompt_ms"),
        "llm_gen_ms": _total(calls, "gen_ms"),
        "tokens_in": _total(calls, "tokens_in"),
        "tokens_out": _total(calls, "tokens_out"),
        "llm_detail": calls,
        "response_chars": len(state.get("llm_response") or ""),
        "edits_proposed": len(state.get("proposed_edits") or []),
        "preferences_proposed": len(state.get("proposed_preferences") or []),
        "compaction": compaction,
        "office_platform": client.get("office_platform", ""),
        "office_version": client.get("office_version", ""),
        "pane_build": client.get("pane_build", ""),
        "backend_build": backend_build,
        "user_agent": client.get("user_agent", ""),
        "settings": runtime.get("settings") or {},
        "model_digest": runtime.get("model_digest", ""),
        "ollama_version": runtime.get("ollama_version", ""),
    }


def write_turn(row: dict[str, Any]) -> None:
    """INSERT one row. RAISES — the quiet policy lives in write_turn_quietly.

    Deliberately not @traced: it runs after the response, outside the request's
    span, where a span would start an orphan trace of its own on every turn."""
    values = [
        Jsonb(row[column]) if column in _JSON_COLUMNS and row[column] is not None else row[column]
        for column in COLUMNS
    ]
    with get_pool().connection() as conn:
        conn.execute(
            f"INSERT INTO turn_log ({', '.join(COLUMNS)}) "
            f"VALUES ({', '.join(['%s'] * len(COLUMNS))})",
            values,
        )


def write_turn_quietly(row: dict[str, Any]) -> None:
    """write_turn that never raises. A failure logs the whole row at ERROR —
    journald keeps it, so the turn stays recoverable — and nothing else
    happens: no reason code (class 4, telemetry's own failure), no banner."""
    try:
        write_turn(row)
    except Exception as e:
        logger.error(
            "TURN LOG WRITE FAILED (%s) — row recorded here instead: %s",
            e, json.dumps(row, default=str, ensure_ascii=False),
        )


def recent_turns(
    *, since: str = "", attorney_id: str = "", document_id: str = "", limit: int = 200,
) -> list[dict[str, Any]]:
    """The `limit` most recently written turns within the filter, in the order
    their requests arrived (oldest first), each with what the attorney then did
    with it: Apply / failure / Discard events and flags written against the same
    turn_id. Arrival order, not write order: the row is written after the
    response, so a slow turn lands after a quick one that arrived later."""
    where, params = _filters(since, document_id, attorney_id)
    sql = f"""
        SELECT * FROM (
            SELECT t.*,
                (SELECT count(*) FROM interaction_event e WHERE e.turn_id = t.turn_id
                   AND e.action IN ('edit_applied', 'redline_applied')) AS applied,
                (SELECT count(*) FROM interaction_event e WHERE e.turn_id = t.turn_id
                   AND e.action IN ('edit_failed', 'redline_failed')) AS failed,
                (SELECT count(*) FROM interaction_event e WHERE e.turn_id = t.turn_id
                   AND e.action = 'edit_discarded') AS discarded,
                (SELECT count(*) FROM feedback f WHERE f.turn_id = t.turn_id) AS flags,
                (SELECT max(e.detail::bigint) FROM interaction_event e
                  WHERE e.turn_id = t.turn_id AND e.action = 'turn_waited'
                    AND e.detail ~ '^[0-9]{{1,15}}$') AS waited_ms
            FROM turn_log t
            {"WHERE " + where if where else ""}
            ORDER BY t.id DESC
            LIMIT %s
        ) newest
        ORDER BY timestamp, id
    """
    with get_pool().connection() as conn:
        cur = conn.execute(sql, [*params, limit])
        names = [column.name for column in cur.description]
        return [dict(zip(names, values)) for values in cur.fetchall()]


def turn_summaries(
    *, since: str = "", attorney_id: str = "", document_id: str = "",
) -> list[dict[str, Any]]:
    """Every question in scope, oldest first — the counts and server times that
    feedback_report --activity sets beside the pane's own events. kind='query'
    only: a resume continues a review already counted, a compaction asks nothing."""
    where, params = _filters(since, document_id, attorney_id)
    sql = f"""
        SELECT timestamp, attorney_id, user_name, task_type, duration_ms
        FROM turn_log WHERE kind = 'query' {"AND " + where if where else ""}
        ORDER BY timestamp, id
    """
    with get_pool().connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    keys = ("timestamp", "attorney_id", "user_name", "task_type", "duration_ms")
    return [dict(zip(keys, r)) for r in rows]
