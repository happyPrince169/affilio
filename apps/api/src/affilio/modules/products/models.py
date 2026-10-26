"""Product catalog tables: products, product_status_events, product_media_links.

- Estimated commission fields are operator estimates. Actual earned commission arrives with the
  analytics module (M4) in separate tables and is never written here.
- Shopee product identifiers are not assumed stable or present: `platform_product_id` is
  nullable and uniqueness is enforced only when it is known (partial unique index), alongside a
  normalized product URL.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from affilio.core.enums import ImportStatus, Platform, ProductSource, ProductStatus
from affilio.db.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from affilio.db.types import str_enum
from affilio.modules.media.models import MediaAsset


class Product(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        Index("ix_products_organization_id_status", "organization_id", "status"),
        Index("ix_products_organization_id_created_at", "organization_id", "created_at"),
        Index(
            "uq_products_org_platform_product_id",
            "organization_id",
            "platform",
            "platform_product_id",
            unique=True,
            postgresql_where=text("platform_product_id IS NOT NULL"),
        ),
        Index(
            "uq_products_org_product_url_normalized",
            "organization_id",
            "product_url_normalized",
            unique=True,
            postgresql_where=text("product_url_normalized IS NOT NULL"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    platform: Mapped[Platform] = mapped_column(
        str_enum(Platform, "platform"), nullable=False, default=Platform.SHOPEE
    )
    product_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # Lower-cased scheme/host, no fragment, no trailing slash: duplicate detection key.
    product_url_normalized: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    affiliate_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    platform_product_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    seller_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    images: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    observed_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    price_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Fraction, e.g. 0.0500 == 5 %. Estimates only; actual commission lives in analytics (M4).
    estimated_commission_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    estimated_commission_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    status: Mapped[ProductStatus] = mapped_column(
        str_enum(ProductStatus, "product_status"),
        nullable=False,
        default=ProductStatus.DISCOVERED,
    )
    status_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    research_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Provenance: manual entry or a MeoScout CSV import (with the row's own identifier).
    source: Mapped[ProductSource] = mapped_column(
        str_enum(ProductSource, "product_source"),
        nullable=False,
        default=ProductSource.MANUAL,
        server_default=text("'manual'"),
    )
    source_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    import_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_imports.id", ondelete="SET NULL"), nullable=True
    )

    status_events: Mapped[list[ProductStatusEvent]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="ProductStatusEvent.created_at.desc()",
    )
    media_links: Mapped[list[ProductMediaLink]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Product name={self.name!r} status={self.status}>"


class ProductStatusEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Audit trail for manual status transitions."""

    __tablename__ = "product_status_events"
    __table_args__ = (
        Index("ix_product_status_events_product_id_created_at", "product_id", "created_at"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[ProductStatus | None] = mapped_column(
        str_enum(ProductStatus, "product_status_from"), nullable=True
    )
    to_status: Mapped[ProductStatus] = mapped_column(
        str_enum(ProductStatus, "product_status_to"), nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Free-text actor until authentication exists (e.g. "api", "cli"); becomes a user id in M3.
    actor: Mapped[str] = mapped_column(String(255), nullable=False, default="api")
    confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    product: Mapped[Product] = relationship(back_populates="status_events")


class ProductMediaLink(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Many-to-many between products and media assets; at most one primary product per asset."""

    __tablename__ = "product_media_links"
    __table_args__ = (
        UniqueConstraint("product_id", "media_asset_id"),
        Index("ix_product_media_links_media_asset_id", "media_asset_id"),
        Index("ix_product_media_links_organization_id", "organization_id"),
        Index(
            "uq_product_media_links_primary_per_asset",
            "media_asset_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    media_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    product: Mapped[Product] = relationship(back_populates="media_links")
    media_asset: Mapped[MediaAsset] = relationship()

    def extra(self) -> dict[str, Any]:  # pragma: no cover - debugging aid
        return {"product_id": str(self.product_id), "asset": str(self.media_asset_id)}


class ProductImport(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One MeoScout CSV import run. The file hash makes re-imports idempotent."""

    __tablename__ = "product_imports"
    __table_args__ = (
        Index("ix_product_imports_organization_id_created_at", "organization_id", "created_at"),
        Index("ix_product_imports_organization_id_file_sha256", "organization_id", "file_sha256"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[ProductSource] = mapped_column(
        str_enum(ProductSource, "product_import_source"), nullable=False
    )
    status: Mapped[ImportStatus] = mapped_column(
        str_enum(ImportStatus, "product_import_status"), nullable=False
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mapping: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    on_duplicate: Mapped[str] = mapped_column(String(16), nullable=False, default="skip")
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    duplicate_of_import_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_imports.id", ondelete="SET NULL"), nullable=True
    )
    actor: Mapped[str] = mapped_column(String(255), nullable=False, default="api")
