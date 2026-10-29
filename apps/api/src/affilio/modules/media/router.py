"""Media HTTP routes (M1).

Access model until authentication lands (M3): all routes are organization-scoped through
`get_current_organization`; mutating routes require the admin token (`require_admin`). The
production deployment keeps the API off the public internet (see docs/deployment/synology.md).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, Response, status
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from affilio.api.deps import (
    CurrentIdentity,
    CurrentOrganization,
    DbSession,
    Identity,
    MediaRegistry,
    RequireAdmin,
    RequireEditor,
    SettingsDep,
)
from affilio.core.audit import record_audit
from affilio.core.enums import (
    CategorySuggestionStatus,
    ExportTrigger,
    HandoffStatus,
    MediaAssetKind,
    MediaAssetStatus,
    MembershipRole,
    ScanTrigger,
    ThumbnailStatus,
    UploadStatus,
)
from affilio.modules.media import classification, facets, owner_mappings, service, uploads
from affilio.modules.media import exports as exports_module
from affilio.modules.media.errors import UploadStorageUnavailableError
from affilio.modules.media.models import CategorySuggestion, FolderCategoryRule, MediaUpload
from affilio.modules.media.schemas import (
    AssetCategoriesUpdate,
    AssetExportStatus,
    CategoryCountEntry,
    CategorySuggestionResponse,
    CreateScanRequest,
    ExportJobResponse,
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
    OwnerCategoryRef,
    OwnerMappingCreate,
    OwnerMappingResponse,
    PaginatedAssets,
    ScanJobResponse,
    ScanStatusResponse,
    SortKey,
    SuggestionApprove,
    SuggestionDecisionResponse,
    SuggestionReject,
    UploadChunkResponse,
    UploadCreate,
    UploadLimitsResponse,
    UploadManifestResponse,
    UploadOptionsResponse,
    UploadResponse,
    UploadReview,
    UploadTaxonomyRef,
)
from affilio.modules.media.service import AssetFilters, HandoffNotApplicableError
from affilio.modules.media.upload_storage import StagingStore
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
    production_period: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
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
        production_period=production_period,
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
    registry: MediaRegistry,
) -> MediaAssetDetail:
    asset = service.get_asset(session, organization.id, asset_id)
    return service.asset_detail(
        session,
        asset,
        thumbnail_url_prefix=_thumbnail_prefix(request, settings.api_v1_prefix),
        registry=registry,
        settings=settings,
    )


# ---------------------------------------------------------------- publish-ready exports (E2)


@router.get(
    "/assets/{asset_id}/export",
    response_model=AssetExportStatus | None,
    summary="Publish-ready export state of a final",
)
def read_asset_export(
    asset_id: uuid.UUID,
    session: DbSession,
    organization: CurrentOrganization,
    registry: MediaRegistry,
    settings: SettingsDep,
) -> AssetExportStatus | None:
    asset = service.get_asset(session, organization.id, asset_id)
    return service.asset_export_status(session, asset, registry, settings)


@router.post(
    "/assets/{asset_id}/export",
    response_model=ExportJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a publish-ready export of one final (admin)",
)
def create_asset_export(
    asset_id: uuid.UUID,
    session: DbSession,
    organization: CurrentOrganization,
    settings: SettingsDep,
    identity: RequireAdmin,
) -> ExportJobResponse:
    job = exports_module.enqueue_export(
        session,
        organization.id,
        asset_id,
        trigger=ExportTrigger.MANUAL,
        requested_by=identity.label,
        max_attempts=settings.export_max_attempts,
    )
    record_audit(
        session,
        organization_id=organization.id,
        identity=identity,
        action="export.queued",
        entity_type="media_export_job",
        entity_id=job.id,
        data={"media_asset_id": str(asset_id), "recipe_key": job.recipe_key},
    )
    session.commit()
    session.refresh(job)
    return service.export_job_response(job)


@router.post(
    "/exports/{job_id}/cancel",
    response_model=ExportJobResponse,
    summary="Cancel a queued or running export (admin)",
)
def cancel_asset_export(
    job_id: uuid.UUID,
    session: DbSession,
    organization: CurrentOrganization,
    identity: RequireAdmin,
) -> ExportJobResponse:
    previous = exports_module.get_export_job(session, organization.id, job_id).status
    job = exports_module.cancel_export(session, organization.id, job_id, actor=identity.label)
    record_audit(
        session,
        organization_id=organization.id,
        identity=identity,
        action="export.cancelled",
        entity_type="media_export_job",
        entity_id=job.id,
        data={"media_asset_id": str(job.media_asset_id), "status_before": previous.value},
    )
    session.commit()
    session.refresh(job)
    return service.export_job_response(job)


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


@router.post(
    "/scans/{job_id}/cancel",
    response_model=ScanJobResponse,
    summary="Cancel a queued or running scan (admin)",
)
def cancel_scan(
    job_id: uuid.UUID,
    session: DbSession,
    organization: CurrentOrganization,
    identity: RequireAdmin,
) -> ScanJobResponse:
    previous = service.get_scan_job(session, organization.id, job_id).status
    job = service.cancel_scan(session, organization.id, job_id, actor=identity.label)
    record_audit(
        session,
        organization_id=organization.id,
        identity=identity,
        action="scan.cancelled",
        entity_type="media_scan_job",
        entity_id=job.id,
        data={"root_key": job.root.key, "status_before": previous.value},
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


# ---------------------------------------------------------------- facet suggestions


def _suggestion_response(row: CategorySuggestion) -> CategorySuggestionResponse:
    return CategorySuggestionResponse(
        id=row.id,
        facet=row.facet,
        name=row.name,
        normalized_name=row.normalized_name,
        root_key=row.root.key,
        relative_folder_path=row.relative_folder_path,
        example_relative_path=row.example_relative_path,
        status=row.status,
        seen_count=row.seen_count,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        decided_at=row.decided_at,
        decided_by=row.decided_by,
        note=row.note,
        category_id=row.category_id,
        category_name=row.category.name if row.category is not None else None,
    )


@router.get(
    "/category-suggestions",
    response_model=list[CategorySuggestionResponse],
    summary="Facet values seen in finals folders that match no category yet",
)
def list_category_suggestions(
    session: DbSession,
    identity: CurrentIdentity,
    status_filter: Annotated[CategorySuggestionStatus | None, Query(alias="status")] = None,
) -> list[CategorySuggestionResponse]:
    rows = facets.list_suggestions(session, identity.organization.id, status=status_filter)
    return [_suggestion_response(r) for r in rows]


@router.post(
    "/category-suggestions/{suggestion_id}/approve",
    response_model=SuggestionDecisionResponse,
    summary="Create the category for a suggestion and classify matching finals (admin)",
)
def approve_category_suggestion(
    suggestion_id: uuid.UUID,
    body: SuggestionApprove,
    session: DbSession,
    identity: RequireAdmin,
) -> SuggestionDecisionResponse:
    decision = facets.approve_suggestion(
        session,
        identity.organization.id,
        suggestion_id,
        name=body.name,
        slug=body.slug,
        decided_by=identity.label,
    )
    record_audit(
        session,
        organization_id=identity.organization.id,
        identity=identity,
        action="category_suggestion.approved",
        entity_type="category_suggestion",
        entity_id=suggestion_id,
        data={
            "facet": decision.suggestion.facet.value,
            "name": decision.suggestion.name,
            "category_id": str(decision.category.id) if decision.category else None,
            "reclassified_assets": decision.reclassified_assets,
        },
    )
    session.commit()
    row = facets.get_suggestion(session, identity.organization.id, suggestion_id)
    return SuggestionDecisionResponse(
        suggestion=_suggestion_response(row),
        category_id=row.category_id,
        reclassified_assets=decision.reclassified_assets,
    )


@router.post(
    "/category-suggestions/{suggestion_id}/reject",
    response_model=CategorySuggestionResponse,
    summary="Reject a suggestion so the scanner stops raising it (admin)",
)
def reject_category_suggestion(
    suggestion_id: uuid.UUID,
    body: SuggestionReject,
    session: DbSession,
    identity: RequireAdmin,
) -> CategorySuggestionResponse:
    row = facets.reject_suggestion(
        session,
        identity.organization.id,
        suggestion_id,
        note=body.note,
        decided_by=identity.label,
    )
    record_audit(
        session,
        organization_id=identity.organization.id,
        identity=identity,
        action="category_suggestion.rejected",
        entity_type="category_suggestion",
        entity_id=suggestion_id,
        data={"facet": row.facet.value, "name": row.name, "note": row.note},
    )
    session.commit()
    return _suggestion_response(facets.get_suggestion(session, identity.organization.id, row.id))


# ---------------------------------------------------------------- owner mappings (upload track U2)


def _owner_mapping_response(m) -> OwnerMappingResponse:
    return OwnerMappingResponse(
        id=m.id,
        user_id=m.user_id,
        user_email=m.user.email,
        user_display_name=m.user.display_name,
        production_owner=OwnerCategoryRef.model_validate(m.category),
        status=m.status,
        created_at=m.created_at,
        updated_at=m.updated_at,
        revoked_at=m.revoked_at,
        created_by_user_id=m.created_by_user_id,
        revoked_by_user_id=m.revoked_by_user_id,
    )


@router.get(
    "/owner-mappings",
    response_model=list[OwnerMappingResponse],
    summary="Production-owner mappings of the organization's members (admin)",
)
def list_owner_mappings(
    session: DbSession,
    identity: RequireAdmin,
    user_id: uuid.UUID | None = None,
    include_revoked: Annotated[bool, Query()] = False,
) -> list[OwnerMappingResponse]:
    rows = owner_mappings.list_mappings(
        session, identity.organization.id, user_id=user_id, include_revoked=include_revoked
    )
    return [_owner_mapping_response(m) for m in rows]


@router.get(
    "/owner-mappings/me",
    response_model=list[OwnerCategoryRef],
    summary="Production owners the current user may submit videos for",
)
def my_submittable_owners(session: DbSession, identity: CurrentIdentity) -> list[OwnerCategoryRef]:
    if identity.user is None:
        return []
    owners = owner_mappings.submittable_owners(session, identity.organization.id, identity.user.id)
    return [OwnerCategoryRef.model_validate(c) for c in owners]


@router.post(
    "/owner-mappings",
    response_model=OwnerMappingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Allow a member to submit videos for a production owner (admin)",
)
def create_owner_mapping(
    body: OwnerMappingCreate, session: DbSession, identity: RequireAdmin
) -> OwnerMappingResponse:
    actor_id = identity.user.id if identity.user is not None else None
    change = owner_mappings.create_mapping(
        session,
        identity.organization.id,
        user_id=body.user_id,
        category_id=body.production_owner_category_id,
        actor_user_id=actor_id,
    )
    record_audit(
        session,
        organization_id=identity.organization.id,
        identity=identity,
        action="owner_mapping.created",
        entity_type="media_owner_mapping",
        entity_id=change.mapping.id,
        data={
            "user_id": str(body.user_id),
            "production_owner_category_id": str(change.mapping.production_owner_category_id),
            "production_owner_slug": change.mapping.category.slug,
            "reactivated": change.reactivated,
        },
    )
    session.commit()
    return _owner_mapping_response(
        owner_mappings.get_mapping(session, identity.organization.id, change.mapping.id)
    )


@router.delete(
    "/owner-mappings/{mapping_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a production-owner mapping; the row is kept for history (admin)",
)
def revoke_owner_mapping(
    mapping_id: uuid.UUID, session: DbSession, identity: RequireAdmin, response: Response
) -> Response:
    actor_id = identity.user.id if identity.user is not None else None
    mapping, changed = owner_mappings.revoke_mapping(
        session, identity.organization.id, mapping_id, actor_user_id=actor_id
    )
    if changed:
        record_audit(
            session,
            organization_id=identity.organization.id,
            identity=identity,
            action="owner_mapping.revoked",
            entity_type="media_owner_mapping",
            entity_id=mapping.id,
            data={
                "user_id": str(mapping.user_id),
                "production_owner_category_id": str(mapping.production_owner_category_id),
                "production_owner_slug": mapping.category.slug,
            },
        )
    session.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


# ---------------------------------------------------------------- editor uploads (upload track U4)
# Contract: docs/architecture/editor-uploads.md section 2. Progress is server-authoritative.


def _upload_response(
    upload: MediaUpload, identity: Identity, api_prefix: str = "/api/v1"
) -> UploadResponse:
    asset = upload.media_asset
    is_admin = identity.has_role(MembershipRole.ADMIN)
    thumbnail_url = (
        f"{api_prefix}/media/assets/{asset.id}/thumbnail"
        if asset is not None and asset.thumbnail_status == ThumbnailStatus.READY
        else None
    )
    review = (
        UploadReview(
            handoff_status=asset.handoff_status,
            handoff_note=asset.handoff_note,
            handoff_reviewed_at=asset.handoff_reviewed_at,
            handoff_reviewed_by=asset.handoff_reviewed_by,
        )
        if asset is not None and asset.handoff_status is not None
        else None
    )
    return UploadResponse(
        id=upload.id,
        status=upload.status,
        original_filename=upload.original_filename,
        expected_size=upload.expected_size,
        chunk_bytes=upload.chunk_bytes,
        committed_offset=upload.committed_offset,
        production_owner=UploadTaxonomyRef.model_validate(upload.production_owner),
        production_owner_name=upload.production_owner_name,
        product_category=UploadTaxonomyRef.model_validate(upload.product_category),
        content_type=UploadTaxonomyRef.model_validate(upload.content_type),
        batch_id=upload.batch_id,
        replaces_asset_id=upload.replaces_asset_id,
        uploaded_by_user_id=upload.uploaded_by_user_id,
        submitted_for_user_id=upload.submitted_for_user_id,
        media_asset_id=upload.media_asset_id,
        duplicate_of_asset_id=upload.duplicate_of_asset_id if is_admin else None,
        failure_code=upload.failure_code,
        failure_detail=upload.failure_detail,
        finalize_attempts=upload.finalize_attempts,
        review=review,
        last_chunk_offset=upload.last_chunk_offset,
        last_chunk_sha256=upload.last_chunk_sha256,
        asset_title=asset.title if asset is not None else None,
        thumbnail_url=thumbnail_url,
        created_at=upload.created_at,
        updated_at=upload.updated_at,
        uploaded_at=upload.uploaded_at,
        completed_at=upload.completed_at,
        expires_at=upload.expires_at,
    )


def _staging(settings) -> StagingStore | None:
    return StagingStore.for_api(settings.media_incoming_path)


@router.get(
    "/uploads/options",
    response_model=UploadOptionsResponse,
    summary="Owners, categories and limits the current user may upload with",
)
def upload_options(
    session: DbSession, identity: RequireEditor, settings: SettingsDep
) -> UploadOptionsResponse:
    opts = uploads.options_for(session, identity)
    limits = uploads.UploadLimits.from_settings(settings)
    return UploadOptionsResponse(
        facets_enabled=opts.facets_enabled,
        can_submit_for_others=opts.can_submit_for_others,
        owners=[UploadTaxonomyRef.model_validate(c) for c in opts.owners],
        product_categories=[UploadTaxonomyRef.model_validate(c) for c in opts.product_categories],
        content_types=[UploadTaxonomyRef.model_validate(c) for c in opts.content_types],
        allowed_extensions=list(settings.media_video_extensions),
        limits=UploadLimitsResponse(
            chunk_bytes=limits.chunk_bytes,
            max_file_bytes=limits.max_file_bytes,
            max_active_sessions_per_user=limits.max_active_sessions_per_user,
            session_ttl_hours=limits.session_ttl_hours,
        ),
    )


@router.post(
    "/uploads",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a resumable upload session for one final video (editor)",
)
def create_upload(
    body: UploadCreate, session: DbSession, identity: RequireEditor, settings: SettingsDep
) -> UploadResponse:
    upload = uploads.create_upload(
        session,
        identity,
        data=body,
        staging=_staging(settings),
        limits=uploads.UploadLimits.from_settings(settings),
        allowed_extensions=set(settings.media_video_extensions),
    )
    record_audit(
        session,
        organization_id=identity.organization.id,
        identity=identity,
        action="upload.created",
        entity_type="media_upload",
        entity_id=upload.id,
        data={
            "filename": upload.safe_filename,
            "size": upload.expected_size,
            "production_owner_category_id": str(upload.production_owner_category_id),
            "submitted_for_user_id": (
                str(upload.submitted_for_user_id) if upload.submitted_for_user_id else None
            ),
            "batch_id": str(upload.batch_id) if upload.batch_id else None,
            "replaces_asset_id": str(upload.replaces_asset_id)
            if upload.replaces_asset_id
            else None,
        },
    )
    session.commit()
    return _upload_response(
        uploads.get_upload(session, identity.organization.id, upload.id),
        identity,
        settings.api_v1_prefix,
    )


@router.get(
    "/uploads",
    response_model=list[UploadResponse],
    summary="Upload sessions: the caller's own, or everyone's for admins",
)
def list_uploads(
    session: DbSession,
    identity: RequireEditor,
    settings: SettingsDep,
    status_filter: Annotated[UploadStatus | None, Query(alias="status")] = None,
    user_id: uuid.UUID | None = None,
    batch_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[UploadResponse]:
    rows = uploads.list_uploads(
        session, identity, status=status_filter, user_id=user_id, batch_id=batch_id, limit=limit
    )
    return [_upload_response(u, identity, settings.api_v1_prefix) for u in rows]


@router.get("/uploads/{upload_id}", response_model=UploadResponse, summary="Upload session state")
def read_upload(
    upload_id: uuid.UUID, session: DbSession, identity: RequireEditor, settings: SettingsDep
) -> UploadResponse:
    upload = uploads.get_upload(session, identity.organization.id, upload_id)
    uploads.authorize_access(identity, upload)
    return _upload_response(upload, identity, settings.api_v1_prefix)


@router.get(
    "/uploads/{upload_id}/manifest",
    response_model=UploadManifestResponse,
    summary="Digests of the committed chunks (resume identity check)",
)
def read_upload_manifest(
    upload_id: uuid.UUID, session: DbSession, identity: RequireEditor
) -> UploadManifestResponse:
    upload = uploads.get_upload(session, identity.organization.id, upload_id)
    uploads.authorize_access(identity, upload)
    return UploadManifestResponse(
        id=upload.id,
        status=upload.status,
        chunk_bytes=upload.chunk_bytes,
        expected_size=upload.expected_size,
        committed_offset=upload.committed_offset,
        digests=list(upload.chunk_manifest or []),
    )


@router.patch(
    "/uploads/{upload_id}/chunk",
    response_model=UploadChunkResponse,
    summary="Append one chunk at the committed offset (streamed, idempotent on retry)",
)
async def upload_chunk(
    upload_id: uuid.UUID,
    request: Request,
    session: DbSession,
    identity: RequireEditor,
    settings: SettingsDep,
    upload_offset: Annotated[int, Header(alias="Upload-Offset", ge=0)],
    content_length: Annotated[int, Header(alias="Content-Length", ge=0)],
    chunk_sha256: Annotated[
        str, Header(alias="X-Upload-Chunk-Sha256", pattern=r"^[0-9a-fA-F]{64}$")
    ],
) -> UploadChunkResponse:
    staging = _staging(settings)
    if staging is None:
        raise UploadStorageUnavailableError("Uploads are not enabled on this server")
    now = datetime.now(UTC)
    org_id = identity.organization.id

    def _prepare() -> uploads.ChunkPlan:
        upload = uploads.lock_for_chunk(session, org_id, upload_id)
        uploads.authorize_access(identity, upload)
        return uploads.plan_chunk(
            upload, offset=upload_offset, length=content_length, sha256=chunk_sha256, now=now
        )

    plan = await run_in_threadpool(_prepare)
    if plan.replay:
        await run_in_threadpool(session.rollback)
        return UploadChunkResponse(
            id=plan.upload.id,
            status=plan.upload.status,
            committed_offset=plan.upload.committed_offset,
            expected_size=plan.upload.expected_size,
            replayed=True,
        )
    try:
        committed = await uploads.write_chunk(
            plan.upload,
            staging,
            offset=upload_offset,
            length=content_length,
            sha256=chunk_sha256,
            body=request.stream(),
            now=now,
            ttl_hours=settings.upload_session_ttl_hours,
        )
    except Exception:
        await run_in_threadpool(session.rollback)
        raise
    await run_in_threadpool(session.commit)
    return UploadChunkResponse(
        id=plan.upload.id,
        status=plan.upload.status,
        committed_offset=committed,
        expected_size=plan.upload.expected_size,
        replayed=False,
    )


@router.post(
    "/uploads/{upload_id}/complete",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Hand a fully received upload to the worker for verification and placement",
)
def complete_upload(
    upload_id: uuid.UUID, session: DbSession, identity: RequireEditor, settings: SettingsDep
) -> UploadResponse:
    org_id = identity.organization.id
    uploads.authorize_access(identity, uploads.get_upload(session, org_id, upload_id))
    upload, changed = uploads.complete_upload(session, org_id, upload_id, now=datetime.now(UTC))
    if changed:
        record_audit(
            session,
            organization_id=org_id,
            identity=identity,
            action="upload.uploaded",
            entity_type="media_upload",
            entity_id=upload.id,
            data={"size": upload.expected_size},
        )
    session.commit()
    return _upload_response(
        uploads.get_upload(session, org_id, upload_id), identity, settings.api_v1_prefix
    )


@router.delete(
    "/uploads/{upload_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Abort an upload session and discard its staged bytes",
)
def abort_upload(
    upload_id: uuid.UUID,
    session: DbSession,
    identity: RequireEditor,
    settings: SettingsDep,
    response: Response,
) -> Response:
    org_id = identity.organization.id
    uploads.authorize_access(identity, uploads.get_upload(session, org_id, upload_id))
    upload, changed = uploads.abort_upload(session, org_id, upload_id, _staging(settings))
    if changed:
        record_audit(
            session,
            organization_id=org_id,
            identity=identity,
            action="upload.aborted",
            entity_type="media_upload",
            entity_id=upload.id,
        )
    session.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
