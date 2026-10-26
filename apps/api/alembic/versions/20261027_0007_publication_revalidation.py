"""publication revalidation markers (forward-only, idempotent)

Revision ID: 0007_publication_revalidation
Revises: 0006_content_routing
Create Date: 2026-10-27 12:00:00 UTC

History note: these two columns were briefly part of 0006 in the working tree after 0006 had
already been applied to local databases, which were then patched by hand. 0006 is restored to
its originally applied shape and this revision owns the columns. The upgrade checks whether a
column already exists so databases stamped at 0006 that carry the columns (hand-patched or
built from the interim 0006) upgrade cleanly without a manual stamp; see
docs/architecture/database.md#reconciling-databases-patched-by-hand.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_publication_revalidation"
down_revision: str | None = "0006_content_routing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "publication_jobs"
COLUMNS: dict[str, sa.Column] = {
    "revalidated_at": sa.Column("revalidated_at", sa.DateTime(timezone=True), nullable=True),
    "revalidated_by_label": sa.Column("revalidated_by_label", sa.String(length=255), nullable=True),
}


def _existing_columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(TABLE)}


def upgrade() -> None:
    present = _existing_columns()
    for name, column in COLUMNS.items():
        if name not in present:
            op.add_column(TABLE, column)


def downgrade() -> None:
    present = _existing_columns()
    for name in reversed(list(COLUMNS)):
        if name in present:
            op.drop_column(TABLE, name)
