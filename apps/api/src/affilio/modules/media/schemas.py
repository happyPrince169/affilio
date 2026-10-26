"""Media API schemas. Mirrored in packages/shared-types/src/media.ts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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

Orientation = Literal["vertical", "horizontal", "square"]
SortKey = Literal["newest", "oldest", "title", "duration", "size", "last_seen"]


class MediaModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class MediaRootStatus(MediaModel):
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


class MediaAssetSummary(MediaModel):
    id: uuid.UUID
    title: str
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
