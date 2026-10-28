"""Folder-driven multi-facet classification: content_categories.facet, path assignment source,
media_assets.production_period, category_suggestions.

Revision ID: 0013_facet_classification
Revises: 0012_upload_file_identity
Create Date: 2026-11-01 10:00:00 UTC
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013_facet_classification"
down_revision: str | None = "0012_upload_file_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FACETS = ("production_owner", "product_category", "content_type")
SUGGESTION_STATUSES = ("pending", "approved", "rejected")
ASSIGNMENT_SOURCES_BEFORE = ("folder", "manual", "import", "ai_future")
ASSIGNMENT_SOURCES_AFTER = (*ASSIGNMENT_SOURCES_BEFORE, "path")


def _in_list(column: str, values: Sequence[str]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


def upgrade() -> None:
    # --- categories gain an optional facet; one root per (organization, facet)
    op.add_column("content_categories", sa.Column("facet", sa.String(length=32), nullable=True))
    op.create_check_constraint(
        op.f("ck_content_categories_category_facet"),
        "content_categories",
        _in_list("facet", FACETS),
    )
    op.create_index(
        "uq_content_categories_org_facet_root",
        "content_categories",
        ["organization_id", "facet"],
        unique=True,
        postgresql_where=sa.text("parent_id IS NULL AND facet IS NOT NULL"),
    )

    # --- assignments derived from the structured path
    op.drop_constraint(
        op.f("ck_media_content_categories_category_assignment_source"),
        "media_content_categories",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_media_content_categories_category_assignment_source"),
        "media_content_categories",
        _in_list("assignment_source", ASSIGNMENT_SOURCES_AFTER),
    )

    # --- production period from the first ingestion (UTC month); existing rows backfilled
    op.add_column(
        "media_assets", sa.Column("production_period", sa.String(length=7), nullable=True)
    )
    op.execute(
        "UPDATE media_assets SET production_period = "
        "to_char(first_seen_at AT TIME ZONE 'UTC', 'YYYY-MM') WHERE production_period IS NULL"
    )

    # --- pending facet values discovered by the scanner
    op.create_table(
        "category_suggestions",
        sa.Column(
            "id", sa.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("media_root_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("facet", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("normalized_name", sa.String(length=160), nullable=False),
        sa.Column("relative_folder_path", sa.String(length=1024), nullable=False),
        sa.Column("example_relative_path", sa.String(length=1024), nullable=True),
        sa.Column(
            "status", sa.String(length=32), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("seen_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(length=255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("category_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _in_list("facet", FACETS), name=op.f("ck_category_suggestions_category_facet")
        ),
        sa.CheckConstraint(
            _in_list("status", SUGGESTION_STATUSES),
            name=op.f("ck_category_suggestions_category_suggestion_status"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_category_suggestions_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["media_root_id"],
            ["media_roots.id"],
            name=op.f("fk_category_suggestions_media_root_id_media_roots"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["content_categories.id"],
            name=op.f("fk_category_suggestions_category_id_content_categories"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_category_suggestions")),
        sa.UniqueConstraint(
            "organization_id",
            "facet",
            "normalized_name",
            name="uq_category_suggestions_org_facet_name",
        ),
    )
    op.create_index(
        "ix_category_suggestions_organization_id_status",
        "category_suggestions",
        ["organization_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_category_suggestions_organization_id_status", table_name="category_suggestions"
    )
    op.drop_table("category_suggestions")

    op.drop_column("media_assets", "production_period")

    # Rows derived from paths cannot survive the narrower constraint.
    op.execute("DELETE FROM media_content_categories WHERE assignment_source = 'path'")
    op.drop_constraint(
        op.f("ck_media_content_categories_category_assignment_source"),
        "media_content_categories",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_media_content_categories_category_assignment_source"),
        "media_content_categories",
        _in_list("assignment_source", ASSIGNMENT_SOURCES_BEFORE),
    )

    op.drop_index("uq_content_categories_org_facet_root", table_name="content_categories")
    op.drop_constraint(
        op.f("ck_content_categories_category_facet"), "content_categories", type_="check"
    )
    op.drop_column("content_categories", "facet")
