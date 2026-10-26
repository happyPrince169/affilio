"""Media API schemas. Mirrored in packages/shared-types/src/media.ts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from affilio.core.enums import (
    CategoryAssignmentSource,
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

Orientation = Literal["vertical", "horizontal", "square"]
SortKey = Literal["newest", "oldest", "title", "duration", "size", "last_seen"]


class MediaModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class MediaRootStatus(MediaModel):
    id: uuid.UUID
    key: str
    kind: MediaRootKind
    access: MediaRootAccess
    name: str
    configured: bool
    exists: bool
    readable: bool
    writable: bool
    scan_enabled: bool
    last_scan_at: datetime | None = None


class MediaVariantResponse(MediaModel):
    id: uuid.UUID
    kind: MediaVariantKind
    status: MediaVariantStatus
    root_key: str
    relative_path: str
    file_name: str
    extension: str
    file_size: int
    modified_at: datetime
    content_hash: str | None = None
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    frame_rate: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    pixel_format: str | None = None
    bit_depth: int | None = None
    bit_rate: int | None = None
    container_format: str | None = None
    rotation: int | None = None
    probed_at: datetime | None = None
    probe_error: str | None = None
    last_seen_at: datetime
    missing_since: datetime | None = None


class AssetCategoryResponse(MediaModel):
    category_id: uuid.UUID
    slug: str
    name: str
    path: str
    is_primary: bool
    assignment_source: CategoryAssignmentSource


class MediaAssetSummary(MediaModel):
    id: uuid.UUID
    title: str
    folder_path: str | None = None
    categories: list[AssetCategoryResponse] = Field(default_factory=list)
    classification_changed_at: datetime | None = None
    status: MediaAssetStatus
    asset_kind: MediaAssetKind = MediaAssetKind.FOOTAGE
    handoff_status: HandoffStatus | None = None
    orientation: Orientation | None = None
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    frame_rate: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    pixel_format: str | None = None
    bit_depth: int | None = None
    bit_rate: int | None = None
    container_format: str | None = None
    file_size: int | None = None
    relative_path: str | None = None
    file_name: str | None = None
    has_proxy: bool = False
    thumbnail_status: ThumbnailStatus
    thumbnail_url: str | None = None
    duplicate_of_id: uuid.UUID | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    missing_since: datetime | None = None
    created_at: datetime
    updated_at: datetime


class MediaAssetDetail(MediaAssetSummary):
    handoff_note: str | None = None
    handoff_reviewed_at: datetime | None = None
    handoff_reviewed_by: str | None = None
    probe_error: str | None = None
    thumbnail_error: str | None = None
    thumbnail_attempts: int = 0
    rotation: int | None = None
    variants: list[MediaVariantResponse] = Field(default_factory=list)
    duplicates: list[uuid.UUID] = Field(default_factory=list)


class PaginatedAssets(MediaModel):
    items: list[MediaAssetSummary]
    page: int
    page_size: int
    total: int
    total_pages: int


class MediaStats(MediaModel):
    total: int
    footage: int = 0
    finals: int = 0
    finals_pending_review: int = 0
    finals_approved: int = 0
    finals_rejected: int = 0
    active: int
    missing: int
    unsupported: int
    duplicates: int
    with_proxy: int
    thumbnails_pending: int
    thumbnails_ready: int
    thumbnails_failed: int
    total_bytes: int
    total_duration_seconds: float


class ScanErrorEntry(MediaModel):
    kind: str
    path: str
    message: str


class ScanJobResponse(MediaModel):
    id: uuid.UUID
    root_key: str
    status: ScanJobStatus
    trigger: ScanTrigger
    attempt: int
    max_attempts: int
    full_rescan: bool
    requested_by: str | None = None
    next_attempt_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    heartbeat_at: datetime | None = None
    created_at: datetime
    stats: dict[str, Any] = Field(default_factory=dict)
    errors: list[ScanErrorEntry] = Field(default_factory=list)
    error: str | None = None


class ScanStatusResponse(MediaModel):
    current: ScanJobResponse | None = None
    last_completed: ScanJobResponse | None = None
    roots: list[MediaRootStatus]
    worker_hint: str


class HandoffDecision(BaseModel):
    """Human review of a final video. Approval is required before publishing (M3)."""

    model_config = ConfigDict(extra="forbid")

    status: HandoffStatus
    note: str | None = Field(default=None, max_length=5_000)


class CreateScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root_key: str = "originals"
    full_rescan: bool = False


# ---------------------------------------------------------------- classification (M3A.1)


class AssetCategoriesUpdate(BaseModel):
    """Manual override. `primary_category_id=None` hands primacy back to the folder rule."""

    model_config = ConfigDict(extra="forbid")

    primary_category_id: uuid.UUID | None = None
    secondary_category_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)

    @field_validator("secondary_category_ids")
    @classmethod
    def _unique(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        return list(dict.fromkeys(value))


class FolderRuleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_root_id: uuid.UUID
    relative_folder_path: str = Field(max_length=1024)
    category_id: uuid.UUID
    include_subfolders: bool = True
    priority: int = Field(default=0, ge=0, le=1000)
    is_active: bool = True


class FolderRuleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relative_folder_path: str | None = Field(default=None, max_length=1024)
    category_id: uuid.UUID | None = None
    include_subfolders: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=1000)
    is_active: bool | None = None


class FolderRuleResponse(MediaModel):
    id: uuid.UUID
    media_root_id: uuid.UUID
    root_key: str
    relative_folder_path: str
    category_id: uuid.UUID
    category_name: str
    category_path: str
    include_subfolders: bool
    priority: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class FolderPreviewResponse(MediaModel):
    relative_folder_path: str
    rule: FolderRuleResponse | None
    category_id: uuid.UUID
    category_name: str
    fallback: bool
    candidates: list[FolderRuleResponse]


class FolderReportEntry(MediaModel):
    media_root_id: uuid.UUID
    root_key: str
    folder: str
    file_count: int
    rule: FolderRuleResponse | None
    mapped: bool


class CategoryCountEntry(MediaModel):
    category_id: uuid.UUID
    primary_count: int
