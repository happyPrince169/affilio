"""Production services: jobs, status machines, optional product and media associations."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from affilio.core.enums import EditingStatus, ProductionMediaRole, ProductionStatus
from affilio.core.errors import NotFoundError
from affilio.modules.media import service as media_service
from affilio.modules.media.models import MediaAsset
from affilio.modules.procurement import service as procurement_service
from affilio.modules.procurement.models import PurchaseOrderItem
from affilio.modules.procurement.schemas import ProductRef
from affilio.modules.production.errors import (
    DuplicateJobAssociationError,
    InvalidProductionTransitionError,
)
from affilio.modules.production.lifecycle import EDITING_TRANSITIONS, PRODUCTION_TRANSITIONS
from affilio.modules.production.models import (
    ProductionJob,
    ProductionJobMedia,
    ProductionJobProduct,
)
from affilio.modules.production.schemas import (
    JobMediaInput,
    JobMediaResponse,
    JobProductInput,
    JobProductResponse,
    PaginatedJobs,
    ProductionJobCreate,
    ProductionJobDetail,
    ProductionJobSummary,
    ProductionJobUpdate,
    ProductionSortKey,
    ProductionStats,
    ProductionStatusChange,
)
from affilio.modules.products import service as products_service

MAX_PAGE_SIZE = 100


def _query():
    return select(ProductionJob).options(
        selectinload(ProductionJob.products).selectinload(ProductionJobProduct.product),
        selectinload(ProductionJob.products)
        .selectinload(ProductionJobProduct.purchase_order_item)
        .selectinload(PurchaseOrderItem.order),
        selectinload(ProductionJob.media)
        .selectinload(ProductionJobMedia.media_asset)
        .selectinload(MediaAsset.variants),
    )


def get_job(session: Session, organization_id: uuid.UUID, job_id: uuid.UUID) -> ProductionJob:
    job = session.scalar(
        _query().where(ProductionJob.organization_id == organization_id, ProductionJob.id == job_id)
    )
    if job is None:
        raise NotFoundError("Production job not found")
    return job


def _next_job_number(session: Session, organization_id: uuid.UUID) -> str:
    year = datetime.now(UTC).year
    prefix = f"PJ-{year}-"
    count = session.scalar(
        select(func.count(ProductionJob.id)).where(
            ProductionJob.organization_id == organization_id,
            ProductionJob.job_number.like(f"{prefix}%"),
        )
    )
    return f"{prefix}{int(count or 0) + 1:04d}"


def _summary(job: ProductionJob) -> ProductionJobSummary:
    return ProductionJobSummary(
        id=job.id,
        job_number=job.job_number,
        title=job.title,
        status=job.status,
        editing_status=job.editing_status,
        planned_filming_date=job.planned_filming_date,
        deadline=job.deadline,
        staff_labels=list(job.staff_labels or []),
        product_count=len(job.products),
        media_count=len(job.media),
        final_count=sum(1 for m in job.media if m.role == ProductionMediaRole.FINAL),
        status_changed_at=job.status_changed_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def detail(job: ProductionJob, *, thumbnail_url_prefix: str) -> ProductionJobDetail:
    return ProductionJobDetail(
        **_summary(job).model_dump(),
        description=job.description,
        notes=job.notes,
        products=[
            JobProductResponse(
                id=p.id,
                product=ProductRef(
                    id=p.product.id,
                    name=p.product.name,
                    status=p.product.status,
                    platform=p.product.platform,
                    product_url=p.product.product_url,
                ),
                purchase_order_item_id=p.purchase_order_item_id,
                order_number=(
                    p.purchase_order_item.order.order_number
                    if p.purchase_order_item and p.purchase_order_item.order
                    else None
                ),
                quantity_used=p.quantity_used,
                notes=p.notes,
                created_at=p.created_at,
            )
            for p in job.products
        ],
        media=[
            JobMediaResponse(
                id=m.id,
                role=m.role,
                notes=m.notes,
                created_at=m.created_at,
                asset=media_service.asset_summary(
                    m.media_asset, thumbnail_url_prefix=thumbnail_url_prefix
                ),
            )
            for m in job.media
        ],
        allowed_transitions=sorted(PRODUCTION_TRANSITIONS[job.status]),
        allowed_editing_transitions=sorted(EDITING_TRANSITIONS[job.editing_status]),
    )


def create_job(
    session: Session, organization_id: uuid.UUID, data: ProductionJobCreate
) -> ProductionJob:
    job = ProductionJob(
        id=uuid.uuid4(),
        organization_id=organization_id,
        job_number=_next_job_number(session, organization_id),
        title=data.title,
        description=(data.description or "").strip() or None,
        planned_filming_date=data.planned_filming_date,
        deadline=data.deadline,
        staff_labels=data.staff_labels,
        notes=(data.notes or "").strip() or None,
        status_changed_at=datetime.now(UTC),
    )
    session.add(job)
    for product_id in dict.fromkeys(data.product_ids):
        product = products_service.get_product(session, organization_id, product_id)
        session.add(ProductionJobProduct(id=uuid.uuid4(), job=job, product_id=product.id))
    session.flush()
    return get_job(session, organization_id, job.id)


def update_job(
    session: Session, organization_id: uuid.UUID, job_id: uuid.UUID, data: ProductionJobUpdate
) -> ProductionJob:
    job = get_job(session, organization_id, job_id)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(job, key, value)
    session.flush()
    return job


def change_status(
    session: Session, organization_id: uuid.UUID, job_id: uuid.UUID, data: ProductionStatusChange
) -> ProductionJob:
    job = get_job(session, organization_id, job_id)
    if data.status is None and data.editing_status is None:
        raise InvalidProductionTransitionError("Provide status and/or editing_status")
    now = datetime.now(UTC)
    if data.status is not None:
        if data.status not in PRODUCTION_TRANSITIONS[job.status]:
            raise InvalidProductionTransitionError(
                f"Cannot move job from {job.status} to {data.status}",
                details={
                    "from": job.status,
                    "to": data.status,
                    "allowed": sorted(PRODUCTION_TRANSITIONS[job.status]),
                },
            )
        job.status = data.status
        job.status_changed_at = now
    if data.editing_status is not None:
        if data.editing_status not in EDITING_TRANSITIONS[job.editing_status]:
            raise InvalidProductionTransitionError(
                f"Cannot move editing from {job.editing_status} to {data.editing_status}",
                details={
                    "from": job.editing_status,
                    "to": data.editing_status,
                    "allowed": sorted(EDITING_TRANSITIONS[job.editing_status]),
                },
            )
        if (
            job.status != ProductionStatus.FILMED
            and data.editing_status != EditingStatus.NOT_STARTED
        ):
            raise InvalidProductionTransitionError(
                "Editing can only progress once the job is filmed", details={"status": job.status}
            )
        job.editing_status = data.editing_status
    if data.note:
        job.notes = f"{job.notes}\n{data.note}".strip() if job.notes else data.note
    session.flush()
    return job


# ------------------------------------------------------------------ associations (optional)


def add_product(
    session: Session, organization_id: uuid.UUID, job_id: uuid.UUID, data: JobProductInput
) -> ProductionJob:
    job = get_job(session, organization_id, job_id)
    product = products_service.get_product(session, organization_id, data.product_id)
    if any(p.product_id == product.id for p in job.products):
        raise DuplicateJobAssociationError("Product already attached to this job")
    item_id = None
    if data.purchase_order_item_id is not None:
        item = session.get(PurchaseOrderItem, data.purchase_order_item_id)
        if item is None or item.product_id != product.id:
            raise NotFoundError("Purchase order item not found for this product")
        procurement_service.get_order(session, organization_id, item.order_id)  # org check
        item_id = item.id
    session.add(
        ProductionJobProduct(
            id=uuid.uuid4(),
            job=job,
            product_id=product.id,
            purchase_order_item_id=item_id,
            quantity_used=data.quantity_used,
            notes=(data.notes or "").strip() or None,
        )
    )
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise DuplicateJobAssociationError("Product already attached to this job") from exc
    return get_job(session, organization_id, job.id)


def remove_product(
    session: Session, organization_id: uuid.UUID, job_id: uuid.UUID, product_id: uuid.UUID
) -> ProductionJob:
    job = get_job(session, organization_id, job_id)
    link = next((p for p in job.products if p.product_id == product_id), None)
    if link is None:
        raise NotFoundError("Product is not attached to this job")
    job.products.remove(link)
    session.flush()
    return get_job(session, organization_id, job.id)


def add_media(
    session: Session, organization_id: uuid.UUID, job_id: uuid.UUID, data: JobMediaInput
) -> ProductionJob:
    job = get_job(session, organization_id, job_id)
    asset = media_service.get_asset(session, organization_id, data.media_asset_id)  # 404 if foreign
    if any(m.media_asset_id == asset.id for m in job.media):
        raise DuplicateJobAssociationError("Video already attached to this job")
    session.add(
        ProductionJobMedia(
            id=uuid.uuid4(),
            job=job,
            media_asset_id=asset.id,
            role=data.role,
            notes=(data.notes or "").strip() or None,
        )
    )
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise DuplicateJobAssociationError("Video already attached to this job") from exc
    return get_job(session, organization_id, job.id)


def remove_media(
    session: Session, organization_id: uuid.UUID, job_id: uuid.UUID, media_asset_id: uuid.UUID
) -> ProductionJob:
    job = get_job(session, organization_id, job_id)
    link = next((m for m in job.media if m.media_asset_id == media_asset_id), None)
    if link is None:
        raise NotFoundError("Video is not attached to this job")
    job.media.remove(link)
    session.flush()
    return get_job(session, organization_id, job.id)


# ------------------------------------------------------------------ queries


@dataclass(slots=True)
class JobFilters:
    q: str | None = None
    status: ProductionStatus | None = None
    editing_status: EditingStatus | None = None
    product_id: uuid.UUID | None = None
    media_asset_id: uuid.UUID | None = None
    sort: ProductionSortKey = "planned"
    page: int = 1
    page_size: int = 50


def list_jobs(session: Session, organization_id: uuid.UUID, filters: JobFilters) -> PaginatedJobs:
    page = max(1, filters.page)
    page_size = min(max(1, filters.page_size), MAX_PAGE_SIZE)
    base = _query().where(ProductionJob.organization_id == organization_id)
    if filters.q:
        pattern = f"%{filters.q.strip()}%"
        base = base.where(
            ProductionJob.title.ilike(pattern) | ProductionJob.job_number.ilike(pattern)
        )
    if filters.status:
        base = base.where(ProductionJob.status == filters.status)
    if filters.editing_status:
        base = base.where(ProductionJob.editing_status == filters.editing_status)
    if filters.product_id:
        base = base.where(
            ProductionJob.id.in_(
                select(ProductionJobProduct.job_id).where(
                    ProductionJobProduct.product_id == filters.product_id
                )
            )
        )
    if filters.media_asset_id:
        base = base.where(
            ProductionJob.id.in_(
                select(ProductionJobMedia.job_id).where(
                    ProductionJobMedia.media_asset_id == filters.media_asset_id
                )
            )
        )
    total = int(
        session.scalar(select(func.count()).select_from(base.order_by(None).subquery())) or 0
    )
    order = {
        "planned": [
            ProductionJob.planned_filming_date.asc().nulls_last(),
            ProductionJob.created_at.desc(),
        ],
        "deadline": [ProductionJob.deadline.asc().nulls_last(), ProductionJob.created_at.desc()],
        "newest": [ProductionJob.created_at.desc()],
        "updated": [ProductionJob.updated_at.desc()],
    }[filters.sort]
    rows = session.scalars(
        base.order_by(*order).offset((page - 1) * page_size).limit(page_size)
    ).unique()
    return PaginatedJobs(
        items=[_summary(j) for j in rows],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=max(1, math.ceil(total / page_size)) if total else 0,
    )


def stats(session: Session, organization_id: uuid.UUID) -> ProductionStats:
    rows = session.execute(
        select(ProductionJob.status, func.count(ProductionJob.id))
        .where(ProductionJob.organization_id == organization_id)
        .group_by(ProductionJob.status)
    ).all()
    editing_rows = session.execute(
        select(ProductionJob.editing_status, func.count(ProductionJob.id))
        .where(ProductionJob.organization_id == organization_id)
        .group_by(ProductionJob.editing_status)
    ).all()
    today = date.today()  # noqa: DTZ011 - calendar dates are timezone-free by design
    upcoming = int(
        session.scalar(
            select(func.count(ProductionJob.id)).where(
                ProductionJob.organization_id == organization_id,
                ProductionJob.status.in_([ProductionStatus.PLANNED, ProductionStatus.SCHEDULED]),
                ProductionJob.planned_filming_date.is_not(None),
                ProductionJob.planned_filming_date >= today,
                ProductionJob.planned_filming_date <= today + timedelta(days=7),
            )
        )
        or 0
    )
    overdue = int(
        session.scalar(
            select(func.count(ProductionJob.id)).where(
                ProductionJob.organization_id == organization_id,
                ProductionJob.deadline.is_not(None),
                ProductionJob.deadline < today,
                ProductionJob.editing_status != EditingStatus.DONE,
                ProductionJob.status != ProductionStatus.CANCELLED,
            )
        )
        or 0
    )
    by_status = {s.value: 0 for s in ProductionStatus}
    for status, count in rows:
        by_status[str(status)] = int(count)
    by_editing = {s.value: 0 for s in EditingStatus}
    for status, count in editing_rows:
        by_editing[str(status)] = int(count)
    return ProductionStats(
        by_status=by_status, by_editing_status=by_editing, upcoming=upcoming, overdue=overdue
    )
