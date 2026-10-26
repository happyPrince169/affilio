"""Publishing schemas. Mirrored in packages/shared-types/src/publishing.ts."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from affilio.core.enums import (
    CampaignStatus,
    EvidenceKind,
    Platform,
    PublicationAttemptStatus,
    PublicationStatus,
    PublishMode,
)
from affilio.modules.products.urls import validate_http_url

MAX_HASHTAGS = 30
MAX_LINKS = 10


class PubModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


def _clean_hashtags(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for raw in values:
        tag = raw.strip().lstrip("#").strip()
        if not tag:
            continue
        if any(ch.isspace() for ch in tag):
            msg = f"hashtag '{raw}' must not contain spaces"
            raise ValueError(msg)
        if tag.lower() not in {t.lower() for t in cleaned}:
            cleaned.append(tag)
    if len(cleaned) > MAX_HASHTAGS:
        msg = f"at most {MAX_HASHTAGS} hashtags"
        raise ValueError(msg)
    return cleaned


def _to_utc(value: datetime | None) -> datetime | None:
    return value.astimezone(UTC) if value is not None else None


# ------------------------------------------------------------------ campaigns


class _CampaignRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("name", check_fields=False)
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            msg = "name must not be blank"
            raise ValueError(msg)
        return value.strip()


class _UtcRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("scheduled_at", "published_at", check_fields=False)
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return _to_utc(value)


class CampaignCreate(_CampaignRules):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5_000)
    starts_on: date | None = None
    ends_on: date | None = None
    notes: str | None = Field(default=None, max_length=5_000)
    media_asset_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    social_account_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def _dates(self) -> CampaignCreate:
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            msg = "ends_on must not be before starts_on"
            raise ValueError(msg)
        return self


class CampaignUpdate(_CampaignRules):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5_000)
    status: CampaignStatus | None = None
    starts_on: date | None = None
    ends_on: date | None = None
    notes: str | None = Field(default=None, max_length=5_000)


class CampaignMediaAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_asset_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)


class CampaignTargetsAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    social_account_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)


class CampaignMediaResponse(PubModel):
    media_asset_id: uuid.UUID
    title: str
    file_name: str | None
    handoff_status: str | None
    asset_kind: str
    duration_seconds: float | None
    width: int | None
    height: int | None
    thumbnail_url: str | None
    added_at: datetime


class CampaignTargetResponse(PubModel):
    social_account_id: uuid.UUID
    platform: Platform
    display_name: str
    handle: str | None
    status: str
    is_verified: bool
    connection_label: str


class CampaignSummary(PubModel):
    id: uuid.UUID
    name: str
    description: str | None
    status: CampaignStatus
    starts_on: date | None
    ends_on: date | None
    media_count: int
    target_count: int
    publication_counts: dict[str, int]
    created_at: datetime
    updated_at: datetime


class CampaignDetail(CampaignSummary):
    notes: str | None
    media: list[CampaignMediaResponse]
    targets: list[CampaignTargetResponse]
    publications: list[PublicationSummary]


class PaginatedCampaigns(PubModel):
    items: list[CampaignSummary]
    page: int
    page_size: int
    total: int
    total_pages: int


# ------------------------------------------------------------------ publications


class PublicationContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=255)
    caption: str | None = Field(default=None, max_length=10_000)
    cta: str | None = Field(default=None, max_length=255)
    hashtags: list[str] = Field(default_factory=list, max_length=60)
    link_ids: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_LINKS)
    content_notes: str | None = Field(default=None, max_length=5_000)

    @field_validator("hashtags")
    @classmethod
    def _tags(cls, value: list[str]) -> list[str]:
        return _clean_hashtags(value)

    @field_validator("link_ids")
    @classmethod
    def _links(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(set(value)) != len(value):
            msg = "link_ids must be unique"
            raise ValueError(msg)
        return value


class PublicationCreate(PublicationContent, _UtcRules):
    campaign_id: uuid.UUID
    media_asset_id: uuid.UUID
    social_account_id: uuid.UUID
    scheduled_at: AwareDatetime | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class PublicationBatchItem(PublicationContent, _UtcRules):
    social_account_id: uuid.UUID
    scheduled_at: AwareDatetime | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class PublicationBatchCreate(BaseModel):
    """Composer: one media asset, unique content per target."""

    model_config = ConfigDict(extra="forbid")

    campaign_id: uuid.UUID
    media_asset_id: uuid.UUID
    items: list[PublicationBatchItem] = Field(min_length=1, max_length=50)

    @field_validator("items")
    @classmethod
    def _unique_targets(cls, value: list[PublicationBatchItem]) -> list[PublicationBatchItem]:
        ids = [i.social_account_id for i in value]
        if len(set(ids)) != len(ids):
            msg = "each target may appear only once"
            raise ValueError(msg)
        return value


class PublicationContentUpdate(BaseModel):
    """Partial content edit; `version` is the optimistic-lock token the client last saw."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=255)
    caption: str | None = Field(default=None, max_length=10_000)
    cta: str | None = Field(default=None, max_length=255)
    hashtags: list[str] | None = Field(default=None, max_length=60)
    link_ids: list[uuid.UUID] | None = Field(default=None, max_length=MAX_LINKS)
    content_notes: str | None = Field(default=None, max_length=5_000)

    @field_validator("hashtags")
    @classmethod
    def _tags(cls, value: list[str] | None) -> list[str] | None:
        return _clean_hashtags(value) if value is not None else None

    @field_validator("link_ids")
    @classmethod
    def _links(cls, value: list[uuid.UUID] | None) -> list[uuid.UUID] | None:
        if value is not None and len(set(value)) != len(value):
            msg = "link_ids must be unique"
            raise ValueError(msg)
        return value


class VersionedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=2_000)


class ScheduleRequest(VersionedRequest, _UtcRules):
    scheduled_at: AwareDatetime
    allow_conflict: bool = False


class EvidenceRequest(VersionedRequest, _UtcRules):
    """Operator-reported evidence that the post exists. Never treated as API-verified."""

    external_post_url: str = Field(min_length=1, max_length=2048)
    external_post_id: str | None = Field(default=None, max_length=255)
    published_at: AwareDatetime | None = None

    @field_validator("external_post_url")
    @classmethod
    def _url(cls, value: str) -> str:
        return validate_http_url(value, field="external_post_url")

    @field_validator("external_post_id")
    @classmethod
    def _pid(cls, value: str | None) -> str | None:
        cleaned = (value or "").strip()
        return cleaned or None


class FailureRequest(VersionedRequest):
    reason: str = Field(min_length=1, max_length=2_000)
    outcome: Literal["failed", "ambiguous"] = "failed"


class ReconcileRequest(VersionedRequest, _UtcRules):
    """Resolve an ambiguous outcome. `published` requires evidence; nothing is retried."""

    outcome: Literal["published", "not_published", "still_unknown"]
    external_post_url: str | None = Field(default=None, max_length=2048)
    external_post_id: str | None = Field(default=None, max_length=255)
    published_at: AwareDatetime | None = None

    @field_validator("external_post_url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return (
            validate_http_url(value, field="external_post_url") if value and value.strip() else None
        )

    @field_validator("published_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return _to_utc(value)

    @model_validator(mode="after")
    def _needs_evidence(self) -> ReconcileRequest:
        if self.outcome == "published" and not self.external_post_url:
            msg = "external_post_url is required to reconcile as published"
            raise ValueError(msg)
        if self.outcome == "not_published" and not (self.note or "").strip():
            msg = "a note is required to reconcile as not published"
            raise ValueError(msg)
        return self


class PublicationLinkResponse(PubModel):
    affiliate_link_id: uuid.UUID
    position: int
    label: str
    url: str
    platform: Platform
    status: str


class ValidationResponse(PubModel):
    ok: bool
    errors: list[str]
    warnings: list[str]


class PublicationSnapshotResponse(PubModel):
    id: uuid.UUID
    content_version: int
    title: str | None
    caption: str | None
    cta: str | None
    hashtags: list[str]
    links: list[dict[str, Any]]
    media: dict[str, Any]
    target: dict[str, Any]
    approved_by_user_id: uuid.UUID | None
    approved_by_label: str
    approved_at: datetime


class PublicationAttemptResponse(PubModel):
    id: uuid.UUID
    attempt_no: int
    mode: PublishMode
    adapter: str
    status: PublicationAttemptStatus
    snapshot_id: uuid.UUID | None
    started_by_label: str
    started_at: datetime
    finished_at: datetime | None
    detail: dict[str, Any]
    error: str | None


class PublicationSummary(PubModel):
    id: uuid.UUID
    intent_id: uuid.UUID
    campaign_id: uuid.UUID
    campaign_name: str
    media_asset_id: uuid.UUID
    media_title: str
    media_file_name: str | None
    thumbnail_url: str | None
    social_account_id: uuid.UUID
    account_display_name: str
    account_connection_label: str
    platform: Platform
    status: PublicationStatus
    version: int
    content_version: int
    title: str | None
    caption_preview: str | None
    scheduled_at: datetime | None
    published_at: datetime | None
    external_post_url: str | None
    evidence_kind: EvidenceKind | None
    has_approved_snapshot: bool
    approval_stale: bool
    classification_stale: bool = False
    # Current routing verdict for the target (None = eligible). Re-checked on every read.
    target_blocked_reason: str | None = None
    revalidation_required: bool = False
    revalidated_at: datetime | None = None
    revalidated_by_label: str | None = None
    link_count: int
    attempt_count: int
    last_error: str | None
    status_changed_at: datetime
    created_at: datetime
    updated_at: datetime


class PublicationDetail(PublicationSummary):
    caption: str | None
    cta: str | None
    hashtags: list[str]
    content_notes: str | None
    idempotency_key: str | None
    links: list[PublicationLinkResponse]
    approved_snapshot: PublicationSnapshotResponse | None
    snapshots: list[PublicationSnapshotResponse]
    attempts: list[PublicationAttemptResponse]
    external_post_id: str | None
    confirmed_by_label: str | None
    confirmed_at: datetime | None
    confirmation_note: str | None
    validation: ValidationResponse
    allowed_actions: list[str]
    capabilities: dict[str, Any]


class PaginatedPublications(PubModel):
    items: list[PublicationSummary]
    page: int
    page_size: int
    total: int
    total_pages: int


class PublicationBatchResponse(PubModel):
    created: list[PublicationDetail]
    reused: list[PublicationDetail]


class PreparedPublicationResponse(PubModel):
    mode: PublishMode
    caption_text: str
    links: list[dict[str, Any]]
    checklist: list[dict[str, Any]]
    open_url: str | None
    instructions: list[str]
    intent_id: uuid.UUID
    snapshot_id: uuid.UUID
    media_file_name: str | None
    media_relative_path: str | None


class CalendarResponse(PubModel):
    timezone: str
    range_start: datetime
    range_end: datetime
    items: list[PublicationSummary]


class PublishingStats(PubModel):
    by_status: dict[str, int]
    due_now: int
    needs_attention: int
    campaigns_active: int
    accounts_registered: int
    links_active: int


class ReleaseDueResponse(PubModel):
    released: int
    publication_ids: list[uuid.UUID]


CampaignDetail.model_rebuild()
