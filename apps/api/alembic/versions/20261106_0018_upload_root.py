"""Dedicated upload root: media_roots.kind += upload, assignment source += upload

Revision ID: 0018_upload_root
Revises: 0017_upload_chunk_manifest
Create Date: 2026-11-06 09:00:00 UTC

ADR 0012, amendment 2 (fast track F1). Finals delivered through AFFILIO are stored under the
application-managed `uploads` root instead of the legacy, read-only `finals` tree, and their
owner / product category / content type come from the upload session (assignment source
`upload`) instead of folder names; a person's facet reclassification of a legacy final is an
`override` row that wins over the folders on later rescans. Both are CHECK-constraint extensions; the downgrade folds the new values into the
legacy ones instead of deleting rows.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018_upload_root"
down_revision: str | None = "0017_upload_chunk_manifest"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROOT_KINDS_BEFORE = ("original", "proxy", "final", "thumbnail", "export")
ROOT_KINDS_AFTER = ("original", "proxy", "final", "upload", "thumbnail", "export")
ASSIGNMENT_SOURCES_BEFORE = ("folder", "manual", "import", "ai_future", "path")
ASSIGNMENT_SOURCES_AFTER = (*ASSIGNMENT_SOURCES_BEFORE, "upload", "override")


def _in_list(column: str, values: Sequence[str]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


def _swap_check(table: str, name: str, column: str, values: Sequence[str]) -> None:
    op.drop_constraint(op.f(name), table, type_="check")
    op.create_check_constraint(op.f(name), table, _in_list(column, values))


def upgrade() -> None:
    _swap_check("media_roots", "ck_media_roots_media_root_kind", "kind", ROOT_KINDS_AFTER)
    _swap_check(
        "media_content_categories",
        "ck_media_content_categories_category_assignment_source",
        "assignment_source",
        ASSIGNMENT_SOURCES_AFTER,
    )
    # Re-upgrade after a downgrade: the uploads root row and its assignments were folded into
    # the legacy values below; give them their own values back.
    op.execute("UPDATE media_roots SET kind = 'upload' WHERE key = 'uploads' AND kind = 'final'")
    op.execute(
        "UPDATE media_content_categories SET assignment_source = 'upload' "
        "WHERE assignment_source = 'path' AND media_asset_id IN ("
        "SELECT v.asset_id FROM media_file_variants v "
        "JOIN media_roots r ON r.id = v.media_root_id WHERE r.key = 'uploads')"
    )


def downgrade() -> None:
    # Lossless for assets and files: uploaded finals stay registered. The uploads root row is
    # folded into the legacy `final` kind (older code ignores its key) and upload-derived
    # assignments into `path`, so every row satisfies the previous constraints.
    op.execute("UPDATE media_roots SET kind = 'final' WHERE kind = 'upload'")
    op.execute(
        "UPDATE media_content_categories SET assignment_source = 'path' "
        "WHERE assignment_source = 'upload'"
    )
    op.execute(
        "UPDATE media_content_categories SET assignment_source = 'manual' "
        "WHERE assignment_source = 'override'"
    )
    _swap_check(
        "media_content_categories",
        "ck_media_content_categories_category_assignment_source",
        "assignment_source",
        ASSIGNMENT_SOURCES_BEFORE,
    )
    _swap_check("media_roots", "ck_media_roots_media_root_kind", "kind", ROOT_KINDS_BEFORE)
