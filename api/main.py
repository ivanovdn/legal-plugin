# api/main.py
"""FastAPI application — entry point for the legal plugin backend."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from memory.audit import init_audit_db
from observability.langfuse import init_observability

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init observability, audit DB."""
    settings = get_settings()

    init_observability()

    Path(settings.sqlite_path).parent.mkdir(parents=True, exist_ok=True)
    init_audit_db(settings.sqlite_path)

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

app.include_router(health_router)
app.include_router(query_router)
app.include_router(documents_router)
