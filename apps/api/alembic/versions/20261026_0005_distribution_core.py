"""authentication, audit, social account registry, affiliate links, campaigns, publications

Revision ID: 0005_distribution_core
Revises: 0004_procurement_production
Create Date: 2026-10-26 20:00:00 UTC
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005_distribution_core"
down_revision: str | None = "0004_procurement_production"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLATFORMS = ("shopee", "facebook", "tiktok", "instagram", "youtube")
AUDIT_ACTOR_KINDS = ("user", "service", "system")
ACCOUNT_TYPES = ("page", "profile", "group", "channel", "shop")
ACCOUNT_SOURCES = ("manual", "oauth")
LINK_SOURCES = ("manual", "import")
LINK_STATUSES = ("active", "archived")
CAMPAIGN_STATUSES = ("draft", "active", "completed", "archived")
PUBLICATION_STATUSES = (
    "draft",
    "ready_for_review",
    "approved",
    "scheduled",
    "ready_to_publish",
    "publishing",
    "published",
    "failed",
    "cancelled",
    "needs_reconciliation",
)
EVIDENCE_KINDS = ("operator_reported", "api_verified")
PUBLISH_MODES = ("assisted", "api")
ATTEMPT_STATUSES = ("started", "succeeded", "failed", "ambiguous")


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


def _fk(table: str, column: str, ref_table: str, *, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{ref_table}.id"],
        name=op.f(f"fk_{table}_{column}_{ref_table}"),
        ondelete=ondelete,
    )


def upgrade() -> None:
    # --- identity: organization timezone, user profile, sessions, audit ------------------
    op.add_column(
        "organizations",
        sa.Column(
            "timezone", sa.String(length=64), server_default=sa.text("'UTC'"), nullable=False
        ),
    )
    op.add_column("users", sa.Column("display_name", sa.String(length=120), nullable=True))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "user_sessions",
        _uuid_pk(),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _ts("last_seen_at"),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        _ts("created_at"),
        _fk("user_sessions", "user_id", "users", ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash", name=op.f("uq_user_sessions_token_hash")),
    )
    op.create_index(
        "ix_user_sessions_user_id_expires_at", "user_sessions", ["user_id", "expires_at"]
    )

    op.create_table(
        "audit_events",
        _uuid_pk(),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_kind", sa.String(length=32), nullable=False),
        sa.Column("actor_user_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_label", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("entity_type", sa.String(length=60), nullable=False),
        sa.Column("entity_id", sa.UUID(as_uuid=True), nullable=True),
        _jsonb("data", "{}"),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        _ts("created_at"),
        _fk("audit_events", "organization_id", "organizations", ondelete="CASCADE"),
        _fk("audit_events", "actor_user_id", "users", ondelete="SET NULL"),
        sa.CheckConstraint(
            _in_list("actor_kind", AUDIT_ACTOR_KINDS),
            name=op.f("ck_audit_events_audit_actor_kind"),
        ),
    )
    op.create_index(
        "ix_audit_events_organization_id_created_at",
        "audit_events",
        ["organization_id", "created_at"],
    )
    op.create_index("ix_audit_events_entity", "audit_events", ["entity_type", "entity_id"])

    # --- accounts: registry fields ---------------------------------------------------------
    op.add_column("social_accounts", sa.Column("handle", sa.String(length=120), nullable=True))
    op.add_column(
        "social_accounts",
        sa.Column(
            "account_type",
            sa.String(length=32),
            server_default=sa.text("'page'"),
            nullable=False,
        ),
    )
    op.add_column("social_accounts", _jsonb("capabilities", "{}"))
    op.add_column(
        "social_accounts",
        sa.Column("is_verified", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "social_accounts",
        sa.Column(
            "source", sa.String(length=32), server_default=sa.text("'manual'"), nullable=False
        ),
    )
    op.add_column(
        "social_accounts", sa.Column("profile_url", sa.String(length=2048), nullable=True)
    )
    op.add_column("social_accounts", sa.Column("notes", sa.Text(), nullable=True))
    op.add_column(
        "social_accounts", sa.Column("registered_by", sa.String(length=255), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_social_accounts_social_account_type"),
        "social_accounts",
        _in_list("account_type", ACCOUNT_TYPES),
    )
    op.create_check_constraint(
        op.f("ck_social_accounts_social_account_source"),
        "social_accounts",
        _in_list("source", ACCOUNT_SOURCES),
    )

    # --- content: affiliate links ----------------------------------------------------------
    op.create_table(
        "affiliate_links",
        _uuid_pk(),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("product_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("url_normalized", sa.String(length=2048), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        _fk("affiliate_links", "organization_id", "organizations", ondelete="CASCADE"),
        _fk("affiliate_links", "product_id", "products", ondelete="SET NULL"),
        sa.CheckConstraint(
            _in_list("platform", PLATFORMS), name=op.f("ck_affiliate_links_platform")
        ),
        sa.CheckConstraint(
            _in_list("source", LINK_SOURCES),
            name=op.f("ck_affiliate_links_affiliate_link_source"),
        ),
        sa.CheckConstraint(
            _in_list("status", LINK_STATUSES),
            name=op.f("ck_affiliate_links_affiliate_link_status"),
        ),
    )
    op.create_index(
        "ix_affiliate_links_organization_id_status",
        "affiliate_links",
        ["organization_id", "status"],
    )
    op.create_index("ix_affiliate_links_product_id", "affiliate_links", ["product_id"])
    op.create_index(
        "uq_affiliate_links_org_platform_url",
        "affiliate_links",
        ["organization_id", "platform", "url_normalized"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    # --- publishing: campaigns -------------------------------------------------------------
    op.create_table(
        "campaigns",
        _uuid_pk(),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=True),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(as_uuid=True), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        _fk("campaigns", "organization_id", "organizations", ondelete="CASCADE"),
        _fk("campaigns", "created_by_user_id", "users", ondelete="SET NULL"),
        sa.CheckConstraint(
            _in_list("status", CAMPAIGN_STATUSES), name=op.f("ck_campaigns_campaign_status")
        ),
    )
    op.create_index(
        "ix_campaigns_organization_id_status", "campaigns", ["organization_id", "status"]
    )

    op.create_table(
        "campaign_media",
        _uuid_pk(),
        sa.Column("campaign_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("media_asset_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        _ts("created_at"),
        _fk("campaign_media", "campaign_id", "campaigns", ondelete="CASCADE"),
        _fk("campaign_media", "media_asset_id", "media_assets", ondelete="CASCADE"),
        sa.UniqueConstraint(
            "campaign_id",
            "media_asset_id",
            name=op.f("uq_campaign_media_campaign_id_media_asset_id"),
        ),
    )
    op.create_index("ix_campaign_media_media_asset_id", "campaign_media", ["media_asset_id"])

    op.create_table(
        "campaign_targets",
        _uuid_pk(),
        sa.Column("campaign_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("social_account_id", sa.UUID(as_uuid=True), nullable=False),
        _ts("created_at"),
        _fk("campaign_targets", "campaign_id", "campaigns", ondelete="CASCADE"),
        _fk("campaign_targets", "social_account_id", "social_accounts", ondelete="CASCADE"),
        sa.UniqueConstraint(
            "campaign_id",
            "social_account_id",
            name=op.f("uq_campaign_targets_campaign_id_social_account_id"),
        ),
    )
    op.create_index(
        "ix_campaign_targets_social_account_id", "campaign_targets", ["social_account_id"]
    )

    # --- publishing: jobs, snapshots, links, attempts --------------------------------------
    op.create_table(
        "publication_jobs",
        _uuid_pk(),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("media_asset_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("social_account_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("intent_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("cta", sa.String(length=255), nullable=True),
        _jsonb("hashtags", "[]"),
        sa.Column("content_notes", sa.Text(), nullable=True),
        sa.Column("content_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_snapshot_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_post_id", sa.String(length=255), nullable=True),
        sa.Column("external_post_url", sa.String(length=2048), nullable=True),
        sa.Column("evidence_kind", sa.String(length=32), nullable=True),
        sa.Column("confirmed_by_user_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("confirmed_by_label", sa.String(length=255), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmation_note", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(as_uuid=True), nullable=True),
        _ts("status_changed_at"),
        _ts("created_at"),
        _ts("updated_at"),
        _fk("publication_jobs", "organization_id", "organizations", ondelete="CASCADE"),
        _fk("publication_jobs", "campaign_id", "campaigns", ondelete="CASCADE"),
        _fk("publication_jobs", "media_asset_id", "media_assets", ondelete="CASCADE"),
        _fk("publication_jobs", "social_account_id", "social_accounts", ondelete="CASCADE"),
        _fk("publication_jobs", "confirmed_by_user_id", "users", ondelete="SET NULL"),
        _fk("publication_jobs", "created_by_user_id", "users", ondelete="SET NULL"),
        sa.UniqueConstraint("intent_id", name=op.f("uq_publication_jobs_intent_id")),
        sa.UniqueConstraint(
            "campaign_id",
            "media_asset_id",
            "social_account_id",
            name=op.f("uq_publication_jobs_campaign_media_account"),
        ),
        sa.CheckConstraint(
            _in_list("platform", PLATFORMS), name=op.f("ck_publication_jobs_platform")
        ),
        sa.CheckConstraint(
            _in_list("status", PUBLICATION_STATUSES),
            name=op.f("ck_publication_jobs_publication_status"),
        ),
        sa.CheckConstraint(
            _in_list("evidence_kind", EVIDENCE_KINDS),
            name=op.f("ck_publication_jobs_evidence_kind"),
        ),
    )
    op.create_index(
        "uq_publication_jobs_org_idempotency_key",
        "publication_jobs",
        ["organization_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "uq_publication_jobs_account_external_post",
        "publication_jobs",
        ["social_account_id", "external_post_id"],
        unique=True,
        postgresql_where=sa.text("external_post_id IS NOT NULL"),
    )
    op.create_index(
        "ix_publication_jobs_organization_id_status",
        "publication_jobs",
        ["organization_id", "status"],
    )
    op.create_index(
        "ix_publication_jobs_organization_id_scheduled_at",
        "publication_jobs",
        ["organization_id", "scheduled_at"],
    )
    op.create_index(
        "ix_publication_jobs_social_account_id_scheduled_at",
        "publication_jobs",
        ["social_account_id", "scheduled_at"],
    )
    op.create_index("ix_publication_jobs_media_asset_id", "publication_jobs", ["media_asset_id"])

    op.create_table(
        "publication_snapshots",
        _uuid_pk(),
        sa.Column("job_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("content_version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("cta", sa.String(length=255), nullable=True),
        sa.Column("hashtags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("links", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("media", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("target", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("approved_by_user_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_by_label", sa.String(length=255), nullable=False),
        _ts("approved_at"),
        _fk("publication_snapshots", "job_id", "publication_jobs", ondelete="CASCADE"),
        _fk("publication_snapshots", "approved_by_user_id", "users", ondelete="SET NULL"),
    )
    op.create_index("ix_publication_snapshots_job_id", "publication_snapshots", ["job_id"])
    op.create_foreign_key(
        op.f("fk_publication_jobs_approved_snapshot_id_publication_snapshots"),
        "publication_jobs",
        "publication_snapshots",
        ["approved_snapshot_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "publication_links",
        _uuid_pk(),
        sa.Column("job_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("affiliate_link_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        _ts("created_at"),
        _fk("publication_links", "job_id", "publication_jobs", ondelete="CASCADE"),
        _fk("publication_links", "affiliate_link_id", "affiliate_links", ondelete="CASCADE"),
        sa.UniqueConstraint(
            "job_id",
            "affiliate_link_id",
            name=op.f("uq_publication_links_job_id_affiliate_link_id"),
        ),
    )
    op.create_index(
        "ix_publication_links_affiliate_link_id", "publication_links", ["affiliate_link_id"]
    )

    op.create_table(
        "publication_attempts",
        _uuid_pk(),
        sa.Column("job_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("adapter", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("snapshot_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("started_by_label", sa.String(length=255), nullable=False),
        _ts("started_at"),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        _jsonb("detail", "{}"),
        sa.Column("error", sa.Text(), nullable=True),
        _ts("created_at"),
        _fk("publication_attempts", "job_id", "publication_jobs", ondelete="CASCADE"),
        _fk("publication_attempts", "snapshot_id", "publication_snapshots", ondelete="SET NULL"),
        sa.UniqueConstraint(
            "job_id", "attempt_no", name=op.f("uq_publication_attempts_job_id_attempt_no")
        ),
        sa.CheckConstraint(
            _in_list("mode", PUBLISH_MODES), name=op.f("ck_publication_attempts_publish_mode")
        ),
        sa.CheckConstraint(
            _in_list("status", ATTEMPT_STATUSES),
            name=op.f("ck_publication_attempts_publication_attempt_status"),
        ),
    )


def downgrade() -> None:
    op.drop_table("publication_attempts")
    op.drop_index("ix_publication_links_affiliate_link_id", table_name="publication_links")
    op.drop_table("publication_links")
    op.drop_constraint(
        op.f("fk_publication_jobs_approved_snapshot_id_publication_snapshots"),
        "publication_jobs",
        type_="foreignkey",
    )
    op.drop_index("ix_publication_snapshots_job_id", table_name="publication_snapshots")
    op.drop_table("publication_snapshots")
    for name in (
        "ix_publication_jobs_media_asset_id",
        "ix_publication_jobs_social_account_id_scheduled_at",
        "ix_publication_jobs_organization_id_scheduled_at",
        "ix_publication_jobs_organization_id_status",
        "uq_publication_jobs_account_external_post",
        "uq_publication_jobs_org_idempotency_key",
    ):
        op.drop_index(name, table_name="publication_jobs")
    op.drop_table("publication_jobs")
    op.drop_index("ix_campaign_targets_social_account_id", table_name="campaign_targets")
    op.drop_table("campaign_targets")
    op.drop_index("ix_campaign_media_media_asset_id", table_name="campaign_media")
    op.drop_table("campaign_media")
    op.drop_index("ix_campaigns_organization_id_status", table_name="campaigns")
    op.drop_table("campaigns")
    op.drop_index("uq_affiliate_links_org_platform_url", table_name="affiliate_links")
    op.drop_index("ix_affiliate_links_product_id", table_name="affiliate_links")
    op.drop_index("ix_affiliate_links_organization_id_status", table_name="affiliate_links")
    op.drop_table("affiliate_links")
    op.drop_constraint(
        op.f("ck_social_accounts_social_account_source"), "social_accounts", type_="check"
    )
    op.drop_constraint(
        op.f("ck_social_accounts_social_account_type"), "social_accounts", type_="check"
    )
    for column in (
        "registered_by",
        "notes",
        "profile_url",
        "source",
        "is_verified",
        "capabilities",
        "account_type",
        "handle",
    ):
        op.drop_column("social_accounts", column)
    op.drop_index("ix_audit_events_entity", table_name="audit_events")
    op.drop_index("ix_audit_events_organization_id_created_at", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_user_sessions_user_id_expires_at", table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "display_name")
    op.drop_column("organizations", "timezone")
