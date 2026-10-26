"""foundation: organizations, users, organization_memberships, social_accounts

Revision ID: 0001_foundation
Revises:
Create Date: 2026-10-26 00:00:00 UTC
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USER_STATUSES = ("invited", "active", "suspended")
MEMBERSHIP_ROLES = ("owner", "admin", "editor", "viewer")
PLATFORMS = ("shopee", "facebook", "tiktok", "instagram", "youtube")
SOCIAL_ACCOUNT_STATUSES = ("pending", "connected", "disconnected", "disabled")


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


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def _updated_at() -> sa.Column:
    return sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "organizations",
        _uuid_pk(),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint("slug", name=op.f("uq_organizations_slug")),
    )

    op.create_table(
        "users",
        _uuid_pk(),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
        sa.CheckConstraint(_in_list("status", USER_STATUSES), name=op.f("ck_users_user_status")),
    )

    op.create_table(
        "organization_memberships",
        _uuid_pk(),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_organization_memberships_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_organization_memberships_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "user_id",
            name=op.f("uq_organization_memberships_organization_id_user_id"),
        ),
        sa.CheckConstraint(
            _in_list("role", MEMBERSHIP_ROLES),
            name=op.f("ck_organization_memberships_membership_role"),
        ),
    )
    op.create_index(
        "ix_organization_memberships_user_id",
        "organization_memberships",
        ["user_id"],
    )

    op.create_table(
        "social_accounts",
        _uuid_pk(),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("external_account_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        _created_at(),
        _updated_at(),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_social_accounts_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "platform",
            "external_account_id",
            name=op.f("uq_social_accounts_org_platform_external_id"),
        ),
        sa.CheckConstraint(
            _in_list("platform", PLATFORMS), name=op.f("ck_social_accounts_platform")
        ),
        sa.CheckConstraint(
            _in_list("status", SOCIAL_ACCOUNT_STATUSES),
            name=op.f("ck_social_accounts_social_account_status"),
        ),
    )
    op.create_index(
        "ix_social_accounts_organization_id_platform",
        "social_accounts",
        ["organization_id", "platform"],
    )


def downgrade() -> None:
    op.drop_index("ix_social_accounts_organization_id_platform", table_name="social_accounts")
    op.drop_table("social_accounts")
    op.drop_index("ix_organization_memberships_user_id", table_name="organization_memberships")
    op.drop_table("organization_memberships")
    op.drop_table("users")
    op.drop_table("organizations")
