"""Production reporting over final videos (upload track U6).

Read-only view over what already exists: final assets (with their facet assignments,
handoff review fields and `production_period`), completed upload sessions (uploader,
attributed editor, owner snapshot) and the revision links between assets. Nothing here
writes, and no parallel table is kept.

Definitions (also in docs/architecture/production-reporting.md):

- file: one final asset row (any status; missing files are flagged, not hidden).
- submission: a final that entered through an upload session (`ingest_source = upload`).
- historical: a final indexed from the NAS by a scan; owner may come from its folder path,
  uploader and attributed editor are unknown and are never invented.
- pending / approved / rejected: the asset's current `handoff_status`.
- corrected submission: an asset with `revision_of_asset_id` (links are only ever created by
  the uploader choosing "submit corrected version" for a rejected final).
- work chain: an original asset plus every correction that points back to it, directly or
  through earlier corrections. Unrelated assets never form a chain.
- unique approved work unit: a chain with at least one approved asset counts once, on the
  row of its most recently approved version (latest `handoff_reviewed_at`, ties broken by
  the newer asset). That row's period, owner and editor carry the unit.
- production_period: the UTC month of first ingestion (ADR 0011). For historical finals it
  is the month the scan first saw the file, not the month the video was edited; legacy
  folder names such as `T9` are never read as periods.
"""

from __future__ import annotations

import csv
import io
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from affilio.core.enums import (
    CategoryAssignmentSource,
    CategoryFacet,
    HandoffStatus,
    IngestSource,
    MediaAssetKind,
    MediaAssetStatus,
    UploadStatus,
)
from affilio.modules.identity.models import User
from affilio.modules.media.models import MediaAsset, MediaContentCategory, MediaUpload

PERIOD_SOURCE = "ingestion"

_SOURCE_RANK = {
    CategoryAssignmentSource.MANUAL: 0,
    CategoryAssignmentSource.PATH: 1,
    CategoryAssignmentSource.FOLDER: 2,
}


@dataclass(slots=True)
class PersonRef:
    id: uuid.UUID
    email: str
    display_name: str | None


@dataclass(slots=True)
class CategoryRef:
    id: uuid.UUID
    name: str
    slug: str | None = None


@dataclass(slots=True)
class ReportRow:
    asset_id: uuid.UUID
    title: str
    file_name: str | None
    asset_status: MediaAssetStatus
    production_period: str | None
    period_source: str
    ingest_source: IngestSource
    owner: CategoryRef | None
    owner_source: str | None  # upload | path | None
    uploader: PersonRef | None
    attributed_editor: PersonRef | None
    product_category: CategoryRef | None
    content_type: CategoryRef | None
    handoff_status: HandoffStatus
    reviewed_by: str | None
    reviewed_at: datetime | None
    review_note: str | None
    submitted_at: datetime
    completed_at: datetime | None
    upload_id: uuid.UUID | None
    revision_of_asset_id: uuid.UUID | None
    work_root_id: uuid.UUID = field(default_factory=uuid.uuid4)
    revision_index: int = 0
    chain_length: int = 1
    is_latest_in_chain: bool = True
    approved_unit: bool = False


@dataclass(slots=True)
class ReportFilters:
    period: str | None = None
    owner_id: uuid.UUID | None = None
    editor_id: uuid.UUID | None = None
    status: HandoffStatus | None = None
    attribution: str | None = None  # upload | historical
    product_category_id: uuid.UUID | None = None
    content_type_id: uuid.UUID | None = None
    unknown_editor: bool | None = None


@dataclass(slots=True)
class Bucket:
    key: str
    label: str
    files: int = 0
    submissions: int = 0
    historical: int = 0
    pending: int = 0
    approved: int = 0
    rejected: int = 0
    corrected: int = 0
    approved_units: int = 0

    def add(self, row: ReportRow) -> None:
        self.files += 1
        if row.ingest_source == IngestSource.UPLOAD:
            self.submissions += 1
        else:
            self.historical += 1
        if row.handoff_status == HandoffStatus.PENDING_REVIEW:
            self.pending += 1
        elif row.handoff_status == HandoffStatus.APPROVED:
            self.approved += 1
        elif row.handoff_status == HandoffStatus.REJECTED:
            self.rejected += 1
        if row.revision_of_asset_id is not None:
            self.corrected += 1
        if row.approved_unit:
            self.approved_units += 1


@dataclass(slots=True)
class Summary:
    totals: Bucket
    by_owner: list[Bucket]
    by_editor: list[Bucket]
    by_status: dict[str, int]
    unknown_editor: int
    unknown_owner: int
    missing_files: int
    periods: list[str]


# ---------------------------------------------------------------- loading


def _category_of(
    asset: MediaAsset, facet: CategoryFacet
) -> tuple[CategoryRef, CategoryAssignmentSource] | None:
    candidates = [
        a
        for a in asset.category_assignments
        if a.category is not None and a.category.facet == facet and a.category.parent_id is not None
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda a: (_SOURCE_RANK.get(a.assignment_source, 9), not a.is_primary))
    chosen = candidates[0]
    return (
        CategoryRef(id=chosen.category.id, name=chosen.category.name, slug=chosen.category.slug),
        chosen.assignment_source,
    )


def load_rows(session: Session, organization_id: uuid.UUID) -> list[ReportRow]:
    """Every final of the organization as a report row, with revision lineage resolved.
    Three queries: assets (+assignments, +variants), completed uploads, users."""
    assets = list(
        session.scalars(
            select(MediaAsset)
            .options(
                selectinload(MediaAsset.category_assignments).selectinload(
                    MediaContentCategory.category
                ),
                selectinload(MediaAsset.variants),
            )
            .where(
                MediaAsset.organization_id == organization_id,
                MediaAsset.asset_kind == MediaAssetKind.FINAL,
            )
            .order_by(MediaAsset.first_seen_at, MediaAsset.id)
        )
    )
    uploads = {
        u.media_asset_id: u
        for u in session.scalars(
            select(MediaUpload)
            .options(
                selectinload(MediaUpload.production_owner),
                selectinload(MediaUpload.product_category),
                selectinload(MediaUpload.content_type),
            )
            .where(
                MediaUpload.organization_id == organization_id,
                MediaUpload.status == UploadStatus.COMPLETED,
                MediaUpload.media_asset_id.is_not(None),
            )
        )
        if u.media_asset_id is not None
    }
    user_ids = {
        uid
        for u in uploads.values()
        for uid in (u.uploaded_by_user_id, u.submitted_for_user_id)
        if uid is not None
    } | {a.uploaded_by_user_id for a in assets if a.uploaded_by_user_id is not None}
    users = (
        {
            u.id: PersonRef(id=u.id, email=u.email, display_name=u.display_name)
            for u in session.scalars(select(User).where(User.id.in_(user_ids)))
        }
        if user_ids
        else {}
    )

    rows: list[ReportRow] = []
    for asset in assets:
        upload = uploads.get(asset.id)
        owner: CategoryRef | None = None
        owner_source: str | None = None
        if upload is not None:
            owner = CategoryRef(
                id=upload.production_owner_category_id,
                name=upload.production_owner_name,
                slug=upload.production_owner.slug if upload.production_owner else None,
            )
            owner_source = "upload"
        else:
            found = _category_of(asset, CategoryFacet.PRODUCTION_OWNER)
            if found is not None:
                owner, owner_source = found[0], "path"
        product = _category_of(asset, CategoryFacet.PRODUCT_CATEGORY)
        content = _category_of(asset, CategoryFacet.CONTENT_TYPE)
        if product is None and upload is not None and upload.product_category is not None:
            product = (
                CategoryRef(
                    id=upload.product_category.id,
                    name=upload.product_category.name,
                    slug=upload.product_category.slug,
                ),
                CategoryAssignmentSource.PATH,
            )
        if content is None and upload is not None and upload.content_type is not None:
            content = (
                CategoryRef(
                    id=upload.content_type.id,
                    name=upload.content_type.name,
                    slug=upload.content_type.slug,
                ),
                CategoryAssignmentSource.PATH,
            )
        uploader_id = upload.uploaded_by_user_id if upload else asset.uploaded_by_user_id
        uploader = users.get(uploader_id) if uploader_id else None
        attributed_id = (upload.submitted_for_user_id if upload else None) or uploader_id
        attributed = users.get(attributed_id) if attributed_id else None
        main = asset.main_variant()
        rows.append(
            ReportRow(
                asset_id=asset.id,
                title=asset.title,
                file_name=main.file_name if main else None,
                asset_status=asset.status,
                production_period=asset.production_period,
                period_source=PERIOD_SOURCE,
                ingest_source=asset.ingest_source,
                owner=owner,
                owner_source=owner_source,
                uploader=uploader,
                attributed_editor=attributed,
                product_category=product[0] if product else None,
                content_type=content[0] if content else None,
                handoff_status=asset.handoff_status or HandoffStatus.PENDING_REVIEW,
                reviewed_by=asset.handoff_reviewed_by,
                reviewed_at=asset.handoff_reviewed_at,
                review_note=asset.handoff_note,
                submitted_at=upload.created_at if upload else asset.first_seen_at,
                completed_at=upload.completed_at if upload else None,
                upload_id=upload.id if upload else None,
                revision_of_asset_id=asset.revision_of_asset_id,
            )
        )
    resolve_lineage(rows)
    return rows


# ---------------------------------------------------------------- lineage and counting


def resolve_lineage(rows: list[ReportRow]) -> None:
    """Fill `work_root_id`, `revision_index`, `chain_length`, `is_latest_in_chain` and mark
    the single `approved_unit` row per chain. Only explicit `revision_of_asset_id` links form
    chains; a dangling or cyclic link stops at the last reachable asset."""
    by_id = {r.asset_id: r for r in rows}
    for row in rows:
        root = row
        depth = 0
        seen = {row.asset_id}
        while root.revision_of_asset_id is not None and root.revision_of_asset_id in by_id:
            parent = by_id[root.revision_of_asset_id]
            if parent.asset_id in seen:
                break  # defensive: cycles cannot be created through the API
            seen.add(parent.asset_id)
            root = parent
            depth += 1
        row.work_root_id = root.asset_id
        row.revision_index = depth
    chains: dict[uuid.UUID, list[ReportRow]] = defaultdict(list)
    for row in rows:
        chains[row.work_root_id].append(row)
    for members in chains.values():
        members.sort(key=lambda r: (r.revision_index, r.submitted_at, str(r.asset_id)))
        latest = members[-1]
        for member in members:
            member.chain_length = len(members)
            member.is_latest_in_chain = member is latest
            member.approved_unit = False
        approved = [m for m in members if m.handoff_status == HandoffStatus.APPROVED]
        if approved:
            unit = max(
                approved,
                key=lambda r: (
                    r.reviewed_at.timestamp() if r.reviewed_at else 0.0,
                    r.submitted_at.timestamp(),
                    str(r.asset_id),
                ),
            )
            unit.approved_unit = True


def apply_filters(rows: list[ReportRow], filters: ReportFilters) -> list[ReportRow]:
    out = rows
    if filters.period:
        out = [r for r in out if r.production_period == filters.period]
    if filters.owner_id:
        out = [r for r in out if r.owner is not None and r.owner.id == filters.owner_id]
    if filters.editor_id:
        out = [
            r
            for r in out
            if r.attributed_editor is not None and r.attributed_editor.id == filters.editor_id
        ]
    if filters.status:
        out = [r for r in out if r.handoff_status == filters.status]
    if filters.attribution == "upload":
        out = [r for r in out if r.ingest_source == IngestSource.UPLOAD]
    elif filters.attribution == "historical":
        out = [r for r in out if r.ingest_source == IngestSource.SCAN]
    if filters.product_category_id:
        out = [
            r
            for r in out
            if r.product_category is not None
            and r.product_category.id == filters.product_category_id
        ]
    if filters.content_type_id:
        out = [
            r
            for r in out
            if r.content_type is not None and r.content_type.id == filters.content_type_id
        ]
    if filters.unknown_editor is True:
        out = [r for r in out if r.attributed_editor is None]
    elif filters.unknown_editor is False:
        out = [r for r in out if r.attributed_editor is not None]
    return out


def summarize(rows: list[ReportRow], all_rows: list[ReportRow]) -> Summary:
    totals = Bucket(key="all", label="All")
    owners: dict[str, Bucket] = {}
    editors: dict[str, Bucket] = {}
    by_status: dict[str, int] = {s.value: 0 for s in HandoffStatus}
    for row in rows:
        totals.add(row)
        by_status[row.handoff_status.value] = by_status.get(row.handoff_status.value, 0) + 1
        okey = str(row.owner.id) if row.owner else "unknown"
        owners.setdefault(
            okey, Bucket(key=okey, label=row.owner.name if row.owner else "Unknown owner")
        ).add(row)
        ekey = str(row.attributed_editor.id) if row.attributed_editor else "unknown"
        elabel = (
            (row.attributed_editor.display_name or row.attributed_editor.email)
            if row.attributed_editor
            else "Unknown editor (historical)"
        )
        editors.setdefault(ekey, Bucket(key=ekey, label=elabel)).add(row)
    order = lambda b: (b.key == "unknown", b.label.lower())  # noqa: E731
    return Summary(
        totals=totals,
        by_owner=sorted(owners.values(), key=order),
        by_editor=sorted(editors.values(), key=order),
        by_status=by_status,
        unknown_editor=sum(1 for r in rows if r.attributed_editor is None),
        unknown_owner=sum(1 for r in rows if r.owner is None),
        missing_files=sum(1 for r in rows if r.asset_status != MediaAssetStatus.ACTIVE),
        periods=sorted(
            {r.production_period for r in all_rows if r.production_period}, reverse=True
        ),
    )


CSV_COLUMNS = [
    "asset_id",
    "title",
    "file_name",
    "production_period",
    "period_source",
    "attribution",
    "production_owner",
    "owner_source",
    "uploader",
    "attributed_editor",
    "product_category",
    "content_type",
    "handoff_status",
    "reviewed_by",
    "reviewed_at",
    "review_note",
    "submitted_at",
    "completed_at",
    "revision_of_asset_id",
    "work_root_id",
    "revision_index",
    "chain_length",
    "approved_unit",
    "asset_status",
]


def rows_to_csv(rows: list[ReportRow]) -> str:
    """RFC 4180 CSV with a UTF-8 BOM so spreadsheets open it correctly; the csv module
    quotes commas, quotes and newlines. Cells starting with a formula character are
    prefixed with an apostrophe to neutralise spreadsheet formula injection."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for r in rows:
        writer.writerow(
            [
                _cell(v)
                for v in (
                    r.asset_id,
                    r.title,
                    r.file_name,
                    r.production_period,
                    r.period_source,
                    "upload" if r.ingest_source == IngestSource.UPLOAD else "historical",
                    r.owner.name if r.owner else "",
                    r.owner_source or "",
                    r.uploader.email if r.uploader else "",
                    r.attributed_editor.email if r.attributed_editor else "",
                    r.product_category.name if r.product_category else "",
                    r.content_type.name if r.content_type else "",
                    r.handoff_status.value,
                    r.reviewed_by,
                    r.reviewed_at.isoformat() if r.reviewed_at else "",
                    r.review_note,
                    r.submitted_at.isoformat(),
                    r.completed_at.isoformat() if r.completed_at else "",
                    r.revision_of_asset_id,
                    r.work_root_id,
                    r.revision_index,
                    r.chain_length,
                    "yes" if r.approved_unit else "no",
                    r.asset_status.value,
                )
            ]
        )
    return "﻿" + buffer.getvalue()


def _cell(value: object) -> str:
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


__all__ = [
    "CSV_COLUMNS",
    "PERIOD_SOURCE",
    "Bucket",
    "CategoryRef",
    "PersonRef",
    "ReportFilters",
    "ReportRow",
    "Summary",
    "apply_filters",
    "load_rows",
    "resolve_lineage",
    "rows_to_csv",
    "summarize",
]
