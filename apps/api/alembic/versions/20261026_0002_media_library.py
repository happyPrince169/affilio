"""media library: media_roots, media_assets, media_file_variants, media_scan_jobs

Revision ID: 0002_media_library
Revises: 0001_foundation
Create Date: 2026-10-26 12:00:00 UTC
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_media_library"
down_revision: str | None = "0001_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MEDIA_ROOT_KINDS = ("original", "proxy", "thumbnail", "export")
MEDIA_ROOT_ACCESS = ("read_only", "read_write")
MEDIA_ASSET_STATUSES = ("active", "missing", "unsupported")
MEDIA_VARIANT_KINDS = ("original", "proxy", "thumbnail", "export")
MEDIA_VARIANT_STATUSES = ("present", "missing")
THUMBNAIL_STATUSES = ("pending", "ready", "failed", "skipped")
SCAN_JOB_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")
SCAN_TRIGGERS = ("manual", "cli", "scheduled")


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


def _ts(name: str, *, nullable: bool = False, default_now: bool = True) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.text("now()") if default_now else None,
        nullable=nullable,
    )


def _probe_columns() -> list[sa.Column]:
    return [
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("frame_rate", sa.Float(), nullable=True),
        sa.Column("video_codec", sa.String(length=64), nullable=True),
        sa.Column("audio_codec", sa.String(length=64), nullable=True),
        sa.Column("pixel_format", sa.String(length=32), nullable=True),
        sa.Column("bit_depth", sa.Integer(), nullable=True),
        sa.Column("bit_rate", sa.BigInteger(), nullable=True),
        sa.Column("container_format", sa.String(length=64), nullable=True),
        sa.Column("rotation", sa.Integer(), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "media_roots",
        _uuid_pk(),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("access", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("scan_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_scan_at", sa.DateTime(timezone=True), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_media_roots_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "organization_id", "key", name=op.f("uq_media_roots_organization_id_key")
        ),
        sa.CheckConstraint(
            _in_list("kind", MEDIA_ROOT_KINDS), name=op.f("ck_media_roots_media_root_kind")
        ),
        sa.CheckConstraint(
            _in_list("access", MEDIA_ROOT_ACCESS), name=op.f("ck_media_roots_media_root_access")
        ),
    )

    op.create_table(
        "media_assets",
        _uuid_pk(),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("duplicate_of_id", sa.UUID(as_uuid=True), nullable=True),
        *_probe_columns(),
        sa.Column("probe_error", sa.Text(), nullable=True),
        sa.Column("thumbnail_status", sa.String(length=32), nullable=False),
        sa.Column("thumbnail_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("thumbnail_error", sa.Text(), nullable=True),
        _ts("first_seen_at"),
        _ts("last_seen_at"),
        sa.Column("missing_since", sa.DateTime(timezone=True), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_media_assets_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["duplicate_of_id"],
            ["media_assets.id"],
            name=op.f("fk_media_assets_duplicate_of_id_media_assets"),
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            _in_list("status", MEDIA_ASSET_STATUSES),
            name=op.f("ck_media_assets_media_asset_status"),
        ),
        sa.CheckConstraint(
            _in_list("thumbnail_status", THUMBNAIL_STATUSES),
            name=op.f("ck_media_assets_thumbnail_status"),
        ),
    )
    op.create_index(
        "ix_media_assets_organization_id_status", "media_assets", ["organization_id", "status"]
    )
    op.create_index(
        "ix_media_assets_organization_id_created_at",
        "media_assets",
        ["organization_id", "created_at"],
    )
    op.create_index("ix_media_assets_thumbnail_status", "media_assets", ["thumbnail_status"])

    op.create_table(
        "media_file_variants",
        _uuid_pk(),
        sa.Column("asset_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("media_root_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("relative_path", sa.String(length=1024), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("extension", sa.String(length=16), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("modified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        *_probe_columns(),
        sa.Column("probe", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("probed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("probe_error", sa.Text(), nullable=True),
        _ts("last_seen_at"),
        sa.Column("missing_since", sa.DateTime(timezone=True), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["media_assets.id"],
            name=op.f("fk_media_file_variants_asset_id_media_assets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["media_root_id"],
            ["media_roots.id"],
            name=op.f("fk_media_file_variants_media_root_id_media_roots"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "media_root_id", "relative_path", name=op.f("uq_media_file_variants_root_path")
        ),
        sa.CheckConstraint(
            _in_list("kind", MEDIA_VARIANT_KINDS),
            name=op.f("ck_media_file_variants_media_variant_kind"),
        ),
        sa.CheckConstraint(
            _in_list("status", MEDIA_VARIANT_STATUSES),
            name=op.f("ck_media_file_variants_media_variant_status"),
        ),
    )
    op.create_index("ix_media_file_variants_asset_id", "media_file_variants", ["asset_id"])
    op.create_index(
        "ix_media_file_variants_root_fingerprint",
        "media_file_variants",
        ["media_root_id", "fingerprint"],
    )
    op.create_index("ix_media_file_variants_content_hash", "media_file_variants", ["content_hash"])

    op.create_table(
        "media_scan_jobs",
        _uuid_pk(),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("media_root_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        sa.Column("requested_by", sa.String(length=255), nullable=True),
        sa.Column(
            "stats",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "errors",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("full_rescan", sa.Boolean(), server_default=sa.false(), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_media_scan_jobs_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["media_root_id"],
            ["media_roots.id"],
            name=op.f("fk_media_scan_jobs_media_root_id_media_roots"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            _in_list("status", SCAN_JOB_STATUSES), name=op.f("ck_media_scan_jobs_scan_job_status")
        ),
        sa.CheckConstraint(
            _in_list("trigger", SCAN_TRIGGERS), name=op.f("ck_media_scan_jobs_scan_trigger")
        ),
    )
    op.create_index(
        "ix_media_scan_jobs_organization_id_created_at",
        "media_scan_jobs",
        ["organization_id", "created_at"],
    )
    op.create_index(
        "ix_media_scan_jobs_status_next_attempt_at",
        "media_scan_jobs",
        ["status", "next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_media_scan_jobs_status_next_attempt_at", table_name="media_scan_jobs")
    op.drop_index("ix_media_scan_jobs_organization_id_created_at", table_name="media_scan_jobs")
    op.drop_table("media_scan_jobs")
    op.drop_index("ix_media_file_variants_content_hash", table_name="media_file_variants")
    op.drop_index("ix_media_file_variants_root_fingerprint", table_name="media_file_variants")
    op.drop_index("ix_media_file_variants_asset_id", table_name="media_file_variants")
    op.drop_table("media_file_variants")
    op.drop_index("ix_media_assets_thumbnail_status", table_name="media_assets")
    op.drop_index("ix_media_assets_organization_id_created_at", table_name="media_assets")
    op.drop_index("ix_media_assets_organization_id_status", table_name="media_assets")
    op.drop_table("media_assets")
    op.drop_table("media_roots")
