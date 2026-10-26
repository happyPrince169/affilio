"""Liveness endpoint. Intentionally does not touch the database so it stays cheap."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from affilio import __version__
from affilio.api.schemas import HealthResponse
from affilio.core.config import get_settings

health_router = APIRouter(tags=["health"])


@health_router.get("/health", response_model=HealthResponse, summary="Liveness probe")
def read_health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        service=settings.app_name,
        version=__version__,
        environment=settings.app_env,
        timestamp=datetime.now(UTC),
    )
