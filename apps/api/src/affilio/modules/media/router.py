"""Media HTTP routes (M1).

Access model until authentication lands (M3): all routes are organization-scoped through
`get_current_organization`; mutating routes require the admin token (`require_admin`). The
production deployment keeps the API off the public internet (see docs/deployment/synology.md).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import FileResponse

from affilio.api.deps import (
    AdminRequired,
    CurrentOrganization,
    DbSession,
    MediaRegistry,
    SettingsDep,
)
from affilio.core.enums import HandoffStatus, MediaAssetKind, MediaAssetStatus, ScanTrigger
from affilio.modules.media import service
from affilio.modules.media.schemas import (
    CreateScanRequest,
    HandoffDecision,
    MediaAssetDetail,
    MediaRootStatus,
    MediaStats,
    Orientation,
    PaginatedAssets,
    ScanJobResponse,
    ScanStatusResponse,
    SortKey,
)
from affilio.modules.media.service import AssetFilters

router = APIRouter(prefix="/media", tags=["media"])


def _thumbnail_prefix(request: Request, settings_prefix: str) -> str:
    # Relative to the API origin so the web proxy and direct access both work.
    return f"{settings_prefix}/media/assets"


@router.get("/roots", response_model=list[MediaRootStatus], summary="Configured media roots")
def read_roots(
    session: DbSession, organization: CurrentOrganization, registry: MediaRegistry
) -> list[MediaRootStatus]:
    return service.root_statuses(session, organization.id, registry)


@router.get("/stats", response_model=MediaStats, summary="Library counters")
def read_stats(session: DbSession, organization: CurrentOrganization) -> MediaStats:
    return service.media_stats(session, organization.id)


@router.get("/assets", response_model=PaginatedAssets, summary="List and filter media assets")
def list_assets(
    request: Request,
    session: DbSession,
    organization: CurrentOrganization,
    settings: SettingsDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    status_filter: Annotated[MediaAssetStatus | None, Query(alias="status")] = None,
    asset_kind: MediaAssetKind | None = None,
    handoff_status: HandoffStatus | None = None,
    orientation: Orientation | None = None,
    has_proxy: bool | None = None,
    video_codec: Annotated[str | None, Query(max_length=32)] = None,
    duplicates_only: bool = False,
    sort: SortKey = "newest",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=service.MAX_PAGE_SIZE)] = 24,
) -> PaginatedAssets:
    filters = AssetFilters(
        q=q,
        status=status_filter,
        asset_kind=asset_kind,
        handoff_status=handoff_status,
        orientation=orientation,
        has_proxy=has_proxy,
        video_codec=video_codec,
        duplicates_only=duplicates_only,
        sort=sort,
        page=page,
        page_size=page_size,
    )
    return service.list_assets(
        session,
        organization.id,
        filters,
        thumbnail_url_prefix=_thumbnail_prefix(request, settings.api_v1_prefix),
    )


@router.get("/assets/{asset_id}", response_model=MediaAssetDetail, summary="Media asset detail")
def read_asset(
    request: Request,
    asset_id: uuid.UUID,
    session: DbSession,
    organization: CurrentOrganization,
    settings: SettingsDep,
) -> MediaAssetDetail:
    asset = service.get_asset(session, organization.id, asset_id)
    return service.asset_detail(
        session, asset, thumbnail_url_prefix=_thumbnail_prefix(request, settings.api_v1_prefix)
    )


@router.post(
    "/assets/{asset_id}/handoff",
    response_model=MediaAssetDetail,
    summary="Review a final video (approve / reject) — admin",
)
def review_handoff(
    request: Request,
    asset_id: uuid.UUID,
    body: HandoffDecision,
    session: DbSession,
    organization: CurrentOrganization,
    settings: SettingsDep,
    _admin: AdminRequired,
) -> MediaAssetDetail:
    service.review_handoff(session, organization.id, asset_id, body)
    session.commit()
    asset = service.get_asset(session, organization.id, asset_id)
    return service.asset_detail(
        session, asset, thumbnail_url_prefix=_thumbnail_prefix(request, settings.api_v1_prefix)
    )


@router.get(
    "/assets/{asset_id}/thumbnail",
    summary="Thumbnail image (JPEG)",
    response_class=FileResponse,
    responses={404: {"description": "Thumbnail not generated yet"}},
)
def read_thumbnail(
    asset_id: uuid.UUID,
    session: DbSession,
    organization: CurrentOrganization,
    registry: MediaRegistry,
) -> Response:
    path = service.thumbnail_path(session, organization.id, asset_id, registry)
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/scans/status", response_model=ScanStatusResponse, summary="Current scan state")
def read_scan_status(
    session: DbSession, organization: CurrentOrganization, registry: MediaRegistry
) -> ScanStatusResponse:
    return service.scan_status(session, organization.id, registry)


@router.get("/scans", response_model=list[ScanJobResponse], summary="Scan history")
def list_scans(
    session: DbSession,
    organization: CurrentOrganization,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[ScanJobResponse]:
    return [
        service.scan_job_response(job)
        for job in service.list_scan_jobs(session, organization.id, limit)
    ]


@router.get("/scans/{job_id}", response_model=ScanJobResponse, summary="Scan job detail")
def read_scan(
    job_id: uuid.UUID, session: DbSession, organization: CurrentOrganization
) -> ScanJobResponse:
    return service.scan_job_response(service.get_scan_job(session, organization.id, job_id))


@router.post(
    "/scans",
    response_model=ScanJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a scan (admin)",
)
def create_scan(
    body: CreateScanRequest,
    session: DbSession,
    organization: CurrentOrganization,
    registry: MediaRegistry,
    settings: SettingsDep,
    _admin: AdminRequired,
) -> ScanJobResponse:
    job = service.enqueue_scan(
        session,
        organization.id,
        registry,
        root_key=body.root_key,
        trigger=ScanTrigger.MANUAL,
        requested_by="api",
        full_rescan=body.full_rescan,
        max_attempts=settings.media_scan_max_attempts,
    )
    session.commit()
    session.refresh(job)
    return service.scan_job_response(job)
