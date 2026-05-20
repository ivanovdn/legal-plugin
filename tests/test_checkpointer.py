"""Tests for the Redis checkpointer factory."""

from unittest.mock import patch, MagicMock

from config import get_settings
from graph.checkpointer import build_checkpointer


def test_build_checkpointer_returns_none_when_redis_unavailable(monkeypatch):
    """If RedisSaver construction raises, factory returns None and logs a warning."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("REDIS_URL", "redis://invalid-host:9999")
    get_settings.cache_clear()

    with patch("graph.checkpointer.RedisSaver") as mock_saver_cls:
        mock_saver_cls.from_conn_string.side_effect = ConnectionError("nope")
        result = build_checkpointer()

    assert result is None


def test_build_checkpointer_returns_saver_when_redis_ok(monkeypatch):
    """When RedisSaver constructs successfully, factory returns it after calling setup()."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()

    fake_saver = MagicMock()
    with patch("graph.checkpointer.RedisSaver") as mock_saver_cls:
        mock_saver_cls.from_conn_string.return_value = fake_saver
        result = build_checkpointer()

    assert result is fake_saver
    fake_saver.setup.assert_called_once()
