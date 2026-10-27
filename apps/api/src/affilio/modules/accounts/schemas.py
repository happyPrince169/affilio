"""Social account registry schemas. Mirrored in packages/shared-types/src/accounts.ts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from affilio.core.enums import (
    ChannelMatchPolicy,
    ConnectionProvider,
    ConnectionStatus,
    CredentialKind,
    CredentialStatus,
    OAuthAttemptStatus,
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
    # --- connection facts (M3B.1A); never a token ---
    connection_id: uuid.UUID | None = None
    connected_at: datetime | None = None
    last_verified_at: datetime | None = None
    disconnect_reason: str | None = None
    page_tasks: list[str] | None = None
    is_mock: bool = False
    can_publish: bool = False
    # ok | missing_page_task | missing_scopes | unknown | not_connected
    permission_status: str = "not_connected"


# ---------------------------------------------------------------- connections (M3B.1A)


class ConnectionConfigResponse(AccountModel):
    """What the UI needs to offer "Connect Facebook": booleans and setting *names* only."""

    provider: ConnectionProvider = ConnectionProvider.META
    mode: Literal["live", "mock"]
    configured: bool
    vault_ready: bool
    missing: list[str]
    scopes: list[str]
    mock_scenarios: list[str]
    graph_api_version: str


class ConnectStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Accepted only in mock mode (404 `mock_mode_disabled` otherwise).
    mock_scenario: str | None = Field(default=None, max_length=64)


class ConnectStartResponse(AccountModel):
    authorize_url: str
    state_expires_at: datetime
    mode: Literal["live", "mock"]
    mock_scenario: str | None


class PlatformConnectionResponse(AccountModel):
    id: uuid.UUID
    provider: ConnectionProvider
    external_user_id: str
    external_user_name: str | None
    status: ConnectionStatus
    granted_scopes: list[str]
    missing_scopes: list[str]
    token_expires_at: datetime | None
    data_access_expires_at: datetime | None
    is_mock: bool
    connected_by_label: str
    connected_at: datetime
    last_checked_at: datetime | None
    last_error: str | None
    page_count: int
    linked_account_count: int
    created_at: datetime
    updated_at: datetime


class DiscoveredPageResponse(AccountModel):
    id: str
    name: str
    category: str | None
    tasks: list[str]
    has_publish_task: bool
    can_publish: bool
    linked_account_id: uuid.UUID | None
    linked_status: str | None
    linked_source: str | None
    linked_connected: bool


class LinkPagesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_ids: list[str] = Field(min_length=1, max_length=50)

    @field_validator("page_ids")
    @classmethod
    def _ids(cls, value: list[str]) -> list[str]:
        cleaned = [v.strip() for v in value if v and v.strip()]
        if not cleaned:
            msg = "page_ids must not be empty"
            raise ValueError(msg)
        return cleaned


class DisconnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=255)


class CredentialSummary(AccountModel):
    """Metadata about a stored token. The ciphertext and the plaintext are never exposed."""

    id: uuid.UUID
    kind: CredentialKind
    version: int
    status: CredentialStatus
    key_id: str
    scopes: list[str]
    page_tasks: list[str] | None
    issued_at: datetime
    expires_at: datetime | None
    data_access_expires_at: datetime | None
    last_validated_at: datetime | None
    last_used_at: datetime | None
    invalidated_reason: str | None
    is_mock: bool
    created_by_label: str


class OAuthAttemptResponse(AccountModel):
    id: uuid.UUID
    status: OAuthAttemptStatus
    error: str | None
    mock_scenario: str | None
    connection_id: uuid.UUID | None
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None


MockActionName = Literal[
    "revoke_page_token",
    "revoke_user_token",
    "outage_on",
    "outage_off",
    "reset",
    "script_fault",
    "clear_faults",
]


class MockActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: MockActionName
    account_id: uuid.UUID | None = None
    connection_id: uuid.UUID | None = None
    # Reels fault to script for the Page (`script_fault`); see providers/mock.py REEL_FAULTS.
    fault: str | None = Field(default=None, max_length=64)


class MockActionResponse(AccountModel):
    action: str
    outage: bool
    revoked_tokens: int


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
