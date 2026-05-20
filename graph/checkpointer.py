# graph/checkpointer.py
"""Redis checkpointer factory — used to wire LangGraph thread persistence.

Returns None on failure so the app can still boot and run without memory.
"""

import logging

from langgraph.checkpoint.redis import RedisSaver

from config import get_settings

logger = logging.getLogger(__name__)


def build_checkpointer():
    """Build a Redis checkpointer from settings. Returns None on failure."""
    settings = get_settings()
    try:
        saver = RedisSaver.from_conn_string(settings.redis_url)
        saver.setup()
        logger.info("Redis checkpointer initialized at %s", settings.redis_url)
        return saver
    except Exception as e:
        logger.warning("Checkpointer unavailable (%s) — running without memory", e)
        return None
