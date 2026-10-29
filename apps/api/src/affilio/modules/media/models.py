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
    CategoryAssignmentSource,
    CategoryFacet,
    CategorySuggestionStatus,
    ExportJobStatus,
    ExportTrigger,
    HandoffStatus,
    IngestSource,
    MediaAssetKind,
    MediaAssetStatus,
    MediaRootAccess,
    MediaRootKind,
    MediaVariantKind,
    MediaVariantStatus,
    OwnerMappingStatus,
    ScanJobStatus,
    ScanTrigger,
    ThumbnailStatus,
    UploadStatus,
)
from affilio.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from affilio.db.types import str_enum
from affilio.modules.identity.models import User
from affilio.modules.taxonomy import service as taxonomy_service
from affilio.modules.taxonomy.models import ContentCategory


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
        # Production reporting (U6): finals by period, and revision chains.
        Index(
            "ix_media_assets_organization_id_asset_kind_production_period",
            "organization_id",
            "asset_kind",
            "production_period",
        ),
        Index("ix_media_assets_revision_of_asset_id", "revision_of_asset_id"),
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
    # Set whenever the folder-derived classification changes (publications compare it with
    # their approved snapshot to flag stale approvals). M3A.1.
    classification_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # "YYYY-MM" (UTC) of the first ingestion. Written once when the asset is created; rescans
    # and moves never change it (facet classification).
    production_period: Mapped[str | None] = mapped_column(String(7), nullable=True)

    category_assignments: Mapped[list[MediaContentCategory]] = relationship(
        back_populates="asset", cascade="all, delete-orphan"
    )
    # How the asset entered the library and, for uploads, who delivered it (upload track U4).
    # Written once at registration; there is no update path (attribution history).
    ingest_source: Mapped[IngestSource] = mapped_column(
        str_enum(IngestSource, "ingest_source"),
        nullable=False,
        default=IngestSource.SCAN,
        server_default=text("'scan'"),
    )
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    # A corrected submission points at the rejected final it replaces; the original stays.
    revision_of_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True
    )
    variants: Mapped[list[MediaFileVariant]] = relationship(
        back_populates="asset", cascade="all, delete-orphan", order_by="MediaFileVariant.kind"
    )
    duplicate_of: Mapped[MediaAsset | None] = relationship(
        remote_side="MediaAsset.id", foreign_keys="MediaAsset.duplicate_of_id"
    )

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
        # One export per (asset, recipe): a re-export replaces the file and the row in place.
        Index(
            "uq_media_file_variants_asset_export_recipe",
            "asset_id",
            "recipe_key",
            unique=True,
            postgresql_where=text("kind = 'export'"),
        ),
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

    # Provenance of derived files (E1 exports): which variant they were rendered from, with
    # which fixed recipe, and the exact source identity at render time. A consumer must treat
    # the export as stale when the source's size, mtime or fingerprint no longer match.
    derived_from_variant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_file_variants.id", ondelete="SET NULL"),
        nullable=True,
    )
    recipe_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_modified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

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


class MediaExportJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One request to render a publish-ready export of a final (E1).

    Doubles as the export worker's queue (status=queued). The output is a `media_file_variants`
    row of kind `export` on the same asset; no new asset is ever created. `recipe` records the
    fixed, versioned ffmpeg invocation (placeholders instead of paths) and the ffmpeg version.
    """

    __tablename__ = "media_export_jobs"
    __table_args__ = (
        Index("ix_media_export_jobs_organization_id_created_at", "organization_id", "created_at"),
        Index("ix_media_export_jobs_status_next_attempt_at", "status", "next_attempt_at"),
        Index("ix_media_export_jobs_media_asset_id", "media_asset_id"),
        # At most one queued/running job per (asset, recipe).
        Index(
            "uq_media_export_jobs_active_asset_recipe",
            "media_asset_id",
            "recipe_key",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    media_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False
    )
    source_variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_file_variants.id", ondelete="CASCADE"),
        nullable=False,
    )
    output_variant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_file_variants.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[ExportJobStatus] = mapped_column(
        str_enum(ExportJobStatus, "export_job_status"),
        nullable=False,
        default=ExportJobStatus.QUEUED,
    )
    trigger: Mapped[ExportTrigger] = mapped_column(
        str_enum(ExportTrigger, "export_trigger"), nullable=False, default=ExportTrigger.MANUAL
    )
    recipe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    recipe: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # Source identity captured when the job starts; the output variant copies it.
    source_file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_modified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)  # always redacted
    stats: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    asset: Mapped[MediaAsset] = relationship(foreign_keys=[media_asset_id])
    source_variant: Mapped[MediaFileVariant] = relationship(foreign_keys=[source_variant_id])
    output_variant: Mapped[MediaFileVariant | None] = relationship(foreign_keys=[output_variant_id])

    @property
    def is_active(self) -> bool:
        return self.status in {ExportJobStatus.QUEUED, ExportJobStatus.RUNNING}

    def __repr__(self) -> str:
        return f"<MediaExportJob status={self.status} recipe={self.recipe_key}>"


# ---------------------------------------------------------------- classification (M3A.1)


class MediaContentCategory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A category assigned to a media asset with its provenance. One primary per asset."""

    __tablename__ = "media_content_categories"
    __table_args__ = (
        UniqueConstraint(
            "media_asset_id", "category_id", name="uq_media_content_categories_asset_category"
        ),
        Index(
            "uq_media_content_categories_primary",
            "media_asset_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
        Index("ix_media_content_categories_org_category", "organization_id", "category_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    media_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_categories.id", ondelete="RESTRICT"), nullable=False
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    assignment_source: Mapped[CategoryAssignmentSource] = mapped_column(
        str_enum(CategoryAssignmentSource, "category_assignment_source"), nullable=False
    )

    asset: Mapped[MediaAsset] = relationship(back_populates="category_assignments")
    category: Mapped[ContentCategory] = relationship()


class FolderCategoryRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Maps a folder (relative to a FINAL root) to a category. Paths are normalized POSIX
    paths without leading/trailing slashes; "" means the root itself."""

    __tablename__ = "folder_category_rules"
    __table_args__ = (
        UniqueConstraint(
            "media_root_id", "relative_folder_path", name="uq_folder_category_rules_root_path"
        ),
        Index("ix_folder_category_rules_organization_id", "organization_id"),
        Index("ix_folder_category_rules_category_id", "category_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    media_root_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_roots.id", ondelete="CASCADE"), nullable=False
    )
    relative_folder_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_categories.id", ondelete="RESTRICT"), nullable=False
    )
    include_subfolders: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    root: Mapped[MediaRoot] = relationship()
    category: Mapped[ContentCategory] = relationship()


class CategorySuggestion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A facet value named by the folders of a valid final that matches no category yet. One
    row per (organization, facet, normalized name); created by the scanner, decided by an admin
    (facet classification). Approval links `category_id`; rejection keeps the row so the scanner
    does not raise it again."""

    __tablename__ = "category_suggestions"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "facet",
            "normalized_name",
            name="uq_category_suggestions_org_facet_name",
        ),
        Index("ix_category_suggestions_organization_id_status", "organization_id", "status"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    media_root_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_roots.id", ondelete="CASCADE"), nullable=False
    )
    facet: Mapped[CategoryFacet] = mapped_column(
        str_enum(CategoryFacet, "category_facet"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)  # folder name as seen
    normalized_name: Mapped[str] = mapped_column(String(160), nullable=False)
    relative_folder_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    example_relative_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[CategorySuggestionStatus] = mapped_column(
        str_enum(CategorySuggestionStatus, "category_suggestion_status"),
        nullable=False,
        default=CategorySuggestionStatus.PENDING,
        server_default=text("'pending'"),
    )
    seen_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )  # finals under the folder in the most recent scan
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_categories.id", ondelete="SET NULL"), nullable=True
    )

    root: Mapped[MediaRoot] = relationship()
    category: Mapped[ContentCategory | None] = relationship()

    def __repr__(self) -> str:
        return f"<CategorySuggestion facet={self.facet} name={self.name!r} status={self.status}>"


class MediaOwnerMapping(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Authorizes a member to submit finals for one production owner (upload track U2).
    Several active rows per member are allowed; revoked rows are kept for attribution
    history and reactivated instead of duplicated."""

    __tablename__ = "media_owner_mappings"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "user_id",
            "production_owner_category_id",
            name="uq_media_owner_mappings_org_user_category",
        ),
        Index("ix_media_owner_mappings_organization_id_user_id", "organization_id", "user_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    production_owner_category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_categories.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[OwnerMappingStatus] = mapped_column(
        str_enum(OwnerMappingStatus, "owner_mapping_status"),
        nullable=False,
        default=OwnerMappingStatus.ACTIVE,
        server_default=text("'active'"),
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    user: Mapped[User] = relationship(foreign_keys=[user_id])
    category: Mapped[ContentCategory] = relationship()

    def __repr__(self) -> str:
        return f"<MediaOwnerMapping user={self.user_id} owner={self.production_owner_category_id}>"


class MediaUpload(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One resumable upload session per file (upload track U4, ADR 0012). Attribution
    (uploader, attributed editor, owner id and name snapshot, taxonomy ids) is written at
    creation and never updated; the state machine and recovery metadata live here."""

    __tablename__ = "media_uploads"
    __table_args__ = (
        Index(
            "ix_media_uploads_organization_id_uploaded_by_user_id_status",
            "organization_id",
            "uploaded_by_user_id",
            "status",
        ),
        Index("ix_media_uploads_status_updated_at", "status", "updated_at"),
        # Two sessions with identical content converge on one asset: the second one to claim
        # the finalizing state fails this index and becomes a duplicate.
        Index(
            "uq_media_uploads_org_verified_sha256_finalized",
            "organization_id",
            "verified_sha256",
            unique=True,
            postgresql_where=text("status IN ('finalizing', 'completed')"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    uploaded_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    submitted_for_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    production_owner_category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_categories.id", ondelete="RESTRICT"), nullable=False
    )
    production_owner_name: Mapped[str] = mapped_column(String(120), nullable=False)
    product_category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_categories.id", ondelete="RESTRICT"), nullable=False
    )
    content_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_categories.id", ondelete="RESTRICT"), nullable=False
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    replaces_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="RESTRICT"), nullable=True
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    safe_filename: Mapped[str] = mapped_column(String(160), nullable=False)
    expected_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    client_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chunk_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    committed_offset: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    last_chunk_offset: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_chunk_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_chunk_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # SHA-256 of every committed chunk, indexed by offset // chunk_bytes (resume identity, U7):
    # a reselected file must reproduce every digest before more bytes are accepted.
    chunk_manifest: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    staging_name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    status: Mapped[UploadStatus] = mapped_column(
        str_enum(UploadStatus, "upload_status"),
        nullable=False,
        default=UploadStatus.CREATED,
        server_default=text("'created'"),
    )
    verified_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    final_relative_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    media_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True
    )
    duplicate_of_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True
    )
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    finalize_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    production_owner: Mapped[ContentCategory] = relationship(
        foreign_keys=[production_owner_category_id]
    )
    product_category: Mapped[ContentCategory] = relationship(foreign_keys=[product_category_id])
    content_type: Mapped[ContentCategory] = relationship(foreign_keys=[content_type_id])
    media_asset: Mapped[MediaAsset | None] = relationship(foreign_keys=[media_asset_id])

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_UPLOAD_STATUSES

    def __repr__(self) -> str:
        return f"<MediaUpload id={self.id} status={self.status} offset={self.committed_offset}>"


TERMINAL_UPLOAD_STATUSES = frozenset(
    {
        UploadStatus.COMPLETED,
        UploadStatus.DUPLICATE,
        UploadStatus.FAILED,
        UploadStatus.ABORTED,
        UploadStatus.EXPIRED,
    }
)


taxonomy_service.register_reference("media_content_categories", "category_id")
taxonomy_service.register_reference("folder_category_rules", "category_id")
taxonomy_service.register_reference("media_owner_mappings", "production_owner_category_id")
taxonomy_service.register_reference("media_uploads", "production_owner_category_id")
taxonomy_service.register_reference("media_uploads", "product_category_id")
taxonomy_service.register_reference("media_uploads", "content_type_id")
