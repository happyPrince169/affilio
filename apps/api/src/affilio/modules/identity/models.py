"""Identity tables: organizations, users, organization_memberships."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from affilio.core.enums import MembershipRole, UserStatus
from affilio.db.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from affilio.db.types import str_enum

if TYPE_CHECKING:
    from affilio.modules.accounts.models import SocialAccount


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Tenant boundary. Every business record is scoped to exactly one organization."""

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)

    memberships: Mapped[list[OrganizationMembership]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    social_accounts: Mapped[list[SocialAccount]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Organization slug={self.slug!r}>"


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A person who can sign in. Users may belong to many organizations."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    # Nullable until the authentication design is finalized (see docs/architecture/overview.md).
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[UserStatus] = mapped_column(
        str_enum(UserStatus, "user_status"), nullable=False, default=UserStatus.ACTIVE
    )

    memberships: Mapped[list[OrganizationMembership]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User email={self.email!r}>"


class OrganizationMembership(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Links a user to an organization with a role. RBAC checks will read this table."""

    __tablename__ = "organization_memberships"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id"),
        Index("ix_organization_memberships_user_id", "user_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[MembershipRole] = mapped_column(
        str_enum(MembershipRole, "membership_role"), nullable=False
    )

    organization: Mapped[Organization] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")
