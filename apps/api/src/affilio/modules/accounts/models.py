"""Social account table."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from affilio.core.enums import (
    ChannelMatchPolicy,
    Platform,
    SocialAccountSource,
    SocialAccountStatus,
    SocialAccountType,
)
from affilio.db.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from affilio.db.types import str_enum
from affilio.modules.taxonomy import service as taxonomy_service
from affilio.modules.taxonomy.models import ContentCategory

if TYPE_CHECKING:
    from affilio.modules.identity.models import Organization


class SocialAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An authorized (or to-be-authorized) account on an external platform.

    Credentials/tokens are deliberately NOT on this table; they will live in a dedicated,
    encrypted credential store introduced with the first official integration (M3).
    """

    __tablename__ = "social_accounts"
    __table_args__ = (
        # Explicit name: the convention-generated one would hit PostgreSQL's 63-char limit.
        UniqueConstraint(
            "organization_id",
            "platform",
            "external_account_id",
            name="uq_social_accounts_org_platform_external_id",
        ),
        Index("ix_social_accounts_organization_id_platform", "organization_id", "platform"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    platform: Mapped[Platform] = mapped_column(str_enum(Platform, "platform"), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Platform-issued identifier; unknown until an official connection flow completes.
    external_account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[SocialAccountStatus] = mapped_column(
        str_enum(SocialAccountStatus, "social_account_status"),
        nullable=False,
        default=SocialAccountStatus.PENDING,
    )
    # --- registry fields (M3A) ---
    handle: Mapped[str | None] = mapped_column(String(120), nullable=True)
    account_type: Mapped[SocialAccountType] = mapped_column(
        str_enum(SocialAccountType, "social_account_type"),
        nullable=False,
        default=SocialAccountType.PAGE,
        server_default=text("'page'"),
    )
    # Declared by the platform adapter at registration; never implies an authorized integration.
    capabilities: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # True only after an OAuth authorization succeeded (M3B). Manual records stay False.
    is_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    source: Mapped[SocialAccountSource] = mapped_column(
        str_enum(SocialAccountSource, "social_account_source"),
        nullable=False,
        default=SocialAccountSource.MANUAL,
        server_default=text("'manual'"),
    )
    profile_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    registered_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    organization: Mapped[Organization] = relationship(back_populates="social_accounts")
    channel_profile: Mapped[ChannelProfile | None] = relationship(
        back_populates="account", cascade="all, delete-orphan", uselist=False
    )
    category_rules: Mapped[list[ChannelCategoryRule]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
        order_by="ChannelCategoryRule.priority.desc()",
    )

    @property
    def connection_label(self) -> str:
        if self.is_verified and self.status == SocialAccountStatus.CONNECTED:
            return "Connected"
        if self.source == SocialAccountSource.MANUAL:
            return "Manual (unverified, not connected)"
        return self.status.value.replace("_", " ").capitalize()

    def __repr__(self) -> str:
        return f"<SocialAccount platform={self.platform} name={self.display_name!r}>"


class ChannelProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Routing metadata for one social account (M3A.1). Separate from the registry row so the
    core account table stays small; one profile per account."""

    __tablename__ = "channel_profiles"
    __table_args__ = (UniqueConstraint("social_account_id"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    social_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("social_accounts.id", ondelete="CASCADE"), nullable=False
    )
    primary_category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_categories.id", ondelete="SET NULL"), nullable=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_audience: Mapped[str | None] = mapped_column(String(255), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    routing_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

    account: Mapped[SocialAccount] = relationship(back_populates="channel_profile")
    primary_category: Mapped[ContentCategory | None] = relationship()


class ChannelCategoryRule(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """ALLOW / PREFER / BLOCK a category (and, by inheritance, its descendants) for an account."""

    __tablename__ = "channel_category_rules"
    __table_args__ = (
        UniqueConstraint(
            "social_account_id", "category_id", name="uq_channel_category_rules_account_category"
        ),
        Index("ix_channel_category_rules_organization_id", "organization_id"),
        Index("ix_channel_category_rules_category_id", "category_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    social_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("social_accounts.id", ondelete="CASCADE"), nullable=False
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_categories.id", ondelete="RESTRICT"), nullable=False
    )
    match_policy: Mapped[ChannelMatchPolicy] = mapped_column(
        str_enum(ChannelMatchPolicy, "channel_match_policy"), nullable=False
    )
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    account: Mapped[SocialAccount] = relationship(back_populates="category_rules")
    category: Mapped[ContentCategory] = relationship()


taxonomy_service.register_reference("channel_category_rules", "category_id")
taxonomy_service.register_reference("channel_profiles", "primary_category_id")
