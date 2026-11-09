"""Integration API keys and MeoScout product ingestion (M3.2-B.2 P1, ADR 0013)

Revision ID: 0019_product_ingest
Revises: 0018_upload_root
Create Date: 2026-11-09 09:00:00 UTC

- `integration_api_keys`: scoped, revocable machine credentials (SHA-256 of the key only).
- `product_ingest_batches` / `product_ingest_items`: one row per `POST /products/ingest`
  call and per candidate, holding the results the client was told (idempotent replays).
- `products.ingest_batch_id`: the batch that created a product (parallel to `import_id`).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0019_product_ingest"
down_revision: str | None = "0018_upload_root"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BATCH_STATUSES = ("completed", "failed")
ITEM_STATUSES = ("created", "updated", "unchanged", "conflict", "rejected")


def _in_list(column: str, values: Sequence[str]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


def upgrade() -> None:
    op.create_table(
        "integration_api_keys",
        sa.Column(
            "id", sa.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "scopes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_user_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("rotated_from_key_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_integration_api_keys_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_integration_api_keys_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"],
            ["users.id"],
            name=op.f("fk_integration_api_keys_revoked_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["rotated_from_key_id"],
            ["integration_api_keys.id"],
            name=op.f("fk_integration_api_keys_rotated_from_key_id_integration_api_keys"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_api_keys")),
        sa.UniqueConstraint("key_hash", name=op.f("uq_integration_api_keys_key_hash")),
    )
    op.create_index(
        "ix_integration_api_keys_organization_id_created_at",
        "integration_api_keys",
        ["organization_id", "created_at"],
    )

    op.create_table(
        "product_ingest_batches",
        sa.Column(
            "id", sa.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", sa.String(length=128), nullable=False),
        sa.Column("contract", sa.String(length=64), nullable=False),
        sa.Column("contract_version", sa.String(length=16), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "source",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("on_conflict", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("created_count", sa.Integer(), nullable=False),
        sa.Column("updated_count", sa.Integer(), nullable=False),
        sa.Column("unchanged_count", sa.Integer(), nullable=False),
        sa.Column("conflict_count", sa.Integer(), nullable=False),
        sa.Column("rejected_count", sa.Integer(), nullable=False),
        sa.Column("api_key_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _in_list("status", BATCH_STATUSES),
            name=op.f("ck_product_ingest_batches_product_ingest_batch_status"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_product_ingest_batches_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["api_key_id"],
            ["integration_api_keys.id"],
            name=op.f("fk_product_ingest_batches_api_key_id_integration_api_keys"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_ingest_batches")),
        sa.UniqueConstraint(
            "organization_id", "batch_id", name="uq_product_ingest_batches_org_batch"
        ),
    )
    op.create_index(
        "ix_product_ingest_batches_organization_id_created_at",
        "product_ingest_batches",
        ["organization_id", "created_at"],
    )

    op.create_table(
        "product_ingest_items",
        sa.Column(
            "id", sa.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("ingest_batch_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("client_ref", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("product_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "changed_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "conflicts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _in_list("status", ITEM_STATUSES),
            name=op.f("ck_product_ingest_items_product_ingest_item_status"),
        ),
        sa.ForeignKeyConstraint(
            ["ingest_batch_id"],
            ["product_ingest_batches.id"],
            name=op.f("fk_product_ingest_items_ingest_batch_id_product_ingest_batches"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_product_ingest_items_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_product_ingest_items_product_id_products"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_ingest_items")),
        sa.UniqueConstraint(
            "ingest_batch_id", "client_ref", name="uq_product_ingest_items_batch_client_ref"
        ),
    )
    op.create_index("ix_product_ingest_items_product_id", "product_ingest_items", ["product_id"])

    op.add_column("products", sa.Column("ingest_batch_id", sa.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        op.f("fk_products_ingest_batch_id_product_ingest_batches"),
        "products",
        "product_ingest_batches",
        ["ingest_batch_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    # Products ingested stay in the catalog (source=meoscout, source_ref=client_ref); only the
    # batch provenance, the stored results and the credentials are dropped. Every integration
    # key stops working at once, which is the intended effect of removing the feature.
    op.drop_constraint(
        op.f("fk_products_ingest_batch_id_product_ingest_batches"), "products", type_="foreignkey"
    )
    op.drop_column("products", "ingest_batch_id")
    op.drop_index("ix_product_ingest_items_product_id", table_name="product_ingest_items")
    op.drop_table("product_ingest_items")
    op.drop_index(
        "ix_product_ingest_batches_organization_id_created_at", table_name="product_ingest_batches"
    )
    op.drop_table("product_ingest_batches")
    op.drop_index(
        "ix_integration_api_keys_organization_id_created_at", table_name="integration_api_keys"
    )
    op.drop_table("integration_api_keys")
