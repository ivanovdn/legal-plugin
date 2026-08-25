# tests/test_api.py
"""API endpoint tests."""

import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from config import get_settings


def _mock_graph_invoke(state, config=None):
    """Fake graph.invoke that returns a completed state."""
    state["task_type"] = state.get("task_type") or "research"
    state["skill_plan"] = [state["task_type"]]
    state["llm_response"] = "Based on Contract A (doc_id: d1), the answer is X."
    state["risk_level"] = "low"
    state["risk_flags"] = []
    state["awaiting_review"] = state["task_type"] in ("contract_generation", "drafting")
    state["report"] = {
        "task_type": state["task_type"],
        "response": state["llm_response"],
        "risk_level": "low",
        "risk_flags": [],
        "awaiting_review": state["awaiting_review"],
        "sources": [],
    }
    state["filters"] = state.get("filters") or {"client_id": "internal"}
    state["retrieved_chunks"] = []
    state["retrieval_query"] = state["request"]
    return state


def test_health_returns_ok():
    """GET /health returns status ok."""
    from api.main import app
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "services" in data["data"]


def test_query_submits_request(monkeypatch):
    """POST /api/query invokes the graph and returns report."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    with patch("api.routes.query._get_graph") as mock_get_graph:
        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _mock_graph_invoke
        mock_get_graph.return_value = mock_graph

        from api.main import app
        client = TestClient(app)
        response = client.post(
            "/api/query",
            json={"request": "What are indemnification standards?"},
            headers={"X-User-ID": "attorney-1"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "report" in data["data"]
    assert data["data"]["session_id"] != ""


def test_query_requires_request_field():
    """POST /api/query returns 422 without request field."""
    from api.main import app
    client = TestClient(app)
    response = client.post("/api/query", json={})
    assert response.status_code == 422


def test_query_uses_user_id_header(monkeypatch):
    """POST /api/query reads user_id from X-User-ID header."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    with patch("api.routes.query._get_graph") as mock_get_graph:
        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _mock_graph_invoke
        mock_get_graph.return_value = mock_graph

        from api.main import app
        client = TestClient(app)
        response = client.post(
            "/api/query",
            json={"request": "test"},
            headers={"X-User-ID": "attorney-42"},
        )

    assert response.status_code == 200
    call_args = mock_graph.invoke.call_args[0][0]
    assert call_args["user_id"] == "attorney-42"


def test_query_contract_gen_awaiting_review(monkeypatch):
    """Contract generation returns awaiting_review=true."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    with patch("api.routes.query._get_graph") as mock_get_graph:
        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _mock_graph_invoke
        mock_get_graph.return_value = mock_graph

        from api.main import app
        client = TestClient(app)
        response = client.post(
            "/api/query",
            json={"request": "Generate a service agreement", "task_type": "contract_generation"},
            headers={"X-User-ID": "attorney-1"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["data"]["awaiting_review"] is True


def test_ingest_uploads_docx(monkeypatch):
    """POST /api/ingest accepts a DOCX file and returns chunk count."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("EMBEDDING_MODEL", "embeddinggemma:latest")
    get_settings.cache_clear()

    with patch("api.routes.documents.ingest_document", return_value=5):
        from api.main import app
        client = TestClient(app)

        from docx import Document as DocxDocument
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            doc = DocxDocument()
            doc.add_paragraph("Test contract content")
            doc.save(f.name)
            tmp_path = f.name

        with open(tmp_path, "rb") as f:
            response = client.post(
                "/api/ingest",
                files={"file": ("test.docx", f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                data={"client_id": "client-abc", "doc_type": "contract"},
            )

        Path(tmp_path).unlink()

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["data"]["chunks"] == 5


def test_ingest_rejects_unsupported_format():
    """POST /api/ingest rejects non-PDF/DOCX files."""
    from api.main import app
    client = TestClient(app)

    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        f.write(b"plain text")
        tmp_path = f.name

    with open(tmp_path, "rb") as f:
        response = client.post(
            "/api/ingest",
            files={"file": ("test.txt", f, "text/plain")},
            data={"client_id": "x", "doc_type": "contract"},
        )

    Path(tmp_path).unlink()
    assert response.status_code == 400


def test_submit_query_passes_thread_id_to_graph(monkeypatch):
    """graph.invoke is called with config containing thread_id = session_id."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    with patch("api.routes.query._get_graph") as mock_get_graph:
        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _mock_graph_invoke
        mock_get_graph.return_value = mock_graph

        from api.main import app
        client = TestClient(app)
        response = client.post(
            "/api/query",
            json={"request": "test", "session_id": "sess-fixed-123"},
            headers={"X-User-ID": "attorney-1"},
        )

    assert response.status_code == 200
    # graph.invoke is called as invoke(state, config=...)
    call_kwargs = mock_graph.invoke.call_args.kwargs
    config = call_kwargs.get("config") or mock_graph.invoke.call_args.args[1]
    assert config["configurable"]["thread_id"] == "sess-fixed-123"


def test_submit_query_passes_empty_chat_history_in_initial_state(monkeypatch):
    """initial_state always carries chat_history=[] — the reducer merges saved state."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    with patch("api.routes.query._get_graph") as mock_get_graph:
        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _mock_graph_invoke
        mock_get_graph.return_value = mock_graph

        from api.main import app
        client = TestClient(app)
        client.post(
            "/api/query",
            json={"request": "test"},
            headers={"X-User-ID": "attorney-1"},
        )

    state_arg = mock_graph.invoke.call_args.args[0]
    assert state_arg["chat_history"] == []


def test_submit_query_threads_interactive_review_flag(monkeypatch):
    """interactive_review defaults False and is honored when the caller sets it.

    The flag gates the contract-review human_review interrupt: callers without a
    resume UI (Word) leave it False so a blocker is reported, not interrupted.
    """
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    with patch("api.routes.query._get_graph") as mock_get_graph:
        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _mock_graph_invoke
        mock_get_graph.return_value = mock_graph

        from api.main import app
        client = TestClient(app)
        client.post("/api/query", json={"request": "review"}, headers={"X-User-ID": "word-addin"})
        default_state = mock_graph.invoke.call_args.args[0]

        client.post(
            "/api/query",
            json={"request": "review", "interactive_review": True},
            headers={"X-User-ID": "chainlit"},
        )
        flagged_state = mock_graph.invoke.call_args.args[0]

    assert default_state["interactive_review"] is False
    assert flagged_state["interactive_review"] is True


def test_get_graph_builds_with_checkpointer_when_enabled(monkeypatch):
    """_get_graph passes the result of build_checkpointer() to build_graph()."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("CHECKPOINTER_ENABLED", "true")
    get_settings.cache_clear()

    # Reset module-level cache
    import api.routes.query as qmod
    qmod._graph = None

    fake_cp = MagicMock(name="RedisSaver")
    with patch("api.routes.query.build_checkpointer", return_value=fake_cp) as mock_factory, \
         patch("api.routes.query.build_graph") as mock_build_graph:
        mock_build_graph.return_value = MagicMock()
        qmod._get_graph()

    mock_factory.assert_called_once()
    mock_build_graph.assert_called_once_with(checkpointer=fake_cp)


def test_get_graph_builds_without_checkpointer_when_disabled(monkeypatch):
    """When CHECKPOINTER_ENABLED=false, build_graph receives checkpointer=None."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("CHECKPOINTER_ENABLED", "false")
    get_settings.cache_clear()

    import api.routes.query as qmod
    qmod._graph = None

    with patch("api.routes.query.build_checkpointer") as mock_factory, \
         patch("api.routes.query.build_graph") as mock_build_graph:
        mock_build_graph.return_value = MagicMock()
        qmod._get_graph()

    mock_factory.assert_not_called()
    mock_build_graph.assert_called_once_with(checkpointer=None)


def test_submit_query_returns_interrupt_payload_when_awaiting_review(monkeypatch):
    """When graph result has awaiting_review=True, API returns interrupt_payload."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    def _interrupt_invoke(state, config=None):
        state["awaiting_review"] = True
        state["task_type"] = "contract_generation"
        state["llm_response"] = "DRAFT"
        state["risk_level"] = "medium"
        state["risk_flags"] = []
        state["review_iterations"] = 0
        return state

    with patch("api.routes.query._get_graph") as mock_get_graph:
        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _interrupt_invoke
        mock_get_graph.return_value = mock_graph

        from api.main import app
        client = TestClient(app)
        response = client.post(
            "/api/query",
            json={"request": "Generate", "task_type": "contract_generation"},
            headers={"X-User-ID": "attorney-1"},
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["awaiting_review"] is True
    assert "interrupt_payload" in data
    assert data["interrupt_payload"]["llm_response"] == "DRAFT"
    assert data["interrupt_payload"]["review_iterations"] == 0


def test_resume_query_calls_graph_with_command_resume(monkeypatch):
    """POST /api/query/{sid}/resume invokes graph with Command(resume=...) and matching thread_id."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    fake_state = MagicMock()
    fake_state.values = {"awaiting_review": False}

    with patch("api.routes.query._get_graph") as mock_get_graph, \
         patch("api.routes.query.refresh_ttl"):
        mock_graph = MagicMock()
        mock_graph.get_state.return_value = fake_state
        mock_graph.invoke.return_value = {
            "task_type": "contract_generation",
            "report": {"response": "FINAL"},
            "risk_level": "low",
            "awaiting_review": False,
        }
        mock_get_graph.return_value = mock_graph

        from api.main import app
        client = TestClient(app)
        response = client.post(
            "/api/query/sess-X/resume",
            json={"approved": True, "notes": "", "revised_response": ""},
        )

    assert response.status_code == 200
    # First positional arg to invoke is the Command; assert thread_id propagated
    call = mock_graph.invoke.call_args
    config = call.kwargs.get("config") or call.args[1]
    assert config["configurable"]["thread_id"] == "sess-X"
    # And the resume value passed:
    cmd = call.args[0]
    assert hasattr(cmd, "resume") or isinstance(cmd, dict)  # langgraph Command object or dict-like


def test_resume_query_returns_error_when_session_unknown(monkeypatch):
    """If get_state returns no values, API returns session-expired error envelope."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    fake_state = MagicMock()
    fake_state.values = {}  # no checkpoint values = unknown session

    with patch("api.routes.query._get_graph") as mock_get_graph:
        mock_graph = MagicMock()
        mock_graph.get_state.return_value = fake_state
        mock_get_graph.return_value = mock_graph

        from api.main import app
        client = TestClient(app)
        response = client.post(
            "/api/query/sess-missing/resume",
            json={"approved": True, "notes": "", "revised_response": ""},
        )

    assert response.status_code == 200  # API uses envelope, not HTTP status
    data = response.json()
    assert data["status"] == "error"
    assert any("session expired" in e.lower() or "not found" in e.lower() for e in data["errors"])


def test_submit_query_returns_interrupt_payload_from_dunder_interrupt_key(monkeypatch):
    """When graph result has __interrupt__ key (real LangGraph 0.6 behavior), API extracts payload from it."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    from unittest.mock import MagicMock as _MM
    fake_interrupt = _MM()
    fake_interrupt.value = {
        "type": "human_review",
        "task_type": "contract_generation",
        "risk_level": "medium",
        "llm_response": "DRAFT FROM INTERRUPT",
        "risk_flags": [],
        "review_iterations": 2,
    }

    def _invoke_with_interrupt(state, config=None):
        # Simulate LangGraph 0.6: __interrupt__ surfaced; awaiting_review NOT persisted.
        return {"__interrupt__": (fake_interrupt,)}

    with patch("api.routes.query._get_graph") as mock_get_graph, \
         patch("api.routes.query.refresh_ttl"):
        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _invoke_with_interrupt
        mock_get_graph.return_value = mock_graph

        from api.main import app
        client = TestClient(app)
        response = client.post(
            "/api/query",
            json={"request": "Generate", "task_type": "contract_generation"},
            headers={"X-User-ID": "attorney-1"},
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["awaiting_review"] is True
    assert data["interrupt_payload"]["llm_response"] == "DRAFT FROM INTERRUPT"
    assert data["interrupt_payload"]["review_iterations"] == 2


def test_submit_query_passes_document_uuid_as_document_id(monkeypatch):
    """A client document_uuid reaches initial_state['document_id']."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    with patch("api.routes.query._get_graph") as mock_get_graph:
        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _mock_graph_invoke
        mock_get_graph.return_value = mock_graph

        from api.main import app
        client = TestClient(app)
        client.post(
            "/api/query",
            json={"request": "test", "document_uuid": "doc-uuid-abc"},
            headers={"X-User-ID": "attorney-1"},
        )

    state_arg = mock_graph.invoke.call_args.args[0]
    assert state_arg["document_id"] == "doc-uuid-abc"


def test_query_returns_context_truncated_and_tokens_in_payload(monkeypatch):
    """Report keys must survive to the client, not just into the report dict.

    query.py returns the report wholesale; this locks that in. Note this test
    mocks the graph, so it deliberately does NOT exercise output_formatter —
    tests/test_nodes.py covers that half. Together they cover the whole path.
    """
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    truncation = {"doc_chars": 84859, "kept_chars": 49537, "kept_pct": 58}
    usage = {"input": 25270, "output": 412, "total": 25682, "unit": "TOKENS"}

    def _invoke(state, config=None):
        state = _mock_graph_invoke(state, config)
        state["report"]["context_truncated"] = truncation
        state["report"]["tokens"] = usage
        return state

    with patch("api.routes.query._get_graph") as mock_get_graph:
        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _invoke
        mock_get_graph.return_value = mock_graph

        from api.main import app
        client = TestClient(app)
        response = client.post("/api/query", json={"request": "q"})

    assert response.status_code == 200
    report = response.json()["data"]["report"]
    assert report["context_truncated"] == truncation
    assert report["tokens"] == usage


def test_second_turn_early_return_does_not_report_prior_turn_truncation(monkeypatch):
    """A stale context_truncated flag from a PRIOR turn must not leak into a
    turn whose skill takes llm_caller's early-return path.

    Turn 1 (compliance) is forced over the review headroom (tiny
    OLLAMA_NUM_CTX/OLLAMA_NUM_PREDICT_REVIEW), so context_truncated lands in
    the checkpoint. Turn 2 (contract_generation) runs its ReAct agent, which
    sets llm_response directly and never sets `messages` — the exact shape
    that makes llm_caller hit its early-return BEFORE its own reset runs
    (see graph/nodes/llm_caller.py). contract_generation always routes to
    human_review (route_risk), and legal_research/research resets these keys
    itself (would mask the bug being tested), so turn 2 has to go through a
    real submit -> interrupt -> resume cycle to reach output_formatter without
    either of those confounds. The only thing standing between turn 1's flag
    and turn 2's final report is api/routes/query.py's initial_state seeding
    context_truncated/token_usage to None on every submit.

    Uses a REAL graph + in-memory checkpointer (not a mocked graph.invoke),
    so this actually exercises submit_query's initial_state dict rather than
    a hand-built stand-in for it.
    """
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("OLLAMA_NUM_CTX", "1000")
    monkeypatch.setenv("OLLAMA_NUM_PREDICT_REVIEW", "500")
    get_settings.cache_clear()

    from langgraph.checkpoint.memory import MemorySaver
    import api.routes.query as query_mod
    from graph.graph import build_graph

    def _fake_llm_post(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = lambda: None
        resp.json.return_value = {
            "message": {"content": "Based on Contract A (doc_id: d1), the answer is X."}
        }
        return resp

    def _fake_agent():
        agent = MagicMock()
        fake_msg = MagicMock()
        fake_msg.content = "Generated draft text."
        agent.invoke.return_value = {"messages": [fake_msg]}
        return agent

    with patch("graph.nodes.llm_caller.httpx.post", side_effect=_fake_llm_post), \
         patch("graph.nodes.rag_retriever.hybrid_search", return_value=[
             {"chunk_id": "c1", "doc_id": "d1", "doc_title": "Contract A",
              "text": "relevant legal text", "rrf_score": 0.8,
              "doc_type": "contract", "client_id": "internal", "jurisdiction": "US"},
         ]), \
         patch("skills.contract_generation.contract_generation._build_agent", return_value=_fake_agent()):

        real_graph = build_graph(checkpointer=MemorySaver())
        monkeypatch.setattr(query_mod, "_graph", real_graph)

        from api.main import app
        client = TestClient(app)
        session_id = "leak-test-session"

        # Turn 1: compliance, forced over the (test-tiny) headroom.
        resp1 = client.post(
            "/api/query",
            json={"request": "x" * 5000, "task_type": "compliance", "session_id": session_id},
            headers={"X-User-ID": "attorney-1"},
        )
        assert resp1.status_code == 200
        report1 = resp1.json()["data"]["report"]
        assert report1["context_truncated"] is not None
        assert report1["context_truncated"]["kept_pct"] < 100

        # Turn 2a: contract_generation submit. Its ReAct-agent path sets
        # llm_response directly and never sets messages (llm_caller
        # early-returns, no reset), then risk_assessor/route_risk send it to
        # human_review unconditionally, which pauses (interrupt_enabled
        # defaults True) instead of reaching output_formatter yet.
        resp2a = client.post(
            "/api/query",
            json={
                "request": "Generate an NDA", "task_type": "contract_generation",
                "session_id": session_id,
            },
            headers={"X-User-ID": "attorney-1"},
        )
        assert resp2a.status_code == 200
        assert resp2a.json()["data"]["awaiting_review"] is True

        # Turn 2b: resume/approve — completes the SAME turn's run to
        # output_formatter. Nothing between the pause and here touches
        # context_truncated, so this reports exactly what was sitting in the
        # channel when turn 2 started.
        resp2b = client.post(
            f"/api/query/{session_id}/resume",
            json={"approved": True, "notes": ""},
        )

    assert resp2b.status_code == 200
    report2 = resp2b.json()["data"]["report"]
    # Same seam, same seeding: context_breakdown must be present in the payload and
    # None on a turn that assembled no chat context. A KeyError here means
    # output_formatter never emitted it; a non-None value means initial_state failed
    # to seed it and a prior turn's numbers leaked through.
    assert report2["context_breakdown"] is None
    assert report2["context_truncated"] is None, (
        "Turn 2 took llm_caller's early-return path, which never resets "
        "context_truncated itself — api/routes/query.py's initial_state must "
        "seed context_truncated=None on every submit so a stale flag from a "
        "prior turn can't leak through."
    )


def test_submit_seeds_context_breakdown_in_initial_state(monkeypatch):
    """The seed is invisible in the report, so assert on what the route passes IN.

    Without it, a context_breakdown checkpointed by a previous doc-chat turn survives
    into a later turn whose skill early-returns, and the pane shows the earlier turn's
    numbers as if they were this turn's. That leak cannot be seen downstream: an
    absent key and a None value both read as None out of report.get(), which is why
    this test reaches for the input rather than the output.
    """
    import api.routes.query as query_mod

    captured = {}

    class FakeGraph:
        def invoke(self, state, config=None):
            captured.update(state)
            return {**state, "report": {"response": "ok"}, "llm_response": "ok"}

    monkeypatch.setattr(query_mod, "_graph", FakeGraph())

    from api.main import app
    client = TestClient(app)
    resp = client.post(
        "/api/query",
        json={"request": "hello", "task_type": "research", "session_id": "seed-test"},
        headers={"X-User-ID": "attorney-1"},
    )
    assert resp.status_code == 200
    assert "context_breakdown" in captured, "initial_state must SEED the key, not omit it"
    assert captured["context_breakdown"] is None
