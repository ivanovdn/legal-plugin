"""The query route derives user_id via resolve_user_id (SSO seam)."""
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import api.routes.query as q
from config import get_settings


def _fake_graph(captured):
    """A graph whose invoke records the initial_state it received."""
    class _G:
        def invoke(self, state, config=None):
            captured["state"] = state
            return {"task_type": "research", "report": {"response": "ok"}}
    return _G()


def test_sso_off_uses_x_user_id_header(monkeypatch):
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setattr(get_settings(), "sso_enabled", False, raising=False)
    captured = {}
    with patch("api.routes.query._get_graph", return_value=_fake_graph(captured)), \
         patch("api.routes.query.refresh_ttl", lambda s: None):
        from api.main import app
        client = TestClient(app)
        resp = client.post("/api/query",
                           headers={"X-User-ID": "atty-localstorage-uuid"},
                           json={"request": "who signs?", "task_type": "research"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert captured["state"]["user_id"] == "atty-localstorage-uuid"


def test_sso_off_missing_header_is_anonymous(monkeypatch):
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setattr(get_settings(), "sso_enabled", False, raising=False)
    captured = {}
    with patch("api.routes.query._get_graph", return_value=_fake_graph(captured)), \
         patch("api.routes.query.refresh_ttl", lambda s: None):
        from api.main import app
        client = TestClient(app)
        resp = client.post("/api/query",
                           json={"request": "who signs?", "task_type": "research"})
    assert resp.status_code == 200
    assert captured["state"]["user_id"] == "anonymous"


def test_sso_on_without_token_is_401_and_graph_not_invoked(monkeypatch):
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setattr(get_settings(), "sso_enabled", True, raising=False)
    graph_mock = MagicMock()
    with patch("api.routes.query._get_graph", return_value=graph_mock):
        from api.main import app
        client = TestClient(app)
        resp = client.post("/api/query",
                           headers={"X-User-ID": "spoofed"},
                           json={"request": "who signs?", "task_type": "research"})
    assert resp.status_code == 401
    graph_mock.invoke.assert_not_called()
