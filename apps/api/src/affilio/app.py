"""Application factory for the AFFILIO API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from affilio import __version__
from affilio.api.v1.health import health_router, read_health
from affilio.api.v1.router import api_v1_router
from affilio.core.config import Settings, get_settings
from affilio.core.errors import register_exception_handlers
from affilio.core.logging import configure_logging
from affilio.core.middleware import RequestContextMiddleware
from affilio.db.session import dispose_engine

log = structlog.get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a fully configured FastAPI application.

    Accepting `settings` makes the factory testable without touching process environment.
    """
    settings = settings or get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        log.info(
            "app.startup",
            environment=settings.app_env,
            version=__version__,
            api_prefix=settings.api_v1_prefix,
        )
        yield
        dispose_engine()
        log.info("app.shutdown")

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="AFFILIO — AI-Powered Affiliate Content Operating System.",
        lifespan=lifespan,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )
    app.state.settings = settings

    # Middleware is applied in reverse registration order; CORS must be outermost
    # so that even error responses carry CORS headers.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    register_exception_handlers(app)

    # Unversioned liveness probe for infrastructure (Docker, load balancers).
    app.add_api_route(
        "/health",
        read_health,
        methods=["GET"],
        tags=["health"],
        include_in_schema=False,
    )
    app.include_router(health_router, prefix=settings.api_v1_prefix)
    app.include_router(api_v1_router, prefix=settings.api_v1_prefix)

    return app
