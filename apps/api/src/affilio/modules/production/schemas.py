"""Production API schemas. Mirrored in packages/shared-types/src/production.ts."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from affilio.core.enums import EditingStatus, ProductionMediaRole, ProductionStatus
from affilio.modules.media.schemas import MediaAssetSummary
from affilio.modules.procurement.schemas import ProductRef

ProductionSortKey = Literal["planned", "deadline", "newest", "updated"]


class ProdModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


def _labels(value: list[str]) -> list[str]:
    cleaned: list[str] = []
    for item in value:
        label = item.strip()
        if label and label not in cleaned:
            cleaned.append(label[:80])
    return cleaned[:20]


class ProductionJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)
    planned_filming_date: date | None = None
    deadline: date | None = None
    staff_labels: list[str] = Field(default_factory=list, max_length=20)
    notes: str | None = Field(default=None, max_length=10_000)
    product_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)

    @field_validator("title")
    @classmethod
    def _title(cls, value: str) -> str:
        if not value.strip():
            msg = "title must not be blank"
            raise ValueError(msg)
        return value.strip()

    @field_validator("staff_labels")
    @classmethod
    def _staff(cls, value: list[str]) -> list[str]:
        return _labels(value)


class ProductionJobUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)
    planned_filming_date: date | None = None
    deadline: date | None = None
    staff_labels: list[str] | None = Field(default=None, max_length=20)
    notes: str | None = Field(default=None, max_length=10_000)

    _title = ProductionJobCreate._title  # type: ignore[assignment]
    _staff = ProductionJobCreate._staff  # type: ignore[assignment]


class ProductionStatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ProductionStatus | None = None
    editing_status: EditingStatus | None = None
    note: str | None = Field(default=None, max_length=2_000)


class JobProductInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: uuid.UUID
    purchase_order_item_id: uuid.UUID | None = None
    quantity_used: int = Field(default=1, ge=1, le=1_000)
    notes: str | None = Field(default=None, max_length=2_000)


class JobMediaInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_asset_id: uuid.UUID
    role: ProductionMediaRole = ProductionMediaRole.FOOTAGE
    notes: str | None = Field(default=None, max_length=2_000)


class JobProductResponse(ProdModel):
    id: uuid.UUID
    product: ProductRef
    purchase_order_item_id: uuid.UUID | None
    order_number: str | None = None
    quantity_used: int
    notes: str | None
    created_at: datetime


class JobMediaResponse(ProdModel):
    id: uuid.UUID
    role: ProductionMediaRole
    notes: str | None
    created_at: datetime
    asset: MediaAssetSummary


class ProductionJobSummary(ProdModel):
    id: uuid.UUID
    job_number: str
    title: str
    status: ProductionStatus
    editing_status: EditingStatus
    planned_filming_date: date | None
    deadline: date | None
    staff_labels: list[str]
    product_count: int
    media_count: int
    final_count: int
    status_changed_at: datetime
    created_at: datetime
    updated_at: datetime


class ProductionJobDetail(ProductionJobSummary):
    description: str | None
    notes: str | None
    products: list[JobProductResponse]
    media: list[JobMediaResponse]
    allowed_transitions: list[ProductionStatus]
    allowed_editing_transitions: list[EditingStatus]


class PaginatedJobs(ProdModel):
    items: list[ProductionJobSummary]
    page: int
    page_size: int
    total: int
    total_pages: int


class ProductionStats(ProdModel):
    by_status: dict[str, int]
    by_editing_status: dict[str, int]
    upcoming: int  # planned/scheduled with a filming date within 7 days
    overdue: int  # deadline passed and editing not done
