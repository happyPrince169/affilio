"""Procurement pipeline. Requires TEST_DATABASE_URL."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from affilio.app import create_app
from affilio.core.config import get_settings
from affilio.core.enums import ProductStatus, PurchaseOrderStatus
from affilio.core.errors import NotFoundError
from affilio.db.session import dispose_engine
from affilio.modules.procurement import service
from affilio.modules.procurement.errors import (
    DuplicateOrderError,
    InvalidProcurementTransitionError,
    OrderNotEditableError,
    ReceiveQuantityError,
    RequestNotApprovedError,
)
from affilio.modules.procurement.schemas import (
    OrderItemInput,
    PurchaseOrderCreate,
    PurchaseOrderStatusChange,
    PurchaseRequestCreate,
    PurchaseRequestDecision,
    ReceiveItem,
    ReceiveRequest,
)
from affilio.modules.products import service as products
from affilio.modules.products.schemas import ProductCreate
from tests.media_helpers import create_organization

pytestmark = pytest.mark.database


def _product(session: Session, org_id: uuid.UUID, name: str = "Blender"):
    return products.create_product(session, org_id, ProductCreate(name=name))


def _approved_request(session: Session, org_id: uuid.UUID, product_id: uuid.UUID, qty: int = 2):
    req = service.create_request(
        session,
        org_id,
        PurchaseRequestCreate(product_id=product_id, quantity=qty, reason="for filming"),
    )
    return service.decide_request(
        session, org_id, req.id, PurchaseRequestDecision(status="approved", note="ok")
    )


def test_request_lifecycle_and_product_status_untouched(db_session: Session, organization) -> None:
    product = _product(db_session, organization.id)
    req = service.create_request(
        db_session, organization.id, PurchaseRequestCreate(product_id=product.id, quantity=3)
    )
    assert req.status == "requested" and req.product.name == "Blender"

    with pytest.raises(InvalidProcurementTransitionError):  # requested → ordered directly
        service.decide_request(
            db_session, organization.id, req.id, PurchaseRequestDecision(status="requested")
        )
    req = service.decide_request(
        db_session,
        organization.id,
        req.id,
        PurchaseRequestDecision(status="rejected", note="too pricey"),
    )
    assert (
        req.status == "rejected"
        and req.decision_note == "too pricey"
        and req.decided_at is not None
    )
    req = service.decide_request(
        db_session, organization.id, req.id, PurchaseRequestDecision(status="requested")
    )
    req = service.decide_request(
        db_session, organization.id, req.id, PurchaseRequestDecision(status="approved")
    )
    assert req.status == "approved"
    db_session.refresh(product)
    assert product.status == ProductStatus.DISCOVERED  # research status is independent


def test_order_from_requests_blocks_duplicate_orders(db_session: Session, organization) -> None:
    product = _product(db_session, organization.id)
    other = _product(db_session, organization.id, "Mat")
    req = _approved_request(db_session, organization.id, product.id)
    pending = service.create_request(
        db_session, organization.id, PurchaseRequestCreate(product_id=other.id)
    )

    with pytest.raises(RequestNotApprovedError):
        service.create_order(
            db_session,
            organization.id,
            PurchaseOrderCreate(items=[OrderItemInput(purchase_request_id=pending.id)]),
        )

    order = service.create_order(
        db_session,
        organization.id,
        PurchaseOrderCreate(
            supplier_name="KitchenMate",
            shipping_cost="20000",
            items=[OrderItemInput(purchase_request_id=req.id, unit_cost="150000")],
        ),
    )
    assert order.order_number.startswith("PO-") and order.status == PurchaseOrderStatus.DRAFT
    assert order.items[0].quantity_ordered == 2 and str(order.total_cost) == "320000.00"
    db_session.refresh(req)
    assert req.status == "ordered"

    with pytest.raises(DuplicateOrderError):  # same approved request on a second order
        service.create_order(
            db_session,
            organization.id,
            PurchaseOrderCreate(items=[OrderItemInput(purchase_request_id=req.id)]),
        )
    with pytest.raises(DuplicateOrderError):  # same product twice on one order
        service.add_order_item(
            db_session,
            organization.id,
            order.id,
            OrderItemInput(product_id=product.id, quantity_ordered=1),
        )

    second = service.create_order(db_session, organization.id, PurchaseOrderCreate())
    assert second.order_number != order.order_number
    with pytest.raises(OrderNotEditableError):  # cannot place an empty order
        service.change_order_status(
            db_session,
            organization.id,
            second.id,
            PurchaseOrderStatusChange(status=PurchaseOrderStatus.PLACED),
        )


def test_order_transitions_and_receiving(db_session: Session, organization) -> None:
    product = _product(db_session, organization.id)
    req = _approved_request(db_session, organization.id, product.id, qty=4)
    order = service.create_order(
        db_session,
        organization.id,
        PurchaseOrderCreate(items=[OrderItemInput(purchase_request_id=req.id, unit_cost="10.00")]),
    )
    item = order.items[0]

    with pytest.raises(InvalidProcurementTransitionError):  # nothing to receive on a draft
        service.receive(
            db_session,
            organization.id,
            order.id,
            ReceiveRequest(items=[ReceiveItem(item_id=item.id, quantity=1)]),
        )
    with pytest.raises(InvalidProcurementTransitionError):  # draft → received is not allowed
        service.change_order_status(
            db_session,
            organization.id,
            order.id,
            PurchaseOrderStatusChange(status=PurchaseOrderStatus.RECEIVED),
        )

    order = service.change_order_status(
        db_session,
        organization.id,
        order.id,
        PurchaseOrderStatusChange(status=PurchaseOrderStatus.PLACED),
    )
    assert order.ordered_at is not None
    order = service.change_order_status(
        db_session,
        organization.id,
        order.id,
        PurchaseOrderStatusChange(status=PurchaseOrderStatus.SHIPPED),
    )

    with pytest.raises(ReceiveQuantityError):
        service.receive(
            db_session,
            organization.id,
            order.id,
            ReceiveRequest(items=[ReceiveItem(item_id=item.id, quantity=9)]),
        )
    with pytest.raises(NotFoundError):
        service.receive(
            db_session,
            organization.id,
            order.id,
            ReceiveRequest(items=[ReceiveItem(item_id=uuid.uuid4(), quantity=1)]),
        )

    order = service.receive(
        db_session,
        organization.id,
        order.id,
        ReceiveRequest(items=[ReceiveItem(item_id=item.id, quantity=1)], note="1 box arrived"),
    )
    assert order.status == PurchaseOrderStatus.PARTIALLY_RECEIVED and order.delivered_at is not None
    order = service.receive(
        db_session,
        organization.id,
        order.id,
        ReceiveRequest(items=[ReceiveItem(item_id=item.id, quantity=4)]),
    )
    assert order.status == PurchaseOrderStatus.RECEIVED
    assert (
        service.order_detail(order).allowed_transitions == []
        and service.order_detail(order).receivable is False
    )

    summary = service.product_summary(db_session, organization.id, product.id)
    assert (summary.units_ordered, summary.units_received, summary.open_requests) == (4, 4, 0)
    stats = service.stats(db_session, organization.id)
    assert stats.orders_by_status["received"] == 1 and stats.units_received == 4


def test_cancel_releases_requests(db_session: Session, organization) -> None:
    product = _product(db_session, organization.id)
    req = _approved_request(db_session, organization.id, product.id)
    order = service.create_order(
        db_session,
        organization.id,
        PurchaseOrderCreate(items=[OrderItemInput(purchase_request_id=req.id)]),
    )
    service.change_order_status(
        db_session,
        organization.id,
        order.id,
        PurchaseOrderStatusChange(status=PurchaseOrderStatus.CANCELLED),
    )
    db_session.refresh(req)
    assert req.status == "approved"  # can be ordered again
    service.create_order(
        db_session,
        organization.id,
        PurchaseOrderCreate(items=[OrderItemInput(purchase_request_id=req.id)]),
    )


def test_cross_organization_access(db_session: Session, organization) -> None:
    product = _product(db_session, organization.id)
    req = _approved_request(db_session, organization.id, product.id)
    order = service.create_order(
        db_session,
        organization.id,
        PurchaseOrderCreate(items=[OrderItemInput(purchase_request_id=req.id)]),
    )
    other = create_organization(db_session)
    with pytest.raises(NotFoundError):
        service.get_order(db_session, other.id, order.id)
    with pytest.raises(NotFoundError):
        service.get_request(db_session, other.id, req.id)
    with pytest.raises(NotFoundError):  # foreign product in a request
        service.create_request(db_session, other.id, PurchaseRequestCreate(product_id=product.id))
    with pytest.raises(NotFoundError):  # foreign request on an order
        service.create_order(
            db_session,
            other.id,
            PurchaseOrderCreate(items=[OrderItemInput(purchase_request_id=req.id)]),
        )
    assert service.list_orders(db_session, other.id, service.OrderFilters()).total == 0


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, media_dirs, organization, schema) -> Iterator[TestClient]:
    for key, value in media_dirs.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DEFAULT_ORGANIZATION_SLUG", organization.slug)
    monkeypatch.delenv("MEDIA_ADMIN_TOKEN", raising=False)
    get_settings.cache_clear()
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        yield client
    dispose_engine()
    get_settings.cache_clear()


def test_procurement_api_flow(api: TestClient) -> None:
    product_id = api.post("/api/v1/products", json={"name": "Lamp"}).json()["id"]
    req = api.post(
        "/api/v1/procurement/requests",
        json={"product_id": product_id, "quantity": 2, "reason": "shoot"},
    )
    assert req.status_code == 201, req.text
    req_id = req.json()["id"]
    assert (
        api.post(
            f"/api/v1/procurement/requests/{req_id}/decision", json={"status": "requested"}
        ).status_code
        == 409
    )
    assert (
        api.post(
            f"/api/v1/procurement/requests/{req_id}/decision",
            json={"status": "approved", "note": "go"},
        ).json()["status"]
        == "approved"
    )

    order = api.post(
        "/api/v1/procurement/orders",
        json={
            "supplier_name": "Shop A",
            "items": [{"purchase_request_id": req_id, "unit_cost": "99.50"}],
            "tracking_url": "https://spx.vn/track/1",
        },
    )
    assert order.status_code == 201, order.text
    body = order.json()
    order_id = body["id"]
    assert (
        body["units_ordered"] == 2
        and body["total_cost"] == "199.00"
        and body["items"][0]["line_total"] == "199.00"
    )
    assert api.get("/api/v1/procurement/requests").json()[0]["order_number"] == body["order_number"]

    dup = api.post("/api/v1/procurement/orders", json={"items": [{"purchase_request_id": req_id}]})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "duplicate_order"
    bad_url = api.post("/api/v1/procurement/orders", json={"tracking_url": "javascript:x"})
    assert bad_url.status_code == 422

    assert (
        api.post(f"/api/v1/procurement/orders/{order_id}/status", json={"status": "placed"}).json()[
            "status"
        ]
        == "placed"
    )
    invalid = api.post(f"/api/v1/procurement/orders/{order_id}/status", json={"status": "received"})
    assert (
        invalid.status_code == 409
        and invalid.json()["error"]["code"] == "invalid_procurement_transition"
    )
    item_id = body["items"][0]["id"]
    received = api.post(
        f"/api/v1/procurement/orders/{order_id}/receive",
        json={"items": [{"item_id": item_id, "quantity": 1}]},
    )
    assert received.status_code == 200 and received.json()["status"] == "partially_received"
    assert (
        api.get("/api/v1/procurement/orders", params={"status": "partially_received"}).json()[
            "total"
        ]
        == 1
    )
    stats = api.get("/api/v1/procurement/stats").json()
    assert stats["units_received"] == 1 and stats["open_orders"] == 1
    summary = api.get(f"/api/v1/procurement/products/{product_id}").json()
    assert summary["units_ordered"] == 2 and summary["orders"][0]["id"] == order_id
    assert api.get(f"/api/v1/procurement/orders/{uuid.uuid4()}").status_code == 404
