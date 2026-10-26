"""Content category tree."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from affilio.core.enums import CategoryStatus
from affilio.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from affilio.db.types import str_enum

UNCATEGORIZED_SLUG = "uncategorized"


class ContentCategory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "content_categories"
    __table_args__ = (
        # Sibling slugs are unique; NULL parents collapse to a sentinel in the index below.
        Index(
            "uq_content_categories_org_parent_slug",
            "organization_id",
            text("coalesce(parent_id, '00000000-0000-0000-0000-000000000000'::uuid)"),
            "slug",
            unique=True,
        ),
        UniqueConstraint("organization_id", "id", name="uq_content_categories_org_id"),
        Index("ix_content_categories_organization_id_parent_id", "organization_id", "parent_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_categories.id", ondelete="RESTRICT"), nullable=True
    )
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[CategoryStatus] = mapped_column(
        str_enum(CategoryStatus, "category_status"),
        nullable=False,
        default=CategoryStatus.ACTIVE,
        server_default=text("'active'"),
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    parent: Mapped[ContentCategory | None] = relationship(
        remote_side="ContentCategory.id", back_populates="children"
    )
    children: Mapped[list[ContentCategory]] = relationship(back_populates="parent")

    def __repr__(self) -> str:
        return f"<ContentCategory slug={self.slug!r}>"
