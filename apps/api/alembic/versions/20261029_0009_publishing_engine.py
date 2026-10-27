"""Reels publishing engine: publication_uploads, publisher_workers, per-Page holds (M3B.2A)

Revision ID: 0009_publishing_engine
Revises: 0008_meta_connections
Create Date: 2026-10-29 09:00:00 UTC
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0009_publishing_engine"
down_revision: str | None = "0008_meta_connections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROVIDERS = ("meta",)
PHASES = (
    "pending",
    "validating",
    "session_starting",
    "uploading",
    "upload_complete",
    "processing",
    "finalizing",
    "verifying",
    "retry_wait",
    "published",
    "failed",
    "needs_reconciliation",
    "abandoned",
)
PUBLISH_REQUEST_STATUSES = (
    "not_sent",
    "sent",
    "acknowledged",
    "confirmed",
    "rejected",
    "unknown",
)
LIVE_PHASES_SQL = "phase NOT IN ('published', 'failed', 'abandoned')"


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
    op.create_table(
        "publication_uploads",
        _uuid_pk(),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("social_account_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("media_asset_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("is_mock", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("publish_request_status", sa.String(length=32), nullable=False),
        sa.Column("provider_session_id", sa.String(length=2048), nullable=True),
        sa.Column("provider_video_id", sa.String(length=255), nullable=True),
        sa.Column("external_post_id", sa.String(length=255), nullable=True),
        sa.Column("external_post_url", sa.String(length=2048), nullable=True),
        sa.Column("file_relative_path", sa.String(length=1024), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("file_mtime", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bytes_total", sa.BigInteger(), nullable=True),
        sa.Column("bytes_sent", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("processing_status", sa.String(length=64), nullable=True),
        sa.Column(
            "last_status",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("poll_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("finish_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finish_response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_transient", sa.Boolean(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_retries", sa.Integer(), server_default=sa.text("5"), nullable=False),
        _ts("next_action_at"),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        _fk("publication_uploads", "organization_id", "organizations", ondelete="CASCADE"),
        _fk("publication_uploads", "job_id", "publication_jobs", ondelete="CASCADE"),
        _fk("publication_uploads", "attempt_id", "publication_attempts", ondelete="CASCADE"),
        _fk("publication_uploads", "social_account_id", "social_accounts", ondelete="CASCADE"),
        _fk("publication_uploads", "media_asset_id", "media_assets", ondelete="CASCADE"),
        _fk("publication_uploads", "snapshot_id", "publication_snapshots", ondelete="SET NULL"),
        sa.CheckConstraint(
            _in_list("provider", PROVIDERS),
            name=op.f("ck_publication_uploads_connection_provider"),
        ),
        sa.CheckConstraint(
            _in_list("phase", PHASES), name=op.f("ck_publication_uploads_upload_phase")
        ),
        sa.CheckConstraint(
            _in_list("publish_request_status", PUBLISH_REQUEST_STATUSES),
            name=op.f("ck_publication_uploads_publish_request_status"),
        ),
        sa.UniqueConstraint("attempt_id", name="uq_publication_uploads_attempt_id"),
    )
    op.create_index(
        "uq_publication_uploads_job_live",
        "publication_uploads",
        ["job_id"],
        unique=True,
        postgresql_where=sa.text(LIVE_PHASES_SQL),
    )
    op.create_index(
        "ix_publication_uploads_next_action",
        "publication_uploads",
        ["next_action_at"],
        postgresql_where=sa.text(LIVE_PHASES_SQL),
    )
    op.create_index(
        "ix_publication_uploads_social_account_id", "publication_uploads", ["social_account_id"]
    )
    op.create_index(
        "ix_publication_uploads_organization_id_phase",
        "publication_uploads",
        ["organization_id", "phase"],
    )

    op.create_table(
        "publisher_workers",
        _uuid_pk(),
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("max_concurrency", sa.Integer(), nullable=False),
        sa.Column("in_flight", sa.Integer(), server_default=sa.text("0"), nullable=False),
        _ts("started_at"),
        _ts("last_seen_at"),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("worker_id", name=op.f("uq_publisher_workers_worker_id")),
    )

    op.add_column(
        "social_accounts",
        sa.Column("publish_hold_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "social_accounts", sa.Column("publish_hold_reason", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("social_accounts", "publish_hold_reason")
    op.drop_column("social_accounts", "publish_hold_until")
    op.drop_table("publisher_workers")
    op.drop_index("ix_publication_uploads_organization_id_phase", table_name="publication_uploads")
    op.drop_index("ix_publication_uploads_social_account_id", table_name="publication_uploads")
    op.drop_index("ix_publication_uploads_next_action", table_name="publication_uploads")
    op.drop_index("uq_publication_uploads_job_live", table_name="publication_uploads")
    op.drop_table("publication_uploads")
