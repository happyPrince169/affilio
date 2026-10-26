"""Authentication and membership schemas. Mirrored in packages/shared-types/src/auth.ts."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

from affilio.core.enums import MembershipRole, UserStatus
from affilio.core.security import MIN_PASSWORD_LENGTH

# Deliberately permissive: self-hosted LAN deployments use internal domains (.local, .lan)
# that strict validators reject. Shape only; no deliverability check.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _email(value: str) -> str:
    cleaned = value.strip().lower()
    if len(cleaned) > 320 or not _EMAIL_RE.match(cleaned):
        msg = "value is not a valid email address"
        raise ValueError(msg)
    return cleaned


EmailAddress = Annotated[str, AfterValidator(_email)]


class AuthModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailAddress
    password: str = Field(min_length=1, max_length=1024)
    organization_slug: str | None = Field(default=None, max_length=100)


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=1024)


class MembershipResponse(AuthModel):
    organization_id: uuid.UUID
    organization_slug: str
    organization_name: str
    timezone: str
    role: MembershipRole


class CurrentUserResponse(AuthModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    status: UserStatus
    identity_kind: str  # user | service
    organization: MembershipResponse
    memberships: list[MembershipResponse]
    permissions: dict[str, bool]
    session_expires_at: datetime | None = None


class MemberCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailAddress
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=1024)
    display_name: str | None = Field(default=None, max_length=120)
    role: MembershipRole = MembershipRole.EDITOR

    @field_validator("role")
    @classmethod
    def _no_owner(cls, value: MembershipRole) -> MembershipRole:
        if value == MembershipRole.OWNER:
            msg = "owner memberships are managed by the organization owner only"
            raise ValueError(msg)
        return value


class MemberRoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: MembershipRole

    @field_validator("role")
    @classmethod
    def _no_owner(cls, value: MembershipRole) -> MembershipRole:
        if value == MembershipRole.OWNER:
            msg = "owner memberships are managed by the organization owner only"
            raise ValueError(msg)
        return value


class MemberResponse(AuthModel):
    user_id: uuid.UUID
    email: str
    display_name: str | None
    status: UserStatus
    role: MembershipRole
    last_login_at: datetime | None
    created_at: datetime


class OrganizationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    timezone: str | None = Field(default=None, max_length=64)


class AuditEventResponse(AuthModel):
    id: uuid.UUID
    actor_kind: str
    actor_user_id: uuid.UUID | None
    actor_label: str
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    data: dict
    request_id: str | None
    created_at: datetime
