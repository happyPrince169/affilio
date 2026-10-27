"""Meta connection infrastructure: platform_connections, account_credentials, oauth_states,
connection facts on social_accounts (M3B.1A)

Revision ID: 0008_meta_connections
Revises: 0007_publication_revalidation
Create Date: 2026-10-28 09:00:00 UTC
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0008_meta_connections"
down_revision: str | None = "0007_publication_revalidation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROVIDERS = ("meta",)
CONNECTION_STATUSES = ("active", "revoked", "expired", "invalid")
CREDENTIAL_KINDS = ("meta_user_long_lived", "meta_page")
CREDENTIAL_STATUSES = ("active", "superseded", "revoked", "invalid")
OAUTH_STATUSES = ("pending", "completed", "denied", "failed", "expired")


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


def _jsonb(name: str, default: str = "[]") -> sa.Column:
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


def _bool(name: str) -> sa.Column:
    return sa.Column(name, sa.Boolean(), server_default=sa.false(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "platform_connections",
        _uuid_pk(),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("external_user_id", sa.String(length=255), nullable=False),
        sa.Column("external_user_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        _jsonb("granted_scopes"),
        _jsonb("missing_scopes"),
        _jsonb("discovered_pages"),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_access_expires_at", sa.DateTime(timezone=True), nullable=True),
        _bool("is_mock"),
        sa.Column("connected_by_user_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("connected_by_label", sa.String(length=255), nullable=False),
        _ts("connected_at"),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=255), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        _fk("platform_connections", "organization_id", "organizations", ondelete="CASCADE"),
        _fk("platform_connections", "connected_by_user_id", "users", ondelete="SET NULL"),
        sa.CheckConstraint(
            _in_list("provider", PROVIDERS),
            name=op.f("ck_platform_connections_connection_provider"),
        ),
        sa.CheckConstraint(
            _in_list("status", CONNECTION_STATUSES),
            name=op.f("ck_platform_connections_connection_status"),
        ),
        sa.UniqueConstraint(
            "organization_id",
            "provider",
            "external_user_id",
            name="uq_platform_connections_org_provider_user",
        ),
    )

    op.create_table(
        "account_credentials",
        _uuid_pk(),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("social_account_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("external_subject_id", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("key_id", sa.String(length=32), nullable=False),
        sa.Column("token_fingerprint", sa.String(length=64), nullable=False),
        _jsonb("scopes"),
        sa.Column("page_tasks", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        _ts("issued_at"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_access_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_reason", sa.String(length=64), nullable=True),
        _bool("is_mock"),
        sa.Column("created_by_label", sa.String(length=255), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        _fk("account_credentials", "organization_id", "organizations", ondelete="CASCADE"),
        _fk("account_credentials", "connection_id", "platform_connections", ondelete="CASCADE"),
        _fk("account_credentials", "social_account_id", "social_accounts", ondelete="CASCADE"),
        sa.CheckConstraint(
            _in_list("kind", CREDENTIAL_KINDS), name=op.f("ck_account_credentials_credential_kind")
        ),
        sa.CheckConstraint(
            _in_list("status", CREDENTIAL_STATUSES),
            name=op.f("ck_account_credentials_credential_status"),
        ),
    )
    op.create_index(
        "uq_account_credentials_active_subject",
        "account_credentials",
        ["connection_id", "external_subject_id", "kind"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_account_credentials_social_account_id", "account_credentials", ["social_account_id"]
    )
    op.create_index(
        "ix_account_credentials_organization_id", "account_credentials", ["organization_id"]
    )

    op.create_table(
        "oauth_states",
        _uuid_pk(),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("redirect_uri", sa.String(length=2048), nullable=False),
        _jsonb("requested_scopes"),
        sa.Column("mock_scenario", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.String(length=255), nullable=True),
        sa.Column("connection_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        _ts("created_at"),
        _fk("oauth_states", "organization_id", "organizations", ondelete="CASCADE"),
        _fk("oauth_states", "user_id", "users", ondelete="CASCADE"),
        _fk("oauth_states", "session_id", "user_sessions", ondelete="SET NULL"),
        _fk("oauth_states", "connection_id", "platform_connections", ondelete="SET NULL"),
        sa.CheckConstraint(
            _in_list("provider", PROVIDERS), name=op.f("ck_oauth_states_connection_provider")
        ),
        sa.CheckConstraint(
            _in_list("status", OAUTH_STATUSES), name=op.f("ck_oauth_states_oauth_attempt_status")
        ),
        sa.UniqueConstraint("state_hash", name=op.f("uq_oauth_states_state_hash")),
    )
    op.create_index(
        "ix_oauth_states_organization_id_created_at",
        "oauth_states",
        ["organization_id", "created_at"],
    )

    op.add_column(
        "social_accounts", sa.Column("connection_id", sa.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "social_accounts", sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "social_accounts",
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "social_accounts", sa.Column("disconnect_reason", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "social_accounts",
        sa.Column("page_tasks", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("social_accounts", _bool("is_mock"))
    op.create_foreign_key(
        op.f("fk_social_accounts_connection_id_platform_connections"),
        "social_accounts",
        "platform_connections",
        ["connection_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_social_accounts_connection_id_platform_connections"),
        "social_accounts",
        type_="foreignkey",
    )
    for column in (
        "is_mock",
        "page_tasks",
        "disconnect_reason",
        "last_verified_at",
        "connected_at",
        "connection_id",
    ):
        op.drop_column("social_accounts", column)
    op.drop_index("ix_oauth_states_organization_id_created_at", table_name="oauth_states")
    op.drop_table("oauth_states")
    op.drop_index("ix_account_credentials_organization_id", table_name="account_credentials")
    op.drop_index("ix_account_credentials_social_account_id", table_name="account_credentials")
    op.drop_index("uq_account_credentials_active_subject", table_name="account_credentials")
    op.drop_table("account_credentials")
    op.drop_table("platform_connections")
