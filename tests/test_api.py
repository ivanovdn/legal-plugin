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
