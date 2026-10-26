"""Procurement API schemas. Mirrored in packages/shared-types/src/procurement.ts."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from affilio.core.enums import Platform, PurchaseOrderStatus, PurchaseRequestStatus
from affilio.modules.products.urls import validate_http_url


class ProcModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


def _money(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.2f}"


class ProductRef(ProcModel):
    id: uuid.UUID
    name: str
    status: str
    platform: Platform
    product_url: str | None = None


# ---------------------------------------------------------------- requests


class PurchaseRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: uuid.UUID
    quantity: int = Field(default=1, ge=1, le=10_000)
    reason: str | None = Field(default=None, max_length=5_000)


class PurchaseRequestDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["approved", "rejected", "cancelled", "requested"]
    note: str | None = Field(default=None, max_length=5_000)


class PurchaseRequestResponse(ProcModel):
    id: uuid.UUID
    product: ProductRef
    status: PurchaseRequestStatus
    quantity: int
    reason: str | None
    requested_by: str
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None
    order_id: uuid.UUID | None = None
    order_number: str | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------- orders


class OrderItemInput(BaseModel):
    """Either an approved request (quantity/product come from it) or a free product line."""

    model_config = ConfigDict(extra="forbid")

    purchase_request_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    quantity_ordered: int | None = Field(default=None, ge=1, le=10_000)
    unit_cost: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    notes: str | None = Field(default=None, max_length=2_000)


class PurchaseOrderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: Platform = Platform.SHOPEE
    supplier_name: str | None = Field(default=None, max_length=255)
    platform_order_ref: str | None = Field(default=None, max_length=128)
    currency: str | None = Field(default="VND", min_length=3, max_length=3)
    shipping_cost: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    tracking_number: str | None = Field(default=None, max_length=128)
    tracking_url: str | None = Field(default=None, max_length=2048)
    expected_delivery_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=5_000)
    items: list[OrderItemInput] = Field(default_factory=list, max_length=200)

    @field_validator("tracking_url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return validate_http_url(value, field="tracking_url") if value and value.strip() else None

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str | None) -> str | None:
        return value.strip().upper() if value and value.strip() else None


class PurchaseOrderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier_name: str | None = Field(default=None, max_length=255)
    platform_order_ref: str | None = Field(default=None, max_length=128)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    shipping_cost: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    tracking_number: str | None = Field(default=None, max_length=128)
    tracking_url: str | None = Field(default=None, max_length=2048)
    expected_delivery_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=5_000)

    _url = PurchaseOrderCreate._url  # type: ignore[assignment]
    _currency = PurchaseOrderCreate._currency  # type: ignore[assignment]


class PurchaseOrderStatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: PurchaseOrderStatus
    note: str | None = Field(default=None, max_length=2_000)


class ReceiveItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: uuid.UUID
    quantity: int = Field(ge=0, le=10_000)


class ReceiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ReceiveItem] = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=2_000)


class PurchaseOrderItemResponse(ProcModel):
    id: uuid.UUID
    product: ProductRef
    purchase_request_id: uuid.UUID | None
    quantity_ordered: int
    quantity_received: int
    unit_cost: Decimal | None
    line_total: Decimal | None
    received_at: datetime | None
    notes: str | None

    @field_serializer("unit_cost", "line_total")
    def _ser(self, value: Decimal | None) -> str | None:
        return _money(value)


class PurchaseOrderSummary(ProcModel):
    id: uuid.UUID
    order_number: str
    status: PurchaseOrderStatus
    platform: Platform
    supplier_name: str | None
    platform_order_ref: str | None
    currency: str | None
    shipping_cost: Decimal | None
    total_cost: Decimal | None
    tracking_number: str | None
    tracking_url: str | None
    ordered_at: datetime | None
    expected_delivery_at: datetime | None
    delivered_at: datetime | None
    item_count: int
    units_ordered: int
    units_received: int
    status_changed_at: datetime
    created_at: datetime
    updated_at: datetime

    @field_serializer("shipping_cost", "total_cost")
    def _ser(self, value: Decimal | None) -> str | None:
        return _money(value)


class PurchaseOrderDetail(PurchaseOrderSummary):
    notes: str | None
    items: list[PurchaseOrderItemResponse]
    allowed_transitions: list[PurchaseOrderStatus]
    receivable: bool


class PaginatedOrders(ProcModel):
    items: list[PurchaseOrderSummary]
    page: int
    page_size: int
    total: int
    total_pages: int


class ProcurementStats(ProcModel):
    requests_by_status: dict[str, int]
    orders_by_status: dict[str, int]
    units_ordered: int
    units_received: int
    open_orders: int


class ProductProcurementSummary(ProcModel):
    """Procurement view of one product (shown on the product page)."""

    open_requests: int
    units_ordered: int
    units_received: int
    orders: list[PurchaseOrderSummary]
