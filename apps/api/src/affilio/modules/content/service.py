"""Affiliate link library services."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from affilio.core.enums import AffiliateLinkStatus, Platform
from affilio.core.errors import ConflictError, NotFoundError
from affilio.modules.content.models import AffiliateLink
from affilio.modules.content.schemas import (
    AffiliateLinkCreate,
    AffiliateLinkResponse,
    AffiliateLinkUpdate,
)
from affilio.modules.products import service as products_service
from affilio.modules.products.urls import normalize_url


def to_response(link: AffiliateLink) -> AffiliateLinkResponse:
    return AffiliateLinkResponse(
        id=link.id,
        platform=link.platform,
        product_id=link.product_id,
        product_name=link.product.name if link.product else None,
        label=link.label,
        url=link.url,
        source=link.source,
        status=link.status,
        notes=link.notes,
        created_by=link.created_by,
        created_at=link.created_at,
        updated_at=link.updated_at,
    )


def _query():
    return select(AffiliateLink).options(selectinload(AffiliateLink.product))


def list_links(
    session: Session,
    organization_id: uuid.UUID,
    *,
    status: AffiliateLinkStatus | None = AffiliateLinkStatus.ACTIVE,
    platform: Platform | None = None,
    product_id: uuid.UUID | None = None,
    q: str | None = None,
) -> list[AffiliateLink]:
    stmt = _query().where(AffiliateLink.organization_id == organization_id)
    if status:
        stmt = stmt.where(AffiliateLink.status == status)
    if platform:
        stmt = stmt.where(AffiliateLink.platform == platform)
    if product_id:
        stmt = stmt.where(AffiliateLink.product_id == product_id)
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(AffiliateLink.label.ilike(pattern) | AffiliateLink.url.ilike(pattern))
    return list(session.scalars(stmt.order_by(AffiliateLink.label)))


def get_link(session: Session, organization_id: uuid.UUID, link_id: uuid.UUID) -> AffiliateLink:
    link = session.scalar(
        _query().where(
            AffiliateLink.organization_id == organization_id, AffiliateLink.id == link_id
        )
    )
    if link is None:
        raise NotFoundError("Affiliate link not found")
    return link


def get_links(
    session: Session, organization_id: uuid.UUID, ids: list[uuid.UUID]
) -> list[AffiliateLink]:
    """Fetch several links preserving the requested order; 404 if any is missing/foreign."""
    found = {
        link.id: link
        for link in session.scalars(
            _query().where(
                AffiliateLink.organization_id == organization_id, AffiliateLink.id.in_(ids)
            )
        )
    }
    missing = [str(i) for i in ids if i not in found]
    if missing:
        raise NotFoundError("Affiliate link not found", details={"missing": missing})
    return [found[i] for i in ids]


def create_link(
    session: Session, organization_id: uuid.UUID, data: AffiliateLinkCreate, *, actor: str
) -> AffiliateLink:
    if data.product_id is not None:
        products_service.get_product(session, organization_id, data.product_id)
    link = AffiliateLink(
        id=uuid.uuid4(),
        organization_id=organization_id,
        platform=data.platform,
        product_id=data.product_id,
        label=data.label,
        url=data.url,
        url_normalized=normalize_url(data.url),
        notes=(data.notes or "").strip() or None,
        created_by=actor,
    )
    session.add(link)
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError as exc:
        raise ConflictError(
            "This URL is already in the library", details={"url": data.url}
        ) from exc
    return get_link(session, organization_id, link.id)


def update_link(
    session: Session, organization_id: uuid.UUID, link_id: uuid.UUID, data: AffiliateLinkUpdate
) -> AffiliateLink:
    link = get_link(session, organization_id, link_id)
    changes = data.model_dump(exclude_unset=True)
    if "product_id" in changes and changes["product_id"] is not None:
        products_service.get_product(session, organization_id, changes["product_id"])
    for key, value in changes.items():
        setattr(link, key, value)
    if "url" in changes:
        link.url_normalized = normalize_url(link.url)
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError as exc:
        raise ConflictError("This URL is already in the library") from exc
    return link
