"""TDD: mid-session Redis failure degrades to stateless run instead of hard-failing."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnError

import api.routes.query as q
from config import get_settings


def _make_stateless_invoke(state, config=None):
    """Fake stateless graph.invoke — returns a normal terminal result."""
    return {"task_type": "research", "report": {"response": "answer"}, "risk_level": "low"}


def test_redis_failure_midsession_degrades_and_answers(monkeypatch):
    """When the checkpointed graph's invoke raises a Redis ConnectionError mid-session,
    the endpoint must degrade to the stateless graph and return status='ok'
    with memory_degraded=True — not a hard error."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    class _BoomGraph:
        def invoke(self, state, config=None):
            raise RedisConnError("Error 61 connecting to localhost:6379. Connection refused.")

    class _StatelessGraph:
        def invoke(self, state, config=None):
            return _make_stateless_invoke(state, config)

    with patch("api.routes.query._get_graph", return_value=_BoomGraph()), \
         patch("api.routes.query._get_stateless_graph", return_value=_StatelessGraph()), \
         patch("api.routes.query._checkpointer_active", True, create=False), \
         patch("api.routes.query.refresh_ttl"):

        from api.main import app
        client = TestClient(app)
        response = client.post(
            "/api/query",
            json={"request": "who is X?", "task_type": "research",
                  "session_id": "s1", "filters": {"client_id": "internal"},
                  "uploaded_text": "MUTUAL NDA\n\nbody"},
            headers={"X-User-ID": "word-addin"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok", f"Expected ok, got: {data}"
    assert data["data"]["memory_degraded"] is True


def test_redis_failure_wrapped_in_other_exception_degrades(monkeypatch):
    """A Redis error wrapped inside another exception (via __cause__) is still detected."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    class _BoomGraph:
        def invoke(self, state, config=None):
            try:
                raise RedisConnError("Connection refused")
            except RedisConnError as inner:
                raise RuntimeError("Checkpointer I/O error") from inner

    class _StatelessGraph:
        def invoke(self, state, config=None):
            return _make_stateless_invoke(state, config)

    with patch("api.routes.query._get_graph", return_value=_BoomGraph()), \
         patch("api.routes.query._get_stateless_graph", return_value=_StatelessGraph()), \
         patch("api.routes.query._checkpointer_active", True, create=False), \
         patch("api.routes.query.refresh_ttl"):

        from api.main import app
        client = TestClient(app)
        response = client.post(
            "/api/query",
            json={"request": "test", "task_type": "research"},
            headers={"X-User-ID": "word-addin"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["data"]["memory_degraded"] is True


def test_non_redis_exception_still_hard_fails(monkeypatch):
    """A genuine graph bug (not a Redis error) returns status='error' as before."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    class _BoomGraph:
        def invoke(self, state, config=None):
            raise ValueError("unexpected graph bug")

    with patch("api.routes.query._get_graph", return_value=_BoomGraph()), \
         patch("api.routes.query._checkpointer_active", True, create=False), \
         patch("api.routes.query.refresh_ttl"):

        from api.main import app
        client = TestClient(app)
        response = client.post(
            "/api/query",
            json={"request": "test", "task_type": "research"},
            headers={"X-User-ID": "word-addin"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"


def test_redis_failure_with_checkpointer_inactive_hard_fails(monkeypatch):
    """If _checkpointer_active=False (stateless run) and we get a Redis-shaped error,
    we do NOT attempt a second stateless fallback — hard-fail (should never happen
    but guard against infinite loop)."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    class _BoomGraph:
        def invoke(self, state, config=None):
            raise RedisConnError("Connection refused")

    with patch("api.routes.query._get_graph", return_value=_BoomGraph()), \
         patch("api.routes.query._checkpointer_active", False, create=False), \
         patch("api.routes.query.refresh_ttl"):

        from api.main import app
        client = TestClient(app)
        response = client.post(
            "/api/query",
            json={"request": "test", "task_type": "research"},
            headers={"X-User-ID": "word-addin"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
