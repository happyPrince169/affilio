"""Social account registry schemas. Mirrored in packages/shared-types/src/accounts.ts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from affilio.core.enums import (
    ChannelMatchPolicy,
    Platform,
    SocialAccountSource,
    SocialAccountStatus,
    SocialAccountType,
)
from affilio.modules.products.urls import validate_http_url
from affilio.modules.taxonomy.schemas import CategoryRef


class AccountModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class _AccountFieldRules(BaseModel):
    """Validators shared by create/update payloads (fields declared by subclasses)."""

    model_config = ConfigDict(extra="forbid")

    @field_validator("display_name", check_fields=False)
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            msg = "display_name must not be blank"
            raise ValueError(msg)
        return value.strip()

    @field_validator("handle", "external_account_id", check_fields=False)
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        cleaned = (value or "").strip().lstrip("@")
        return cleaned or None

    @field_validator("profile_url", check_fields=False)
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return validate_http_url(value, field="profile_url") if value and value.strip() else None


class SocialAccountCreate(_AccountFieldRules):
    """Manual registration. The record is unverified and disconnected until OAuth (M3B)."""

    platform: Platform = Platform.FACEBOOK
    account_type: SocialAccountType = SocialAccountType.PAGE
    display_name: str = Field(min_length=1, max_length=200)
    handle: str | None = Field(default=None, max_length=120)
    external_account_id: str | None = Field(default=None, max_length=255)
    profile_url: str | None = Field(default=None, max_length=2048)
    notes: str | None = Field(default=None, max_length=5_000)


class SocialAccountUpdate(_AccountFieldRules):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    handle: str | None = Field(default=None, max_length=120)
    external_account_id: str | None = Field(default=None, max_length=255)
    profile_url: str | None = Field(default=None, max_length=2048)
    notes: str | None = Field(default=None, max_length=5_000)


class SocialAccountStatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["disabled", "disconnected"]
    note: str | None = Field(default=None, max_length=2_000)


class SocialAccountResponse(AccountModel):
    id: uuid.UUID
    platform: Platform
    account_type: SocialAccountType
    display_name: str
    handle: str | None
    external_account_id: str | None
    profile_url: str | None
    status: SocialAccountStatus
    source: SocialAccountSource
    is_verified: bool
    connection_label: str
    capabilities: dict[str, Any]
    notes: str | None
    registered_by: str | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------- channel routing (M3A.1)


class ChannelCategoryRuleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: uuid.UUID
    match_policy: ChannelMatchPolicy
    priority: int = Field(default=0, ge=0, le=1000)


class ChannelProfileUpdate(BaseModel):
    """Full replacement of the profile and its category rules (admin)."""

    model_config = ConfigDict(extra="forbid")

    primary_category_id: uuid.UUID | None = None
    description: str | None = Field(default=None, max_length=2_000)
    target_audience: str | None = Field(default=None, max_length=255)
    language: str | None = Field(default=None, max_length=16)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    routing_enabled: bool = True
    rules: list[ChannelCategoryRuleInput] = Field(default_factory=list, max_length=200)

    @field_validator("country")
    @classmethod
    def _country(cls, value: str | None) -> str | None:
        return value.upper() if value else None

    @field_validator("rules")
    @classmethod
    def _unique_rules(cls, value: list[ChannelCategoryRuleInput]) -> list[ChannelCategoryRuleInput]:
        ids = [r.category_id for r in value]
        if len(set(ids)) != len(ids):
            msg = "each category may appear only once"
            raise ValueError(msg)
        return value


class ChannelCategoryRuleResponse(AccountModel):
    category: CategoryRef
    match_policy: ChannelMatchPolicy
    priority: int


class ChannelProfileResponse(AccountModel):
    social_account_id: uuid.UUID
    primary_category: CategoryRef | None
    description: str | None
    target_audience: str | None
    language: str | None
    country: str | None
    routing_enabled: bool
    rules: list[ChannelCategoryRuleResponse]
    updated_at: datetime | None
