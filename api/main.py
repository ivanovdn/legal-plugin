# api/main.py
"""FastAPI application — entry point for the legal plugin backend."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from memory.db import init_db
from observability.otel import init_observability

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    """Give the root logger a handler and a level, so app records survive.

    Uvicorn configures only its own `uvicorn*` loggers and leaves root at
    WARNING with no handler. Records from api/, graph/, memory/ and skills/
    propagate up to that root, find nothing that will handle them, and are
    dropped — everything below ERROR silently, since `logging.lastResort` only
    covers WARNING and above. The in-flight turn logging, which exists so a slow
    LLM can be told apart from a wedged one, was invisible in production for
    exactly this reason.

    Called at import rather than in the lifespan: uvicorn imports the app after
    configuring its own logging, so this runs late enough to see an unconfigured
    root and early enough to catch anything logged during startup.

    `basicConfig` is a no-op when root already has handlers, which is the
    behaviour we want in both directions — it will not fight pytest's capture
    plugin, and `force=True` would tear that capture out from under every other
    test. uvicorn's `uvicorn` and `uvicorn.access` loggers set propagate=False,
    so their records stop at uvicorn's own handler and are not double-printed
    by the one installed here.
    """
    try:
        level = get_settings().log_level
    except Exception:  # noqa: BLE001 — a bad .env must still get a readable traceback
        level = "INFO"
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init observability; init_db() creates all Postgres store tables
    (audit, review, conversation, feedback, interaction_event)."""
    settings = get_settings()

    init_observability()

    init_db()

    logger.info("Legal plugin API started on port %d", settings.api_port)
    yield
    logger.info("Legal plugin API shutting down")


app = FastAPI(
    title="Legal Plugin API",
    description="AI-powered legal assistant for internal legal teams",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from api.routes.health import router as health_router
from api.routes.query import router as query_router
from api.routes.documents import router as documents_router
from api.routes.preferences import router as preferences_router
from api.routes.feedback import router as feedback_router

app.include_router(health_router)
app.include_router(query_router)
app.include_router(documents_router)
app.include_router(preferences_router)
app.include_router(feedback_router)
