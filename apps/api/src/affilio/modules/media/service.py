"""Media query/command services. Every function is organization-scoped."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Select, and_, case, exists, func, or_, select
from sqlalchemy.orm import Session, aliased, selectinload

from affilio.core.enums import (
    HandoffStatus,
    MediaAssetKind,
    MediaAssetStatus,
    MediaVariantKind,
    MediaVariantStatus,
    ScanJobStatus,
    ScanTrigger,
    ThumbnailStatus,
)
from affilio.core.errors import AppError, NotFoundError
from affilio.modules.media import classification
from affilio.modules.media.errors import (
    MediaRootNotConfiguredError,
    MediaRootUnavailableError,
    ScanAlreadyActiveError,
)
from affilio.modules.media.models import (
    MediaAsset,
    MediaContentCategory,
    MediaFileVariant,
    MediaScanJob,
)
from affilio.modules.media.roots import ROOT_KEYS, SCANNABLE_KINDS, MediaRootRegistry
from affilio.modules.media.schemas import (
    AssetCategoryResponse,
    HandoffDecision,
    MediaAssetDetail,
    MediaAssetSummary,
    MediaRootStatus,
    MediaStats,
    MediaVariantResponse,
    Orientation,
    PaginatedAssets,
    ScanJobResponse,
    ScanStatusResponse,
    SortKey,
)
from affilio.modules.taxonomy import service as taxonomy_service

MAX_PAGE_SIZE = 100


@dataclass(slots=True)
class AssetFilters:
    q: str | None = None
    status: MediaAssetStatus | None = None
    asset_kind: MediaAssetKind | None = None
    handoff_status: HandoffStatus | None = None
    orientation: Orientation | None = None
    has_proxy: bool | None = None
    video_codec: str | None = None
    duplicates_only: bool = False
    category_id: uuid.UUID | None = None  # includes descendants
    uncategorized: bool = False  # no assignment at all, or only the Uncategorized fallback
    sort: SortKey = "newest"
    page: int = 1
    page_size: int = 24


# ---------------------------------------------------------------------------- roots


def root_statuses(
    session: Session, organization_id: uuid.UUID, registry: MediaRootRegistry
) -> list[MediaRootStatus]:
    rows = registry.ensure_db_roots(session, organization_id)
    statuses: list[MediaRootStatus] = []
    for key, (kind, access, name) in ROOT_KEYS.items():
        row = rows[key]
        binding = registry.get(key)
        availability = binding.availability() if binding else None
        statuses.append(
            MediaRootStatus(
                id=row.id,
                key=key,
                kind=kind,
                access=access,
                name=row.name or name,
                configured=binding is not None,
                exists=bool(availability and availability.exists),
                readable=bool(availability and availability.readable),
                writable=bool(availability and availability.writable),
                scan_enabled=row.scan_enabled,
                last_scan_at=row.last_scan_at,
            )
        )
    return statuses


# ---------------------------------------------------------------------------- scans


def enqueue_scan(
    session: Session,
    organization_id: uuid.UUID,
    registry: MediaRootRegistry,
    *,
    root_key: str = "originals",
    trigger: ScanTrigger = ScanTrigger.MANUAL,
    requested_by: str | None = None,
    full_rescan: bool = False,
    max_attempts: int = 3,
) -> MediaScanJob:
    rows = registry.ensure_db_roots(session, organization_id)
    root = rows.get(root_key)
    if root is None or root.kind not in SCANNABLE_KINDS:
        raise NotFoundError(f"Scannable media root '{root_key}' not found")
    binding = registry.get(root_key)
    if binding is None:
        raise MediaRootNotConfiguredError(
            f"Media root '{root_key}' has no path configured (MEDIA_ORIGINALS_PATH)"
        )
    availability = binding.availability()
    if not availability.readable:
        raise MediaRootUnavailableError(
            f"Media root '{root_key}' is not readable right now; check the mount"
        )
    active = session.scalar(
        select(MediaScanJob).where(
            MediaScanJob.media_root_id == root.id,
            MediaScanJob.status.in_([ScanJobStatus.QUEUED, ScanJobStatus.RUNNING]),
        )
    )
    if active is not None:
        raise ScanAlreadyActiveError(
            "A scan is already queued or running for this root",
            details={"job_id": str(active.id), "status": active.status},
        )
    job = MediaScanJob(
        organization_id=organization_id,
        media_root_id=root.id,
        status=ScanJobStatus.QUEUED,
        trigger=trigger,
        requested_by=requested_by,
        full_rescan=full_rescan,
        max_attempts=max_attempts,
        next_attempt_at=datetime.now(UTC),
    )
    session.add(job)
    session.flush()
    return job


def list_scan_jobs(
    session: Session, organization_id: uuid.UUID, limit: int = 20
) -> list[MediaScanJob]:
    stmt = (
        select(MediaScanJob)
        .options(selectinload(MediaScanJob.root))
        .where(MediaScanJob.organization_id == organization_id)
        .order_by(MediaScanJob.created_at.desc())
        .limit(min(max(limit, 1), 100))
    )
    return list(session.scalars(stmt))


def get_scan_job(session: Session, organization_id: uuid.UUID, job_id: uuid.UUID) -> MediaScanJob:
    job = session.scalar(
        select(MediaScanJob)
        .options(selectinload(MediaScanJob.root))
        .where(MediaScanJob.organization_id == organization_id, MediaScanJob.id == job_id)
    )
    if job is None:
        raise NotFoundError("Scan job not found")
    return job


def scan_status(
    session: Session, organization_id: uuid.UUID, registry: MediaRootRegistry
) -> ScanStatusResponse:
    roots = root_statuses(session, organization_id, registry)
    current = session.scalar(
        select(MediaScanJob)
        .options(selectinload(MediaScanJob.root))
        .where(
            MediaScanJob.organization_id == organization_id,
            MediaScanJob.status.in_([ScanJobStatus.QUEUED, ScanJobStatus.RUNNING]),
        )
        .order_by(MediaScanJob.created_at.desc())
    )
    last_completed = session.scalar(
        select(MediaScanJob)
        .options(selectinload(MediaScanJob.root))
        .where(
            MediaScanJob.organization_id == organization_id,
            MediaScanJob.status.in_(
                [ScanJobStatus.SUCCEEDED, ScanJobStatus.FAILED, ScanJobStatus.CANCELLED]
            ),
        )
        .order_by(MediaScanJob.finished_at.desc().nulls_last(), MediaScanJob.created_at.desc())
    )
    return ScanStatusResponse(
        current=scan_job_response(current) if current else None,
        last_completed=scan_job_response(last_completed) if last_completed else None,
        roots=roots,
        worker_hint="Scans and thumbnails run in the background worker (pnpm dev:worker).",
    )


def scan_job_response(job: MediaScanJob) -> ScanJobResponse:
    return ScanJobResponse(
        id=job.id,
        root_key=job.root.key,
        status=job.status,
        trigger=job.trigger,
        attempt=job.attempt,
        max_attempts=job.max_attempts,
        full_rescan=job.full_rescan,
        requested_by=job.requested_by,
        next_attempt_at=job.next_attempt_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        heartbeat_at=job.heartbeat_at,
        created_at=job.created_at,
        stats=job.stats or {},
        errors=[e for e in (job.errors or []) if isinstance(e, dict)],  # type: ignore[misc]
        error=job.error,
    )


# ---------------------------------------------------------------------------- assets


def _original_subquery():
    return (
        select(
            MediaFileVariant.asset_id.label("asset_id"),
            MediaFileVariant.file_size.label("file_size"),
            MediaFileVariant.relative_path.label("relative_path"),
            MediaFileVariant.file_name.label("file_name"),
        )
        .where(MediaFileVariant.kind.in_([MediaVariantKind.ORIGINAL, MediaVariantKind.FINAL]))
        .subquery()
    )


def _has_proxy_exists():
    proxy = aliased(MediaFileVariant)
    return exists().where(
        and_(
            proxy.asset_id == MediaAsset.id,
            proxy.kind == MediaVariantKind.PROXY,
            proxy.status == MediaVariantStatus.PRESENT,
        )
    )


def _apply_filters(
    stmt: Select, filters: AssetFilters, original, *, organization_id: uuid.UUID, session: Session
) -> Select:
    if filters.category_id:
        tree = classification.tree_for(session, organization_id)
        ids = {filters.category_id, *tree.descendants(filters.category_id)}
        stmt = stmt.where(
            MediaAsset.id.in_(classification.asset_ids_in_categories(session, organization_id, ids))
        )
    if filters.uncategorized:
        fallback = taxonomy_service.ensure_uncategorized(session, organization_id)
        real = (
            select(MediaContentCategory.media_asset_id)
            .where(
                MediaContentCategory.organization_id == organization_id,
                MediaContentCategory.category_id != fallback.id,
            )
            .scalar_subquery()
        )
        stmt = stmt.where(MediaAsset.id.not_in(real))
    if filters.q:
        pattern = f"%{filters.q.strip()}%"
        stmt = stmt.where(
            or_(MediaAsset.title.ilike(pattern), original.c.relative_path.ilike(pattern))
        )
    if filters.status:
        stmt = stmt.where(MediaAsset.status == filters.status)
    if filters.asset_kind:
        stmt = stmt.where(MediaAsset.asset_kind == filters.asset_kind)
    if filters.handoff_status:
        stmt = stmt.where(MediaAsset.handoff_status == filters.handoff_status)
    if filters.orientation == "vertical":
        stmt = stmt.where(MediaAsset.height > MediaAsset.width)
    elif filters.orientation == "horizontal":
        stmt = stmt.where(MediaAsset.width > MediaAsset.height)
    elif filters.orientation == "square":
        stmt = stmt.where(MediaAsset.width == MediaAsset.height)
    if filters.has_proxy is True:
        stmt = stmt.where(_has_proxy_exists())
    elif filters.has_proxy is False:
        stmt = stmt.where(~_has_proxy_exists())
    if filters.video_codec:
        stmt = stmt.where(MediaAsset.video_codec == filters.video_codec.lower())
    if filters.duplicates_only:
        stmt = stmt.where(MediaAsset.duplicate_of_id.is_not(None))
    return stmt


def list_assets(
    session: Session,
    organization_id: uuid.UUID,
    filters: AssetFilters,
    *,
    thumbnail_url_prefix: str,
) -> PaginatedAssets:
    page = max(1, filters.page)
    page_size = min(max(1, filters.page_size), MAX_PAGE_SIZE)
    original = _original_subquery()

    base = (
        select(MediaAsset)
        .outerjoin(original, original.c.asset_id == MediaAsset.id)
        .where(MediaAsset.organization_id == organization_id)
    )
    base = _apply_filters(base, filters, original, organization_id=organization_id, session=session)

    count_stmt = select(func.count()).select_from(base.order_by(None).subquery())
    total = int(session.scalar(count_stmt) or 0)

    order = {
        "newest": [MediaAsset.created_at.desc(), MediaAsset.id.desc()],
        "oldest": [MediaAsset.created_at.asc(), MediaAsset.id.asc()],
        "title": [MediaAsset.title.asc(), MediaAsset.id.asc()],
        "duration": [MediaAsset.duration_seconds.desc().nulls_last(), MediaAsset.id.desc()],
        "size": [original.c.file_size.desc().nulls_last(), MediaAsset.id.desc()],
        "last_seen": [MediaAsset.last_seen_at.desc(), MediaAsset.id.desc()],
    }[filters.sort]
    stmt = (
        base.options(
            selectinload(MediaAsset.variants),
            selectinload(MediaAsset.category_assignments).selectinload(
                MediaContentCategory.category
            ),
        )
        .order_by(*order)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    assets = list(session.scalars(stmt))
    items = [asset_summary(asset, thumbnail_url_prefix=thumbnail_url_prefix) for asset in assets]
    return PaginatedAssets(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        total_pages=max(1, math.ceil(total / page_size)) if total else 0,
    )


def get_asset(session: Session, organization_id: uuid.UUID, asset_id: uuid.UUID) -> MediaAsset:
    asset = session.scalar(
        select(MediaAsset)
        .options(
            selectinload(MediaAsset.variants).selectinload(MediaFileVariant.root),
            selectinload(MediaAsset.category_assignments).selectinload(
                MediaContentCategory.category
            ),
        )
        .where(MediaAsset.organization_id == organization_id, MediaAsset.id == asset_id)
    )
    if asset is None:
        raise NotFoundError("Media asset not found")
    return asset


def _asset_categories(asset: MediaAsset) -> list[AssetCategoryResponse]:
    out: list[AssetCategoryResponse] = []
    for a in sorted(
        asset.category_assignments,
        key=lambda x: (not x.is_primary, x.created_at or datetime.min.replace(tzinfo=UTC)),
    ):
        chain: list[str] = []
        node = a.category
        guard = 0
        while node is not None and guard < 10:
            chain.append(node.name)
            node = node.parent
            guard += 1
        out.append(
            AssetCategoryResponse(
                category_id=a.category_id,
                slug=a.category.slug,
                name=a.category.name,
                path=" / ".join(reversed(chain)),
                is_primary=a.is_primary,
                assignment_source=a.assignment_source,
            )
        )
    return out


def _summary_fields(asset: MediaAsset, *, thumbnail_url_prefix: str) -> dict:
    original = asset.main_variant()
    proxy = asset.variant(MediaVariantKind.PROXY)
    return {
        "id": asset.id,
        "title": asset.title,
        "folder_path": (
            classification.folder_of(original.relative_path) if original is not None else None
        ),
        "categories": _asset_categories(asset),
        "classification_changed_at": asset.classification_changed_at,
        "status": asset.status,
        "asset_kind": asset.asset_kind,
        "handoff_status": asset.handoff_status,
        "orientation": asset.orientation,
        "duration_seconds": asset.duration_seconds,
        "width": asset.width,
        "height": asset.height,
        "frame_rate": asset.frame_rate,
        "video_codec": asset.video_codec,
        "audio_codec": asset.audio_codec,
        "pixel_format": asset.pixel_format,
        "bit_depth": asset.bit_depth,
        "bit_rate": asset.bit_rate,
        "container_format": asset.container_format,
        "file_size": original.file_size if original else None,
        "relative_path": original.relative_path if original else None,
        "file_name": original.file_name if original else None,
        "has_proxy": bool(proxy and proxy.status == MediaVariantStatus.PRESENT),
        "thumbnail_status": asset.thumbnail_status,
        "thumbnail_url": (
            f"{thumbnail_url_prefix}/{asset.id}/thumbnail"
            if asset.thumbnail_status == ThumbnailStatus.READY
            else None
        ),
        "duplicate_of_id": asset.duplicate_of_id,
        "first_seen_at": asset.first_seen_at,
        "last_seen_at": asset.last_seen_at,
        "missing_since": asset.missing_since,
        "created_at": asset.created_at,
        "updated_at": asset.updated_at,
    }


def asset_summary(asset: MediaAsset, *, thumbnail_url_prefix: str) -> MediaAssetSummary:
    return MediaAssetSummary(**_summary_fields(asset, thumbnail_url_prefix=thumbnail_url_prefix))


def asset_detail(
    session: Session, asset: MediaAsset, *, thumbnail_url_prefix: str
) -> MediaAssetDetail:
    duplicates = list(
        session.scalars(
            select(MediaAsset.id)
            .where(MediaAsset.duplicate_of_id == asset.id)
            .order_by(MediaAsset.created_at)
        )
    )
    variants = [
        MediaVariantResponse(
            id=v.id,
            kind=v.kind,
            status=v.status,
            root_key=v.root.key if v.root else "",
            relative_path=v.relative_path,
            file_name=v.file_name,
            extension=v.extension,
            file_size=v.file_size,
            modified_at=v.modified_at,
            content_hash=v.content_hash,
            duration_seconds=v.duration_seconds,
            width=v.width,
            height=v.height,
            frame_rate=v.frame_rate,
            video_codec=v.video_codec,
            audio_codec=v.audio_codec,
            pixel_format=v.pixel_format,
            bit_depth=v.bit_depth,
            bit_rate=v.bit_rate,
            container_format=v.container_format,
            rotation=v.rotation,
            probed_at=v.probed_at,
            probe_error=v.probe_error,
            last_seen_at=v.last_seen_at,
            missing_since=v.missing_since,
        )
        for v in asset.variants
    ]
    return MediaAssetDetail(
        **_summary_fields(asset, thumbnail_url_prefix=thumbnail_url_prefix),
        handoff_note=asset.handoff_note,
        handoff_reviewed_at=asset.handoff_reviewed_at,
        handoff_reviewed_by=asset.handoff_reviewed_by,
        probe_error=asset.probe_error,
        thumbnail_error=asset.thumbnail_error,
        thumbnail_attempts=asset.thumbnail_attempts,
        rotation=asset.rotation,
        variants=variants,
        duplicates=duplicates,
    )


def media_stats(session: Session, organization_id: uuid.UUID) -> MediaStats:
    original = _original_subquery()
    row = session.execute(
        select(
            func.count(MediaAsset.id),
            func.count(case((MediaAsset.asset_kind == MediaAssetKind.FOOTAGE, 1))),
            func.count(case((MediaAsset.asset_kind == MediaAssetKind.FINAL, 1))),
            func.count(case((MediaAsset.handoff_status == HandoffStatus.PENDING_REVIEW, 1))),
            func.count(case((MediaAsset.handoff_status == HandoffStatus.APPROVED, 1))),
            func.count(case((MediaAsset.handoff_status == HandoffStatus.REJECTED, 1))),
            func.count(case((MediaAsset.status == MediaAssetStatus.ACTIVE, 1))),
            func.count(case((MediaAsset.status == MediaAssetStatus.MISSING, 1))),
            func.count(case((MediaAsset.status == MediaAssetStatus.UNSUPPORTED, 1))),
            func.count(case((MediaAsset.duplicate_of_id.is_not(None), 1))),
            func.count(case((_has_proxy_exists(), 1))),
            func.count(case((MediaAsset.thumbnail_status == ThumbnailStatus.PENDING, 1))),
            func.count(case((MediaAsset.thumbnail_status == ThumbnailStatus.READY, 1))),
            func.count(case((MediaAsset.thumbnail_status == ThumbnailStatus.FAILED, 1))),
            func.coalesce(func.sum(original.c.file_size), 0),
            func.coalesce(func.sum(MediaAsset.duration_seconds), 0.0),
        )
        .select_from(MediaAsset)
        .outerjoin(original, original.c.asset_id == MediaAsset.id)
        .where(MediaAsset.organization_id == organization_id)
    ).one()
    return MediaStats(
        total=row[0],
        footage=row[1],
        finals=row[2],
        finals_pending_review=row[3],
        finals_approved=row[4],
        finals_rejected=row[5],
        active=row[6],
        missing=row[7],
        unsupported=row[8],
        duplicates=row[9],
        with_proxy=row[10],
        thumbnails_pending=row[11],
        thumbnails_ready=row[12],
        thumbnails_failed=row[13],
        total_bytes=int(row[14]),
        total_duration_seconds=float(row[15]),
    )


class HandoffNotApplicableError(AppError):
    status_code = 409
    code = "handoff_not_applicable"


class HandoffNoteRequiredError(AppError):
    status_code = 422
    code = "handoff_note_required"


def review_handoff(
    session: Session,
    organization_id: uuid.UUID,
    asset_id: uuid.UUID,
    decision: HandoffDecision,
    *,
    actor: str = "api",
) -> MediaAsset:
    """Record a human handoff decision for a FINAL asset. Footage cannot be approved."""
    asset = get_asset(session, organization_id, asset_id)
    if asset.asset_kind != MediaAssetKind.FINAL:
        raise HandoffNotApplicableError("Only final videos go through the handoff review")
    if asset.status != MediaAssetStatus.ACTIVE and decision.status == HandoffStatus.APPROVED:
        raise HandoffNotApplicableError("A missing or unsupported final cannot be approved")
    note = (decision.note or "").strip() or None
    if decision.status in {HandoffStatus.APPROVED, HandoffStatus.REJECTED} and not note:
        raise HandoffNoteRequiredError("Approving or rejecting a final requires a note")
    asset.handoff_status = decision.status
    asset.handoff_note = note
    asset.handoff_reviewed_at = datetime.now(UTC)
    asset.handoff_reviewed_by = actor
    session.flush()
    return asset


def thumbnail_path(
    session: Session, organization_id: uuid.UUID, asset_id: uuid.UUID, registry: MediaRootRegistry
) -> Path:
    """Resolve the on-disk thumbnail for an asset; the client never supplies a path."""
    asset = get_asset(session, organization_id, asset_id)
    variant = asset.variant(MediaVariantKind.THUMBNAIL)
    thumbs = registry.thumbnails
    if (
        asset.thumbnail_status != ThumbnailStatus.READY
        or variant is None
        or variant.status != MediaVariantStatus.PRESENT
        or thumbs is None
    ):
        raise NotFoundError("Thumbnail not available")
    path = thumbs.resolve(variant.relative_path)
    if not path.is_file():
        raise NotFoundError("Thumbnail file missing")
    return path
