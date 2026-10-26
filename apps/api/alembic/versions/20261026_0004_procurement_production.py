"""procurement + production + imports + final handoff

Revision ID: 0004_procurement_production
Revises: 0003_product_catalog
Create Date: 2026-10-26 16:00:00 UTC
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004_procurement_production"
down_revision: str | None = "0003_product_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLATFORMS = ("shopee", "facebook", "tiktok", "instagram", "youtube")
PRODUCT_SOURCES = ("manual", "meoscout")
IMPORT_STATUSES = ("completed", "failed", "skipped")
REQUEST_STATUSES = ("requested", "approved", "rejected", "ordered", "cancelled")
ORDER_STATUSES = (
    "draft",
    "placed",
    "shipped",
    "delivered",
    "partially_received",
    "received",
    "cancelled",
)
PRODUCTION_STATUSES = ("planned", "scheduled", "filming", "filmed", "cancelled")
EDITING_STATUSES = ("not_started", "in_progress", "review", "done")
MEDIA_ROLES = ("footage", "final", "reference")
ASSET_KINDS = ("footage", "final")
HANDOFF_STATUSES = ("pending_review", "approved", "rejected")
MEDIA_ROOT_KINDS = ("original", "proxy", "final", "thumbnail", "export")
MEDIA_VARIANT_KINDS = ("original", "proxy", "final", "thumbnail", "export")


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


def _jsonb(name: str, default: str) -> sa.Column:
    return sa.Column(
        name,
        postgresql.JSONB(astext_type=sa.Text()),
        server_default=sa.text(f"'{default}'::jsonb"),
        nullable=False,
    )


def upgrade() -> None:
    # --- media: finals root kind, asset kind, handoff gate -------------------------------
    op.drop_constraint(op.f("ck_media_roots_media_root_kind"), "media_roots", type_="check")
    op.create_check_constraint(
        op.f("ck_media_roots_media_root_kind"), "media_roots", _in_list("kind", MEDIA_ROOT_KINDS)
    )
    op.drop_constraint(
        op.f("ck_media_file_variants_media_variant_kind"), "media_file_variants", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_media_file_variants_media_variant_kind"),
        "media_file_variants",
        _in_list("kind", MEDIA_VARIANT_KINDS),
    )
    op.add_column(
        "media_assets",
        sa.Column(
            "asset_kind", sa.String(length=32), server_default=sa.text("'footage'"), nullable=False
        ),
    )
    op.add_column("media_assets", sa.Column("handoff_status", sa.String(length=32), nullable=True))
    op.add_column("media_assets", sa.Column("handoff_note", sa.Text(), nullable=True))
    op.add_column(
        "media_assets", sa.Column("handoff_reviewed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "media_assets", sa.Column("handoff_reviewed_by", sa.String(length=255), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_media_assets_media_asset_kind"),
        "media_assets",
        _in_list("asset_kind", ASSET_KINDS),
    )
    op.create_check_constraint(
        op.f("ck_media_assets_handoff_status"),
        "media_assets",
        _in_list("handoff_status", HANDOFF_STATUSES),
    )
    op.create_index(
        "ix_media_assets_organization_id_asset_kind",
        "media_assets",
        ["organization_id", "asset_kind"],
    )

    # --- product imports + provenance --------------------------------------------------------
    op.create_table(
        "product_imports",
        _uuid_pk(),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        _jsonb("mapping", "{}"),
        sa.Column("on_duplicate", sa.String(length=16), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("created_count", sa.Integer(), nullable=False),
        sa.Column("updated_count", sa.Integer(), nullable=False),
        sa.Column("skipped_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        _jsonb("errors", "[]"),
        sa.Column("duplicate_of_import_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("actor", sa.String(length=255), nullable=False),
        _ts("created_at"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_product_imports_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["duplicate_of_import_id"],
            ["product_imports.id"],
            name=op.f("fk_product_imports_duplicate_of_import_id_product_imports"),
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            _in_list("source", PRODUCT_SOURCES),
            name=op.f("ck_product_imports_product_import_source"),
        ),
        sa.CheckConstraint(
            _in_list("status", IMPORT_STATUSES),
            name=op.f("ck_product_imports_product_import_status"),
        ),
    )
    op.create_index(
        "ix_product_imports_organization_id_created_at",
        "product_imports",
        ["organization_id", "created_at"],
    )
    op.create_index(
        "ix_product_imports_organization_id_file_sha256",
        "product_imports",
        ["organization_id", "file_sha256"],
    )
    op.add_column(
        "products",
        sa.Column(
            "source", sa.String(length=32), server_default=sa.text("'manual'"), nullable=False
        ),
    )
    op.add_column("products", sa.Column("source_ref", sa.String(length=128), nullable=True))
    op.add_column("products", sa.Column("import_id", sa.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        op.f("fk_products_import_id_product_imports"),
        "products",
        "product_imports",
        ["import_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        op.f("ck_products_product_source"), "products", _in_list("source", PRODUCT_SOURCES)
    )

    # --- procurement ----------------------------------------------------------------------------
    op.create_table(
        "purchase_requests",
        _uuid_pk(),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("requested_by", sa.String(length=255), nullable=False),
        sa.Column("decided_by", sa.String(length=255), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_purchase_requests_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_purchase_requests_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            _in_list("status", REQUEST_STATUSES),
            name=op.f("ck_purchase_requests_purchase_request_status"),
        ),
    )
    op.create_index(
        "ix_purchase_requests_organization_id_status",
        "purchase_requests",
        ["organization_id", "status"],
    )
    op.create_index("ix_purchase_requests_product_id", "purchase_requests", ["product_id"])

    op.create_table(
        "purchase_orders",
        _uuid_pk(),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("order_number", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("supplier_name", sa.String(length=255), nullable=True),
        sa.Column("platform_order_ref", sa.String(length=128), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("shipping_cost", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("total_cost", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("tracking_number", sa.String(length=128), nullable=True),
        sa.Column("tracking_url", sa.String(length=2048), nullable=True),
        sa.Column("ordered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expected_delivery_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        _ts("status_changed_at"),
        _ts("created_at"),
        _ts("updated_at"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_purchase_orders_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "order_number",
            name=op.f("uq_purchase_orders_organization_id_order_number"),
        ),
        sa.CheckConstraint(
            _in_list("status", ORDER_STATUSES),
            name=op.f("ck_purchase_orders_purchase_order_status"),
        ),
        sa.CheckConstraint(
            _in_list("platform", PLATFORMS), name=op.f("ck_purchase_orders_platform")
        ),
    )
    op.create_index(
        "ix_purchase_orders_organization_id_status",
        "purchase_orders",
        ["organization_id", "status"],
    )

    op.create_table(
        "purchase_order_items",
        _uuid_pk(),
        sa.Column("order_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("purchase_request_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("quantity_ordered", sa.Integer(), nullable=False),
        sa.Column("quantity_received", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("unit_cost", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["purchase_orders.id"],
            name=op.f("fk_purchase_order_items_order_id_purchase_orders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_purchase_order_items_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["purchase_request_id"],
            ["purchase_requests.id"],
            name=op.f("fk_purchase_order_items_purchase_request_id_purchase_requests"),
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "order_id", "product_id", name=op.f("uq_purchase_order_items_order_id_product_id")
        ),
        sa.UniqueConstraint(
            "purchase_request_id", name=op.f("uq_purchase_order_items_purchase_request_id")
        ),
    )
    op.create_index("ix_purchase_order_items_product_id", "purchase_order_items", ["product_id"])

    # --- production -------------------------------------------------------------------------------
    op.create_table(
        "production_jobs",
        _uuid_pk(),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("job_number", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("editing_status", sa.String(length=32), nullable=False),
        sa.Column("planned_filming_date", sa.Date(), nullable=True),
        sa.Column("deadline", sa.Date(), nullable=True),
        _jsonb("staff_labels", "[]"),
        sa.Column("notes", sa.Text(), nullable=True),
        _ts("status_changed_at"),
        _ts("created_at"),
        _ts("updated_at"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_production_jobs_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "job_number",
            name=op.f("uq_production_jobs_organization_id_job_number"),
        ),
        sa.CheckConstraint(
            _in_list("status", PRODUCTION_STATUSES),
            name=op.f("ck_production_jobs_production_status"),
        ),
        sa.CheckConstraint(
            _in_list("editing_status", EDITING_STATUSES),
            name=op.f("ck_production_jobs_editing_status"),
        ),
    )
    op.create_index(
        "ix_production_jobs_organization_id_status",
        "production_jobs",
        ["organization_id", "status"],
    )
    op.create_index(
        "ix_production_jobs_organization_id_planned",
        "production_jobs",
        ["organization_id", "planned_filming_date"],
    )

    op.create_table(
        "production_job_products",
        _uuid_pk(),
        sa.Column("job_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("purchase_order_item_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("quantity_used", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        _ts("created_at"),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["production_jobs.id"],
            name=op.f("fk_production_job_products_job_id_production_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_production_job_products_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["purchase_order_item_id"],
            ["purchase_order_items.id"],
            name=op.f("fk_production_job_products_purchase_order_item_id_purchase_order_items"),
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "job_id", "product_id", name=op.f("uq_production_job_products_job_id_product_id")
        ),
    )
    op.create_index(
        "ix_production_job_products_product_id", "production_job_products", ["product_id"]
    )

    op.create_table(
        "production_job_media",
        _uuid_pk(),
        sa.Column("job_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("media_asset_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        _ts("created_at"),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["production_jobs.id"],
            name=op.f("fk_production_job_media_job_id_production_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["media_asset_id"],
            ["media_assets.id"],
            name=op.f("fk_production_job_media_media_asset_id_media_assets"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "job_id", "media_asset_id", name=op.f("uq_production_job_media_job_id_media_asset_id")
        ),
        sa.CheckConstraint(
            _in_list("role", MEDIA_ROLES),
            name=op.f("ck_production_job_media_production_media_role"),
        ),
    )
    op.create_index(
        "ix_production_job_media_media_asset_id", "production_job_media", ["media_asset_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_production_job_media_media_asset_id", table_name="production_job_media")
    op.drop_table("production_job_media")
    op.drop_index("ix_production_job_products_product_id", table_name="production_job_products")
    op.drop_table("production_job_products")
    op.drop_index("ix_production_jobs_organization_id_planned", table_name="production_jobs")
    op.drop_index("ix_production_jobs_organization_id_status", table_name="production_jobs")
    op.drop_table("production_jobs")
    op.drop_index("ix_purchase_order_items_product_id", table_name="purchase_order_items")
    op.drop_table("purchase_order_items")
    op.drop_index("ix_purchase_orders_organization_id_status", table_name="purchase_orders")
    op.drop_table("purchase_orders")
    op.drop_index("ix_purchase_requests_product_id", table_name="purchase_requests")
    op.drop_index("ix_purchase_requests_organization_id_status", table_name="purchase_requests")
    op.drop_table("purchase_requests")
    op.drop_constraint(op.f("ck_products_product_source"), "products", type_="check")
    op.drop_constraint(
        op.f("fk_products_import_id_product_imports"), "products", type_="foreignkey"
    )
    op.drop_column("products", "import_id")
    op.drop_column("products", "source_ref")
    op.drop_column("products", "source")
    op.drop_index("ix_product_imports_organization_id_file_sha256", table_name="product_imports")
    op.drop_index("ix_product_imports_organization_id_created_at", table_name="product_imports")
    op.drop_table("product_imports")
    op.drop_index("ix_media_assets_organization_id_asset_kind", table_name="media_assets")
    op.drop_constraint(op.f("ck_media_assets_handoff_status"), "media_assets", type_="check")
    op.drop_constraint(op.f("ck_media_assets_media_asset_kind"), "media_assets", type_="check")
    op.drop_column("media_assets", "handoff_reviewed_by")
    op.drop_column("media_assets", "handoff_reviewed_at")
    op.drop_column("media_assets", "handoff_note")
    op.drop_column("media_assets", "handoff_status")
    op.drop_column("media_assets", "asset_kind")
    op.drop_constraint(
        op.f("ck_media_file_variants_media_variant_kind"), "media_file_variants", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_media_file_variants_media_variant_kind"),
        "media_file_variants",
        _in_list("kind", ("original", "proxy", "thumbnail", "export")),
    )
    op.drop_constraint(op.f("ck_media_roots_media_root_kind"), "media_roots", type_="check")
    op.create_check_constraint(
        op.f("ck_media_roots_media_root_kind"),
        "media_roots",
        _in_list("kind", ("original", "proxy", "thumbnail", "export")),
    )
