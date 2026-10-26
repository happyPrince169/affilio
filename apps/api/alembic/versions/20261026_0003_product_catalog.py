"""product catalog: products, product_status_events, product_media_links

Revision ID: 0003_product_catalog
Revises: 0002_media_library
Create Date: 2026-10-26 14:00:00 UTC
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003_product_catalog"
down_revision: str | None = "0002_media_library"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLATFORMS = ("shopee", "facebook", "tiktok", "instagram", "youtube")
PRODUCT_STATUSES = ("discovered", "shortlisted", "testing", "winner", "paused", "archived")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id",
        sa.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def _ts(name: str) -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "products",
        _uuid_pk(),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("product_url", sa.String(length=2048), nullable=True),
        sa.Column("product_url_normalized", sa.String(length=2048), nullable=True),
        sa.Column("affiliate_url", sa.String(length=2048), nullable=True),
        sa.Column("platform_product_id", sa.String(length=128), nullable=True),
        sa.Column("seller_name", sa.String(length=255), nullable=True),
        sa.Column("category", sa.String(length=120), nullable=True),
        sa.Column(
            "images",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("observed_price", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("price_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("estimated_commission_rate", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column("estimated_commission_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        _ts("status_changed_at"),
        sa.Column("research_notes", sa.Text(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_products_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(_in_list("platform", PLATFORMS), name=op.f("ck_products_platform")),
        sa.CheckConstraint(
            _in_list("status", PRODUCT_STATUSES), name=op.f("ck_products_product_status")
        ),
    )
    op.create_index("ix_products_organization_id_status", "products", ["organization_id", "status"])
    op.create_index(
        "ix_products_organization_id_created_at", "products", ["organization_id", "created_at"]
    )
    op.create_index(
        "uq_products_org_platform_product_id",
        "products",
        ["organization_id", "platform", "platform_product_id"],
        unique=True,
        postgresql_where=sa.text("platform_product_id IS NOT NULL"),
    )
    op.create_index(
        "uq_products_org_product_url_normalized",
        "products",
        ["organization_id", "product_url_normalized"],
        unique=True,
        postgresql_where=sa.text("product_url_normalized IS NOT NULL"),
    )

    op.create_table(
        "product_status_events",
        _uuid_pk(),
        sa.Column("product_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("confirmed", sa.Boolean(), server_default=sa.false(), nullable=False),
        _ts("created_at"),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_product_status_events_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            _in_list("from_status", PRODUCT_STATUSES),
            name=op.f("ck_product_status_events_product_status_from"),
        ),
        sa.CheckConstraint(
            _in_list("to_status", PRODUCT_STATUSES),
            name=op.f("ck_product_status_events_product_status_to"),
        ),
    )
    op.create_index(
        "ix_product_status_events_product_id_created_at",
        "product_status_events",
        ["product_id", "created_at"],
    )

    op.create_table(
        "product_media_links",
        _uuid_pk(),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("media_asset_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        _ts("created_at"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_product_media_links_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_product_media_links_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["media_asset_id"],
            ["media_assets.id"],
            name=op.f("fk_product_media_links_media_asset_id_media_assets"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "product_id",
            "media_asset_id",
            name=op.f("uq_product_media_links_product_id_media_asset_id"),
        ),
    )
    op.create_index(
        "ix_product_media_links_media_asset_id", "product_media_links", ["media_asset_id"]
    )
    op.create_index(
        "ix_product_media_links_organization_id", "product_media_links", ["organization_id"]
    )
    op.create_index(
        "uq_product_media_links_primary_per_asset",
        "product_media_links",
        ["media_asset_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )


def downgrade() -> None:
    op.drop_index("uq_product_media_links_primary_per_asset", table_name="product_media_links")
    op.drop_index("ix_product_media_links_organization_id", table_name="product_media_links")
    op.drop_index("ix_product_media_links_media_asset_id", table_name="product_media_links")
    op.drop_table("product_media_links")
    op.drop_index(
        "ix_product_status_events_product_id_created_at", table_name="product_status_events"
    )
    op.drop_table("product_status_events")
    op.drop_index("uq_products_org_product_url_normalized", table_name="products")
    op.drop_index("uq_products_org_platform_product_id", table_name="products")
    op.drop_index("ix_products_organization_id_created_at", table_name="products")
    op.drop_index("ix_products_organization_id_status", table_name="products")
    op.drop_table("products")
