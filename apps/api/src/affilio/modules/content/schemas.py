"""Affiliate link schemas. Mirrored in packages/shared-types/src/content.ts."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from affilio.core.enums import AffiliateLinkSource, AffiliateLinkStatus, Platform
from affilio.modules.products.urls import validate_http_url


class LinkModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class _LinkFieldRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("label", check_fields=False)
    @classmethod
    def _label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            msg = "label must not be blank"
            raise ValueError(msg)
        return value.strip()

    @field_validator("url", check_fields=False)
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return validate_http_url(value, field="url") if value is not None else None


class AffiliateLinkCreate(_LinkFieldRules):
    platform: Platform = Platform.SHOPEE
    product_id: uuid.UUID | None = None
    label: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2048)
    notes: str | None = Field(default=None, max_length=2_000)


class AffiliateLinkUpdate(_LinkFieldRules):
    product_id: uuid.UUID | None = None
    label: str | None = Field(default=None, min_length=1, max_length=200)
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    notes: str | None = Field(default=None, max_length=2_000)
    status: AffiliateLinkStatus | None = None


class AffiliateLinkResponse(LinkModel):
    id: uuid.UUID
    platform: Platform
    product_id: uuid.UUID | None
    product_name: str | None = None
    label: str
    url: str
    source: AffiliateLinkSource
    status: AffiliateLinkStatus
    notes: str | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime
