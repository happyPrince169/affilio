"""Product API schemas. Mirrored in packages/shared-types/src/products.ts."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from affilio.core.enums import ImportStatus, Platform, ProductSource, ProductStatus
from affilio.modules.media.schemas import MediaAssetSummary
from affilio.modules.products.urls import validate_http_url

ProductSortKey = Literal["newest", "oldest", "name", "updated", "price", "commission"]
MAX_IMAGES = 10


class ProductModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


class ProductFieldsBase(BaseModel):
    """Writable fields shared by create and update. Unknown values are allowed (null)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    platform: Platform = Platform.SHOPEE
    product_url: str | None = Field(default=None, max_length=2048)
    affiliate_url: str | None = Field(default=None, max_length=2048)
    platform_product_id: str | None = Field(default=None, max_length=128)
    seller_name: str | None = Field(default=None, max_length=255)
    category: str | None = Field(default=None, max_length=120)
    images: list[str] = Field(default_factory=list, max_length=MAX_IMAGES)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    observed_price: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    price_observed_at: datetime | None = None
    estimated_commission_rate: Decimal | None = Field(
        default=None, ge=0, le=1, max_digits=6, decimal_places=4
    )
    estimated_commission_amount: Decimal | None = Field(
        default=None, ge=0, max_digits=14, decimal_places=2
    )
    research_notes: str | None = Field(default=None, max_length=20_000)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            msg = "name must not be blank"
            raise ValueError(msg)
        return stripped

    @field_validator("platform_product_id", "seller_name", "category", "research_notes")
    @classmethod
    def _strip_optional(cls, value: str | None) -> str | None:
        return _clean_optional(value)

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, value: str | None) -> str | None:
        cleaned = _clean_optional(value)
        if cleaned is None:
            return None
        if not cleaned.isalpha():
            msg = "currency must be a 3-letter ISO code"
            raise ValueError(msg)
        return cleaned.upper()

    @field_validator("product_url", "affiliate_url")
    @classmethod
    def _validate_url(cls, value: str | None, info) -> str | None:
        cleaned = _clean_optional(value)
        if cleaned is None:
            return None
        return validate_http_url(cleaned, field=info.field_name or "url")

    @field_validator("images")
    @classmethod
    def _validate_images(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for index, item in enumerate(value):
            url = _clean_optional(item)
            if url is None:
                continue
            url = validate_http_url(url, field=f"images[{index}]")
            if url not in cleaned:
                cleaned.append(url)
        return cleaned


class ProductCreate(ProductFieldsBase):
    status: ProductStatus = ProductStatus.DISCOVERED

    @field_validator("status")
    @classmethod
    def _no_winner_on_create(cls, value: ProductStatus) -> ProductStatus:
        if value == ProductStatus.WINNER:
            msg = "A product cannot be created as WINNER; confirm it through a status change"
            raise ValueError(msg)
        return value


class ProductUpdate(BaseModel):
    """Partial update: only provided fields change. Status is changed via its own endpoint."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    platform: Platform | None = None
    product_url: str | None = Field(default=None, max_length=2048)
    affiliate_url: str | None = Field(default=None, max_length=2048)
    platform_product_id: str | None = Field(default=None, max_length=128)
    seller_name: str | None = Field(default=None, max_length=255)
    category: str | None = Field(default=None, max_length=120)
    images: list[str] | None = Field(default=None, max_length=MAX_IMAGES)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    observed_price: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    price_observed_at: datetime | None = None
    estimated_commission_rate: Decimal | None = Field(
        default=None, ge=0, le=1, max_digits=6, decimal_places=4
    )
    estimated_commission_amount: Decimal | None = Field(
        default=None, ge=0, max_digits=14, decimal_places=2
    )
    research_notes: str | None = Field(default=None, max_length=20_000)

    _strip_name = ProductFieldsBase._strip_name  # type: ignore[assignment]
    _strip_optional = ProductFieldsBase._strip_optional  # type: ignore[assignment]
    _upper_currency = ProductFieldsBase._upper_currency  # type: ignore[assignment]
    _validate_url = ProductFieldsBase._validate_url  # type: ignore[assignment]
    _validate_images = ProductFieldsBase._validate_images  # type: ignore[assignment]


class ProductStatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ProductStatus
    note: str | None = Field(default=None, max_length=5_000)
    # Required (true) when moving to WINNER: an explicit human decision, never automatic.
    confirmed: bool = False

    @field_validator("note")
    @classmethod
    def _strip_note(cls, value: str | None) -> str | None:
        return _clean_optional(value)


class ProductStatusEventResponse(ProductModel):
    id: uuid.UUID
    from_status: ProductStatus | None
    to_status: ProductStatus
    note: str | None
    actor: str
    confirmed: bool
    created_at: datetime


class ProductSummary(ProductModel):
    id: uuid.UUID
    name: str
    platform: Platform
    status: ProductStatus
    source: ProductSource = ProductSource.MANUAL
    source_ref: str | None = None
    product_url: str | None
    affiliate_url: str | None
    platform_product_id: str | None
    seller_name: str | None
    category: str | None
    images: list[str]
    currency: str | None
    observed_price: Decimal | None
    price_observed_at: datetime | None
    estimated_commission_rate: Decimal | None
    estimated_commission_amount: Decimal | None
    media_count: int = 0
    status_changed_at: datetime
    created_at: datetime
    updated_at: datetime

    @field_serializer("observed_price", "estimated_commission_amount")
    def _money(self, value: Decimal | None) -> str | None:
        return None if value is None else f"{value:.2f}"

    @field_serializer("estimated_commission_rate")
    def _rate(self, value: Decimal | None) -> str | None:
        return None if value is None else f"{value:.4f}"


class ProductDetail(ProductSummary):
    research_notes: str | None
    status_events: list[ProductStatusEventResponse] = Field(default_factory=list)
    allowed_transitions: list[ProductStatus] = Field(default_factory=list)


class PaginatedProducts(ProductModel):
    items: list[ProductSummary]
    page: int
    page_size: int
    total: int
    total_pages: int


class ProductStats(ProductModel):
    total: int
    by_status: dict[str, int]
    with_media: int
    with_affiliate_url: int
    with_estimated_commission: int


class ProductMediaLinkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_asset_id: uuid.UUID
    is_primary: bool = False
    notes: str | None = Field(default=None, max_length=5_000)


class ProductMediaLinkUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_primary: bool | None = None
    notes: str | None = Field(default=None, max_length=5_000)


class ProductMediaLinkResponse(ProductModel):
    id: uuid.UUID
    product_id: uuid.UUID
    media_asset_id: uuid.UUID
    is_primary: bool
    notes: str | None
    created_at: datetime
    asset: MediaAssetSummary


class LinkedProductResponse(ProductModel):
    """A product as seen from a media asset."""

    link_id: uuid.UUID
    is_primary: bool
    notes: str | None
    created_at: datetime
    product: ProductSummary


# ------------------------------------------------------------------ MeoScout import


class ImportRowPreview(ProductModel):
    row_number: int
    action: Literal["create", "update", "skip", "error"]
    name: str | None = None
    reason: str | None = None
    existing_product_id: uuid.UUID | None = None


class ImportPreviewResponse(ProductModel):
    file_name: str
    file_sha256: str
    delimiter: str
    headers: list[str]
    suggested_mapping: dict[str, str]
    mapping: dict[str, str]
    target_fields: list[str]
    row_count: int
    counts: dict[str, int]
    rows: list[ImportRowPreview]
    already_imported: bool
    previous_import_id: uuid.UUID | None = None


class ImportErrorEntry(ProductModel):
    row: int
    name: str | None = None
    message: str | None = None


class ProductImportResponse(ProductModel):
    id: uuid.UUID
    source: ProductSource
    status: ImportStatus
    file_name: str
    file_sha256: str
    mapping: dict[str, str]
    on_duplicate: str
    row_count: int
    created_count: int
    updated_count: int
    skipped_count: int
    failed_count: int
    errors: list[ImportErrorEntry]
    duplicate_of_import_id: uuid.UUID | None = None
    actor: str
    created_at: datetime


# ------------------------------------------------------------------ MeoScout ingest (M3.2-B.2 P1)

INGEST_CONTRACT = "meoscout.affilio.candidates"
INGEST_CONTRACT_MAJOR = 2
MAX_INGEST_ITEMS = 200
MAX_CLIENT_REF_LENGTH = 128
MAX_EVIDENCE_BYTES = 8 * 1024
OnConflict = Literal["keep", "replace"]
IngestItemStatusValue = Literal["created", "updated", "unchanged", "conflict", "rejected"]
IngestStatusValue = Literal["completed", "failed"]
_BATCH_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{7,127}$")
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


class IngestSourceInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = Field(min_length=1, max_length=64)
    build: str | None = Field(default=None, max_length=64)
    extension_version: str | None = Field(default=None, max_length=32)


class IngestCandidate(ProductFieldsBase):
    """One approved MeoScout candidate. Field validation is the product's own
    (`ProductFieldsBase`), so an ingested product obeys exactly the rules of a created one.
    `estimated_commission_rate` is a fraction (0 to 1); the CSV path's percent heuristic does
    not apply here. `evidence` is informational and stored on the ingest item only."""

    client_ref: str = Field(min_length=1, max_length=MAX_CLIENT_REF_LENGTH)
    image_url: str | None = Field(default=None, max_length=2048)
    evidence: dict[str, object] | None = None

    @field_validator("client_ref")
    @classmethod
    def _strip_client_ref(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped or any(ord(ch) < 32 or ch == "\x7f" for ch in stripped):
            msg = "client_ref must be a non-blank printable string"
            raise ValueError(msg)
        return stripped

    @field_validator("image_url")
    @classmethod
    def _validate_image_url(cls, value: str | None) -> str | None:
        cleaned = _clean_optional(value)
        if cleaned is None:
            return None
        return validate_http_url(cleaned, field="image_url")

    @field_validator("evidence")
    @classmethod
    def _bound_evidence(cls, value: dict[str, object] | None) -> dict[str, object] | None:
        if value is None:
            return None
        encoded = json.dumps(value, ensure_ascii=False, default=str)
        if len(encoded.encode("utf-8")) > MAX_EVIDENCE_BYTES:
            msg = f"evidence must serialize to at most {MAX_EVIDENCE_BYTES} bytes"
            raise ValueError(msg)
        return value

    def all_images(self) -> list[str]:
        images = list(self.images)
        if self.image_url and self.image_url not in images:
            images.insert(0, self.image_url)
        return images[:MAX_IMAGES]

    def has_canonical_identity(self) -> bool:
        return bool(self.platform_product_id or self.product_url)


class IngestRequest(BaseModel):
    """Batch envelope. Envelope errors fail the whole request (422); per-item field errors
    become `rejected` results so the rest of the batch still lands."""

    model_config = ConfigDict(extra="forbid")

    contract: str = Field(max_length=64)
    version: str = Field(max_length=16)
    batch_id: str = Field(min_length=8, max_length=128)
    sent_at: datetime | None = None
    source: IngestSourceInfo | None = None
    on_conflict: OnConflict = "keep"
    items: list[dict[str, object]] = Field(min_length=1, max_length=MAX_INGEST_ITEMS)

    @field_validator("contract")
    @classmethod
    def _known_contract(cls, value: str) -> str:
        if value != INGEST_CONTRACT:
            msg = f"contract must be '{INGEST_CONTRACT}'"
            raise ValueError(msg)
        return value

    @field_validator("version")
    @classmethod
    def _supported_version(cls, value: str) -> str:
        match = _VERSION_RE.match(value.strip())
        if match is None or int(match.group(1)) != INGEST_CONTRACT_MAJOR:
            msg = f"version must be {INGEST_CONTRACT_MAJOR}.x.y"
            raise ValueError(msg)
        return value.strip()

    @field_validator("batch_id")
    @classmethod
    def _safe_batch_id(cls, value: str) -> str:
        if not _BATCH_ID_RE.match(value):
            msg = "batch_id must be 8-128 characters of letters, digits, '.', '_', ':' or '-'"
            raise ValueError(msg)
        return value

    @field_validator("items")
    @classmethod
    def _client_refs(cls, value: list[dict[str, object]]) -> list[dict[str, object]]:
        seen: set[str] = set()
        for index, item in enumerate(value):
            ref = item.get("client_ref") if isinstance(item, dict) else None
            if not isinstance(ref, str) or not ref.strip():
                msg = f"items[{index}].client_ref is required"
                raise ValueError(msg)
            if len(ref) > MAX_CLIENT_REF_LENGTH:
                msg = f"items[{index}].client_ref is longer than {MAX_CLIENT_REF_LENGTH}"
                raise ValueError(msg)
            key = ref.strip()
            if key in seen:
                msg = f"items[{index}].client_ref '{key[:60]}' appears more than once"
                raise ValueError(msg)
            seen.add(key)
        return value


class IngestConflict(ProductModel):
    field: str
    code: str
    resolution: Literal["kept", "replaced"]


class IngestItemResult(ProductModel):
    client_ref: str
    status: IngestItemStatusValue
    product_id: uuid.UUID | None = None
    product_status: ProductStatus | None = None
    code: str | None = None
    reason: str | None = None
    changed_fields: list[str] = Field(default_factory=list)
    conflicts: list[IngestConflict] = Field(default_factory=list)


class IngestCounts(ProductModel):
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    conflict: int = 0
    rejected: int = 0


class IngestResponse(ProductModel):
    id: uuid.UUID
    batch_id: str
    contract: str
    version: str
    status: IngestStatusValue
    replayed: bool
    on_conflict: OnConflict
    received_at: datetime
    completed_at: datetime | None
    counts: IngestCounts
    results: list[IngestItemResult]
