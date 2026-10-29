"""Production reporting indexes (upload track U6)

Revision ID: 0016_reporting_indexes
Revises: 0015_media_uploads
Create Date: 2026-11-04 09:00:00 UTC
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016_reporting_indexes"
down_revision: str | None = "0015_media_uploads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_media_assets_organization_id_asset_kind_production_period",
        "media_assets",
        ["organization_id", "asset_kind", "production_period"],
    )
    op.create_index(
        "ix_media_assets_revision_of_asset_id", "media_assets", ["revision_of_asset_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_media_assets_revision_of_asset_id", table_name="media_assets")
    op.drop_index(
        "ix_media_assets_organization_id_asset_kind_production_period", table_name="media_assets"
    )
