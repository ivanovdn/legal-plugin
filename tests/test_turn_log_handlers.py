"""Every accepted request leaves exactly one turn_log row, and that row agrees
with the trace (spec 2026-10-09, D3-D4).

Handlers are driven the way tests/test_turn_outcome.py drives them — as plain
functions with injected failures — through tests.conftest.call_handler, which
supplies what FastAPI would inject and runs the background tasks afterwards.
"""
from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import RedisError

import api.routes.query as q
import api.turn_record as turn_record
import memory.turn_log as turn_log
from api.client_info import ClientInfo
from api.main import app
from api.models import QueryRequest, ResumeRequest
from memory.db import get_pool
from tests.conftest import call_handler, spans_by_name


def _rows() -> list[dict]:
    with get_pool().connection() as conn:
        cur = conn.execute("SELECT * FROM turn_log ORDER BY id")
        names = [column.name for column in cur.description]
        return [dict(zip(names, values)) for values in cur.fetchall()]


class _State:
    def __init__(self, values):
        self.values = values


class _Graph:
    """One fake for every scenario: what invoke and get_state do is passed in."""
    def __init__(self, invoke=None, get_state=None):
        self._invoke, self._get_state = invoke, get_state

    def invoke(self, state_or_command, config=None):
        return self._invoke(state_or_command)

    def get_state(self, config):
        return self._get_state()


def _clean(state):
    return {**state, "llm_response": "done", "report": {}}


def _raises(exc):
    def _fail(*_args):
        raise exc
    return _fail


_CHECKPOINTED = {
    "user_id": "atty-1", "user_name": "A. Tester", "task_type": "contract_review",
    "document_id": "doc-9", "uploaded_docs": [{"text": "MASTER SERVICES AGREEMENT"}],
    "awaiting_review": True,
}
_RESUMED = {"task_type": "contract_review", "report": {}, "risk_level": "low",
            "awaiting_review": False}

SUBMIT_EXITS = {
    "ok": dict(graph=_Graph(invoke=_clean)),
    "stateless_ok": dict(graph=_Graph(invoke=_raises(RedisError("connection refused"))),
                         stateless=_Graph(invoke=_clean)),
    "stateless_failed": dict(graph=_Graph(invoke=_raises(RedisError("connection refused"))),
                             stateless=_Graph(invoke=_raises(ValueError("stateless exploded")))),
    "graph_failed": dict(graph=_Graph(invoke=_raises(ValueError("node exploded")))),
}

RESUME_EXITS = {
    "state_load_failed": _Graph(get_state=_raises(RuntimeError("redis down"))),
    "expired": _Graph(get_state=lambda: _State({})),
    "ok": _Graph(get_state=lambda: _State(_CHECKPOINTED), invoke=lambda _c: dict(_RESUMED)),
    "failed": _Graph(get_state=lambda: _State(_CHECKPOINTED),
                     invoke=_raises(ValueError("node exploded on resume"))),
}


def _wire(monkeypatch, graph, *, stateless=None, active=True):
    monkeypatch.setattr(q, "_get_graph", lambda: graph)
    monkeypatch.setattr(q, "_checkpointer_active", active)
    monkeypatch.setattr(q, "refresh_ttl", lambda *a, **k: None)
    if stateless is not None:
        monkeypatch.setattr(q, "_get_stateless_graph", lambda: stateless)


def _submit(monkeypatch, graph, *, stateless=None, active=True, **call):
    _wire(monkeypatch, graph, stateless=stateless, active=active)
    return call_handler(
        q.submit_query, QueryRequest(request="review this", uploaded_text="NDA text"),
        user_id="atty-1", user_name="A. Tester", **call,
    )


def _resume(monkeypatch, graph):
    _wire(monkeypatch, graph)
    return call_handler(
        q.resume_query, "sess-r",
        ResumeRequest(approved=False, notes="tighten the cap", revised_response=""),
    )


@pytest.mark.parametrize("exit_name", list(SUBMIT_EXITS))
def test_every_submit_exit_writes_exactly_one_row(monkeypatch, exit_name):
    """RED first. Mutation: drop finish_turn from any one exit."""
    _submit(monkeypatch, **SUBMIT_EXITS[exit_name])
    rows = _rows()
    assert len(rows) == 1
    assert (rows[0]["kind"], rows[0]["attorney_id"]) == ("query", "atty-1")


@pytest.mark.parametrize("exit_name", list(RESUME_EXITS))
def test_every_resume_exit_writes_exactly_one_row(monkeypatch, exit_name):
    _resume(monkeypatch, RESUME_EXITS[exit_name])
    rows = _rows()
    assert len(rows) == 1
    assert (rows[0]["kind"], rows[0]["session_id"]) == ("resume", "sess-r")
    assert rows[0]["request"] == "tighten the cap"


@pytest.mark.parametrize("exit_name", list(SUBMIT_EXITS))
def test_the_row_and_the_trace_agree(monkeypatch, exit_name):
    """RED first. finish_turn stamps app.outcome and builds the row from the same
    value. Mutation: make build_turn_row derive outcome from `status` instead —
    stateless_ok (status ok, outcome degraded) must then fail."""
    _submit(monkeypatch, **SUBMIT_EXITS[exit_name])
    (row,) = _rows()
    span = spans_by_name("query:auto")[0]
    assert row["outcome"] == span.attributes["app.outcome"]
    assert row["degradations"] == json.loads(span.attributes.get("app.degradations", "[]"))


def test_a_crashed_graph_still_leaves_a_row_that_names_the_failure(monkeypatch):
    resp = _submit(monkeypatch, **SUBMIT_EXITS["graph_failed"])
    assert resp.status == "error"
    (row,) = _rows()
    assert (row["status"], row["outcome"]) == ("error", "failed")
    assert row["error"] == "graph_invoke_failed: ValueError: node exploded"
    assert row["degradations"] == ["graph_invoke_failed"]
    assert row["doc_chars"] == len("NDA text")


def test_a_paused_review_row_is_awaiting_review(monkeypatch):
    """Review Focus #3: Chainlit's interactive review pauses for sign-off."""
    def interrupted(state):
        return {**state, "llm_response": "draft review", "__interrupt__": [
            {"task_type": "contract_review", "risk_level": "high",
             "llm_response": "draft review", "risk_flags": [], "review_iterations": 0}]}

    resp = _submit(monkeypatch, _Graph(invoke=interrupted))
    assert resp.data["awaiting_review"] is True
    (row,) = _rows()
    assert row["awaiting_review"] is True and row["response_chars"] == len("draft review")


def test_a_resume_row_takes_who_and_what_from_the_checkpointed_run(monkeypatch):
    _resume(monkeypatch, RESUME_EXITS["ok"])
    (row,) = _rows()
    assert (row["attorney_id"], row["user_name"], row["document_id"]) == (
        "atty-1", "A. Tester", "doc-9")
    assert row["task_type"] == "contract_review"
    assert row["doc_chars"] == len("MASTER SERVICES AGREEMENT")


def test_a_resume_of_a_pre_sp2_checkpoint_still_writes_a_row(monkeypatch):
    """Review Focus #5: an older checkpoint has no identity fields."""
    _resume(monkeypatch, _Graph(get_state=lambda: _State({"awaiting_review": True}),
                                invoke=lambda _c: dict(_RESUMED)))
    (row,) = _rows()
    assert (row["attorney_id"], row["user_name"]) == ("", "")
    assert row["status"] == "ok"


def test_the_client_facts_reach_the_row_and_the_trace(monkeypatch):
    client = ClientInfo(office_platform="Mac", office_version="16.89",
                        pane_build="abc1234", user_agent="Mozilla/5.0 (Macintosh)")
    _submit(monkeypatch, **SUBMIT_EXITS["ok"], client=client)
    (row,) = _rows()
    assert (row["office_platform"], row["office_version"], row["pane_build"]) == (
        "Mac", "16.89", "abc1234")
    assert row["user_agent"] == "Mozilla/5.0 (Macintosh)" and row["backend_build"] == "dev"
    metadata = json.loads(spans_by_name("query:auto")[0].attributes["metadata"])
    assert (metadata["office.platform"], metadata["pane.build"]) == ("Mac", "abc1234")
    assert metadata["backend.build"] == "dev" and metadata["turn_id"] == row["turn_id"]


def test_over_http_the_headers_become_the_row(monkeypatch):
    """RED first. Proves FastAPI injects BackgroundTasks and ClientInfo through
    the @traced wrapper, and runs the write after the response."""
    _wire(monkeypatch, _Graph(invoke=_clean))
    resp = TestClient(app).post(
        "/api/query",
        json={"request": "hi", "task_type": "research"},
        headers={"X-User-ID": "atty-1", "X-Office-Platform": "PC",
                 "X-Office-Version": "16.0.18129.20116", "X-Pane-Build": "abc1234"},
    ).json()
    (row,) = _rows()
    assert row["turn_id"] == resp["data"]["turn_id"]
    assert (row["office_platform"], row["office_version"], row["pane_build"]) == (
        "PC", "16.0.18129.20116", "abc1234")
    assert row["user_agent"] == "testclient"


def test_a_failed_write_changes_nothing_the_attorney_sees(monkeypatch, caplog):
    def boom(row):
        raise RuntimeError("pool timeout")

    monkeypatch.setattr(turn_log, "write_turn", boom)
    with caplog.at_level(logging.ERROR, logger="memory.turn_log"):
        resp = _submit(monkeypatch, **SUBMIT_EXITS["ok"])
    assert resp.status == "ok" and resp.data["report"] == {}
    assert "TURN LOG WRITE FAILED" in caplog.text and resp.data["turn_id"] in caplog.text


def test_a_failed_row_build_changes_nothing_the_attorney_sees(monkeypatch, caplog):
    """RED first. Mutation: remove the try around build_turn_row in finish_turn."""
    def boom(**kwargs):
        raise KeyError("a bug in the builder")

    monkeypatch.setattr(turn_record, "build_turn_row", boom)
    with caplog.at_level(logging.ERROR, logger="api.turn_record"):
        resp = _submit(monkeypatch, **SUBMIT_EXITS["ok"])
    assert resp.status == "ok"
    assert _rows() == []
    assert "could not build the row" in caplog.text and resp.data["turn_id"] in caplog.text
