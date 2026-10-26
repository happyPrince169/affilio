"""content taxonomy, folder classification, channel routing profiles

Revision ID: 0006_content_routing
Revises: 0005_distribution_core
Create Date: 2026-10-27 09:00:00 UTC
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_content_routing"
down_revision: str | None = "0005_distribution_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CATEGORY_STATUSES = ("active", "archived")
ASSIGNMENT_SOURCES = ("folder", "manual", "import", "ai_future")
MATCH_POLICIES = ("allow", "prefer", "block")


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


def _fk(table: str, column: str, ref_table: str, *, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{ref_table}.id"],
        name=op.f(f"fk_{table}_{column}_{ref_table}"),
        ondelete=ondelete,
    )


def upgrade() -> None:
    # --- taxonomy ----------------------------------------------------------------------
    op.create_table(
        "content_categories",
        _uuid_pk(),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.String(length=32), server_default=sa.text("'active'"), nullable=False
        ),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        _fk("content_categories", "organization_id", "organizations", ondelete="CASCADE"),
        _fk("content_categories", "parent_id", "content_categories", ondelete="RESTRICT"),
        sa.UniqueConstraint("organization_id", "id", name=op.f("uq_content_categories_org_id")),
        sa.CheckConstraint(
            _in_list("status", CATEGORY_STATUSES),
            name=op.f("ck_content_categories_category_status"),
        ),
    )
    op.create_index(
        "uq_content_categories_org_parent_slug",
        "content_categories",
        [
            "organization_id",
            sa.text("coalesce(parent_id, '00000000-0000-0000-0000-000000000000'::uuid)"),
            "slug",
        ],
        unique=True,
    )
    op.create_index(
        "ix_content_categories_organization_id_parent_id",
        "content_categories",
        ["organization_id", "parent_id"],
    )

    # --- channel routing (accounts) -------------------------------------------------------
    op.create_table(
        "channel_profiles",
        _uuid_pk(),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("social_account_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("primary_category_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("target_audience", sa.String(length=255), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("country", sa.String(length=2), nullable=True),
        sa.Column("routing_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        _fk("channel_profiles", "organization_id", "organizations", ondelete="CASCADE"),
        _fk("channel_profiles", "social_account_id", "social_accounts", ondelete="CASCADE"),
        _fk("channel_profiles", "primary_category_id", "content_categories", ondelete="SET NULL"),
        sa.UniqueConstraint(
            "social_account_id", name=op.f("uq_channel_profiles_social_account_id")
        ),
    )
    op.create_table(
        "channel_category_rules",
        _uuid_pk(),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("social_account_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("category_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("match_policy", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.Integer(), server_default=sa.text("0"), nullable=False),
        _ts("created_at"),
        _fk("channel_category_rules", "organization_id", "organizations", ondelete="CASCADE"),
        _fk("channel_category_rules", "social_account_id", "social_accounts", ondelete="CASCADE"),
        _fk("channel_category_rules", "category_id", "content_categories", ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "social_account_id",
            "category_id",
            name=op.f("uq_channel_category_rules_account_category"),
        ),
        sa.CheckConstraint(
            _in_list("match_policy", MATCH_POLICIES),
            name=op.f("ck_channel_category_rules_channel_match_policy"),
        ),
    )
    op.create_index(
        "ix_channel_category_rules_organization_id", "channel_category_rules", ["organization_id"]
    )
    op.create_index(
        "ix_channel_category_rules_category_id", "channel_category_rules", ["category_id"]
    )

    # --- media classification ---------------------------------------------------------------
    op.add_column(
        "media_assets",
        sa.Column("classification_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "media_content_categories",
        _uuid_pk(),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("media_asset_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("category_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("assignment_source", sa.String(length=32), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        _fk("media_content_categories", "organization_id", "organizations", ondelete="CASCADE"),
        _fk("media_content_categories", "media_asset_id", "media_assets", ondelete="CASCADE"),
        _fk("media_content_categories", "category_id", "content_categories", ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "media_asset_id",
            "category_id",
            name=op.f("uq_media_content_categories_asset_category"),
        ),
        sa.CheckConstraint(
            _in_list("assignment_source", ASSIGNMENT_SOURCES),
            name=op.f("ck_media_content_categories_category_assignment_source"),
        ),
    )
    op.create_index(
        "uq_media_content_categories_primary",
        "media_content_categories",
        ["media_asset_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )
    op.create_index(
        "ix_media_content_categories_org_category",
        "media_content_categories",
        ["organization_id", "category_id"],
    )
    op.create_table(
        "folder_category_rules",
        _uuid_pk(),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("media_root_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("relative_folder_path", sa.String(length=1024), nullable=False),
        sa.Column("category_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("include_subfolders", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("priority", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        _fk("folder_category_rules", "organization_id", "organizations", ondelete="CASCADE"),
        _fk("folder_category_rules", "media_root_id", "media_roots", ondelete="CASCADE"),
        _fk("folder_category_rules", "category_id", "content_categories", ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "media_root_id",
            "relative_folder_path",
            name=op.f("uq_folder_category_rules_root_path"),
        ),
    )
    op.create_index(
        "ix_folder_category_rules_organization_id", "folder_category_rules", ["organization_id"]
    )
    op.create_index(
        "ix_folder_category_rules_category_id", "folder_category_rules", ["category_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_folder_category_rules_category_id", table_name="folder_category_rules")
    op.drop_index("ix_folder_category_rules_organization_id", table_name="folder_category_rules")
    op.drop_table("folder_category_rules")
    op.drop_index("ix_media_content_categories_org_category", table_name="media_content_categories")
    op.drop_index("uq_media_content_categories_primary", table_name="media_content_categories")
    op.drop_table("media_content_categories")
    op.drop_column("media_assets", "classification_changed_at")
    op.drop_index("ix_channel_category_rules_category_id", table_name="channel_category_rules")
    op.drop_index("ix_channel_category_rules_organization_id", table_name="channel_category_rules")
    op.drop_table("channel_category_rules")
    op.drop_table("channel_profiles")
    op.drop_index(
        "ix_content_categories_organization_id_parent_id", table_name="content_categories"
    )
    op.drop_index("uq_content_categories_org_parent_slug", table_name="content_categories")
    op.drop_table("content_categories")
