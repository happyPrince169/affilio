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
    CurrentIdentity,
    CurrentOrganization,
    DbSession,
    MediaRegistry,
    RequireAdmin,
    RequireEditor,
    SettingsDep,
)
from affilio.core.audit import record_audit
from affilio.core.enums import HandoffStatus, MediaAssetKind, MediaAssetStatus, ScanTrigger
from affilio.modules.media import classification, service
from affilio.modules.media.models import FolderCategoryRule
from affilio.modules.media.schemas import (
    AssetCategoriesUpdate,
    CategoryCountEntry,
    CreateScanRequest,
    FolderPreviewResponse,
    FolderReportEntry,
    FolderRuleCreate,
    FolderRuleResponse,
    FolderRuleUpdate,
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
from affilio.modules.media.service import AssetFilters, HandoffNotApplicableError
from affilio.modules.taxonomy import service as taxonomy_service

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
    category_id: uuid.UUID | None = None,
    uncategorized: bool = False,
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
        category_id=category_id,
        uncategorized=uncategorized,
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
    identity: RequireAdmin,
) -> MediaAssetDetail:
    service.review_handoff(session, organization.id, asset_id, body, actor=identity.label)
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
    identity: RequireAdmin,
) -> ScanJobResponse:
    job = service.enqueue_scan(
        session,
        organization.id,
        registry,
        root_key=body.root_key,
        trigger=ScanTrigger.MANUAL,
        requested_by=identity.label,
        full_rescan=body.full_rescan,
        max_attempts=settings.media_scan_max_attempts,
    )
    session.commit()
    session.refresh(job)
    return service.scan_job_response(job)


# ---------------------------------------------------------------- classification (M3A.1)


def _rule_response(rule: FolderCategoryRule) -> FolderRuleResponse:
    chain: list[str] = []
    node = rule.category
    guard = 0
    while node is not None and guard < 10:
        chain.append(node.name)
        node = node.parent
        guard += 1
    return FolderRuleResponse(
        id=rule.id,
        media_root_id=rule.media_root_id,
        root_key=rule.root.key,
        relative_folder_path=rule.relative_folder_path,
        category_id=rule.category_id,
        category_name=rule.category.name,
        category_path=" / ".join(reversed(chain)),
        include_subfolders=rule.include_subfolders,
        priority=rule.priority,
        is_active=rule.is_active,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    )


@router.put(
    "/assets/{asset_id}/categories",
    response_model=MediaAssetDetail,
    summary="Manual category override for a final video (editor)",
)
def set_asset_categories(
    asset_id: uuid.UUID,
    body: AssetCategoriesUpdate,
    request: Request,
    session: DbSession,
    identity: RequireEditor,
    settings: SettingsDep,
) -> MediaAssetDetail:
    asset = service.get_asset(session, identity.organization.id, asset_id)
    if asset.asset_kind != MediaAssetKind.FINAL:
        raise HandoffNotApplicableError("Only final videos are classified")
    classification.set_manual_categories(
        session,
        identity.organization.id,
        asset,
        primary_category_id=body.primary_category_id,
        secondary_category_ids=body.secondary_category_ids,
    )
    record_audit(
        session,
        organization_id=identity.organization.id,
        identity=identity,
        action="media.categories_overridden",
        entity_type="media_asset",
        entity_id=asset.id,
        data=body.model_dump(mode="json"),
    )
    session.commit()
    asset = service.get_asset(session, identity.organization.id, asset_id)
    return service.asset_detail(
        session, asset, thumbnail_url_prefix=_thumbnail_prefix(request, settings.api_v1_prefix)
    )


@router.get(
    "/categories/counts", response_model=list[CategoryCountEntry], summary="Primary-category counts"
)
def category_counts(session: DbSession, identity: CurrentIdentity) -> list[CategoryCountEntry]:
    return [
        CategoryCountEntry(category_id=cid, primary_count=n)
        for cid, n in classification.category_counts(session, identity.organization.id).items()
    ]


@router.get(
    "/folder-rules", response_model=list[FolderRuleResponse], summary="Folder → category rules"
)
def list_folder_rules(session: DbSession, identity: CurrentIdentity) -> list[FolderRuleResponse]:
    return [_rule_response(r) for r in classification.list_rules(session, identity.organization.id)]


@router.post(
    "/folder-rules",
    response_model=FolderRuleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a folder rule (admin)",
)
def create_folder_rule(
    body: FolderRuleCreate, session: DbSession, identity: RequireAdmin
) -> FolderRuleResponse:
    rule = classification.create_rule(
        session,
        identity.organization.id,
        media_root_id=body.media_root_id,
        relative_folder_path=body.relative_folder_path,
        category_id=body.category_id,
        include_subfolders=body.include_subfolders,
        priority=body.priority,
        is_active=body.is_active,
    )
    record_audit(
        session,
        organization_id=identity.organization.id,
        identity=identity,
        action="folder_rule.created",
        entity_type="folder_category_rule",
        entity_id=rule.id,
        data={"path": rule.relative_folder_path, "category_id": str(rule.category_id)},
    )
    session.commit()
    return _rule_response(rule)


@router.get(
    "/folder-rules/report",
    response_model=list[FolderReportEntry],
    summary="Folders holding finals and how they map",
)
def folder_rules_report(session: DbSession, identity: CurrentIdentity) -> list[FolderReportEntry]:
    return [
        FolderReportEntry(
            media_root_id=entry.root_id,
            root_key=entry.root_key,
            folder=entry.folder,
            file_count=entry.file_count,
            rule=_rule_response(entry.rule) if entry.rule else None,
            mapped=entry.rule is not None,
        )
        for entry in classification.folder_report(session, identity.organization.id)
    ]


@router.get(
    "/folder-rules/preview",
    response_model=FolderPreviewResponse,
    summary="Which rule would classify a folder",
)
def preview_folder_rule(
    session: DbSession,
    identity: CurrentIdentity,
    media_root_id: uuid.UUID,
    relative_folder_path: Annotated[str, Query(max_length=1024)] = "",
) -> FolderPreviewResponse:
    folder = classification.normalize_folder_path(relative_folder_path)
    rules = [
        r
        for r in classification.list_rules(session, identity.organization.id)
        if r.media_root_id == media_root_id
    ]
    rule = classification.resolve_rule(rules, folder)
    candidates = [
        r
        for r in rules
        if r.is_active
        and classification._matches(r.relative_folder_path, folder, r.include_subfolders)
    ]
    if rule is not None:
        category_id, category_name, fallback = rule.category_id, rule.category.name, False
    else:
        fb = taxonomy_service.ensure_uncategorized(session, identity.organization.id)
        session.commit()
        category_id, category_name, fallback = fb.id, fb.name, True
    return FolderPreviewResponse(
        relative_folder_path=folder,
        rule=_rule_response(rule) if rule else None,
        category_id=category_id,
        category_name=category_name,
        fallback=fallback,
        candidates=[_rule_response(r) for r in candidates],
    )


@router.patch(
    "/folder-rules/{rule_id}",
    response_model=FolderRuleResponse,
    summary="Edit / disable a folder rule (admin)",
)
def update_folder_rule(
    rule_id: uuid.UUID, body: FolderRuleUpdate, session: DbSession, identity: RequireAdmin
) -> FolderRuleResponse:
    rule = classification.update_rule(
        session, identity.organization.id, rule_id, body.model_dump(exclude_unset=True)
    )
    record_audit(
        session,
        organization_id=identity.organization.id,
        identity=identity,
        action="folder_rule.updated",
        entity_type="folder_category_rule",
        entity_id=rule.id,
        data=body.model_dump(exclude_unset=True, mode="json"),
    )
    session.commit()
    return _rule_response(classification.get_rule(session, identity.organization.id, rule_id))


@router.delete(
    "/folder-rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a folder rule (admin)",
)
def delete_folder_rule(
    rule_id: uuid.UUID, session: DbSession, identity: RequireAdmin, response: Response
) -> Response:
    classification.delete_rule(session, identity.organization.id, rule_id)
    record_audit(
        session,
        organization_id=identity.organization.id,
        identity=identity,
        action="folder_rule.deleted",
        entity_type="folder_category_rule",
        entity_id=rule_id,
    )
    session.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
