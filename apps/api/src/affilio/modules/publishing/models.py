"""Distribution tables: campaigns (+media, +targets), publication_jobs, publication_links,
publication_snapshots (immutable approved content), publication_attempts."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from affilio.core.enums import (
    CampaignStatus,
    ConnectionProvider,
    EvidenceKind,
    Platform,
    PublicationAttemptStatus,
    PublicationStatus,
    PublishMode,
    PublishRequestStatus,
    UploadPhase,
)
from affilio.db.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from affilio.db.types import str_enum
from affilio.modules.accounts.models import SocialAccount
from affilio.modules.content.models import AffiliateLink
from affilio.modules.media.models import MediaAsset


class Campaign(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "campaigns"
    __table_args__ = (Index("ix_campaigns_organization_id_status", "organization_id", "status"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[CampaignStatus] = mapped_column(
        str_enum(CampaignStatus, "campaign_status"), nullable=False, default=CampaignStatus.DRAFT
    )
    starts_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    media: Mapped[list[CampaignMedia]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )
    targets: Mapped[list[CampaignTarget]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )
    jobs: Mapped[list[PublicationJob]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )


class CampaignMedia(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """An approved FINAL video selected for a campaign (checked at insertion time)."""

    __tablename__ = "campaign_media"
    __table_args__ = (
        UniqueConstraint("campaign_id", "media_asset_id"),
        Index("ix_campaign_media_media_asset_id", "media_asset_id"),
    )

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    media_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    campaign: Mapped[Campaign] = relationship(back_populates="media")
    media_asset: Mapped[MediaAsset] = relationship()


class CampaignTarget(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "campaign_targets"
    __table_args__ = (
        UniqueConstraint("campaign_id", "social_account_id"),
        Index("ix_campaign_targets_social_account_id", "social_account_id"),
    )

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    social_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("social_accounts.id", ondelete="CASCADE"), nullable=False
    )

    campaign: Mapped[Campaign] = relationship(back_populates="targets")
    social_account: Mapped[SocialAccount] = relationship()


class PublicationJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One intent to publish one media asset to one target with its own content and schedule."""

    __tablename__ = "publication_jobs"
    __table_args__ = (
        UniqueConstraint(
            "campaign_id",
            "media_asset_id",
            "social_account_id",
            name="uq_publication_jobs_campaign_media_account",
        ),
        Index(
            "uq_publication_jobs_org_idempotency_key",
            "organization_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        # Duplicate evidence guard: the same external post can confirm only one job.
        Index(
            "uq_publication_jobs_account_external_post",
            "social_account_id",
            "external_post_id",
            unique=True,
            postgresql_where=text("external_post_id IS NOT NULL"),
        ),
        Index("ix_publication_jobs_organization_id_status", "organization_id", "status"),
        Index(
            "ix_publication_jobs_organization_id_scheduled_at", "organization_id", "scheduled_at"
        ),
        Index(
            "ix_publication_jobs_social_account_id_scheduled_at",
            "social_account_id",
            "scheduled_at",
        ),
        Index("ix_publication_jobs_media_asset_id", "media_asset_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    media_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False
    )
    social_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("social_accounts.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[Platform] = mapped_column(str_enum(Platform, "platform"), nullable=False)
    # Stable identifier of the publishing intent (used in checklists and future API requests).
    intent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[PublicationStatus] = mapped_column(
        str_enum(PublicationStatus, "publication_status"),
        nullable=False,
        default=PublicationStatus.DRAFT,
    )
    # Optimistic concurrency: every write increments; clients send the version they saw.
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )

    # Live (editable) content. The approved copy lives in publication_snapshots.
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    cta: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hashtags: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    content_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )

    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "publication_snapshots.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_publication_jobs_approved_snapshot_id_publication_snapshots",
        ),
        nullable=True,
    )

    # Outcome / evidence
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    external_post_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_post_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    evidence_kind: Mapped[EvidenceKind | None] = mapped_column(
        str_enum(EvidenceKind, "evidence_kind"), nullable=True
    )
    confirmed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_by_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmation_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Explicit admin decision that the approved snapshot still applies after the video was
    # reclassified (M3A.1 hardening). Cleared implicitly by a new approval.
    revalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revalidated_by_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    campaign: Mapped[Campaign] = relationship(back_populates="jobs")
    media_asset: Mapped[MediaAsset] = relationship()
    social_account: Mapped[SocialAccount] = relationship()
    links: Mapped[list[PublicationLink]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="PublicationLink.position"
    )
    snapshots: Mapped[list[PublicationSnapshot]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        foreign_keys="PublicationSnapshot.job_id",
        order_by="PublicationSnapshot.approved_at.desc()",
    )
    approved_snapshot: Mapped[PublicationSnapshot | None] = relationship(
        foreign_keys=[approved_snapshot_id], post_update=True
    )
    attempts: Mapped[list[PublicationAttempt]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="PublicationAttempt.attempt_no"
    )
    uploads: Mapped[list[PublicationUpload]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="PublicationUpload.created_at"
    )


class PublicationLink(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Ordered affiliate links attached to a publication's live content."""

    __tablename__ = "publication_links"
    __table_args__ = (
        UniqueConstraint("job_id", "affiliate_link_id"),
        Index("ix_publication_links_affiliate_link_id", "affiliate_link_id"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("publication_jobs.id", ondelete="CASCADE"), nullable=False
    )
    affiliate_link_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("affiliate_links.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    job: Mapped[PublicationJob] = relationship(back_populates="links")
    affiliate_link: Mapped[AffiliateLink] = relationship()


class PublicationSnapshot(UUIDPrimaryKeyMixin, Base):
    """Immutable copy of the content approved for a publication. Never updated or deleted
    while its job exists; a new approval creates a new snapshot."""

    __tablename__ = "publication_snapshots"
    __table_args__ = (Index("ix_publication_snapshots_job_id", "job_id"),)

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("publication_jobs.id", ondelete="CASCADE"), nullable=False
    )
    content_version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    cta: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hashtags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    links: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    media: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    target: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_by_label: Mapped[str] = mapped_column(String(255), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    job: Mapped[PublicationJob] = relationship(back_populates="snapshots", foreign_keys=[job_id])


class PublicationAttempt(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One publishing attempt (assisted or, later, API). Complete history is kept."""

    __tablename__ = "publication_attempts"
    __table_args__ = (UniqueConstraint("job_id", "attempt_no"),)

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("publication_jobs.id", ondelete="CASCADE"), nullable=False
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[PublishMode] = mapped_column(str_enum(PublishMode, "publish_mode"), nullable=False)
    adapter: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[PublicationAttemptStatus] = mapped_column(
        str_enum(PublicationAttemptStatus, "publication_attempt_status"), nullable=False
    )
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publication_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    started_by_label: Mapped[str] = mapped_column(String(255), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    job: Mapped[PublicationJob] = relationship(back_populates="attempts")


UPLOAD_TERMINAL_PHASES: frozenset[UploadPhase] = frozenset(
    {UploadPhase.PUBLISHED, UploadPhase.FAILED, UploadPhase.ABANDONED}
)
# Claimable by the publisher (needs_reconciliation is parked: only an explicit check advances it).
UPLOAD_ACTIVE_PHASES: frozenset[UploadPhase] = frozenset(
    set(UploadPhase) - UPLOAD_TERMINAL_PHASES - {UploadPhase.NEEDS_RECONCILIATION}
)
_LIVE_PHASES_SQL = "phase NOT IN ('published', 'failed', 'abandoned')"


class PublicationUpload(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Durable record of one API publishing attempt against the provider (M3B.2A).

    Every provider-side identifier is written *before* the next network call so a crashed
    worker can resume from the persisted phase. The finish request is persisted as
    `FINALIZING` before it is sent and is never sent twice: an unknown outcome parks the row
    in `needs_reconciliation` until the provider (or an admin) confirms it.
    """

    __tablename__ = "publication_uploads"
    __table_args__ = (
        # At most one live upload per job.
        Index(
            "uq_publication_uploads_job_live",
            "job_id",
            unique=True,
            postgresql_where=text(_LIVE_PHASES_SQL),
        ),
        Index(
            "ix_publication_uploads_next_action",
            "next_action_at",
            postgresql_where=text(_LIVE_PHASES_SQL),
        ),
        Index("ix_publication_uploads_social_account_id", "social_account_id"),
        Index("ix_publication_uploads_organization_id_phase", "organization_id", "phase"),
        UniqueConstraint("attempt_id", name="uq_publication_uploads_attempt_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("publication_jobs.id", ondelete="CASCADE"), nullable=False
    )
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publication_attempts.id", ondelete="CASCADE"),
        nullable=False,
    )
    social_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("social_accounts.id", ondelete="CASCADE"), nullable=False
    )
    media_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publication_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    provider: Mapped[ConnectionProvider] = mapped_column(
        str_enum(ConnectionProvider, "connection_provider"), nullable=False
    )
    is_mock: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    phase: Mapped[UploadPhase] = mapped_column(
        str_enum(UploadPhase, "upload_phase"), nullable=False, default=UploadPhase.PENDING
    )
    publish_request_status: Mapped[PublishRequestStatus] = mapped_column(
        str_enum(PublishRequestStatus, "publish_request_status"),
        nullable=False,
        default=PublishRequestStatus.NOT_SENT,
    )
    # --- provider identifiers (durable before the next call) ---
    provider_session_id: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    provider_video_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_post_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_post_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # --- media pinned at claim time; the upload refuses to continue if the file changed ---
    file_relative_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    file_mtime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bytes_total: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    bytes_sent: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    # --- provider status snapshots ---
    processing_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_status: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    poll_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    finish_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finish_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # --- errors and retries ---
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_transient: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_retries: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default=text("5")
    )
    # --- scheduling and leasing ---
    next_action_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped[PublicationJob] = relationship(back_populates="uploads")
    attempt: Mapped[PublicationAttempt] = relationship()
    social_account: Mapped[SocialAccount] = relationship()

    @property
    def is_live(self) -> bool:
        return self.phase not in UPLOAD_TERMINAL_PHASES

    @property
    def progress_percent(self) -> int | None:
        if not self.bytes_total:
            return None
        return max(0, min(100, int(self.bytes_sent * 100 / self.bytes_total)))


class PublisherWorker(UUIDPrimaryKeyMixin, Base):
    """Heartbeat row per publisher process (durable worker health for the UI)."""

    __tablename__ = "publisher_workers"

    worker_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    pid: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False)
    in_flight: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
