"""Editor → production-owner mappings (upload track U2, ADR 0012)

Revision ID: 0014_owner_mappings
Revises: 0013_facet_classification
Create Date: 2026-11-02 09:00:00 UTC
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014_owner_mappings"
down_revision: str | None = "0013_facet_classification"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = ("active", "revoked")


def _in_list(column: str, values: Sequence[str]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


def upgrade() -> None:
    op.create_table(
        "media_owner_mappings",
        sa.Column(
            "id", sa.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("production_owner_category_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status", sa.String(length=32), server_default=sa.text("'active'"), nullable=False
        ),
        sa.Column("created_by_user_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_user_id", sa.UUID(as_uuid=True), nullable=True),
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
            _in_list("status", STATUSES), name=op.f("ck_media_owner_mappings_owner_mapping_status")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_media_owner_mappings_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_media_owner_mappings_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["production_owner_category_id"],
            ["content_categories.id"],
            name=op.f("fk_media_owner_mappings_production_owner_category_id_content_categories"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_media_owner_mappings_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"],
            ["users.id"],
            name=op.f("fk_media_owner_mappings_revoked_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_media_owner_mappings")),
        sa.UniqueConstraint(
            "organization_id",
            "user_id",
            "production_owner_category_id",
            name="uq_media_owner_mappings_org_user_category",
        ),
    )
    op.create_index(
        "ix_media_owner_mappings_organization_id_user_id",
        "media_owner_mappings",
        ["organization_id", "user_id"],
    )


def downgrade() -> None:
    # Mappings only authorize future submissions; dropping them loses no media attribution
    # (uploads record their owner on the session row from U4 on).
    op.drop_index(
        "ix_media_owner_mappings_organization_id_user_id", table_name="media_owner_mappings"
    )
    op.drop_table("media_owner_mappings")
