"""Product services. Every function is organization-scoped; writes are audited."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from affilio.core.enums import Platform, ProductStatus
from affilio.core.errors import NotFoundError
from affilio.modules.media import service as media_service
from affilio.modules.media.models import MediaAsset
from affilio.modules.products.errors import (
    DuplicateMediaLinkError,
    DuplicateProductError,
    InvalidStatusTransitionError,
    WinnerConfirmationRequiredError,
)
from affilio.modules.products.lifecycle import (
    ALLOWED_TRANSITIONS,
    can_transition,
    requires_confirmation,
)
from affilio.modules.products.models import (
    Product,
    ProductImport,
    ProductMediaLink,
    ProductStatusEvent,
)
from affilio.modules.products.schemas import (
    LinkedProductResponse,
    PaginatedProducts,
    ProductCreate,
    ProductDetail,
    ProductMediaLinkCreate,
    ProductMediaLinkResponse,
    ProductMediaLinkUpdate,
    ProductSortKey,
    ProductStats,
    ProductStatusChange,
    ProductStatusEventResponse,
    ProductSummary,
    ProductUpdate,
)
from affilio.modules.products.urls import normalize_url

MAX_PAGE_SIZE = 100


@dataclass(slots=True)
class ProductFilters:
    q: str | None = None
    status: ProductStatus | None = None
    platform: Platform | None = None
    category: str | None = None
    has_media: bool | None = None
    sort: ProductSortKey = "newest"
    page: int = 1
    page_size: int = 25


# ------------------------------------------------------------------------- helpers


def _media_count_subquery():
    return (
        select(
            ProductMediaLink.product_id.label("product_id"),
            func.count(ProductMediaLink.id).label("media_count"),
        )
        .group_by(ProductMediaLink.product_id)
        .subquery()
    )


def _summary(product: Product, media_count: int) -> ProductSummary:
    return ProductSummary(
        id=product.id,
        name=product.name,
        platform=product.platform,
        status=product.status,
        source=product.source,
        source_ref=product.source_ref,
        product_url=product.product_url,
        affiliate_url=product.affiliate_url,
        platform_product_id=product.platform_product_id,
        seller_name=product.seller_name,
        category=product.category,
        images=list(product.images or []),
        currency=product.currency,
        observed_price=product.observed_price,
        price_observed_at=product.price_observed_at,
        estimated_commission_rate=product.estimated_commission_rate,
        estimated_commission_amount=product.estimated_commission_amount,
        media_count=media_count,
        status_changed_at=product.status_changed_at,
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


def _check_duplicates(
    session: Session, organization_id: uuid.UUID, product: Product, *, exclude_id: uuid.UUID | None
) -> None:
    """Application-level duplicate check (clear error before the partial unique index fires)."""
    conditions = []
    if product.platform_product_id:
        conditions.append(
            (Product.platform == product.platform)
            & (Product.platform_product_id == product.platform_product_id)
        )
    if product.product_url_normalized:
        conditions.append(Product.product_url_normalized == product.product_url_normalized)
    if not conditions:
        return
    stmt = select(Product.id, Product.name).where(
        Product.organization_id == organization_id, or_(*conditions)
    )
    if exclude_id is not None:
        stmt = stmt.where(Product.id != exclude_id)
    existing = session.execute(stmt).first()
    if existing is not None:
        raise DuplicateProductError(
            "A product with the same platform id or URL already exists",
            details={"product_id": str(existing[0]), "name": existing[1]},
        )


def _get(session: Session, organization_id: uuid.UUID, product_id: uuid.UUID) -> Product:
    product = session.scalar(
        select(Product)
        .options(selectinload(Product.status_events))
        .where(Product.organization_id == organization_id, Product.id == product_id)
    )
    if product is None:
        raise NotFoundError("Product not found")
    return product


def _media_count(session: Session, product_id: uuid.UUID) -> int:
    return int(
        session.scalar(
            select(func.count(ProductMediaLink.id)).where(ProductMediaLink.product_id == product_id)
        )
        or 0
    )


# ------------------------------------------------------------------------- CRUD


def create_product(
    session: Session, organization_id: uuid.UUID, data: ProductCreate, *, actor: str = "api"
) -> Product:
    product = Product(
        id=uuid.uuid4(),
        organization_id=organization_id,
        **data.model_dump(exclude={"status"}),
        status=data.status,
        status_changed_at=datetime.now(UTC),
    )
    product.product_url_normalized = (
        normalize_url(product.product_url) if product.product_url else None
    )
    _check_duplicates(session, organization_id, product, exclude_id=None)
    session.add(product)
    session.add(
        ProductStatusEvent(
            product=product, from_status=None, to_status=data.status, actor=actor, note="created"
        )
    )
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise DuplicateProductError(
            "A product with the same platform id or URL already exists"
        ) from exc
    return product


def update_product(
    session: Session, organization_id: uuid.UUID, product_id: uuid.UUID, data: ProductUpdate
) -> Product:
    product = _get(session, organization_id, product_id)
    changes = data.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(product, key, value)
    if "product_url" in changes:
        product.product_url_normalized = (
            normalize_url(product.product_url) if product.product_url else None
        )
    _check_duplicates(session, organization_id, product, exclude_id=product.id)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise DuplicateProductError(
            "A product with the same platform id or URL already exists"
        ) from exc
    return product


def get_product(session: Session, organization_id: uuid.UUID, product_id: uuid.UUID) -> Product:
    return _get(session, organization_id, product_id)


def product_detail(session: Session, product: Product) -> ProductDetail:
    summary = _summary(product, _media_count(session, product.id))
    return ProductDetail(
        **summary.model_dump(),
        research_notes=product.research_notes,
        status_events=[
            ProductStatusEventResponse.model_validate(event) for event in product.status_events
        ],
        allowed_transitions=sorted(ALLOWED_TRANSITIONS.get(product.status, frozenset())),
    )


def change_status(
    session: Session,
    organization_id: uuid.UUID,
    product_id: uuid.UUID,
    data: ProductStatusChange,
    *,
    actor: str = "api",
) -> Product:
    product = _get(session, organization_id, product_id)
    if data.status == product.status:
        raise InvalidStatusTransitionError(
            f"Product is already {product.status}", details={"status": product.status}
        )
    if not can_transition(product.status, data.status):
        raise InvalidStatusTransitionError(
            f"Cannot move from {product.status} to {data.status}",
            details={
                "from": product.status,
                "to": data.status,
                "allowed": sorted(ALLOWED_TRANSITIONS.get(product.status, frozenset())),
            },
        )
    if requires_confirmation(data.status) and (not data.confirmed or not data.note):
        raise WinnerConfirmationRequiredError(
            "Marking a product as WINNER requires confirmed=true and a note describing the evidence"
        )
    event = ProductStatusEvent(
        product=product,
        from_status=product.status,
        to_status=data.status,
        note=data.note,
        actor=actor,
        confirmed=data.confirmed,
    )
    product.status = data.status
    product.status_changed_at = datetime.now(UTC)
    session.add(event)
    session.flush()
    session.refresh(product, attribute_names=["status_events"])
    return product


# ------------------------------------------------------------------------- queries


def _apply_filters(stmt: Select, filters: ProductFilters) -> Select:
    if filters.q:
        pattern = f"%{filters.q.strip()}%"
        stmt = stmt.where(
            or_(
                Product.name.ilike(pattern),
                Product.seller_name.ilike(pattern),
                Product.platform_product_id.ilike(pattern),
                Product.category.ilike(pattern),
            )
        )
    if filters.status:
        stmt = stmt.where(Product.status == filters.status)
    if filters.platform:
        stmt = stmt.where(Product.platform == filters.platform)
    if filters.category:
        stmt = stmt.where(Product.category.ilike(filters.category.strip()))
    return stmt


def list_products(
    session: Session, organization_id: uuid.UUID, filters: ProductFilters
) -> PaginatedProducts:
    page = max(1, filters.page)
    page_size = min(max(1, filters.page_size), MAX_PAGE_SIZE)
    counts = _media_count_subquery()
    media_count = func.coalesce(counts.c.media_count, 0)

    base = (
        select(Product, media_count.label("media_count"))
        .outerjoin(counts, counts.c.product_id == Product.id)
        .where(Product.organization_id == organization_id)
    )
    base = _apply_filters(base, filters)
    if filters.has_media is True:
        base = base.where(media_count > 0)
    elif filters.has_media is False:
        base = base.where(media_count == 0)

    total = int(
        session.scalar(select(func.count()).select_from(base.order_by(None).subquery())) or 0
    )
    order = {
        "newest": [Product.created_at.desc(), Product.id.desc()],
        "oldest": [Product.created_at.asc(), Product.id.asc()],
        "name": [Product.name.asc(), Product.id.asc()],
        "updated": [Product.updated_at.desc(), Product.id.desc()],
        "price": [Product.observed_price.desc().nulls_last(), Product.id.desc()],
        "commission": [Product.estimated_commission_amount.desc().nulls_last(), Product.id.desc()],
    }[filters.sort]
    rows = session.execute(
        base.order_by(*order).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return PaginatedProducts(
        items=[_summary(product, int(count)) for product, count in rows],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=max(1, math.ceil(total / page_size)) if total else 0,
    )


def product_stats(session: Session, organization_id: uuid.UUID) -> ProductStats:
    by_status_rows = session.execute(
        select(Product.status, func.count(Product.id))
        .where(Product.organization_id == organization_id)
        .group_by(Product.status)
    ).all()
    by_status = {status.value: 0 for status in ProductStatus}
    for status, count in by_status_rows:
        by_status[str(status)] = int(count)
    with_media = int(
        session.scalar(
            select(func.count(func.distinct(ProductMediaLink.product_id))).where(
                ProductMediaLink.organization_id == organization_id
            )
        )
        or 0
    )
    with_affiliate = int(
        session.scalar(
            select(func.count(Product.id)).where(
                Product.organization_id == organization_id, Product.affiliate_url.is_not(None)
            )
        )
        or 0
    )
    with_commission = int(
        session.scalar(
            select(func.count(Product.id)).where(
                Product.organization_id == organization_id,
                or_(
                    Product.estimated_commission_rate.is_not(None),
                    Product.estimated_commission_amount.is_not(None),
                ),
            )
        )
        or 0
    )
    return ProductStats(
        total=sum(by_status.values()),
        by_status=by_status,
        with_media=with_media,
        with_affiliate_url=with_affiliate,
        with_estimated_commission=with_commission,
    )


# ------------------------------------------------------------------------- media links


def _link_response(
    link: ProductMediaLink, *, thumbnail_url_prefix: str
) -> ProductMediaLinkResponse:
    return ProductMediaLinkResponse(
        id=link.id,
        product_id=link.product_id,
        media_asset_id=link.media_asset_id,
        is_primary=link.is_primary,
        notes=link.notes,
        created_at=link.created_at,
        asset=media_service.asset_summary(
            link.media_asset, thumbnail_url_prefix=thumbnail_url_prefix
        ),
    )


def _links_query(product_id: uuid.UUID) -> Select:
    return (
        select(ProductMediaLink)
        .options(selectinload(ProductMediaLink.media_asset).selectinload(MediaAsset.variants))
        .where(ProductMediaLink.product_id == product_id)
        .order_by(ProductMediaLink.is_primary.desc(), ProductMediaLink.created_at.desc())
    )


def list_media_links(
    session: Session,
    organization_id: uuid.UUID,
    product_id: uuid.UUID,
    *,
    thumbnail_url_prefix: str,
) -> list[ProductMediaLinkResponse]:
    _get(session, organization_id, product_id)
    return [
        _link_response(link, thumbnail_url_prefix=thumbnail_url_prefix)
        for link in session.scalars(_links_query(product_id))
    ]


def _clear_primary(
    session: Session, media_asset_id: uuid.UUID, *, except_link: uuid.UUID | None
) -> None:
    for other in session.scalars(
        select(ProductMediaLink).where(
            ProductMediaLink.media_asset_id == media_asset_id, ProductMediaLink.is_primary.is_(True)
        )
    ):
        if other.id != except_link:
            other.is_primary = False
    session.flush()


def link_media(
    session: Session,
    organization_id: uuid.UUID,
    product_id: uuid.UUID,
    data: ProductMediaLinkCreate,
    *,
    thumbnail_url_prefix: str,
) -> ProductMediaLinkResponse:
    product = _get(session, organization_id, product_id)
    # Organization isolation: the asset must belong to the same organization (404 otherwise).
    asset = media_service.get_asset(session, organization_id, data.media_asset_id)
    existing = session.scalar(
        select(ProductMediaLink).where(
            ProductMediaLink.product_id == product.id,
            ProductMediaLink.media_asset_id == asset.id,
        )
    )
    if existing is not None:
        raise DuplicateMediaLinkError(
            "This video is already linked to the product", details={"link_id": str(existing.id)}
        )
    link = ProductMediaLink(
        id=uuid.uuid4(),
        organization_id=organization_id,
        product_id=product.id,
        media_asset_id=asset.id,
        is_primary=data.is_primary,
        notes=(data.notes or "").strip() or None,
    )
    if data.is_primary:
        _clear_primary(session, asset.id, except_link=link.id)
    session.add(link)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise DuplicateMediaLinkError("This video is already linked to the product") from exc
    link = session.scalar(_links_query(product.id).where(ProductMediaLink.id == link.id))
    assert link is not None  # noqa: S101 - just inserted
    return _link_response(link, thumbnail_url_prefix=thumbnail_url_prefix)


def update_media_link(
    session: Session,
    organization_id: uuid.UUID,
    product_id: uuid.UUID,
    media_asset_id: uuid.UUID,
    data: ProductMediaLinkUpdate,
    *,
    thumbnail_url_prefix: str,
) -> ProductMediaLinkResponse:
    _get(session, organization_id, product_id)
    link = session.scalar(
        _links_query(product_id).where(ProductMediaLink.media_asset_id == media_asset_id)
    )
    if link is None:
        raise NotFoundError("Media link not found")
    changes = data.model_dump(exclude_unset=True)
    if "notes" in changes:
        link.notes = (changes["notes"] or "").strip() or None
    if changes.get("is_primary") is True:
        _clear_primary(session, media_asset_id, except_link=link.id)
        link.is_primary = True
    elif changes.get("is_primary") is False:
        link.is_primary = False
    session.flush()
    return _link_response(link, thumbnail_url_prefix=thumbnail_url_prefix)


def unlink_media(
    session: Session, organization_id: uuid.UUID, product_id: uuid.UUID, media_asset_id: uuid.UUID
) -> None:
    _get(session, organization_id, product_id)
    link = session.scalar(
        select(ProductMediaLink).where(
            ProductMediaLink.product_id == product_id,
            ProductMediaLink.media_asset_id == media_asset_id,
        )
    )
    if link is None:
        raise NotFoundError("Media link not found")
    session.delete(link)
    session.flush()


def products_for_media(
    session: Session, organization_id: uuid.UUID, media_asset_id: uuid.UUID
) -> list[LinkedProductResponse]:
    media_service.get_asset(session, organization_id, media_asset_id)
    links = session.scalars(
        select(ProductMediaLink)
        .options(selectinload(ProductMediaLink.product))
        .where(
            ProductMediaLink.organization_id == organization_id,
            ProductMediaLink.media_asset_id == media_asset_id,
        )
        .order_by(ProductMediaLink.is_primary.desc(), ProductMediaLink.created_at.desc())
    )
    return [
        LinkedProductResponse(
            link_id=link.id,
            is_primary=link.is_primary,
            notes=link.notes,
            created_at=link.created_at,
            product=_summary(link.product, _media_count(session, link.product_id)),
        )
        for link in links
    ]


# ------------------------------------------------------------------------- imports


def list_imports(
    session: Session, organization_id: uuid.UUID, limit: int = 20
) -> list[ProductImport]:
    return list(
        session.scalars(
            select(ProductImport)
            .where(ProductImport.organization_id == organization_id)
            .order_by(ProductImport.created_at.desc())
            .limit(min(max(limit, 1), 100))
        )
    )
