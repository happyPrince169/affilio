"""Non-sensitive runtime diagnostics."""

from __future__ import annotations

import platform
from datetime import UTC, datetime

from fastapi import APIRouter

from affilio import __version__
from affilio.api.schemas import DatabaseStatus, SystemInfoResponse
from affilio.core.config import get_settings
from affilio.db.session import probe_database
from affilio.modules import DOMAIN_MODULES

system_router = APIRouter(prefix="/system", tags=["system"])


@system_router.get("/info", response_model=SystemInfoResponse, summary="Runtime information")
def read_system_info() -> SystemInfoResponse:
    """Return build/runtime facts and a database readiness check.

    Deliberately excludes connection strings, hostnames, and any environment values.
    """
    settings = get_settings()
    probe = probe_database()
    return SystemInfoResponse(
        service=settings.app_name,
        version=__version__,
        environment=settings.app_env,
        api_prefix=settings.api_v1_prefix,
        python_version=platform.python_version(),
        timestamp=datetime.now(UTC),
        database=DatabaseStatus(
            status="ok" if probe.ok else "unavailable",
            dialect=probe.dialect,
            latency_ms=probe.latency_ms,
        ),
        modules=list(DOMAIN_MODULES),
    )
