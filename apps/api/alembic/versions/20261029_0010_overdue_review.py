"""Overdue schedule policy: publication_jobs.overdue_review_at (M3B.2A restart policy)

Revision ID: 0010_overdue_review
Revises: 0009_publishing_engine
Create Date: 2026-10-29 15:00:00 UTC
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010_overdue_review"
down_revision: str | None = "0009_publishing_engine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "publication_jobs",
        sa.Column("overdue_review_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("publication_jobs", "overdue_review_at")
