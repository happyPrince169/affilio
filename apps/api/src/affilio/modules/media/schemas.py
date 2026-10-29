"""Media API schemas. Mirrored in packages/shared-types/src/media.ts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from affilio.core.enums import (
    CategoryAssignmentSource,
    CategoryFacet,
    CategoryStatus,
    CategorySuggestionStatus,
    ExportJobStatus,
    ExportTrigger,
    HandoffStatus,
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
    # Provenance of derived files (exports, E1): absent for scanned files.
    derived_from_variant_id: uuid.UUID | None = None
    recipe_key: str | None = None
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
    facet: CategoryFacet | None = None


class MediaAssetSummary(MediaModel):
    id: uuid.UUID
    title: str
    folder_path: str | None = None
    categories: list[AssetCategoryResponse] = Field(default_factory=list)
    classification_changed_at: datetime | None = None
    production_period: str | None = None  # "YYYY-MM" from the first ingestion (finals)
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


class ExportJobResponse(MediaModel):
    id: uuid.UUID
    media_asset_id: uuid.UUID
    status: ExportJobStatus
    trigger: ExportTrigger
    recipe_key: str
    attempt: int
    max_attempts: int
    requested_by: str | None = None
    error_code: str | None = None
    error: str | None = None
    stats: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    heartbeat_at: datetime | None = None


class AssetExportStatus(MediaModel):
    """Publish-ready export state of a final (E2)."""

    state: Literal[
        "not_needed", "not_generated", "queued", "processing", "ready", "failed", "blocked", "stale"
    ]
    detail: str | None = None
    job: ExportJobResponse | None = None
    export: MediaVariantResponse | None = None
    can_export: bool = False


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
    export: AssetExportStatus | None = None  # finals only (E2)


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


class RootScanState(MediaModel):
    """Scan state of one scannable root (originals, finals). M3C.1 step 2."""

    root_key: str
    current: ScanJobResponse | None = None
    last_completed: ScanJobResponse | None = None


class ScanStatusResponse(MediaModel):
    # `current` / `last_completed` stay the organization-wide view (any root) for callers
    # that predate per-root scanning; `by_root` carries the same per scannable root.
    current: ScanJobResponse | None = None
    last_completed: ScanJobResponse | None = None
    by_root: list[RootScanState] = Field(default_factory=list)
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


# ---------------------------------------------------------------- facet suggestions


class CategorySuggestionResponse(MediaModel):
    id: uuid.UUID
    facet: CategoryFacet
    name: str
    normalized_name: str
    root_key: str
    relative_folder_path: str
    example_relative_path: str | None
    status: CategorySuggestionStatus
    seen_count: int
    first_seen_at: datetime
    last_seen_at: datetime
    decided_at: datetime | None
    decided_by: str | None
    note: str | None
    category_id: uuid.UUID | None
    category_name: str | None


class SuggestionApprove(BaseModel):
    """Optional display name / slug for the new category. Either must still normalize to the
    folder name so later videos in that folder keep resolving to it."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=120)
    slug: str | None = Field(default=None, max_length=80)


class SuggestionReject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=2_000)


class SuggestionDecisionResponse(MediaModel):
    suggestion: CategorySuggestionResponse
    category_id: uuid.UUID | None
    reclassified_assets: int


# ---------------------------------------------------------------- owner mappings (upload track U2)


class OwnerMappingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    production_owner_category_id: uuid.UUID


class OwnerCategoryRef(BaseModel):
    """The production owner a mapping points at; `status` tells the UI about archived owners."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    status: CategoryStatus


class OwnerMappingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    user_email: str
    user_display_name: str | None
    production_owner: OwnerCategoryRef
    status: OwnerMappingStatus
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None
    created_by_user_id: uuid.UUID | None
    revoked_by_user_id: uuid.UUID | None


# ---------------------------------------------------------------- editor uploads (upload track U4)


class UploadCreate(BaseModel):
    """One resumable session per file. The server never uses `filename` as a path."""

    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)
    production_owner_category_id: uuid.UUID
    product_category_id: uuid.UUID
    content_type_id: uuid.UUID
    batch_id: uuid.UUID | None = None
    replaces_asset_id: uuid.UUID | None = None
    client_sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    # Admin only: the editor the work is attributed to. The authenticated uploader is always
    # recorded separately.
    submitted_for_user_id: uuid.UUID | None = None

    @field_validator("client_sha256")
    @classmethod
    def _lower(cls, value: str | None) -> str | None:
        return value.lower() if value else None


class UploadTaxonomyRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str


class UploadReview(BaseModel):
    """Handoff state of the registered asset, for the uploader's own list."""

    handoff_status: HandoffStatus
    handoff_note: str | None
    handoff_reviewed_at: datetime | None
    handoff_reviewed_by: str | None


class UploadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: UploadStatus
    original_filename: str
    expected_size: int
    chunk_bytes: int
    committed_offset: int
    production_owner: UploadTaxonomyRef
    production_owner_name: str
    product_category: UploadTaxonomyRef
    content_type: UploadTaxonomyRef
    batch_id: uuid.UUID | None
    replaces_asset_id: uuid.UUID | None
    uploaded_by_user_id: uuid.UUID
    submitted_for_user_id: uuid.UUID | None
    media_asset_id: uuid.UUID | None
    # Set only for `duplicate`; never carries the other asset's filename, path or owner.
    duplicate_of_asset_id: uuid.UUID | None
    failure_code: str | None
    failure_detail: str | None
    finalize_attempts: int
    review: UploadReview | None
    created_at: datetime
    updated_at: datetime
    uploaded_at: datetime | None
    completed_at: datetime | None
    expires_at: datetime


class UploadChunkResponse(BaseModel):
    id: uuid.UUID
    status: UploadStatus
    committed_offset: int
    expected_size: int
    replayed: bool


class UploadLimitsResponse(BaseModel):
    chunk_bytes: int
    max_file_bytes: int
    max_active_sessions_per_user: int
    session_ttl_hours: int
    default_concurrency: int = 2


class UploadOptionsResponse(BaseModel):
    facets_enabled: bool
    can_submit_for_others: bool
    owners: list[UploadTaxonomyRef]
    product_categories: list[UploadTaxonomyRef]
    content_types: list[UploadTaxonomyRef]
    allowed_extensions: list[str]
    limits: UploadLimitsResponse
