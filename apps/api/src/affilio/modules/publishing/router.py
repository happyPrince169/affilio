"""Distribution routes: campaigns, publications, queue, calendar, stats (M3A)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query, Request, status

from affilio.api.deps import (
    CurrentIdentity,
    DbSession,
    Identity,
    RequireAdmin,
    RequireEditor,
    SettingsDep,
)
from affilio.core.audit import record_audit
from affilio.core.enums import CampaignStatus, Platform, PublicationStatus
from affilio.modules.publishing import service
from affilio.modules.publishing.schemas import (
    CalendarResponse,
    CampaignCreate,
    CampaignDetail,
    CampaignMediaAdd,
    CampaignTargetsAdd,
    CampaignUpdate,
    EngineStatus,
    EvidenceRequest,
    FailureRequest,
    PaginatedCampaigns,
    PaginatedPublications,
    PreparedPublicationResponse,
    PublicationBatchCreate,
    PublicationBatchResponse,
    PublicationContentUpdate,
    PublicationCreate,
    PublicationDetail,
    PublicationSummary,
    PublishingStats,
    ReconcileRequest,
    ReleaseDueResponse,
    ScheduleRequest,
    VersionedRequest,
)

router = APIRouter(prefix="/publishing", tags=["publishing"])


def _thumb(request: Request, prefix: str) -> str:
    # Relative to the API origin so the web proxy and direct access both work.
    return f"{prefix}/media/assets"


def _detail(
    session: DbSession, identity: Identity, request: Request, prefix: str, job
) -> PublicationDetail:
    return service.job_detail(job, thumbnail_url_prefix=_thumb(request, prefix), role=identity.role)


# ------------------------------------------------------------------ stats / queue / calendar


@router.get("/stats", response_model=PublishingStats, summary="Distribution overview")
def stats(identity: CurrentIdentity, session: DbSession) -> PublishingStats:
    return service.stats(session, identity.organization.id)


@router.get(
    "/engine/status",
    response_model=EngineStatus,
    summary="Publisher worker health, upload phases and per-Page publishing status",
)
def engine_status(
    identity: CurrentIdentity, session: DbSession, settings: SettingsDep
) -> EngineStatus:
    return service.engine_status(session, identity.organization.id, settings)


@router.get("/queue", response_model=list[PublicationSummary], summary="Publishing queue")
def queue(
    identity: CurrentIdentity, session: DbSession, request: Request, settings: SettingsDep
) -> list[PublicationSummary]:
    return service.queue(
        session,
        identity.organization.id,
        thumbnail_url_prefix=_thumb(request, settings.api_v1_prefix),
    )


@router.post(
    "/queue/release-due",
    response_model=ReleaseDueResponse,
    summary="Move due SCHEDULED jobs to READY_TO_PUBLISH",
)
def release_due(identity: RequireEditor, session: DbSession) -> ReleaseDueResponse:
    ids = service.release_due(session, identity.organization.id, identity=identity)
    session.commit()
    return ReleaseDueResponse(released=len(ids), publication_ids=ids)


@router.get("/calendar", response_model=CalendarResponse, summary="Publications in a UTC range")
def calendar(
    identity: CurrentIdentity,
    session: DbSession,
    request: Request,
    settings: SettingsDep,
    start: datetime | None = None,
    end: datetime | None = None,
) -> CalendarResponse:
    now = datetime.now(UTC)
    range_start = (
        start or now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=7)
    ).astimezone(UTC)
    range_end = (end or range_start + timedelta(days=35)).astimezone(UTC)
    return service.calendar(
        session,
        identity.organization.id,
        timezone=identity.organization.timezone,
        start=range_start,
        end=range_end,
        thumbnail_url_prefix=_thumb(request, settings.api_v1_prefix),
    )


# ------------------------------------------------------------------ campaigns


@router.get("/campaigns", response_model=PaginatedCampaigns, summary="Campaigns")
def list_campaigns(
    identity: CurrentIdentity,
    session: DbSession,
    status_filter: Annotated[CampaignStatus | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> PaginatedCampaigns:
    return service.list_campaigns(
        session, identity.organization.id, status=status_filter, q=q, page=page, page_size=page_size
    )


@router.post(
    "/campaigns",
    response_model=CampaignDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a campaign (editor)",
)
def create_campaign(
    body: CampaignCreate,
    identity: RequireEditor,
    session: DbSession,
    request: Request,
    settings: SettingsDep,
) -> CampaignDetail:
    campaign = service.create_campaign(session, identity.organization.id, body, identity=identity)
    record_audit(
        session,
        organization_id=identity.organization.id,
        identity=identity,
        action="campaign.created",
        entity_type="campaign",
        entity_id=campaign.id,
        data={"name": campaign.name},
    )
    session.commit()
    return service.campaign_detail(
        session, campaign, thumbnail_url_prefix=_thumb(request, settings.api_v1_prefix)
    )


@router.get("/campaigns/{campaign_id}", response_model=CampaignDetail, summary="Campaign detail")
def read_campaign(
    campaign_id: uuid.UUID,
    identity: CurrentIdentity,
    session: DbSession,
    request: Request,
    settings: SettingsDep,
) -> CampaignDetail:
    campaign = service.get_campaign(session, identity.organization.id, campaign_id)
    return service.campaign_detail(
        session, campaign, thumbnail_url_prefix=_thumb(request, settings.api_v1_prefix)
    )


@router.patch(
    "/campaigns/{campaign_id}", response_model=CampaignDetail, summary="Update a campaign (editor)"
)
def update_campaign(
    campaign_id: uuid.UUID,
    body: CampaignUpdate,
    identity: RequireEditor,
    session: DbSession,
    request: Request,
    settings: SettingsDep,
) -> CampaignDetail:
    campaign = service.get_campaign(session, identity.organization.id, campaign_id)
    service.update_campaign(campaign, body)
    record_audit(
        session,
        organization_id=identity.organization.id,
        identity=identity,
        action="campaign.updated",
        entity_type="campaign",
        entity_id=campaign.id,
        data=body.model_dump(exclude_unset=True, mode="json"),
    )
    session.commit()
    return service.campaign_detail(
        session, campaign, thumbnail_url_prefix=_thumb(request, settings.api_v1_prefix)
    )


@router.post(
    "/campaigns/{campaign_id}/media",
    response_model=CampaignDetail,
    summary="Add approved finals (editor)",
)
def add_media(
    campaign_id: uuid.UUID,
    body: CampaignMediaAdd,
    identity: RequireEditor,
    session: DbSession,
    request: Request,
    settings: SettingsDep,
) -> CampaignDetail:
    campaign = service.get_campaign(session, identity.organization.id, campaign_id)
    added = service.add_campaign_media(
        session, identity.organization.id, campaign, body.media_asset_ids
    )
    record_audit(
        session,
        organization_id=identity.organization.id,
        identity=identity,
        action="campaign.media_added",
        entity_type="campaign",
        entity_id=campaign.id,
        data={"media_asset_ids": [str(a.media_asset_id) for a in added]},
    )
    session.commit()
    return service.campaign_detail(
        session, campaign, thumbnail_url_prefix=_thumb(request, settings.api_v1_prefix)
    )


@router.delete(
    "/campaigns/{campaign_id}/media/{asset_id}",
    response_model=CampaignDetail,
    summary="Remove a video (editor)",
)
def remove_media(
    campaign_id: uuid.UUID,
    asset_id: uuid.UUID,
    identity: RequireEditor,
    session: DbSession,
    request: Request,
    settings: SettingsDep,
) -> CampaignDetail:
    campaign = service.get_campaign(session, identity.organization.id, campaign_id)
    service.remove_campaign_media(session, campaign, asset_id)
    record_audit(
        session,
        organization_id=identity.organization.id,
        identity=identity,
        action="campaign.media_removed",
        entity_type="campaign",
        entity_id=campaign.id,
        data={"media_asset_id": str(asset_id)},
    )
    session.commit()
    return service.campaign_detail(
        session, campaign, thumbnail_url_prefix=_thumb(request, settings.api_v1_prefix)
    )


@router.post(
    "/campaigns/{campaign_id}/targets",
    response_model=CampaignDetail,
    summary="Add target accounts (editor)",
)
def add_targets(
    campaign_id: uuid.UUID,
    body: CampaignTargetsAdd,
    identity: RequireEditor,
    session: DbSession,
    request: Request,
    settings: SettingsDep,
) -> CampaignDetail:
    campaign = service.get_campaign(session, identity.organization.id, campaign_id)
    added = service.add_campaign_targets(
        session, identity.organization.id, campaign, body.social_account_ids
    )
    record_audit(
        session,
        organization_id=identity.organization.id,
        identity=identity,
        action="campaign.targets_added",
        entity_type="campaign",
        entity_id=campaign.id,
        data={"social_account_ids": [str(t.social_account_id) for t in added]},
    )
    session.commit()
    return service.campaign_detail(
        session, campaign, thumbnail_url_prefix=_thumb(request, settings.api_v1_prefix)
    )


@router.delete(
    "/campaigns/{campaign_id}/targets/{account_id}",
    response_model=CampaignDetail,
    summary="Remove a target (editor)",
)
def remove_target(
    campaign_id: uuid.UUID,
    account_id: uuid.UUID,
    identity: RequireEditor,
    session: DbSession,
    request: Request,
    settings: SettingsDep,
) -> CampaignDetail:
    campaign = service.get_campaign(session, identity.organization.id, campaign_id)
    service.remove_campaign_target(session, campaign, account_id)
    record_audit(
        session,
        organization_id=identity.organization.id,
        identity=identity,
        action="campaign.target_removed",
        entity_type="campaign",
        entity_id=campaign.id,
        data={"social_account_id": str(account_id)},
    )
    session.commit()
    return service.campaign_detail(
        session, campaign, thumbnail_url_prefix=_thumb(request, settings.api_v1_prefix)
    )


# ------------------------------------------------------------------ publications


@router.get("/publications", response_model=PaginatedPublications, summary="Publications")
def list_publications(
    identity: CurrentIdentity,
    session: DbSession,
    request: Request,
    settings: SettingsDep,
    status_filter: Annotated[PublicationStatus | None, Query(alias="status")] = None,
    statuses: Annotated[list[PublicationStatus] | None, Query()] = None,
    campaign_id: uuid.UUID | None = None,
    social_account_id: uuid.UUID | None = None,
    media_asset_id: uuid.UUID | None = None,
    platform: Platform | None = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
    scheduled_from: datetime | None = None,
    scheduled_to: datetime | None = None,
    sort: Annotated[
        str, Query(pattern="^(updated_desc|scheduled_asc|scheduled_desc|created_desc)$")
    ] = "updated_desc",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> PaginatedPublications:
    return service.list_jobs(
        session,
        identity.organization.id,
        thumbnail_url_prefix=_thumb(request, settings.api_v1_prefix),
        status=status_filter,
        statuses=statuses,
        campaign_id=campaign_id,
        social_account_id=social_account_id,
        media_asset_id=media_asset_id,
        platform=platform,
        q=q,
        scheduled_from=scheduled_from,
        scheduled_to=scheduled_to,
        sort=sort,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/publications",
    response_model=PublicationDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a publication (editor)",
)
def create_publication(
    body: PublicationCreate,
    identity: RequireEditor,
    session: DbSession,
    request: Request,
    settings: SettingsDep,
):
    from fastapi.responses import JSONResponse

    job, created = service.create_job(session, identity.organization.id, body, identity=identity)
    session.commit()
    detail = _detail(session, identity, request, settings.api_v1_prefix, job)
    if created:
        return detail
    return JSONResponse(status_code=status.HTTP_200_OK, content=detail.model_dump(mode="json"))


@router.post(
    "/publications/batch",
    response_model=PublicationBatchResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Composer: one video, several targets (editor)",
)
def create_publications_batch(
    body: PublicationBatchCreate,
    identity: RequireEditor,
    session: DbSession,
    request: Request,
    settings: SettingsDep,
) -> PublicationBatchResponse:
    created, reused = service.create_jobs_batch(
        session, identity.organization.id, body, identity=identity
    )
    session.commit()
    prefix = settings.api_v1_prefix
    return PublicationBatchResponse(
        created=[_detail(session, identity, request, prefix, j) for j in created],
        reused=[_detail(session, identity, request, prefix, j) for j in reused],
    )


@router.get(
    "/publications/{job_id}", response_model=PublicationDetail, summary="Publication detail"
)
def read_publication(
    job_id: uuid.UUID,
    identity: CurrentIdentity,
    session: DbSession,
    request: Request,
    settings: SettingsDep,
) -> PublicationDetail:
    return _detail(
        session,
        identity,
        request,
        settings.api_v1_prefix,
        service.get_job(session, identity.organization.id, job_id),
    )


@router.get(
    "/publications/{job_id}/prepare",
    response_model=PreparedPublicationResponse,
    summary="Assisted publishing checklist from the approved snapshot",
)
def prepare_publication(
    job_id: uuid.UUID, identity: CurrentIdentity, session: DbSession
) -> PreparedPublicationResponse:
    return service.prepare(session, identity.organization.id, job_id)


@router.patch(
    "/publications/{job_id}",
    response_model=PublicationDetail,
    summary="Edit content (editor). Invalidates approval.",
)
def update_publication(
    job_id: uuid.UUID,
    body: PublicationContentUpdate,
    identity: RequireEditor,
    session: DbSession,
    request: Request,
    settings: SettingsDep,
) -> PublicationDetail:
    job = service.update_content(session, identity.organization.id, job_id, body, identity=identity)
    session.commit()
    return _detail(session, identity, request, settings.api_v1_prefix, job)


@router.post(
    "/publications/{job_id}/submit",
    response_model=PublicationDetail,
    summary="DRAFT → READY_FOR_REVIEW (editor)",
)
def submit_publication(
    job_id: uuid.UUID,
    body: VersionedRequest,
    identity: RequireEditor,
    session: DbSession,
    request: Request,
    settings: SettingsDep,
) -> PublicationDetail:
    job = service.submit(
        session,
        identity.organization.id,
        job_id,
        version=body.version,
        note=body.note,
        identity=identity,
    )
    session.commit()
    return _detail(session, identity, request, settings.api_v1_prefix, job)


@router.post(
    "/publications/{job_id}/return-to-draft",
    response_model=PublicationDetail,
    summary="READY_FOR_REVIEW → DRAFT (editor)",
)
def return_to_draft_publication(
    job_id: uuid.UUID,
    body: VersionedRequest,
    identity: RequireEditor,
    session: DbSession,
    request: Request,
    settings: SettingsDep,
) -> PublicationDetail:
    job = service.return_to_draft(
        session,
        identity.organization.id,
        job_id,
        version=body.version,
        note=body.note,
        identity=identity,
    )
    session.commit()
    return _detail(session, identity, request, settings.api_v1_prefix, job)


@router.post(
    "/publications/{job_id}/approve",
    response_model=PublicationDetail,
    summary="READY_FOR_REVIEW → APPROVED, freezes a snapshot (admin)",
)
def approve_publication(
    job_id: uuid.UUID,
    body: VersionedRequest,
    identity: RequireAdmin,
    session: DbSession,
    request: Request,
    settings: SettingsDep,
) -> PublicationDetail:
    job = service.approve(
        session,
        identity.organization.id,
        job_id,
        version=body.version,
        note=body.note,
        identity=identity,
    )
    session.commit()
    return _detail(session, identity, request, settings.api_v1_prefix, job)


@router.post(
    "/publications/{job_id}/revoke-approval",
    response_model=PublicationDetail,
    summary="APPROVED/SCHEDULED/READY → DRAFT (admin)",
)
def revoke_approval_publication(
    job_id: uuid.UUID,
    body: VersionedRequest,
    identity: RequireAdmin,
    session: DbSession,
    request: Request,
    settings: SettingsDep,
) -> PublicationDetail:
    job = service.revoke_approval(
        session,
        identity.organization.id,
        job_id,
        version=body.version,
        note=body.note,
        identity=identity,
    )
    session.commit()
    return _detail(session, identity, request, settings.api_v1_prefix, job)


@router.post(
    "/publications/{job_id}/unschedule",
    response_model=PublicationDetail,
    summary="SCHEDULED/READY → APPROVED (editor)",
)
def unschedule_publication(
    job_id: uuid.UUID,
    body: VersionedRequest,
    identity: RequireEditor,
    session: DbSession,
    request: Request,
    settings: SettingsDep,
) -> PublicationDetail:
    job = service.unschedule(
        session,
        identity.organization.id,
        job_id,
        version=body.version,
        note=body.note,
        identity=identity,
    )
    session.commit()
    return _detail(session, identity, request, settings.api_v1_prefix, job)


@router.post(
    "/publications/{job_id}/start",
    response_model=PublicationDetail,
    summary="Begin an assisted attempt → PUBLISHING (editor)",
)
def start_publication(
    job_id: uuid.UUID,
    body: VersionedRequest,
    identity: RequireEditor,
    session: DbSession,
    request: Request,
    settings: SettingsDep,
) -> PublicationDetail:
    job = service.start(
        session,
        identity.organization.id,
        job_id,
        version=body.version,
        note=body.note,
        identity=identity,
    )
    session.commit()
    return _detail(session, identity, request, settings.api_v1_prefix, job)


@router.post(
    "/publications/{job_id}/retry",
    response_model=PublicationDetail,
    summary="FAILED → READY_TO_PUBLISH, explicit operator retry (editor)",
)
def retry_publication(
    job_id: uuid.UUID,
    body: VersionedRequest,
    identity: RequireEditor,
    session: DbSession,
    request: Request,
    settings: SettingsDep,
) -> PublicationDetail:
    job = service.retry(
        session,
        identity.organization.id,
        job_id,
        version=body.version,
        note=body.note,
        identity=identity,
    )
    session.commit()
    return _detail(session, identity, request, settings.api_v1_prefix, job)


@router.post(
    "/publications/{job_id}/revise",
    response_model=PublicationDetail,
    summary="FAILED → DRAFT (editor)",
)
def revise_publication(
    job_id: uuid.UUID,
    body: VersionedRequest,
    identity: RequireEditor,
    session: DbSession,
    request: Request,
    settings: SettingsDep,
) -> PublicationDetail:
    job = service.revise(
        session,
        identity.organization.id,
        job_id,
        version=body.version,
        note=body.note,
        identity=identity,
    )
    session.commit()
    return _detail(session, identity, request, settings.api_v1_prefix, job)


@router.post(
    "/publications/{job_id}/revalidate",
    response_model=PublicationDetail,
    summary="Confirm the approved snapshot still applies after reclassification (admin)",
)
def revalidate_publication(
    job_id: uuid.UUID,
    body: VersionedRequest,
    identity: RequireAdmin,
    session: DbSession,
    request: Request,
    settings: SettingsDep,
) -> PublicationDetail:
    job = service.revalidate(
        session,
        identity.organization.id,
        job_id,
        version=body.version,
        note=body.note,
        identity=identity,
    )
    session.commit()
    return _detail(session, identity, request, settings.api_v1_prefix, job)


@router.post(
    "/publications/{job_id}/cancel",
    response_model=PublicationDetail,
    summary="→ CANCELLED (editor)",
)
def cancel_publication(
    job_id: uuid.UUID,
    body: VersionedRequest,
    identity: RequireEditor,
    session: DbSession,
    request: Request,
    settings: SettingsDep,
) -> PublicationDetail:
    job = service.cancel(
        session,
        identity.organization.id,
        job_id,
        version=body.version,
        note=body.note,
        identity=identity,
    )
    session.commit()
    return _detail(session, identity, request, settings.api_v1_prefix, job)


@router.post(
    "/publications/{job_id}/schedule",
    response_model=PublicationDetail,
    summary="APPROVED → SCHEDULED at a UTC instant (editor)",
)
def schedule_publication(
    job_id: uuid.UUID,
    body: ScheduleRequest,
    identity: RequireEditor,
    session: DbSession,
    request: Request,
    settings: SettingsDep,
) -> PublicationDetail:
    job = service.schedule(
        session,
        identity.organization.id,
        job_id,
        body,
        identity=identity,
        min_gap=timedelta(minutes=settings.publication_min_gap_minutes),
    )
    session.commit()
    return _detail(session, identity, request, settings.api_v1_prefix, job)


@router.post(
    "/publications/{job_id}/confirm",
    response_model=PublicationDetail,
    summary="PUBLISHING → PUBLISHED with operator-reported evidence (editor)",
)
def confirm_publication(
    job_id: uuid.UUID,
    body: EvidenceRequest,
    identity: RequireEditor,
    session: DbSession,
    request: Request,
    settings: SettingsDep,
) -> PublicationDetail:
    job = service.confirm(session, identity.organization.id, job_id, body, identity=identity)
    session.commit()
    return _detail(session, identity, request, settings.api_v1_prefix, job)


@router.post(
    "/publications/{job_id}/fail",
    response_model=PublicationDetail,
    summary="PUBLISHING → FAILED or NEEDS_RECONCILIATION (editor)",
)
def fail_publication(
    job_id: uuid.UUID,
    body: FailureRequest,
    identity: RequireEditor,
    session: DbSession,
    request: Request,
    settings: SettingsDep,
) -> PublicationDetail:
    job = service.fail(session, identity.organization.id, job_id, body, identity=identity)
    session.commit()
    return _detail(session, identity, request, settings.api_v1_prefix, job)


@router.post(
    "/publications/{job_id}/reconcile-provider",
    response_model=PublicationDetail,
    summary="Ask the provider about a parked API attempt (admin); never re-sends the publish",
)
def reconcile_with_provider(
    job_id: uuid.UUID,
    body: VersionedRequest,
    identity: RequireAdmin,
    session: DbSession,
    request: Request,
    settings: SettingsDep,
) -> PublicationDetail:
    job = service.reconcile_with_provider(
        session,
        identity.organization.id,
        job_id,
        version=body.version,
        identity=identity,
        settings=settings,
    )
    session.commit()
    return _detail(session, identity, request, settings.api_v1_prefix, job)


@router.post(
    "/publications/{job_id}/reconcile",
    response_model=PublicationDetail,
    summary="Resolve NEEDS_RECONCILIATION (admin)",
)
def reconcile_publication(
    job_id: uuid.UUID,
    body: ReconcileRequest,
    identity: RequireAdmin,
    session: DbSession,
    request: Request,
    settings: SettingsDep,
) -> PublicationDetail:
    job = service.reconcile(session, identity.organization.id, job_id, body, identity=identity)
    session.commit()
    return _detail(session, identity, request, settings.api_v1_prefix, job)
