"""Production tables: production_jobs, production_job_products, production_job_media."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from affilio.core.enums import EditingStatus, ProductionMediaRole, ProductionStatus
from affilio.db.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from affilio.db.types import str_enum
from affilio.modules.media.models import MediaAsset
from affilio.modules.procurement.models import PurchaseOrderItem
from affilio.modules.products.models import Product


class ProductionJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "production_jobs"
    __table_args__ = (
        UniqueConstraint("organization_id", "job_number"),
        Index("ix_production_jobs_organization_id_status", "organization_id", "status"),
        Index(
            "ix_production_jobs_organization_id_planned", "organization_id", "planned_filming_date"
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    job_number: Mapped[str] = mapped_column(String(32), nullable=False)  # PJ-2026-0001, stable
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ProductionStatus] = mapped_column(
        str_enum(ProductionStatus, "production_status"),
        nullable=False,
        default=ProductionStatus.PLANNED,
    )
    editing_status: Mapped[EditingStatus] = mapped_column(
        str_enum(EditingStatus, "editing_status"),
        nullable=False,
        default=EditingStatus.NOT_STARTED,
    )
    planned_filming_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    staff_labels: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    products: Mapped[list[ProductionJobProduct]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    media: Mapped[list[ProductionJobMedia]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )

    def meta(self) -> dict[str, Any]:  # pragma: no cover - debugging aid
        return {"job_number": self.job_number, "status": self.status}


class ProductionJobProduct(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A product used in a job; optionally the purchased unit it came from."""

    __tablename__ = "production_job_products"
    __table_args__ = (
        UniqueConstraint("job_id", "product_id"),
        Index("ix_production_job_products_product_id", "product_id"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_jobs.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    purchase_order_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("purchase_order_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    quantity_used: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    job: Mapped[ProductionJob] = relationship(back_populates="products")
    product: Mapped[Product] = relationship()
    purchase_order_item: Mapped[PurchaseOrderItem | None] = relationship()


class ProductionJobMedia(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Optional association between a job and an indexed media asset (footage/final/reference)."""

    __tablename__ = "production_job_media"
    __table_args__ = (
        UniqueConstraint("job_id", "media_asset_id"),
        Index("ix_production_job_media_media_asset_id", "media_asset_id"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_jobs.id", ondelete="CASCADE"), nullable=False
    )
    media_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[ProductionMediaRole] = mapped_column(
        str_enum(ProductionMediaRole, "production_media_role"),
        nullable=False,
        default=ProductionMediaRole.FOOTAGE,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    job: Mapped[ProductionJob] = relationship(back_populates="media")
    media_asset: Mapped[MediaAsset] = relationship()
