"""Procurement tables: purchase_requests, purchase_orders, purchase_order_items."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from affilio.core.enums import Platform, PurchaseOrderStatus, PurchaseRequestStatus
from affilio.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from affilio.db.types import str_enum
from affilio.modules.products.models import Product


class PurchaseRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A request to buy units of a product for filming. Approval is a human decision."""

    __tablename__ = "purchase_requests"
    __table_args__ = (
        Index("ix_purchase_requests_organization_id_status", "organization_id", "status"),
        Index("ix_purchase_requests_product_id", "product_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[PurchaseRequestStatus] = mapped_column(
        str_enum(PurchaseRequestStatus, "purchase_request_status"),
        nullable=False,
        default=PurchaseRequestStatus.REQUESTED,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by: Mapped[str] = mapped_column(String(255), nullable=False, default="api")
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    product: Mapped[Product] = relationship()
    order_item: Mapped[PurchaseOrderItem | None] = relationship(back_populates="request")


class PurchaseOrder(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        UniqueConstraint("organization_id", "order_number"),
        Index("ix_purchase_orders_organization_id_status", "organization_id", "status"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    order_number: Mapped[str] = mapped_column(String(32), nullable=False)  # PO-2026-0001
    status: Mapped[PurchaseOrderStatus] = mapped_column(
        str_enum(PurchaseOrderStatus, "purchase_order_status"),
        nullable=False,
        default=PurchaseOrderStatus.DRAFT,
    )
    platform: Mapped[Platform] = mapped_column(
        str_enum(Platform, "platform"), nullable=False, default=Platform.SHOPEE
    )
    supplier_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    platform_order_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    shipping_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    # Stored total = sum(items.quantity_ordered * unit_cost) + shipping_cost; recomputed on write.
    total_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    tracking_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tracking_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    ordered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expected_delivery_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    items: Mapped[list[PurchaseOrderItem]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="PurchaseOrderItem.created_at",
    )


class PurchaseOrderItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "purchase_order_items"
    __table_args__ = (
        UniqueConstraint("order_id", "product_id"),
        # An approved request can be placed on at most one order: no duplicate orders.
        UniqueConstraint("purchase_request_id"),
        Index("ix_purchase_order_items_product_id", "product_id"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False
    )
    # CASCADE so organization deletion stays clean; products themselves have no delete endpoint.
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    purchase_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_requests.id", ondelete="SET NULL"), nullable=True
    )
    quantity_ordered: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    quantity_received: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    order: Mapped[PurchaseOrder] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()
    request: Mapped[PurchaseRequest | None] = relationship(back_populates="order_item")
