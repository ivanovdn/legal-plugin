"""Every accepted request leaves exactly one turn_log row, and that row agrees
with the trace (spec 2026-10-09, D3-D4).

Handlers are driven the way tests/test_turn_outcome.py drives them — as plain
functions with injected failures — through tests.conftest.call_handler, which
supplies what FastAPI would inject and runs the background tasks afterwards.
"""
from __future__ import annotations

import importlib
import json
import logging
from types import SimpleNamespace

import psycopg_pool
import pytest
from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver
from redis.exceptions import RedisError

import api.routes.compact as compact_route
import api.routes.query as q
import api.turn_record as turn_record
import memory.turn_log as turn_log
from api.client_info import ClientInfo
from api.main import app
from api.models import CompactRequest, QueryRequest, ResumeRequest
from graph.graph import build_graph
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


@pytest.mark.parametrize("exit_name", list(SUBMIT_EXITS))
def test_a_submit_exit_schedules_exactly_one_write_and_does_not_make_it(monkeypatch, exit_name):
    """The row is SCHEDULED, not written, by the time the handler returns (spec
    D4: the attorney waits for no insert).

    Calls the handler directly, on purpose — call_handler runs the queued tasks
    before returning, so through it a deferred write and a synchronous one look
    the same. Mutations: leave out `background_tasks=` on one exit (finish_turn
    then writes now); call finish_turn twice on one exit (UNIQUE(turn_id) would
    quietly reject the second INSERT and leave one row)."""
    scenario = SUBMIT_EXITS[exit_name]
    _wire(monkeypatch, scenario["graph"], stateless=scenario.get("stateless"))
    tasks = BackgroundTasks()
    q.submit_query(QueryRequest(request="review this", uploaded_text="NDA text"),
                   background_tasks=tasks, user_id="atty-1", user_name="A. Tester", client=ClientInfo())
    assert len(tasks.tasks) == 1 and _rows() == []


@pytest.mark.parametrize("exit_name", list(RESUME_EXITS))
def test_a_resume_exit_schedules_exactly_one_write_and_does_not_make_it(monkeypatch, exit_name):
    """Resume's twin of the test above — direct call on purpose, same mutations."""
    _wire(monkeypatch, RESUME_EXITS[exit_name])
    tasks = BackgroundTasks()
    q.resume_query("sess-r", ResumeRequest(approved=False, notes="tighten the cap", revised_response=""),
                   background_tasks=tasks, client=ClientInfo())
    assert len(tasks.tasks) == 1 and _rows() == []


def _compact(monkeypatch, outcome_fn):
    monkeypatch.setattr(compact_route, "compact_conversation", outcome_fn)
    return call_handler(
        compact_route.post_compact, CompactRequest(document_id="doc-1", reclaim_chars=5000),
        user_id="atty-1", user_name="A. Tester",
    )


_CONDENSED = {"compacted": True, "from_id": 1, "to_id": 6, "messages": 6, "quotes": 5,
              "dropped": 1, "reclaimed": 4200, "requested": 5000, "segment_id": 3,
              "reason": "", "error": ""}

COMPACT_EXITS = {
    "ok": (lambda *a: dict(_CONDENSED), None),
    "refused": (lambda *a: {"compacted": False, "reason": "would not save space", "error": ""},
                None),
    "error": (lambda *a: {"compacted": False, "reason": "",
                          "error": "the condensed summary could not be verified"},
              HTTPException),
    "store_read_raised": (_raises(psycopg_pool.PoolTimeout("couldn't get a connection")),
                          psycopg_pool.PoolTimeout),
}


@pytest.mark.parametrize("exit_name", list(COMPACT_EXITS))
def test_every_compact_exit_writes_exactly_one_row(monkeypatch, exit_name):
    """RED first. The two raising exits must write synchronously: FastAPI never
    runs background tasks for a request that raised, and neither does
    call_handler. Mutation: pass background_tasks on a raising exit."""
    outcome_fn, raises = COMPACT_EXITS[exit_name]
    if raises:
        with pytest.raises(raises):
            _compact(monkeypatch, outcome_fn)
    else:
        _compact(monkeypatch, outcome_fn)
    (row,) = _rows()
    assert (row["kind"], row["document_id"], row["attorney_id"]) == ("compact", "doc-1", "atty-1")
    assert row["user_name"] == "A. Tester"
    assert row["outcome"] == spans_by_name("compact")[0].attributes["app.outcome"]


def test_compaction_rows_keep_what_was_condensed_or_why_not(monkeypatch):
    _compact(monkeypatch, COMPACT_EXITS["ok"][0])
    _compact(monkeypatch, COMPACT_EXITS["refused"][0])
    with pytest.raises(HTTPException):
        _compact(monkeypatch, COMPACT_EXITS["error"][0])
    condensed, refused, failed = _rows()
    assert condensed["compaction"]["reclaimed"] == 4200 and condensed["status"] == "ok"
    assert refused["compaction"]["reason"] == "would not save space"
    assert refused["outcome"] == "ok"
    assert (failed["status"], failed["outcome"]) == ("error", "failed")
    assert failed["error"] == "compaction_failed: the condensed summary could not be verified"


@pytest.mark.parametrize("disabled,document_id", [(True, "doc-1"), (False, "   ")])
def test_the_compact_guards_write_no_row(monkeypatch, disabled, document_id):
    """A 403 or 400 is rejected before any work, like a 422."""
    monkeypatch.setattr(compact_route, "get_settings",
                        lambda: SimpleNamespace(compaction_enabled=not disabled))
    with pytest.raises(HTTPException):
        call_handler(compact_route.post_compact,
                     CompactRequest(document_id=document_id, reclaim_chars=0),
                     user_id="atty-1", user_name="")
    assert _rows() == []


@pytest.mark.parametrize("exit_name", ["ok", "refused"])
def test_a_returning_compact_exit_schedules_exactly_one_write_and_does_not_make_it(monkeypatch, exit_name):
    """Same rule as the query/resume exits: a returning exit queues the write for
    after the response. Calls the handler directly — call_handler would run the
    queued task first and hide the timing."""
    monkeypatch.setattr(compact_route, "compact_conversation", COMPACT_EXITS[exit_name][0])
    tasks = BackgroundTasks()
    compact_route.post_compact(CompactRequest(document_id="doc-1", reclaim_chars=5000),
                               background_tasks=tasks, user_id="atty-1", user_name="A. Tester",
                               client=ClientInfo())
    assert len(tasks.tasks) == 1 and _rows() == []


_SOW = (
    "STATEMENT OF WORK No. 3\n\n"
    "This Statement of Work is issued under the Master Services Agreement between "
    "Acme Corp and Trinetix.\n"
    "Payment is due within 60 days of invoice.\n"
)


class _Message:
    """What ChatOllama returns: content, token usage and Ollama's durations (ns)."""
    def __init__(self, content, tokens_in, tokens_out, load_ns, prompt_ns, gen_ns):
        self.content = content
        self.usage_metadata = {"input_tokens": tokens_in, "output_tokens": tokens_out,
                               "total_tokens": tokens_in + tokens_out}
        self.response_metadata = {"model": "qwen3.6:latest", "load_duration": load_ns,
                                  "prompt_eval_duration": prompt_ns, "eval_duration": gen_ns,
                                  "total_duration": load_ns + prompt_ns + gen_ns}


class _LLM:
    def __init__(self, message):
        self._message = message

    def invoke(self, messages):
        return self._message


def test_a_real_sow_chat_turn_records_its_grounding_and_both_model_calls(monkeypatch):
    """Through the real graph: the collector must cross into graph nodes in the
    suite, not only in production, and LangGraph must keep turn_id (declared in
    LegalAgentState) all the way to the audit row."""
    grounding = importlib.import_module("skills.grounding")
    research = importlib.import_module("skills.legal_research.legal_research")
    monkeypatch.setattr(grounding, "get_parent_msa",
                        lambda client_id, **kw: ("Trinetix Model MSA", "MSA text"))
    monkeypatch.setattr(research, "_build_llm", lambda: _LLM(_Message(
        "I will replace the payment term with net 30 days.", 9000, 120,
        5_900_000_000, 6_100_000_000, 1_100_000_000)))
    monkeypatch.setattr(research, "_build_json_llm", lambda: _LLM(_Message(
        '{"action": "replace", "target_text": "Payment is due within 60 days of invoice.", '
        '"new_text": "Payment is due within 30 days of invoice."}',
        2000, 60, 0, 800_000_000, 300_000_000)))
    monkeypatch.setattr("graph.nodes.rag_retriever.hybrid_search", lambda *a, **k: [])
    _wire(monkeypatch, build_graph(checkpointer=None))

    resp = TestClient(app).post(
        "/api/query",
        json={"request": "Change the payment term to net 30.", "task_type": "research",
              "uploaded_text": _SOW, "filters": {"client_id": "internal"},
              "document_uuid": "doc-sow-3"},
        headers={"X-User-ID": "atty-int", "X-Office-Platform": "Mac",
                 "X-Office-Version": "16.89", "X-Pane-Build": "abc1234"},
    ).json()

    assert resp["status"] == "ok"
    (row,) = _rows()
    assert row["turn_id"] == resp["data"]["turn_id"]
    assert (row["outcome"], row["document_id"]) == ("ok", "doc-sow-3")
    assert (row["contract_type"], row["msa_title"]) == ("sow", "Trinetix Model MSA")
    assert [call["name"] for call in row["llm_detail"]] == ["doc_chat", "doc_chat_json_retry"]
    assert (row["llm_load_ms"], row["llm_prompt_ms"], row["llm_gen_ms"]) == (5900, 6900, 1400)
    assert (row["tokens_in"], row["tokens_out"]) == (11000, 180)
    assert row["edits_proposed"] == 1 and row["context"]["parts"]
    assert row["office_platform"] == "Mac"
    with get_pool().connection() as conn:
        (audit_turn_id,) = conn.execute("SELECT turn_id FROM audit_log").fetchone()
    assert audit_turn_id == row["turn_id"]


_NDA = (
    "MUTUAL NON-DISCLOSURE AGREEMENT\n\n"
    "This Non-Disclosure Agreement is made between Acme Corp and Trinetix.\n"
    "Confidentiality obligations survive for 5 years after termination.\n"
)

# One chat reply holding BOTH structured blocks the pane turns into cards: an
# edit (```json) and a suggested preference (```preference).
_CHAT_REPLY_WITH_AN_EDIT_AND_A_PREFERENCE = (
    "I will change the survival period to 3 years.\n\n"
    "```json\n"
    '{"action": "replace", '
    '"target_text": "Confidentiality obligations survive for 5 years after termination.", '
    '"new_text": "Confidentiality obligations survive for 3 years after termination."}\n'
    "```\n\n"
    "I have noted your preference.\n\n"
    "```preference\n"
    "I prefer a 3-year survival period for confidentiality obligations.\n"
    "```\n"
)


class _OllamaReply:
    """What httpx.post hands llm_caller: a 200 carrying one /api/chat body."""
    def __init__(self, content):
        self._content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"message": {"content": self._content}, "prompt_eval_count": 10, "eval_count": 5}


def test_a_review_turn_does_not_inherit_the_previous_chat_turns_proposals(monkeypatch):
    """RED first. Word shares ONE session_id across both tabs, and the checkpointer
    carries every state key a turn does not set into the next turn, so review ->
    chat with edits -> re-review is a normal workflow. Only legal_research resets
    proposed_edits / proposed_preferences; the re-review's row then counted the
    chat turn's cards as its own. Two real turns over one real graph and one
    session, as in production.

    Mutation: take "proposed_edits" / "proposed_preferences" back out of
    submit_query's initial_state."""
    research = importlib.import_module("skills.legal_research.legal_research")
    monkeypatch.setattr(research, "_build_llm", lambda: _LLM(_Message(
        _CHAT_REPLY_WITH_AN_EDIT_AND_A_PREFERENCE, 9000, 120,
        5_900_000_000, 6_100_000_000, 1_100_000_000)))
    monkeypatch.setattr("graph.nodes.rag_retriever.hybrid_search", lambda *a, **k: [])
    monkeypatch.setattr("graph.nodes.llm_caller.httpx.post",
                        lambda *a, **k: _OllamaReply("# Review Summary\nNo issues."))
    _wire(monkeypatch, build_graph(checkpointer=MemorySaver()))
    client = TestClient(app)

    def post(request, task_type):
        return client.post(
            "/api/query",
            json={"request": request, "task_type": task_type, "uploaded_text": _NDA,
                  "session_id": "sess-both-tabs", "document_uuid": "doc-nda-1"},
            headers={"X-User-ID": "atty-int"},
        ).json()

    post("Change the survival period to 3 years, and remember that I prefer 3 years.", "research")
    review = post("Review this contract.", "contract_review")

    chat_row, review_row = _rows()
    # Without these the assertions below could pass because turn 1 never proposed anything.
    assert (chat_row["task_type"], chat_row["edits_proposed"], chat_row["preferences_proposed"]) == (
        "research", 1, 1)
    assert review_row["task_type"] == "contract_review"
    assert (review_row["edits_proposed"], review_row["preferences_proposed"]) == (0, 0)
    # Neither turn took the SOW path, whose governing-MSA lookup this test does not fake.
    assert (chat_row["contract_type"], review_row["contract_type"]) == ("nda", "nda")
    # The row counts what the pane was sent, so the pane must not get the stale cards either.
    assert review["data"]["report"]["proposed_edits"] == []
    assert review["data"]["report"]["proposed_preferences"] == []
