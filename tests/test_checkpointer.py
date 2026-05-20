"""Tests for the Redis checkpointer factory."""

from unittest.mock import patch, MagicMock

from config import get_settings
from graph.checkpointer import build_checkpointer, refresh_ttl


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
    """When RedisSaver constructs successfully, factory returns the saver after calling setup()."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()

    # RedisSaver.from_conn_string returns a context manager; the saver itself
    # is what __enter__ yields.
    fake_saver = MagicMock()
    fake_cm = MagicMock()
    fake_cm.__enter__.return_value = fake_saver

    with patch("graph.checkpointer.RedisSaver") as mock_saver_cls:
        mock_saver_cls.from_conn_string.return_value = fake_cm
        result = build_checkpointer()

    assert result is fake_saver
    fake_cm.__enter__.assert_called_once()
    fake_saver.setup.assert_called_once()


def test_refresh_ttl_calls_expire_on_checkpoint_keys(monkeypatch):
    """refresh_ttl scans and expires both checkpoint:* and checkpoint_write:* keys."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("CHECKPOINT_TTL_SECONDS", "3600")
    get_settings.cache_clear()

    fake_redis = MagicMock()
    fake_redis.scan_iter.side_effect = [
        iter([b"checkpoint:abc:foo", b"checkpoint:abc:bar"]),
        iter([b"checkpoint_write:abc:foo"]),
    ]

    with patch("graph.checkpointer.Redis", return_value=fake_redis):
        refresh_ttl("abc")

    # 2 checkpoint keys + 1 checkpoint_write key = 3 expire calls
    assert fake_redis.expire.call_count == 3
    # Each call's TTL is 3600
    for call in fake_redis.expire.call_args_list:
        assert call.args[1] == 3600


def test_refresh_ttl_does_nothing_when_checkpointer_disabled(monkeypatch):
    """When checkpointer_enabled=False, refresh_ttl is a no-op."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("CHECKPOINTER_ENABLED", "false")
    get_settings.cache_clear()

    with patch("graph.checkpointer.Redis") as mock_redis_cls:
        refresh_ttl("abc")

    mock_redis_cls.assert_not_called()
