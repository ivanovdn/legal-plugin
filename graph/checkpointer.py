# graph/checkpointer.py
"""Redis checkpointer factory — used to wire LangGraph thread persistence.

Returns None on failure so the app can still boot and run without memory.
"""

import logging

from langgraph.checkpoint.redis import RedisSaver

from config import get_settings

logger = logging.getLogger(__name__)


def build_checkpointer():
    """Build a Redis checkpointer from settings. Returns None on failure.

    RedisSaver.from_conn_string returns a context manager (Iterator[RedisSaver]),
    not the saver directly. We enter the context manager and intentionally never
    exit it — the Redis connection must live for the lifetime of the FastAPI
    process. Any exception (Redis unreachable, bad URL, etc.) is caught and
    None is returned so the app can boot without memory.
    """
    settings = get_settings()
    try:
        cm = RedisSaver.from_conn_string(settings.redis_url)
        saver = cm.__enter__()
        saver.setup()
        logger.info("Redis checkpointer initialized at %s", settings.redis_url)
        return saver
    except Exception as e:
        logger.warning("Checkpointer unavailable (%s) — running without memory", e)
        return None
