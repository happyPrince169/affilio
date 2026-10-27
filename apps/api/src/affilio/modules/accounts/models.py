"""Social account table."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
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
from affilio.db.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from affilio.db.types import str_enum
from affilio.modules.taxonomy import service as taxonomy_service
from affilio.modules.taxonomy.models import ContentCategory

if TYPE_CHECKING:
    from affilio.modules.identity.models import Organization

# Page task Meta must report for a Page to be publishable through the API (feasibility A4).
PUBLISH_TASK = "CREATE_CONTENT"


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
    # --- connection facts (M3B.1A). The Page row *is* the Page identity; tokens live in
    # account_credentials. These columns describe health and permissions, never secrets.
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("platform_connections.id", ondelete="SET NULL"),
        nullable=True,
    )
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    disconnect_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Tasks the provider reported for the authorizing user on this Page (e.g. CREATE_CONTENT).
    page_tasks: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    # True when the connection came from the mock provider. Never shown as a real connection.
    is_mock: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    organization: Mapped[Organization] = relationship(back_populates="social_accounts")
    connection: Mapped[PlatformConnection | None] = relationship(back_populates="accounts")
    credentials: Mapped[list[AccountCredential]] = relationship(
        back_populates="social_account", order_by="AccountCredential.version.desc()"
    )
    channel_profile: Mapped[ChannelProfile | None] = relationship(
        back_populates="account", cascade="all, delete-orphan", uselist=False
    )
    category_rules: Mapped[list[ChannelCategoryRule]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
        order_by="ChannelCategoryRule.priority.desc()",
    )

    @property
    def is_connected(self) -> bool:
        return self.is_verified and self.status == SocialAccountStatus.CONNECTED

    @property
    def permission_status(self) -> str:
        """ok | missing_page_task | missing_scopes | unknown | not_connected (no secrets)."""
        if not self.is_connected:
            return "not_connected"
        if self.page_tasks is None:
            return "unknown"
        if PUBLISH_TASK not in self.page_tasks:
            return "missing_page_task"
        if self.connection is not None and self.connection.missing_scopes:
            return "missing_scopes"
        return "ok"

    @property
    def can_publish(self) -> bool:
        return self.permission_status == "ok"

    @property
    def connection_label(self) -> str:
        if self.is_connected and self.is_mock:
            return "Mock connection (development only, not a real Page)"
        if self.is_connected:
            return "Connected"
        if self.source == SocialAccountSource.OAUTH and self.disconnect_reason:
            return f"Disconnected ({self.disconnect_reason.replace('_', ' ')}) — reconnect needed"
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


class PlatformConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One provider-level grant: a Meta user authorized the app for this organization.

    Several Pages (social_accounts) hang off one connection. Holds no secrets; the user token
    and the Page tokens are rows in `account_credentials`.
    """

    __tablename__ = "platform_connections"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "provider",
            "external_user_id",
            name="uq_platform_connections_org_provider_user",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[ConnectionProvider] = mapped_column(
        str_enum(ConnectionProvider, "connection_provider"), nullable=False
    )
    external_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_user_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[ConnectionStatus] = mapped_column(
        str_enum(ConnectionStatus, "connection_status"),
        nullable=False,
        default=ConnectionStatus.ACTIVE,
    )
    granted_scopes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    missing_scopes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # Pages the provider listed at the last authorization: id, name, category, tasks (no tokens).
    discovered_pages: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    data_access_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_mock: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    connected_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    connected_by_label: Mapped[str] = mapped_column(String(255), nullable=False)
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)

    accounts: Mapped[list[SocialAccount]] = relationship(back_populates="connection")
    credentials: Mapped[list[AccountCredential]] = relationship(
        back_populates="connection", cascade="all, delete-orphan"
    )


class AccountCredential(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An encrypted provider token. One *active* row per (connection, subject, kind); older
    versions stay as `superseded` history. Plaintext never leaves the accounts service."""

    __tablename__ = "account_credentials"
    __table_args__ = (
        Index(
            "uq_account_credentials_active_subject",
            "connection_id",
            "external_subject_id",
            "kind",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index("ix_account_credentials_social_account_id", "social_account_id"),
        Index("ix_account_credentials_organization_id", "organization_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("platform_connections.id", ondelete="CASCADE"),
        nullable=False,
    )
    social_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("social_accounts.id", ondelete="CASCADE"), nullable=True
    )
    # Provider id of what the token is for (Meta user id or Page id).
    external_subject_id: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[CredentialKind] = mapped_column(
        str_enum(CredentialKind, "credential_kind"), nullable=False
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    status: Mapped[CredentialStatus] = mapped_column(
        str_enum(CredentialStatus, "credential_status"),
        nullable=False,
        default=CredentialStatus.ACTIVE,
    )
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_id: Mapped[str] = mapped_column(String(32), nullable=False)
    token_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    page_tasks: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    data_access_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidated_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_mock: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    created_by_label: Mapped[str] = mapped_column(String(255), nullable=False)

    connection: Mapped[PlatformConnection] = relationship(back_populates="credentials")
    social_account: Mapped[SocialAccount | None] = relationship(back_populates="credentials")

    def __repr__(self) -> str:  # never include ciphertext material
        return f"<AccountCredential kind={self.kind} v{self.version}>"


class OAuthState(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One OAuth attempt. The raw `state` is only in the authorize URL; the row keeps its
    SHA-256 and is consumed exactly once."""

    __tablename__ = "oauth_states"
    __table_args__ = (
        Index("ix_oauth_states_organization_id_created_at", "organization_id", "created_at"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_sessions.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[ConnectionProvider] = mapped_column(
        str_enum(ConnectionProvider, "connection_provider"), nullable=False
    )
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    redirect_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    requested_scopes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    mock_scenario: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[OAuthAttemptStatus] = mapped_column(
        str_enum(OAuthAttemptStatus, "oauth_attempt_status"),
        nullable=False,
        default=OAuthAttemptStatus.PENDING,
    )
    error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("platform_connections.id", ondelete="SET NULL"),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


taxonomy_service.register_reference("channel_category_rules", "category_id")
taxonomy_service.register_reference("channel_profiles", "primary_category_id")
