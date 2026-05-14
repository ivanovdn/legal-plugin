# observability/langfuse.py
"""Langfuse integration for agent tracing.

Uses @observe decorator on graph nodes and skills.
Configures langfuse_context for auth at startup.
"""

import logging
import os

from config import get_settings

logger = logging.getLogger(__name__)

_initialized = False


def init_observability() -> None:
    """Initialize Langfuse via environment variables. Call once at startup."""
    global _initialized
    if _initialized:
        return

    settings = get_settings()

    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        logger.warning("Langfuse keys not set — tracing disabled")
        return

    # Langfuse @observe reads from env vars
    os.environ["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key
    os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key
    os.environ["LANGFUSE_HOST"] = settings.langfuse_host

    try:
        from langfuse import Langfuse
        client = Langfuse()
        client.auth_check()
        _initialized = True
        logger.info("Langfuse initialized: %s", settings.langfuse_host)
    except Exception as e:
        logger.warning("Langfuse init failed: %s — tracing disabled", e)


def is_enabled() -> bool:
    return _initialized
