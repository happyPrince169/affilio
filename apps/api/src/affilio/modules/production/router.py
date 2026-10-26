"""Production HTTP routes (M2B)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from affilio.api.deps import CurrentOrganization, DbSession, RequireEditor, SettingsDep
from affilio.core.enums import EditingStatus, ProductionStatus
from affilio.modules.production import service
from affilio.modules.production.schemas import (
    JobMediaInput,
    JobProductInput,
    PaginatedJobs,
    ProductionJobCreate,
    ProductionJobDetail,
    ProductionJobUpdate,
    ProductionSortKey,
    ProductionStats,
    ProductionStatusChange,
)
from affilio.modules.production.service import JobFilters

router = APIRouter(prefix="/production", tags=["production"])


def _prefix(settings_prefix: str) -> str:
    return f"{settings_prefix}/media/assets"


@router.get("/stats", response_model=ProductionStats, summary="Production counters")
def read_stats(session: DbSession, organization: CurrentOrganization) -> ProductionStats:
    return service.stats(session, organization.id)


@router.get("/jobs", response_model=PaginatedJobs, summary="Production jobs")
def list_jobs(
    session: DbSession,
    organization: CurrentOrganization,
    q: Annotated[str | None, Query(max_length=120)] = None,
    status_filter: Annotated[ProductionStatus | None, Query(alias="status")] = None,
    editing_status: EditingStatus | None = None,
    product_id: uuid.UUID | None = None,
    media_asset_id: uuid.UUID | None = None,
    sort: ProductionSortKey = "planned",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=service.MAX_PAGE_SIZE)] = 50,
) -> PaginatedJobs:
    return service.list_jobs(
        session,
        organization.id,
        JobFilters(
            q=q,
            status=status_filter,
            editing_status=editing_status,
            product_id=product_id,
            media_asset_id=media_asset_id,
            sort=sort,
            page=page,
            page_size=page_size,
        ),
    )


@router.post(
    "/jobs",
    response_model=ProductionJobDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a job (editor)",
)
def create_job(
    body: ProductionJobCreate,
    session: DbSession,
    organization: CurrentOrganization,
    settings: SettingsDep,
    _editor: RequireEditor,
) -> ProductionJobDetail:
    job = service.create_job(session, organization.id, body)
    session.commit()
    return service.detail(
        service.get_job(session, organization.id, job.id),
        thumbnail_url_prefix=_prefix(settings.api_v1_prefix),
    )


@router.get("/jobs/{job_id}", response_model=ProductionJobDetail, summary="Job detail")
def read_job(
    job_id: uuid.UUID, session: DbSession, organization: CurrentOrganization, settings: SettingsDep
) -> ProductionJobDetail:
    return service.detail(
        service.get_job(session, organization.id, job_id),
        thumbnail_url_prefix=_prefix(settings.api_v1_prefix),
    )


@router.patch("/jobs/{job_id}", response_model=ProductionJobDetail, summary="Edit a job (editor)")
def update_job(
    job_id: uuid.UUID,
    body: ProductionJobUpdate,
    session: DbSession,
    organization: CurrentOrganization,
    settings: SettingsDep,
    _editor: RequireEditor,
) -> ProductionJobDetail:
    service.update_job(session, organization.id, job_id, body)
    session.commit()
    return service.detail(
        service.get_job(session, organization.id, job_id),
        thumbnail_url_prefix=_prefix(settings.api_v1_prefix),
    )


@router.post(
    "/jobs/{job_id}/status",
    response_model=ProductionJobDetail,
    summary="Change production / editing status (editor)",
)
def change_status(
    job_id: uuid.UUID,
    body: ProductionStatusChange,
    session: DbSession,
    organization: CurrentOrganization,
    settings: SettingsDep,
    _editor: RequireEditor,
) -> ProductionJobDetail:
    service.change_status(session, organization.id, job_id, body)
    session.commit()
    return service.detail(
        service.get_job(session, organization.id, job_id),
        thumbnail_url_prefix=_prefix(settings.api_v1_prefix),
    )


@router.post(
    "/jobs/{job_id}/products",
    response_model=ProductionJobDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Attach a product (editor)",
)
def add_product(
    job_id: uuid.UUID,
    body: JobProductInput,
    session: DbSession,
    organization: CurrentOrganization,
    settings: SettingsDep,
    _editor: RequireEditor,
) -> ProductionJobDetail:
    job = service.add_product(session, organization.id, job_id, body)
    session.commit()
    return service.detail(
        service.get_job(session, organization.id, job.id),
        thumbnail_url_prefix=_prefix(settings.api_v1_prefix),
    )


@router.delete(
    "/jobs/{job_id}/products/{product_id}",
    response_model=ProductionJobDetail,
    summary="Detach a product (editor)",
)
def remove_product(
    job_id: uuid.UUID,
    product_id: uuid.UUID,
    session: DbSession,
    organization: CurrentOrganization,
    settings: SettingsDep,
    _editor: RequireEditor,
) -> ProductionJobDetail:
    job = service.remove_product(session, organization.id, job_id, product_id)
    session.commit()
    return service.detail(
        service.get_job(session, organization.id, job.id),
        thumbnail_url_prefix=_prefix(settings.api_v1_prefix),
    )


@router.post(
    "/jobs/{job_id}/media",
    response_model=ProductionJobDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Attach a video (editor)",
)
def add_media(
    job_id: uuid.UUID,
    body: JobMediaInput,
    session: DbSession,
    organization: CurrentOrganization,
    settings: SettingsDep,
    _editor: RequireEditor,
) -> ProductionJobDetail:
    job = service.add_media(session, organization.id, job_id, body)
    session.commit()
    return service.detail(
        service.get_job(session, organization.id, job.id),
        thumbnail_url_prefix=_prefix(settings.api_v1_prefix),
    )


@router.delete(
    "/jobs/{job_id}/media/{media_asset_id}",
    response_model=ProductionJobDetail,
    summary="Detach a video (editor)",
)
def remove_media(
    job_id: uuid.UUID,
    media_asset_id: uuid.UUID,
    session: DbSession,
    organization: CurrentOrganization,
    settings: SettingsDep,
    _editor: RequireEditor,
) -> ProductionJobDetail:
    job = service.remove_media(session, organization.id, job_id, media_asset_id)
    session.commit()
    return service.detail(
        service.get_job(session, organization.id, job.id),
        thumbnail_url_prefix=_prefix(settings.api_v1_prefix),
    )
