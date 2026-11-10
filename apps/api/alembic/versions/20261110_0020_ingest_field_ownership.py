"""Field ownership for MeoScout ingest (M3.2-B.2 P1.1, ADR 0014)

Revision ID: 0020_ingest_field_ownership
Revises: 0019_product_ingest
Create Date: 2026-11-10 09:00:00 UTC

- `products.ingest_snapshot`: the values ingest last wrote, per field. Ingest refreshes a
  field only while the stored value still equals this record; any other writer takes it over.
- `product_ingest_items.preserved_fields`: fields kept because AFFILIO owns them (stored so
  a replay returns exactly what the first response said).

Existing rows: `ingest_snapshot` stays NULL, so every non-empty field of an existing product is
treated as AFFILIO-owned (fill-only) until ingest writes it. That is the conservative default.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0020_ingest_field_ownership"
down_revision: str | None = "0019_product_ingest"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("ingest_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "product_ingest_items",
        sa.Column(
            "preserved_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("product_ingest_items", "preserved_fields")
    op.drop_column("products", "ingest_snapshot")
