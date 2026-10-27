"""Upload file identity: publication_uploads.file_root_key / file_fingerprint / file_variant_id
(Export pipeline E2: the publisher may stream a publish-ready export instead of the final)

Revision ID: 0012_upload_file_identity
Revises: 0011_media_exports
Create Date: 2026-10-30 15:00:00 UTC
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012_upload_file_identity"
down_revision: str | None = "0011_media_exports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "publication_uploads", sa.Column("file_root_key", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "publication_uploads", sa.Column("file_fingerprint", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "publication_uploads", sa.Column("file_variant_id", sa.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_publication_uploads_file_variant_id_media_file_variants"),
        "publication_uploads",
        "media_file_variants",
        ["file_variant_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # Rows created before this migration streamed the final from the finals root.
    op.execute(
        "UPDATE publication_uploads SET file_root_key = 'finals' "
        "WHERE file_relative_path IS NOT NULL AND file_root_key IS NULL"
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_publication_uploads_file_variant_id_media_file_variants"),
        "publication_uploads",
        type_="foreignkey",
    )
    op.drop_column("publication_uploads", "file_variant_id")
    op.drop_column("publication_uploads", "file_fingerprint")
    op.drop_column("publication_uploads", "file_root_key")
