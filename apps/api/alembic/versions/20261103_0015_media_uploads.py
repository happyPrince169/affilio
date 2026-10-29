"""Editor upload sessions and asset attribution (upload track U4, ADR 0012)

Revision ID: 0015_media_uploads
Revises: 0014_owner_mappings
Create Date: 2026-11-03 09:00:00 UTC
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015_media_uploads"
down_revision: str | None = "0014_owner_mappings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPLOAD_STATUSES = (
    "created",
    "uploading",
    "uploaded",
    "verifying",
    "finalizing",
    "completed",
    "duplicate",
    "failed",
    "aborted",
    "expired",
)
INGEST_SOURCES = ("scan", "upload")


def _in_list(column: str, values: Sequence[str]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


def upgrade() -> None:
    # --- asset attribution: how it entered the library and who delivered it
    op.add_column(
        "media_assets",
        sa.Column(
            "ingest_source",
            sa.String(length=32),
            server_default=sa.text("'scan'"),
            nullable=False,
        ),
    )
    op.add_column(
        "media_assets", sa.Column("uploaded_by_user_id", sa.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "media_assets", sa.Column("revision_of_asset_id", sa.UUID(as_uuid=True), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_media_assets_ingest_source"),
        "media_assets",
        _in_list("ingest_source", INGEST_SOURCES),
    )
    op.create_foreign_key(
        op.f("fk_media_assets_uploaded_by_user_id_users"),
        "media_assets",
        "users",
        ["uploaded_by_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_media_assets_revision_of_asset_id_media_assets"),
        "media_assets",
        "media_assets",
        ["revision_of_asset_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # --- upload sessions
    op.create_table(
        "media_uploads",
        sa.Column(
            "id", sa.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_by_user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("submitted_for_user_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("production_owner_category_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("production_owner_name", sa.String(length=120), nullable=False),
        sa.Column("product_category_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("content_type_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("replaces_asset_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("safe_filename", sa.String(length=160), nullable=False),
        sa.Column("expected_size", sa.BigInteger(), nullable=False),
        sa.Column("client_sha256", sa.String(length=64), nullable=True),
        sa.Column("chunk_bytes", sa.Integer(), nullable=False),
        sa.Column("committed_offset", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_chunk_offset", sa.BigInteger(), nullable=True),
        sa.Column("last_chunk_sha256", sa.String(length=64), nullable=True),
        sa.Column("last_chunk_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("staging_name", sa.String(length=80), nullable=False),
        sa.Column(
            "status", sa.String(length=32), server_default=sa.text("'created'"), nullable=False
        ),
        sa.Column("verified_sha256", sa.String(length=64), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=True),
        sa.Column("final_relative_path", sa.String(length=1024), nullable=True),
        sa.Column("media_asset_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("duplicate_of_asset_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_detail", sa.Text(), nullable=True),
        sa.Column("finalize_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
            _in_list("status", UPLOAD_STATUSES), name=op.f("ck_media_uploads_upload_status")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_media_uploads_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_user_id"],
            ["users.id"],
            name=op.f("fk_media_uploads_uploaded_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["submitted_for_user_id"],
            ["users.id"],
            name=op.f("fk_media_uploads_submitted_for_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["production_owner_category_id"],
            ["content_categories.id"],
            name=op.f("fk_media_uploads_production_owner_category_id_content_categories"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["product_category_id"],
            ["content_categories.id"],
            name=op.f("fk_media_uploads_product_category_id_content_categories"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["content_type_id"],
            ["content_categories.id"],
            name=op.f("fk_media_uploads_content_type_id_content_categories"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["replaces_asset_id"],
            ["media_assets.id"],
            name=op.f("fk_media_uploads_replaces_asset_id_media_assets"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["media_asset_id"],
            ["media_assets.id"],
            name=op.f("fk_media_uploads_media_asset_id_media_assets"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["duplicate_of_asset_id"],
            ["media_assets.id"],
            name=op.f("fk_media_uploads_duplicate_of_asset_id_media_assets"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_media_uploads")),
        sa.UniqueConstraint("staging_name", name=op.f("uq_media_uploads_staging_name")),
    )
    op.create_index(
        "ix_media_uploads_organization_id_uploaded_by_user_id_status",
        "media_uploads",
        ["organization_id", "uploaded_by_user_id", "status"],
    )
    op.create_index("ix_media_uploads_status_updated_at", "media_uploads", ["status", "updated_at"])
    op.create_index(
        "uq_media_uploads_org_verified_sha256_finalized",
        "media_uploads",
        ["organization_id", "verified_sha256"],
        unique=True,
        postgresql_where=sa.text("status IN ('finalizing', 'completed')"),
    )


def downgrade() -> None:
    # Upload session history is dropped with the table; the assets themselves stay, only
    # their uploader and revision attribution columns go.
    op.drop_index("uq_media_uploads_org_verified_sha256_finalized", table_name="media_uploads")
    op.drop_index("ix_media_uploads_status_updated_at", table_name="media_uploads")
    op.drop_index(
        "ix_media_uploads_organization_id_uploaded_by_user_id_status", table_name="media_uploads"
    )
    op.drop_table("media_uploads")

    op.drop_constraint(
        op.f("fk_media_assets_revision_of_asset_id_media_assets"),
        "media_assets",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_media_assets_uploaded_by_user_id_users"), "media_assets", type_="foreignkey"
    )
    op.drop_constraint(op.f("ck_media_assets_ingest_source"), "media_assets", type_="check")
    op.drop_column("media_assets", "revision_of_asset_id")
    op.drop_column("media_assets", "uploaded_by_user_id")
    op.drop_column("media_assets", "ingest_source")
