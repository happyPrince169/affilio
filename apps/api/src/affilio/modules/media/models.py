"""Media library tables: media_roots, media_assets, media_file_variants, media_scan_jobs.

Design notes
- Absolute filesystem paths are never stored. A variant is (media_root_id, relative_path) and the
  root's path is bound at runtime from settings (see `roots.py`).
- Assets are keyed by their ORIGINAL file. Proxies, thumbnails and exports are variants of it.
- Enum-like columns are VARCHAR + CHECK (see affilio.db.types).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from affilio.core.enums import (
    HandoffStatus,
    MediaAssetKind,
    MediaAssetStatus,
    MediaRootAccess,
    MediaRootKind,
    MediaVariantKind,
    MediaVariantStatus,
    ScanJobStatus,
    ScanTrigger,
    ThumbnailStatus,
)
from affilio.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from affilio.db.types import str_enum


class MediaRoot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A logical media location owned by an organization. Its path lives in configuration."""

    __tablename__ = "media_roots"
    __table_args__ = (UniqueConstraint("organization_id", "key"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[MediaRootKind] = mapped_column(
        str_enum(MediaRootKind, "media_root_kind"), nullable=False
    )
    access: Mapped[MediaRootAccess] = mapped_column(
        str_enum(MediaRootAccess, "media_root_access"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    scan_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    variants: Mapped[list[MediaFileVariant]] = relationship(back_populates="root")
    scan_jobs: Mapped[list[MediaScanJob]] = relationship(back_populates="root")

    def __repr__(self) -> str:
        return f"<MediaRoot key={self.key!r} kind={self.kind}>"


class MediaAsset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One piece of source media (keyed by its original file) with denormalized metadata."""

    __tablename__ = "media_assets"
    __table_args__ = (
        Index("ix_media_assets_organization_id_status", "organization_id", "status"),
        Index("ix_media_assets_organization_id_created_at", "organization_id", "created_at"),
        Index("ix_media_assets_thumbnail_status", "thumbnail_status"),
        Index("ix_media_assets_organization_id_asset_kind", "organization_id", "asset_kind"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[MediaAssetStatus] = mapped_column(
        str_enum(MediaAssetStatus, "media_asset_status"),
        nullable=False,
        default=MediaAssetStatus.ACTIVE,
    )
    # footage (originals root) or final (finals root). Independent indexes, never merged.
    asset_kind: Mapped[MediaAssetKind] = mapped_column(
        str_enum(MediaAssetKind, "media_asset_kind"),
        nullable=False,
        default=MediaAssetKind.FOOTAGE,
        server_default=text("'footage'"),
    )
    # Human review gate for finals; NULL for footage.
    handoff_status: Mapped[HandoffStatus | None] = mapped_column(
        str_enum(HandoffStatus, "handoff_status"), nullable=True
    )
    handoff_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    handoff_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    handoff_reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True
    )

    # Technical metadata of the ORIGINAL, denormalized for listing/filtering.
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frame_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    video_codec: Mapped[str | None] = mapped_column(String(64), nullable=True)
    audio_codec: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pixel_format: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bit_depth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bit_rate: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    container_format: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rotation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    probe_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    thumbnail_status: Mapped[ThumbnailStatus] = mapped_column(
        str_enum(ThumbnailStatus, "thumbnail_status"),
        nullable=False,
        default=ThumbnailStatus.PENDING,
    )
    thumbnail_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    thumbnail_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    missing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    variants: Mapped[list[MediaFileVariant]] = relationship(
        back_populates="asset", cascade="all, delete-orphan", order_by="MediaFileVariant.kind"
    )
    duplicate_of: Mapped[MediaAsset | None] = relationship(remote_side="MediaAsset.id")

    @property
    def orientation(self) -> str | None:
        if not self.width or not self.height:
            return None
        if self.width == self.height:
            return "square"
        return "vertical" if self.height > self.width else "horizontal"

    @property
    def main_variant_kind(self) -> MediaVariantKind:
        return (
            MediaVariantKind.FINAL
            if self.asset_kind == MediaAssetKind.FINAL
            else MediaVariantKind.ORIGINAL
        )

    def main_variant(self) -> MediaFileVariant | None:
        return self.variant(self.main_variant_kind)

    def variant(self, kind: MediaVariantKind) -> MediaFileVariant | None:
        for item in self.variants:
            if item.kind == kind:
                return item
        return None

    def __repr__(self) -> str:
        return f"<MediaAsset title={self.title!r} status={self.status}>"


class MediaFileVariant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A concrete file belonging to an asset, located as (root, relative_path)."""

    __tablename__ = "media_file_variants"
    __table_args__ = (
        UniqueConstraint("media_root_id", "relative_path", name="uq_media_file_variants_root_path"),
        Index("ix_media_file_variants_asset_id", "asset_id"),
        Index("ix_media_file_variants_root_fingerprint", "media_root_id", "fingerprint"),
        Index("ix_media_file_variants_content_hash", "content_hash"),
    )

    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False
    )
    media_root_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_roots.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[MediaVariantKind] = mapped_column(
        str_enum(MediaVariantKind, "media_variant_kind"), nullable=False
    )
    status: Mapped[MediaVariantStatus] = mapped_column(
        str_enum(MediaVariantStatus, "media_variant_status"),
        nullable=False,
        default=MediaVariantStatus.PRESENT,
    )
    # POSIX-style path relative to the root; validated on write, never user-supplied.
    relative_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    extension: Mapped[str] = mapped_column(String(16), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Cheap identity: sha256(size + sampled head/middle/tail). Full hash only when needed.
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frame_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    video_codec: Mapped[str | None] = mapped_column(String(64), nullable=True)
    audio_codec: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pixel_format: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bit_depth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bit_rate: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    container_format: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rotation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    probe: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    probed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    probe_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    missing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    asset: Mapped[MediaAsset] = relationship(back_populates="variants")
    root: Mapped[MediaRoot] = relationship(back_populates="variants")

    def __repr__(self) -> str:
        return f"<MediaFileVariant kind={self.kind} path={self.relative_path!r}>"


class MediaScanJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A scan of one media root. Doubles as the worker's queue (status=queued)."""

    __tablename__ = "media_scan_jobs"
    __table_args__ = (
        Index("ix_media_scan_jobs_organization_id_created_at", "organization_id", "created_at"),
        Index("ix_media_scan_jobs_status_next_attempt_at", "status", "next_attempt_at"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    media_root_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_roots.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[ScanJobStatus] = mapped_column(
        str_enum(ScanJobStatus, "scan_job_status"), nullable=False, default=ScanJobStatus.QUEUED
    )
    trigger: Mapped[ScanTrigger] = mapped_column(
        str_enum(ScanTrigger, "scan_trigger"), nullable=False, default=ScanTrigger.MANUAL
    )
    attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default=text("3")
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stats: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    errors: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    full_rescan: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    root: Mapped[MediaRoot] = relationship(back_populates="scan_jobs")

    @property
    def is_active(self) -> bool:
        return self.status in {ScanJobStatus.QUEUED, ScanJobStatus.RUNNING}

    def __repr__(self) -> str:
        return f"<MediaScanJob status={self.status} attempt={self.attempt}>"
