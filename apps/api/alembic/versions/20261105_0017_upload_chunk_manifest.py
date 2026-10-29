"""Upload chunk manifest for resume identity (upload track U7 hardening)

Revision ID: 0017_upload_chunk_manifest
Revises: 0016_reporting_indexes
Create Date: 2026-11-05 09:00:00 UTC
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0017_upload_chunk_manifest"
down_revision: str | None = "0016_reporting_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "media_uploads",
        sa.Column(
            "chunk_manifest",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("media_uploads", "chunk_manifest")
