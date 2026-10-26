"""Affiliate link library."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from affilio.core.enums import AffiliateLinkSource, AffiliateLinkStatus, Platform
from affilio.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from affilio.db.types import str_enum
from affilio.modules.products.models import Product


class AffiliateLink(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "affiliate_links"
    __table_args__ = (
        Index("ix_affiliate_links_organization_id_status", "organization_id", "status"),
        Index("ix_affiliate_links_product_id", "product_id"),
        Index(
            "uq_affiliate_links_org_platform_url",
            "organization_id",
            "platform",
            "url_normalized",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[Platform] = mapped_column(
        str_enum(Platform, "platform"), nullable=False, default=Platform.SHOPEE
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    url_normalized: Mapped[str] = mapped_column(String(2048), nullable=False)
    source: Mapped[AffiliateLinkSource] = mapped_column(
        str_enum(AffiliateLinkSource, "affiliate_link_source"),
        nullable=False,
        default=AffiliateLinkSource.MANUAL,
    )
    status: Mapped[AffiliateLinkStatus] = mapped_column(
        str_enum(AffiliateLinkStatus, "affiliate_link_status"),
        nullable=False,
        default=AffiliateLinkStatus.ACTIVE,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    product: Mapped[Product | None] = relationship()
