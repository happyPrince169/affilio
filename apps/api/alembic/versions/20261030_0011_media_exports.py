"""Publish-ready exports: media_export_jobs + variant provenance (Export pipeline E1)

Revision ID: 0011_media_exports
Revises: 0010_overdue_review
Create Date: 2026-10-30 09:00:00 UTC
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0011_media_exports"
down_revision: str | None = "0010_overdue_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXPORT_JOB_STATUSES = ("queued", "running", "ready", "skipped", "blocked", "failed", "cancelled")
EXPORT_TRIGGERS = ("manual", "cli", "approval", "publish")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


def upgrade() -> None:
    # --- provenance of derived variants (exports) -------------------------------------------
    op.add_column(
        "media_file_variants",
        sa.Column("derived_from_variant_id", sa.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "media_file_variants", sa.Column("recipe_key", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "media_file_variants",
        sa.Column("source_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "media_file_variants", sa.Column("source_file_size", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "media_file_variants",
        sa.Column("source_modified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_media_file_variants_derived_from_variant_id_media_file_variants"),
        "media_file_variants",
        "media_file_variants",
        ["derived_from_variant_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "uq_media_file_variants_asset_export_recipe",
        "media_file_variants",
        ["asset_id", "recipe_key"],
        unique=True,
        postgresql_where=sa.text("kind = 'export'"),
    )

    # --- export jobs (the export worker's queue) ----------------------------------------------
    op.create_table(
        "media_export_jobs",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("media_asset_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("source_variant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("output_variant_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("recipe_key", sa.String(length=64), nullable=False),
        sa.Column(
            "recipe",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("source_file_size", sa.BigInteger(), nullable=True),
        sa.Column("source_modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("attempt", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        sa.Column("requested_by", sa.String(length=255), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "stats",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
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
            _in_list("status", EXPORT_JOB_STATUSES),
            name=op.f("ck_media_export_jobs_export_job_status"),
        ),
        sa.CheckConstraint(
            _in_list("trigger", EXPORT_TRIGGERS), name=op.f("ck_media_export_jobs_export_trigger")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_media_export_jobs_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["media_asset_id"],
            ["media_assets.id"],
            name=op.f("fk_media_export_jobs_media_asset_id_media_assets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_variant_id"],
            ["media_file_variants.id"],
            name=op.f("fk_media_export_jobs_source_variant_id_media_file_variants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["output_variant_id"],
            ["media_file_variants.id"],
            name=op.f("fk_media_export_jobs_output_variant_id_media_file_variants"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_media_export_jobs")),
    )
    op.create_index(
        "ix_media_export_jobs_organization_id_created_at",
        "media_export_jobs",
        ["organization_id", "created_at"],
    )
    op.create_index(
        "ix_media_export_jobs_status_next_attempt_at",
        "media_export_jobs",
        ["status", "next_attempt_at"],
    )
    op.create_index("ix_media_export_jobs_media_asset_id", "media_export_jobs", ["media_asset_id"])
    op.create_index(
        "uq_media_export_jobs_active_asset_recipe",
        "media_export_jobs",
        ["media_asset_id", "recipe_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_media_export_jobs_active_asset_recipe", table_name="media_export_jobs")
    op.drop_index("ix_media_export_jobs_media_asset_id", table_name="media_export_jobs")
    op.drop_index("ix_media_export_jobs_status_next_attempt_at", table_name="media_export_jobs")
    op.drop_index("ix_media_export_jobs_organization_id_created_at", table_name="media_export_jobs")
    op.drop_table("media_export_jobs")
    op.drop_index("uq_media_file_variants_asset_export_recipe", table_name="media_file_variants")
    op.drop_constraint(
        op.f("fk_media_file_variants_derived_from_variant_id_media_file_variants"),
        "media_file_variants",
        type_="foreignkey",
    )
    # Export variant rows would dangle without their provenance: remove them with the feature.
    op.execute("DELETE FROM media_file_variants WHERE kind = 'export'")
    for column in (
        "source_modified_at",
        "source_file_size",
        "source_fingerprint",
        "recipe_key",
        "derived_from_variant_id",
    ):
        op.drop_column("media_file_variants", column)
