"""Response schemas for platform endpoints. Mirrored in packages/shared-types/src/api.ts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class HealthResponse(ApiModel):
    status: Literal["ok"] = "ok"
    service: str
    version: str
    environment: str
    timestamp: datetime


class DatabaseStatus(ApiModel):
    status: Literal["ok", "unavailable"]
    dialect: str
    latency_ms: float | None = None


class MetaIntegrationInfo(ApiModel):
    """Facts about the Meta integration: mode and whether it is configured. Never values."""

    mode: str = Field(description="live | mock (mock is refused outside development/test)")
    configured: bool
    graph_api_version: str


class IntegrationsInfo(ApiModel):
    meta: MetaIntegrationInfo


class SystemInfoResponse(ApiModel):
    service: str
    version: str
    environment: str
    api_prefix: str
    python_version: str
    timestamp: datetime
    database: DatabaseStatus
    modules: list[str] = Field(description="Registered domain modules, in dependency order.")
    integrations: IntegrationsInfo


class ErrorBody(ApiModel):
    code: str
    message: str
    details: Any | None = None
    request_id: str | None = None


class ErrorEnvelope(ApiModel):
    error: ErrorBody
