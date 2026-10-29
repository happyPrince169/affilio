"""Production reporting schemas (upload track U6). Mirrored in
packages/shared-types/src/analytics.ts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from affilio.core.enums import HandoffStatus, IngestSource, MediaAssetStatus


class ReportModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ReportPerson(ReportModel):
    id: uuid.UUID
    email: str
    display_name: str | None


class ReportCategory(ReportModel):
    id: uuid.UUID
    name: str
    slug: str | None


class ReportBucket(ReportModel):
    key: str
    label: str
    files: int
    submissions: int
    historical: int
    pending: int
    approved: int
    rejected: int
    corrected: int
    approved_units: int


class ProductionSummary(ReportModel):
    totals: ReportBucket
    by_owner: list[ReportBucket]
    by_editor: list[ReportBucket]
    by_status: dict[str, int]
    unknown_editor: int
    unknown_owner: int
    missing_files: int
    periods: list[str]
    period_source: Literal["ingestion"] = "ingestion"


class ProductionRow(ReportModel):
    asset_id: uuid.UUID
    title: str
    file_name: str | None
    asset_status: MediaAssetStatus
    production_period: str | None
    period_source: str
    ingest_source: IngestSource
    owner: ReportCategory | None
    owner_source: str | None
    uploader: ReportPerson | None
    attributed_editor: ReportPerson | None
    product_category: ReportCategory | None
    content_type: ReportCategory | None
    handoff_status: HandoffStatus
    reviewed_by: str | None
    reviewed_at: datetime | None
    review_note: str | None
    submitted_at: datetime
    completed_at: datetime | None
    upload_id: uuid.UUID | None
    revision_of_asset_id: uuid.UUID | None
    work_root_id: uuid.UUID
    revision_index: int
    chain_length: int
    is_latest_in_chain: bool
    approved_unit: bool


class ProductionRowsPage(BaseModel):
    items: list[ProductionRow]
    total: int
    page: int
    page_size: int


class ProductionPeriod(BaseModel):
    period: str
    files: int
    submissions: int
