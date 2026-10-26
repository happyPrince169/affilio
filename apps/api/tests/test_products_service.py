"""Product catalog services. Requires TEST_DATABASE_URL."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from affilio.core.enums import ProductStatus, ScanTrigger
from affilio.core.errors import NotFoundError
from affilio.modules.media.models import MediaAsset
from affilio.modules.media.scanner import MediaScanner
from affilio.modules.media.service import enqueue_scan
from affilio.modules.products import service
from affilio.modules.products.errors import (
    DuplicateMediaLinkError,
    DuplicateProductError,
    InvalidProductUrlError,
    InvalidStatusTransitionError,
    WinnerConfirmationRequiredError,
)
from affilio.modules.products.models import Product, ProductMediaLink
from affilio.modules.products.schemas import (
    ProductCreate,
    ProductMediaLinkCreate,
    ProductMediaLinkUpdate,
    ProductStatusChange,
    ProductUpdate,
)
from affilio.modules.products.urls import normalize_url, validate_http_url
from tests.media_helpers import FakeProber, build_settings, create_organization, write_bytes

pytestmark = pytest.mark.database
THUMB = "/api/v1/media/assets"


def _create(session: Session, org_id: uuid.UUID, **overrides) -> Product:
    payload = {"name": "Mini blender", "product_url": "https://shopee.vn/product/123/456"}
    payload.update(overrides)
    return service.create_product(session, org_id, ProductCreate(**payload))


def _assets(session: Session, organization, media_dirs, count: int = 2) -> list[MediaAsset]:
    for index in range(count):
        write_bytes(media_dirs.originals / f"clip_{index}.mp4", 3_000 + index, seed=index)
    job = enqueue_scan(session, organization.id, media_dirs.registry, trigger=ScanTrigger.CLI)
    MediaScanner(session, media_dirs.registry, build_settings(media_dirs), prober=FakeProber()).run(
        job
    )
    session.flush()
    return list(
        session.scalars(
            select(MediaAsset)
            .where(MediaAsset.organization_id == organization.id)
            .order_by(MediaAsset.title)
        )
    )


# ----------------------------------------------------------------------------- URLs


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "data:text/html,hi",
        "file:///etc/passwd",
        "ftp://shopee.vn/x",
        "shopee.vn/no-scheme",
        "https://",
        "https://user:pw@shopee.vn/x",
        "https://shopee.vn/a b",
        "https://shopee.vn/x\ny",
    ],
)
def test_unsafe_urls_rejected(value: str) -> None:
    with pytest.raises(InvalidProductUrlError):
        validate_http_url(value)


def test_url_normalization_for_duplicates() -> None:
    assert normalize_url("HTTPS://Shopee.VN/Product/1#frag") == "https://shopee.vn/Product/1"
    assert normalize_url("https://shopee.vn/p/1/") == "https://shopee.vn/p/1"
    assert normalize_url("https://shopee.vn/p?a=1") == "https://shopee.vn/p?a=1"


def test_schema_rejects_bad_urls_and_winner_on_create() -> None:
    with pytest.raises(InvalidProductUrlError):
        ProductCreate(name="x", affiliate_url="javascript:void(0)")
    with pytest.raises(ValueError, match="WINNER"):
        ProductCreate(name="x", status=ProductStatus.WINNER)
    with pytest.raises(ValueError):
        ProductCreate(name="x", estimated_commission_rate=Decimal("1.5"))  # > 100 %


# ----------------------------------------------------------------------------- CRUD


def test_create_read_update(db_session: Session, organization) -> None:
    product = _create(
        db_session,
        organization.id,
        affiliate_url="https://s.shopee.vn/abc",
        platform_product_id="123.456",
        seller_name=" Kitchen Shop ",
        category="kitchen",
        images=["https://cf.shopee.vn/a.jpg", "https://cf.shopee.vn/a.jpg"],
        currency="vnd",
        observed_price="199000.00",
        estimated_commission_rate="0.05",
    )
    db_session.commit()

    detail = service.product_detail(
        db_session, service.get_product(db_session, organization.id, product.id)
    )
    assert detail.status == ProductStatus.DISCOVERED
    assert detail.seller_name == "Kitchen Shop"
    assert detail.currency == "VND"
    assert detail.images == ["https://cf.shopee.vn/a.jpg"]  # de-duplicated
    assert detail.observed_price == Decimal("199000.00")
    assert detail.estimated_commission_rate == Decimal("0.0500")
    assert detail.estimated_commission_amount is None  # never fabricated
    assert detail.media_count == 0
    assert [e.to_status for e in detail.status_events] == [ProductStatus.DISCOVERED]
    assert set(detail.allowed_transitions) == {ProductStatus.SHORTLISTED, ProductStatus.ARCHIVED}

    updated = service.update_product(
        db_session,
        organization.id,
        product.id,
        ProductUpdate(research_notes="Good margins", observed_price=None),
    )
    assert updated.research_notes == "Good margins"
    assert updated.observed_price is None
    assert updated.seller_name == "Kitchen Shop"  # untouched by partial update


def test_duplicates_by_platform_id_and_url(db_session: Session, organization) -> None:
    _create(db_session, organization.id, platform_product_id="A-1")
    with pytest.raises(DuplicateProductError):
        _create(
            db_session, organization.id, name="Other", product_url=None, platform_product_id="A-1"
        )
    with pytest.raises(DuplicateProductError):
        _create(
            db_session,
            organization.id,
            name="Other",
            product_url="HTTPS://SHOPEE.VN/product/123/456/",
        )
    # Same URL in another organization is fine (organization isolation).
    other_org = create_organization(db_session)
    _create(db_session, other_org.id)
    # Products without identifiers never collide.
    _create(db_session, organization.id, name="Unknown A", product_url=None)
    _create(db_session, organization.id, name="Unknown B", product_url=None)


def test_update_cannot_create_duplicate(db_session: Session, organization) -> None:
    _create(db_session, organization.id, platform_product_id="P1")
    second = _create(
        db_session, organization.id, name="Two", product_url=None, platform_product_id="P2"
    )
    with pytest.raises(DuplicateProductError):
        service.update_product(
            db_session, organization.id, second.id, ProductUpdate(platform_product_id="P1")
        )


def test_organization_isolation(db_session: Session, organization) -> None:
    product = _create(db_session, organization.id)
    other_org = create_organization(db_session)
    with pytest.raises(NotFoundError):
        service.get_product(db_session, other_org.id, product.id)
    with pytest.raises(NotFoundError):
        service.update_product(db_session, other_org.id, product.id, ProductUpdate(name="x"))
    assert service.list_products(db_session, other_org.id, service.ProductFilters()).total == 0


# ----------------------------------------------------------------------------- lifecycle


def test_status_transitions_are_audited(db_session: Session, organization) -> None:
    product = _create(db_session, organization.id)

    with pytest.raises(InvalidStatusTransitionError):  # discovered → testing is not allowed
        service.change_status(
            db_session,
            organization.id,
            product.id,
            ProductStatusChange(status=ProductStatus.TESTING),
        )
    with pytest.raises(InvalidStatusTransitionError):  # no-op
        service.change_status(
            db_session,
            organization.id,
            product.id,
            ProductStatusChange(status=ProductStatus.DISCOVERED),
        )

    service.change_status(
        db_session,
        organization.id,
        product.id,
        ProductStatusChange(status=ProductStatus.SHORTLISTED, note="promising"),
    )
    service.change_status(
        db_session, organization.id, product.id, ProductStatusChange(status=ProductStatus.TESTING)
    )

    with pytest.raises(WinnerConfirmationRequiredError):  # winner needs confirmed + note
        service.change_status(
            db_session,
            organization.id,
            product.id,
            ProductStatusChange(status=ProductStatus.WINNER),
        )
    with pytest.raises(WinnerConfirmationRequiredError):
        service.change_status(
            db_session,
            organization.id,
            product.id,
            ProductStatusChange(status=ProductStatus.WINNER, confirmed=True),
        )

    product = service.change_status(
        db_session,
        organization.id,
        product.id,
        ProductStatusChange(
            status=ProductStatus.WINNER, confirmed=True, note="3 videos, 42 orders in 2 weeks"
        ),
        actor="tester",
    )
    assert product.status == ProductStatus.WINNER
    events = product.status_events
    assert [e.to_status for e in events] == [
        ProductStatus.WINNER,
        ProductStatus.TESTING,
        ProductStatus.SHORTLISTED,
        ProductStatus.DISCOVERED,
    ]
    assert (
        events[0].confirmed is True
        and events[0].actor == "tester"
        and events[0].from_status == ProductStatus.TESTING
    )

    service.change_status(
        db_session, organization.id, product.id, ProductStatusChange(status=ProductStatus.ARCHIVED)
    )
    with pytest.raises(InvalidStatusTransitionError):  # archived cannot jump straight to testing
        service.change_status(
            db_session,
            organization.id,
            product.id,
            ProductStatusChange(status=ProductStatus.TESTING),
        )


# ----------------------------------------------------------------------------- listing


def test_list_filters_and_stats(db_session: Session, organization) -> None:
    a = _create(
        db_session, organization.id, name="Blender Alpha", category="kitchen", product_url=None
    )
    _create(
        db_session,
        organization.id,
        name="Yoga Mat",
        category="fitness",
        product_url=None,
        seller_name="FitCo",
    )
    service.change_status(
        db_session, organization.id, a.id, ProductStatusChange(status=ProductStatus.SHORTLISTED)
    )
    db_session.commit()

    everything = service.list_products(
        db_session, organization.id, service.ProductFilters(sort="name")
    )
    assert [p.name for p in everything.items] == ["Blender Alpha", "Yoga Mat"]
    assert (
        service.list_products(db_session, organization.id, service.ProductFilters(q="fitco")).total
        == 1
    )
    assert (
        service.list_products(
            db_session, organization.id, service.ProductFilters(status=ProductStatus.SHORTLISTED)
        ).total
        == 1
    )
    assert (
        service.list_products(
            db_session, organization.id, service.ProductFilters(category="KITCHEN")
        ).total
        == 1
    )
    paged = service.list_products(
        db_session, organization.id, service.ProductFilters(page=2, page_size=1, sort="name")
    )
    assert paged.total_pages == 2 and [p.name for p in paged.items] == ["Yoga Mat"]

    stats = service.product_stats(db_session, organization.id)
    assert stats.total == 2
    assert stats.by_status["shortlisted"] == 1 and stats.by_status["discovered"] == 1
    assert stats.by_status["winner"] == 0 and stats.with_media == 0


# ----------------------------------------------------------------------------- media links


def test_media_linking(db_session: Session, organization, media_dirs) -> None:
    product = _create(db_session, organization.id)
    other = _create(db_session, organization.id, name="Other", product_url=None)
    first, second = _assets(db_session, organization, media_dirs)

    link = service.link_media(
        db_session,
        organization.id,
        product.id,
        ProductMediaLinkCreate(media_asset_id=first.id, is_primary=True, notes="hero shot"),
        thumbnail_url_prefix=THUMB,
    )
    assert (
        link.is_primary
        and link.asset.title == first.title
        and link.asset.relative_path == "clip_0.mp4"
    )
    with pytest.raises(DuplicateMediaLinkError):
        service.link_media(
            db_session,
            organization.id,
            product.id,
            ProductMediaLinkCreate(media_asset_id=first.id),
            thumbnail_url_prefix=THUMB,
        )

    # Same asset linked to a second product as primary → the first link loses primary.
    service.link_media(
        db_session,
        organization.id,
        other.id,
        ProductMediaLinkCreate(media_asset_id=first.id, is_primary=True),
        thumbnail_url_prefix=THUMB,
    )
    links = service.list_media_links(
        db_session, organization.id, product.id, thumbnail_url_prefix=THUMB
    )
    assert links[0].is_primary is False
    primaries = db_session.scalars(
        select(ProductMediaLink).where(
            ProductMediaLink.media_asset_id == first.id, ProductMediaLink.is_primary.is_(True)
        )
    ).all()
    assert len(primaries) == 1 and primaries[0].product_id == other.id

    # One product, several assets; media_count reflects it.
    service.link_media(
        db_session,
        organization.id,
        product.id,
        ProductMediaLinkCreate(media_asset_id=second.id),
        thumbnail_url_prefix=THUMB,
    )
    assert (
        service.product_detail(
            db_session, service.get_product(db_session, organization.id, product.id)
        ).media_count
        == 2
    )
    assert service.product_stats(db_session, organization.id).with_media == 2

    # Reverse lookup from the asset.
    linked = service.products_for_media(db_session, organization.id, first.id)
    assert [(lp.product.name, lp.is_primary) for lp in linked] == [
        ("Other", True),
        ("Mini blender", False),
    ]

    # Update + unlink.
    updated = service.update_media_link(
        db_session,
        organization.id,
        product.id,
        first.id,
        ProductMediaLinkUpdate(is_primary=True, notes="  "),
        thumbnail_url_prefix=THUMB,
    )
    assert updated.is_primary is True and updated.notes is None
    service.unlink_media(db_session, organization.id, product.id, first.id)
    with pytest.raises(NotFoundError):
        service.unlink_media(db_session, organization.id, product.id, first.id)


def test_media_link_rejects_foreign_or_missing_assets(
    db_session: Session, organization, media_dirs
) -> None:
    product = _create(db_session, organization.id)
    with pytest.raises(NotFoundError):  # unknown asset id → invalid foreign key, clean 404
        service.link_media(
            db_session,
            organization.id,
            product.id,
            ProductMediaLinkCreate(media_asset_id=uuid.uuid4()),
            thumbnail_url_prefix=THUMB,
        )

    other_org = create_organization(db_session)
    db_session.commit()
    foreign_asset = _assets(db_session, other_org, media_dirs, count=1)[0]
    with pytest.raises(NotFoundError):  # asset from another organization is invisible
        service.link_media(
            db_session,
            organization.id,
            product.id,
            ProductMediaLinkCreate(media_asset_id=foreign_asset.id),
            thumbnail_url_prefix=THUMB,
        )
    with pytest.raises(NotFoundError):  # product from another organization
        service.link_media(
            db_session,
            other_org.id,
            product.id,
            ProductMediaLinkCreate(media_asset_id=foreign_asset.id),
            thumbnail_url_prefix=THUMB,
        )


def test_deleting_asset_or_product_cascades_links(
    db_session: Session, organization, media_dirs
) -> None:
    product = _create(db_session, organization.id)
    asset = _assets(db_session, organization, media_dirs, count=1)[0]
    service.link_media(
        db_session,
        organization.id,
        product.id,
        ProductMediaLinkCreate(media_asset_id=asset.id),
        thumbnail_url_prefix=THUMB,
    )
    db_session.commit()
    db_session.delete(asset)
    db_session.commit()
    assert (
        service.list_media_links(
            db_session, organization.id, product.id, thumbnail_url_prefix=THUMB
        )
        == []
    )
