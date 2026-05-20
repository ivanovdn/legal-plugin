# tests/test_nodes.py
"""Unit tests for real node implementations."""

from unittest.mock import patch, MagicMock
import httpx

from config import get_settings
from graph.nodes.history_appender import history_appender
from graph.nodes.human_review import human_review
from graph.nodes.llm_caller import llm_caller


def _make_state(**overrides):
    base = {
        "request": "test request",
        "user_id": "attorney-1",
        "uploaded_docs": [],
        "task_type": "",
        "skill_plan": [],
        "retrieval_query": "",
        "retrieved_chunks": [],
        "filters": {},
        "messages": [],
        "llm_response": "",
        "risk_level": "",
        "risk_flags": [],
        "awaiting_review": False,
        "attorney_notes": "",
        "report": {},
        "session_id": "test-sess",
        "checkpoint_ref": "",
        "trace_id": "",
        "chat_history": [],
        "review_iterations": 0,
        "report_notes_unincorporated": "",
    }
    base.update(overrides)
    return base


# --- intake ---

def test_intake_sets_client_id_filter():
    """Intake resolves client_id and sets filters."""
    from graph.nodes.intake import intake
    state = _make_state(user_id="attorney-1", request="Review a contract")
    result = intake(state)
    assert "client_id" in result["filters"]
    assert result["filters"]["client_id"] != ""


def test_intake_sets_retrieval_query_from_request():
    """Intake sets retrieval_query to the request text."""
    from graph.nodes.intake import intake
    state = _make_state(request="What are indemnification standards?")
    result = intake(state)
    assert result["retrieval_query"] == "What are indemnification standards?"


# --- intent_router ---

def test_intent_router_classifies_via_llm(monkeypatch):
    """intent_router calls LLM and sets task_type from response."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    from config import get_settings
    get_settings.cache_clear()

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "message": {"content": '{"task_type": "contract_review"}'}
    }

    with patch("graph.nodes.intent_router.httpx.post", return_value=fake_response):
        from graph.nodes.intent_router import intent_router
        state = _make_state(request="Review clauses in this NDA")
        result = intent_router(state)

    assert result["task_type"] == "contract_review"
    assert result["skill_plan"] == ["contract_review"]


def test_intent_router_falls_back_on_error(monkeypatch):
    """intent_router defaults to 'research' when LLM fails."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    from config import get_settings
    get_settings.cache_clear()

    with patch("graph.nodes.intent_router.httpx.post", side_effect=httpx.ConnectError("down")):
        from graph.nodes.intent_router import intent_router
        state = _make_state(request="something")
        result = intent_router(state)

    assert result["task_type"] == "research"


def test_intent_router_preserves_existing_task_type():
    """If task_type already set, intent_router keeps it."""
    from graph.nodes.intent_router import intent_router
    state = _make_state(
        request="Generate a contract",
        task_type="contract_generation",
        skill_plan=["contract_generation"],
    )
    result = intent_router(state)
    assert result["task_type"] == "contract_generation"


# --- rag_retriever ---

def test_rag_retriever_calls_hybrid_search():
    """rag_retriever populates retrieved_chunks from hybrid_search."""
    fake_results = [
        {"chunk_id": "c1", "doc_id": "d1", "text": "relevant text",
         "doc_title": "Contract A", "rrf_score": 0.8},
    ]

    with patch("graph.nodes.rag_retriever.hybrid_search", return_value=fake_results):
        from graph.nodes.rag_retriever import rag_retriever
        state = _make_state(
            retrieval_query="indemnification clause",
            filters={"client_id": "test-client"},
        )
        result = rag_retriever(state)

    assert len(result["retrieved_chunks"]) == 1
    assert result["retrieved_chunks"][0]["chunk_id"] == "c1"


def test_rag_retriever_skips_when_no_query():
    """rag_retriever passes through when retrieval_query is empty."""
    from graph.nodes.rag_retriever import rag_retriever
    state = _make_state(retrieval_query="")
    result = rag_retriever(state)
    assert result["retrieved_chunks"] == []


# --- llm_caller ---

def test_llm_caller_sends_context_and_request(monkeypatch):
    """llm_caller builds prompt with chunks and calls Ollama."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    from config import get_settings
    get_settings.cache_clear()

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "message": {"content": "The indemnification clause typically protects..."}
    }

    with patch("graph.nodes.llm_caller.httpx.post", return_value=fake_response) as mock_post:
        from graph.nodes.llm_caller import llm_caller
        state = _make_state(
            request="What are indemnification standards?",
            retrieved_chunks=[
                {"chunk_id": "c1", "doc_id": "d1", "doc_title": "Contract A",
                 "text": "The buyer shall be indemnified against all claims."},
            ],
        )
        result = llm_caller(state)

    assert result["llm_response"] != ""
    call_body = mock_post.call_args[1]["json"]
    assert call_body["options"]["temperature"] == 0.0


def test_llm_caller_handles_no_chunks(monkeypatch):
    """llm_caller works even with no retrieved chunks."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    from config import get_settings
    get_settings.cache_clear()

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "message": {"content": "No context available."}
    }

    with patch("graph.nodes.llm_caller.httpx.post", return_value=fake_response):
        from graph.nodes.llm_caller import llm_caller
        state = _make_state(request="General question", retrieved_chunks=[])
        result = llm_caller(state)

    assert result["llm_response"] != ""


def test_llm_caller_uses_skill_messages(monkeypatch):
    """llm_caller uses messages from state when set by a skill."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    from config import get_settings
    get_settings.cache_clear()

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "message": {"content": "Clause analysis: The indemnification clause is standard."}
    }

    with patch("graph.nodes.llm_caller.httpx.post", return_value=fake_response) as mock_post:
        from graph.nodes.llm_caller import llm_caller
        state = _make_state(
            request="Review clauses",
            messages=[
                {"role": "system", "content": "You are a contract review specialist."},
                {"role": "user", "content": "Review clauses"},
            ],
            retrieved_chunks=[
                {"chunk_id": "c1", "doc_id": "d1", "doc_title": "Contract A", "text": "Indemnity clause text"},
            ],
        )
        result = llm_caller(state)

    call_body = mock_post.call_args[1]["json"]
    assert call_body["messages"][0]["content"] == "You are a contract review specialist."


# --- planner ---

def test_planner_decomposes_multi_skill(monkeypatch):
    """planner calls LLM to break down multi-skill requests."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    from config import get_settings
    get_settings.cache_clear()

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "message": {"content": '{"skill_plan": ["contract_review", "compliance"], "task_type": "contract_review"}'}
    }

    with patch("graph.nodes.planner.httpx.post", return_value=fake_response):
        from graph.nodes.planner import planner
        state = _make_state(
            request="Review this contract and check compliance with GDPR",
            skill_plan=["contract_review", "compliance"],
            task_type="multi",
        )
        result = planner(state)

    assert result["task_type"] in ("contract_review", "compliance")
    assert len(result["skill_plan"]) >= 1


def test_planner_keeps_single_skill():
    """planner passes through when skill_plan has only one skill."""
    from graph.nodes.planner import planner
    state = _make_state(
        request="Review this contract",
        skill_plan=["contract_review"],
        task_type="contract_review",
    )
    result = planner(state)
    assert result["task_type"] == "contract_review"
    assert result["skill_plan"] == ["contract_review"]


# --- risk_assessor ---

def test_risk_assessor_flags_no_citations():
    """risk_assessor flags high risk when response lacks citations."""
    from graph.nodes.risk_assessor import risk_assessor
    state = _make_state(
        task_type="research",
        llm_response="The law says you should do this.",
        retrieved_chunks=[
            {"chunk_id": "c1", "doc_id": "d1", "doc_title": "Contract A", "text": "some text"},
        ],
    )
    result = risk_assessor(state)
    assert result["risk_level"] == "high"
    assert any("citation" in f.get("reason", "").lower() for f in result["risk_flags"])


def test_risk_assessor_low_risk_with_citations():
    """risk_assessor sets low risk when response cites doc_id."""
    from graph.nodes.risk_assessor import risk_assessor
    state = _make_state(
        task_type="research",
        llm_response="According to Contract A (doc_id: d1), indemnification applies.",
        retrieved_chunks=[
            {"chunk_id": "c1", "doc_id": "d1", "doc_title": "Contract A", "text": "indemnification"},
        ],
    )
    result = risk_assessor(state)
    assert result["risk_level"] == "low"


def test_risk_assessor_no_chunks_means_high_risk():
    """risk_assessor flags high risk when no chunks were retrieved."""
    from graph.nodes.risk_assessor import risk_assessor
    state = _make_state(
        task_type="research",
        llm_response="I think the answer is...",
        retrieved_chunks=[],
    )
    result = risk_assessor(state)
    assert result["risk_level"] == "high"


# --- output_formatter ---

def test_output_formatter_builds_report():
    """output_formatter creates a report dict."""
    from graph.nodes.output_formatter import output_formatter
    state = _make_state(
        task_type="research",
        llm_response="The answer is X.",
        risk_level="low",
        risk_flags=[],
    )
    result = output_formatter(state)
    assert "response" in result["report"]
    assert "task_type" in result["report"]
    assert result["report"]["response"] == "The answer is X."


# --- memory_writer ---

def test_memory_writer_writes_audit(tmp_path, monkeypatch):
    """memory_writer writes to SQLite audit log."""
    db_path = str(tmp_path / "test_legal.db")
    monkeypatch.setenv("SQLITE_PATH", db_path)
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    from config import get_settings
    get_settings.cache_clear()

    from memory.audit import init_audit_db
    init_audit_db(db_path)

    import graph.nodes.memory_writer as mw
    mw._db_initialized = True

    state = _make_state(
        task_type="research",
        risk_level="low",
        session_id="sess-test",
        user_id="attorney-1",
    )
    result = mw.memory_writer(state)

    import sqlite3
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT * FROM audit_log").fetchall()
    conn.close()
    assert len(rows) == 1


# --- human_review ---

def test_human_review_sets_awaiting_review():
    """human_review marks state as awaiting review."""
    state = _make_state(task_type="contract_generation")
    result = human_review(state)
    assert result["awaiting_review"] is True


def test_human_review_skips_interrupt_when_disabled(monkeypatch):
    """When interrupt_enabled is False, human_review flags awaiting_review but does NOT call interrupt()."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("INTERRUPT_ENABLED", "false")
    get_settings.cache_clear()

    with patch("graph.nodes.human_review.interrupt") as mock_interrupt:
        state = _make_state(task_type="contract_generation", risk_level="high")
        result = human_review(state)

    assert mock_interrupt.call_count == 0
    assert result["awaiting_review"] is True


def test_human_review_calls_interrupt_when_enabled(monkeypatch):
    """When interrupt_enabled is True, human_review calls interrupt()."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("INTERRUPT_ENABLED", "true")
    get_settings.cache_clear()

    with patch("graph.nodes.human_review.interrupt", return_value={"approved": True, "notes": "ok"}) as mock_interrupt:
        state = _make_state(task_type="contract_generation", risk_level="high")
        result = human_review(state)

    assert mock_interrupt.call_count == 1
    assert result["awaiting_review"] is False


# --- history_appender ---

def test_history_appender_appends_user_and_assistant_pair(monkeypatch):
    """history_appender returns a chat_history list with one user + one assistant message."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("CHAT_HISTORY_TRIM_CHARS", "300")
    get_settings.cache_clear()

    state = _make_state(request="What's the term?", llm_response="The term is 2 years.")
    result = history_appender(state)

    assert "chat_history" in result
    assert len(result["chat_history"]) == 2
    assert result["chat_history"][0] == {"role": "user", "content": "What's the term?"}
    assert result["chat_history"][1] == {"role": "assistant", "content": "The term is 2 years."}


def test_history_appender_trims_long_assistant_response(monkeypatch):
    """Assistant content longer than trim_chars is truncated and gets '[...]' marker."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("CHAT_HISTORY_TRIM_CHARS", "10")
    get_settings.cache_clear()

    state = _make_state(request="Generate NDA", llm_response="A" * 100)
    result = history_appender(state)

    asst = result["chat_history"][1]
    assert asst["content"] == "AAAAAAAAAA[...]"
    assert len(asst["content"]) == 15  # 10 chars + 5-char marker


def test_history_appender_does_not_trim_short_response(monkeypatch):
    """Short responses are kept verbatim, no marker appended."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("CHAT_HISTORY_TRIM_CHARS", "300")
    get_settings.cache_clear()

    state = _make_state(request="Q", llm_response="Short answer.")
    result = history_appender(state)

    assert result["chat_history"][1]["content"] == "Short answer."
    assert "[...]" not in result["chat_history"][1]["content"]


def test_history_appender_does_not_trim_user_request(monkeypatch):
    """User request is stored verbatim even if very long."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("CHAT_HISTORY_TRIM_CHARS", "10")
    get_settings.cache_clear()

    long_request = "B" * 500
    state = _make_state(request=long_request, llm_response="ok")
    result = history_appender(state)

    assert result["chat_history"][0]["content"] == long_request
    assert "[...]" not in result["chat_history"][0]["content"]


# --- llm_caller chat_history injection ---

def test_llm_caller_prepends_chat_history_default_path(monkeypatch):
    """When no skill_messages and chat_history present, history sits between system and user."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"message": {"content": "answer"}}

    captured = {}
    def _capture(*args, **kwargs):
        captured["json"] = kwargs.get("json")
        return fake_response

    history = [
        {"role": "user", "content": "prior Q"},
        {"role": "assistant", "content": "prior A"},
    ]

    with patch("graph.nodes.llm_caller.httpx.post", side_effect=_capture):
        state = _make_state(request="new Q", chat_history=history)
        llm_caller(state)

    sent = captured["json"]["messages"]
    # Expect: [system, prior_user, prior_assistant, current_user]
    assert sent[0]["role"] == "system"
    assert sent[1] == history[0]
    assert sent[2] == history[1]
    assert sent[-1]["role"] == "user"
    assert "new Q" in sent[-1]["content"]


def test_llm_caller_prepends_chat_history_skill_path(monkeypatch):
    """When skill provides system + user messages, history sits between system and user."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"message": {"content": "answer"}}

    captured = {}
    def _capture(*args, **kwargs):
        captured["json"] = kwargs.get("json")
        return fake_response

    history = [
        {"role": "user", "content": "prior Q"},
        {"role": "assistant", "content": "prior A"},
    ]
    skill_messages = [
        {"role": "system", "content": "Skill system prompt"},
        {"role": "user", "content": "Review my doc"},
    ]

    with patch("graph.nodes.llm_caller.httpx.post", side_effect=_capture):
        state = _make_state(messages=skill_messages, chat_history=history)
        llm_caller(state)

    sent = captured["json"]["messages"]
    assert sent[0]["role"] == "system"
    assert sent[0]["content"] == "Skill system prompt"
    assert sent[1] == history[0]
    assert sent[2] == history[1]
    assert sent[-1]["role"] == "user"
    assert "Review my doc" in sent[-1]["content"]


def test_llm_caller_works_when_chat_history_empty(monkeypatch):
    """Empty chat_history: prompt looks exactly like before this feature."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"message": {"content": "answer"}}

    captured = {}
    def _capture(*args, **kwargs):
        captured["json"] = kwargs.get("json")
        return fake_response

    with patch("graph.nodes.llm_caller.httpx.post", side_effect=_capture):
        state = _make_state(request="just one Q", chat_history=[])
        llm_caller(state)

    sent = captured["json"]["messages"]
    assert len(sent) == 2  # system + user, no history
    assert sent[0]["role"] == "system"
    assert sent[1]["role"] == "user"
