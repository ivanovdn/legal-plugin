# observability/langfuse.py
"""Langfuse integration for agent tracing."""

import logging

from config import get_settings

logger = logging.getLogger(__name__)

_initialized = False


def init_observability() -> None:
    """Initialize Langfuse client. Call once at startup before graph compiles."""
    global _initialized
    if _initialized:
        return

    settings = get_settings()

    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        logger.warning("Langfuse keys not set — tracing disabled")
        return

    try:
        from langfuse import Langfuse
        client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        client.auth_check()
        _initialized = True
        logger.info("Langfuse initialized: %s", settings.langfuse_host)
    except Exception as e:
        logger.warning("Langfuse init failed: %s — tracing disabled", e)
