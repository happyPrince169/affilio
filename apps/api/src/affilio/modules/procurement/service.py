"""Procurement services: requests, orders, items, receiving. Organization-scoped."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from affilio.core.enums import PurchaseOrderStatus, PurchaseRequestStatus
from affilio.core.errors import NotFoundError
from affilio.modules.procurement.errors import (
    DuplicateOrderError,
    InvalidProcurementTransitionError,
    OrderNotEditableError,
    ReceiveQuantityError,
    RequestNotApprovedError,
)
from affilio.modules.procurement.lifecycle import (
    EDITABLE,
    ORDER_TRANSITIONS,
    RECEIVABLE,
    REQUEST_TRANSITIONS,
)
from affilio.modules.procurement.models import PurchaseOrder, PurchaseOrderItem, PurchaseRequest
from affilio.modules.procurement.schemas import (
    PaginatedOrders,
    ProcurementStats,
    ProductProcurementSummary,
    ProductRef,
    PurchaseOrderCreate,
    PurchaseOrderDetail,
    PurchaseOrderItemResponse,
    PurchaseOrderStatusChange,
    PurchaseOrderSummary,
    PurchaseOrderUpdate,
    PurchaseRequestCreate,
    PurchaseRequestDecision,
    PurchaseRequestResponse,
    ReceiveRequest,
)
from affilio.modules.products import service as products_service

MAX_PAGE_SIZE = 100


def _product_ref(product) -> ProductRef:
    return ProductRef(
        id=product.id,
        name=product.name,
        status=product.status,
        platform=product.platform,
        product_url=product.product_url,
    )


# ------------------------------------------------------------------ requests


def _request_response(req: PurchaseRequest) -> PurchaseRequestResponse:
    item = req.order_item
    return PurchaseRequestResponse(
        id=req.id,
        product=_product_ref(req.product),
        status=req.status,
        quantity=req.quantity,
        reason=req.reason,
        requested_by=req.requested_by,
        decided_by=req.decided_by,
        decided_at=req.decided_at,
        decision_note=req.decision_note,
        order_id=item.order_id if item else None,
        order_number=item.order.order_number if item and item.order else None,
        created_at=req.created_at,
        updated_at=req.updated_at,
    )


def _request_query():
    return select(PurchaseRequest).options(
        selectinload(PurchaseRequest.product),
        selectinload(PurchaseRequest.order_item).selectinload(PurchaseOrderItem.order),
    )


def get_request(
    session: Session, organization_id: uuid.UUID, request_id: uuid.UUID
) -> PurchaseRequest:
    req = session.scalar(
        _request_query().where(
            PurchaseRequest.organization_id == organization_id, PurchaseRequest.id == request_id
        )
    )
    if req is None:
        raise NotFoundError("Purchase request not found")
    return req


def create_request(
    session: Session, organization_id: uuid.UUID, data: PurchaseRequestCreate, *, actor: str = "api"
) -> PurchaseRequest:
    product = products_service.get_product(
        session, organization_id, data.product_id
    )  # 404 if foreign
    req = PurchaseRequest(
        id=uuid.uuid4(),
        organization_id=organization_id,
        product_id=product.id,
        quantity=data.quantity,
        reason=(data.reason or "").strip() or None,
        requested_by=actor,
    )
    session.add(req)
    session.flush()
    return get_request(session, organization_id, req.id)


def decide_request(
    session: Session,
    organization_id: uuid.UUID,
    request_id: uuid.UUID,
    data: PurchaseRequestDecision,
    *,
    actor: str = "api",
) -> PurchaseRequest:
    req = get_request(session, organization_id, request_id)
    target = PurchaseRequestStatus(data.status)
    if target not in REQUEST_TRANSITIONS[req.status]:
        raise InvalidProcurementTransitionError(
            f"Cannot move request from {req.status} to {target}",
            details={
                "from": req.status,
                "to": target,
                "allowed": sorted(REQUEST_TRANSITIONS[req.status]),
            },
        )
    req.status = target
    req.decided_by = actor
    req.decided_at = datetime.now(UTC)
    req.decision_note = (data.note or "").strip() or None
    session.flush()
    return req


def list_requests(
    session: Session,
    organization_id: uuid.UUID,
    *,
    status: PurchaseRequestStatus | None = None,
    product_id: uuid.UUID | None = None,
) -> list[PurchaseRequestResponse]:
    stmt = _request_query().where(PurchaseRequest.organization_id == organization_id)
    if status:
        stmt = stmt.where(PurchaseRequest.status == status)
    if product_id:
        stmt = stmt.where(PurchaseRequest.product_id == product_id)
    stmt = stmt.order_by(PurchaseRequest.created_at.desc())
    return [_request_response(r) for r in session.scalars(stmt)]


# ------------------------------------------------------------------ orders


def _next_order_number(session: Session, organization_id: uuid.UUID) -> str:
    year = datetime.now(UTC).year
    prefix = f"PO-{year}-"
    count = session.scalar(
        select(func.count(PurchaseOrder.id)).where(
            PurchaseOrder.organization_id == organization_id,
            PurchaseOrder.order_number.like(f"{prefix}%"),
        )
    )
    return f"{prefix}{int(count or 0) + 1:04d}"


def _recompute_total(order: PurchaseOrder) -> None:
    total = Decimal("0")
    any_cost = False
    for item in order.items:
        if item.unit_cost is not None:
            total += item.unit_cost * item.quantity_ordered
            any_cost = True
    if order.shipping_cost is not None:
        total += order.shipping_cost
        any_cost = True
    order.total_cost = total.quantize(Decimal("0.01")) if any_cost else None


def _order_query():
    return select(PurchaseOrder).options(
        selectinload(PurchaseOrder.items).selectinload(PurchaseOrderItem.product)
    )


def get_order(session: Session, organization_id: uuid.UUID, order_id: uuid.UUID) -> PurchaseOrder:
    order = session.scalar(
        _order_query().where(
            PurchaseOrder.organization_id == organization_id, PurchaseOrder.id == order_id
        )
    )
    if order is None:
        raise NotFoundError("Purchase order not found")
    return order


def _order_summary(order: PurchaseOrder) -> PurchaseOrderSummary:
    return PurchaseOrderSummary(
        id=order.id,
        order_number=order.order_number,
        status=order.status,
        platform=order.platform,
        supplier_name=order.supplier_name,
        platform_order_ref=order.platform_order_ref,
        currency=order.currency,
        shipping_cost=order.shipping_cost,
        total_cost=order.total_cost,
        tracking_number=order.tracking_number,
        tracking_url=order.tracking_url,
        ordered_at=order.ordered_at,
        expected_delivery_at=order.expected_delivery_at,
        delivered_at=order.delivered_at,
        item_count=len(order.items),
        units_ordered=sum(i.quantity_ordered for i in order.items),
        units_received=sum(i.quantity_received for i in order.items),
        status_changed_at=order.status_changed_at,
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


def order_detail(order: PurchaseOrder) -> PurchaseOrderDetail:
    summary = _order_summary(order)
    return PurchaseOrderDetail(
        **summary.model_dump(),
        notes=order.notes,
        items=[
            PurchaseOrderItemResponse(
                id=i.id,
                product=_product_ref(i.product),
                purchase_request_id=i.purchase_request_id,
                quantity_ordered=i.quantity_ordered,
                quantity_received=i.quantity_received,
                unit_cost=i.unit_cost,
                line_total=(i.unit_cost * i.quantity_ordered) if i.unit_cost is not None else None,
                received_at=i.received_at,
                notes=i.notes,
            )
            for i in order.items
        ],
        allowed_transitions=sorted(ORDER_TRANSITIONS[order.status]),
        receivable=order.status in RECEIVABLE,
    )


def _add_item(
    session: Session, organization_id: uuid.UUID, order: PurchaseOrder, data
) -> PurchaseOrderItem:
    request = None
    if data.purchase_request_id is not None:
        request = get_request(session, organization_id, data.purchase_request_id)
        if request.order_item is not None:
            raise DuplicateOrderError(
                "This request is already on an order",
                details={"order_id": str(request.order_item.order_id)},
            )
        if request.status != PurchaseRequestStatus.APPROVED:
            raise RequestNotApprovedError(
                f"Request is {request.status}; only approved requests can be ordered",
                details={"request_id": str(request.id), "status": request.status},
            )
        product_id = request.product_id
        quantity = data.quantity_ordered or request.quantity
    else:
        if data.product_id is None or data.quantity_ordered is None:
            raise ReceiveQuantityError("A free line needs product_id and quantity_ordered")
        product_id = products_service.get_product(session, organization_id, data.product_id).id
        quantity = data.quantity_ordered
    if any(i.product_id == product_id for i in order.items):
        raise DuplicateOrderError(
            "This product is already on the order", details={"product_id": str(product_id)}
        )
    item = PurchaseOrderItem(
        id=uuid.uuid4(),
        order=order,
        product_id=product_id,
        purchase_request_id=request.id if request else None,
        quantity_ordered=quantity,
        unit_cost=data.unit_cost,
        notes=(data.notes or "").strip() or None,
    )
    session.add(item)
    if request is not None:
        request.status = PurchaseRequestStatus.ORDERED
    return item


def create_order(
    session: Session, organization_id: uuid.UUID, data: PurchaseOrderCreate
) -> PurchaseOrder:
    order = PurchaseOrder(
        id=uuid.uuid4(),
        organization_id=organization_id,
        order_number=_next_order_number(session, organization_id),
        platform=data.platform,
        supplier_name=(data.supplier_name or "").strip() or None,
        platform_order_ref=(data.platform_order_ref or "").strip() or None,
        currency=data.currency,
        shipping_cost=data.shipping_cost,
        tracking_number=(data.tracking_number or "").strip() or None,
        tracking_url=data.tracking_url,
        expected_delivery_at=data.expected_delivery_at,
        notes=(data.notes or "").strip() or None,
        status_changed_at=datetime.now(UTC),
    )
    # SAVEPOINT: a failing line must not leave a half-built order (or its number) in the session.
    try:
        with session.begin_nested():
            session.add(order)
            for item in data.items:
                _add_item(session, organization_id, order, item)
            _recompute_total(order)
            session.flush()
    except IntegrityError as exc:
        raise DuplicateOrderError(
            "Order conflicts with an existing order (request already ordered?)"
        ) from exc
    return get_order(session, organization_id, order.id)


def add_order_item(
    session: Session, organization_id: uuid.UUID, order_id: uuid.UUID, data
) -> PurchaseOrder:
    order = get_order(session, organization_id, order_id)
    if order.status not in EDITABLE:
        raise OrderNotEditableError(f"Order is {order.status}; items cannot be added")
    try:
        with session.begin_nested():
            _add_item(session, organization_id, order, data)
            _recompute_total(order)
            session.flush()
    except IntegrityError as exc:
        raise DuplicateOrderError("Item conflicts with an existing order line") from exc
    return get_order(session, organization_id, order.id)


def remove_order_item(
    session: Session, organization_id: uuid.UUID, order_id: uuid.UUID, item_id: uuid.UUID
) -> PurchaseOrder:
    order = get_order(session, organization_id, order_id)
    if order.status not in {PurchaseOrderStatus.DRAFT}:
        raise OrderNotEditableError("Items can only be removed from draft orders")
    item = next((i for i in order.items if i.id == item_id), None)
    if item is None:
        raise NotFoundError("Order item not found")
    if item.request is not None:
        item.request.status = PurchaseRequestStatus.APPROVED
    order.items.remove(item)
    _recompute_total(order)
    session.flush()
    return get_order(session, organization_id, order.id)


def update_order(
    session: Session, organization_id: uuid.UUID, order_id: uuid.UUID, data: PurchaseOrderUpdate
) -> PurchaseOrder:
    order = get_order(session, organization_id, order_id)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(order, key, value)
    _recompute_total(order)
    session.flush()
    return order


def change_order_status(
    session: Session,
    organization_id: uuid.UUID,
    order_id: uuid.UUID,
    data: PurchaseOrderStatusChange,
) -> PurchaseOrder:
    order = get_order(session, organization_id, order_id)
    if data.status not in ORDER_TRANSITIONS[order.status]:
        raise InvalidProcurementTransitionError(
            f"Cannot move order from {order.status} to {data.status}",
            details={
                "from": order.status,
                "to": data.status,
                "allowed": sorted(ORDER_TRANSITIONS[order.status]),
            },
        )
    if data.status == PurchaseOrderStatus.PLACED and not order.items:
        raise OrderNotEditableError("Add at least one item before placing the order")
    now = datetime.now(UTC)
    order.status = data.status
    order.status_changed_at = now
    if data.status == PurchaseOrderStatus.PLACED and order.ordered_at is None:
        order.ordered_at = now
    if data.status == PurchaseOrderStatus.DELIVERED and order.delivered_at is None:
        order.delivered_at = now
    if data.status == PurchaseOrderStatus.CANCELLED:
        # Release the requests so they can be ordered again; the cancelled line keeps no claim.
        for item in order.items:
            if item.request is not None:
                item.request.status = PurchaseRequestStatus.APPROVED
                item.purchase_request_id = None
                item.request = None
    if data.note:
        order.notes = f"{order.notes}\n{data.note}".strip() if order.notes else data.note
    session.flush()
    return order


def receive(
    session: Session, organization_id: uuid.UUID, order_id: uuid.UUID, data: ReceiveRequest
) -> PurchaseOrder:
    """Record received quantities (cumulative). Status derives from the totals."""
    order = get_order(session, organization_id, order_id)
    if order.status not in RECEIVABLE:
        raise InvalidProcurementTransitionError(
            f"Order is {order.status}; nothing can be received",
            details={"status": order.status},
        )
    by_id = {i.id: i for i in order.items}
    now = datetime.now(UTC)
    for entry in data.items:
        item = by_id.get(entry.item_id)
        if item is None:
            raise NotFoundError("Order item not found", details={"item_id": str(entry.item_id)})
        if entry.quantity > item.quantity_ordered:
            raise ReceiveQuantityError(
                f"Cannot receive {entry.quantity} of {item.quantity_ordered} ordered",
                details={"item_id": str(item.id)},
            )
        item.quantity_received = entry.quantity
        item.received_at = now if entry.quantity > 0 else None
    received = sum(i.quantity_received for i in order.items)
    ordered = sum(i.quantity_ordered for i in order.items)
    if received >= ordered and ordered > 0:
        order.status = PurchaseOrderStatus.RECEIVED
    elif received > 0:
        order.status = PurchaseOrderStatus.PARTIALLY_RECEIVED
    order.status_changed_at = now
    if order.delivered_at is None and received > 0:
        order.delivered_at = now
    if data.note:
        order.notes = f"{order.notes}\n{data.note}".strip() if order.notes else data.note
    session.flush()
    return order


@dataclass(slots=True)
class OrderFilters:
    status: PurchaseOrderStatus | None = None
    q: str | None = None
    page: int = 1
    page_size: int = 25


def list_orders(
    session: Session, organization_id: uuid.UUID, filters: OrderFilters
) -> PaginatedOrders:
    page = max(1, filters.page)
    page_size = min(max(1, filters.page_size), MAX_PAGE_SIZE)
    base = _order_query().where(PurchaseOrder.organization_id == organization_id)
    if filters.status:
        base = base.where(PurchaseOrder.status == filters.status)
    if filters.q:
        pattern = f"%{filters.q.strip()}%"
        base = base.where(
            PurchaseOrder.order_number.ilike(pattern)
            | PurchaseOrder.supplier_name.ilike(pattern)
            | PurchaseOrder.platform_order_ref.ilike(pattern)
            | PurchaseOrder.tracking_number.ilike(pattern)
        )
    total = int(
        session.scalar(select(func.count()).select_from(base.order_by(None).subquery())) or 0
    )
    rows = session.scalars(
        base.order_by(PurchaseOrder.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).unique()
    return PaginatedOrders(
        items=[_order_summary(o) for o in rows],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=max(1, math.ceil(total / page_size)) if total else 0,
    )


def stats(session: Session, organization_id: uuid.UUID) -> ProcurementStats:
    req_rows = session.execute(
        select(PurchaseRequest.status, func.count(PurchaseRequest.id))
        .where(PurchaseRequest.organization_id == organization_id)
        .group_by(PurchaseRequest.status)
    ).all()
    order_rows = session.execute(
        select(PurchaseOrder.status, func.count(PurchaseOrder.id))
        .where(PurchaseOrder.organization_id == organization_id)
        .group_by(PurchaseOrder.status)
    ).all()
    units = session.execute(
        select(
            func.coalesce(func.sum(PurchaseOrderItem.quantity_ordered), 0),
            func.coalesce(func.sum(PurchaseOrderItem.quantity_received), 0),
        )
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.order_id)
        .where(
            PurchaseOrder.organization_id == organization_id,
            PurchaseOrder.status != PurchaseOrderStatus.CANCELLED,
        )
    ).one()
    requests_by_status = {s.value: 0 for s in PurchaseRequestStatus}
    for status, count in req_rows:
        requests_by_status[str(status)] = int(count)
    orders_by_status = {s.value: 0 for s in PurchaseOrderStatus}
    for status, count in order_rows:
        orders_by_status[str(status)] = int(count)
    open_orders = sum(
        orders_by_status[s.value]
        for s in (
            PurchaseOrderStatus.PLACED,
            PurchaseOrderStatus.SHIPPED,
            PurchaseOrderStatus.DELIVERED,
            PurchaseOrderStatus.PARTIALLY_RECEIVED,
        )
    )
    return ProcurementStats(
        requests_by_status=requests_by_status,
        orders_by_status=orders_by_status,
        units_ordered=int(units[0]),
        units_received=int(units[1]),
        open_orders=open_orders,
    )


def product_summary(
    session: Session, organization_id: uuid.UUID, product_id: uuid.UUID
) -> ProductProcurementSummary:
    products_service.get_product(session, organization_id, product_id)
    open_requests = int(
        session.scalar(
            select(func.count(PurchaseRequest.id)).where(
                PurchaseRequest.organization_id == organization_id,
                PurchaseRequest.product_id == product_id,
                PurchaseRequest.status.in_(
                    [PurchaseRequestStatus.REQUESTED, PurchaseRequestStatus.APPROVED]
                ),
            )
        )
        or 0
    )
    orders = list(
        session.scalars(
            _order_query()
            .join(PurchaseOrderItem, PurchaseOrderItem.order_id == PurchaseOrder.id)
            .where(
                PurchaseOrder.organization_id == organization_id,
                PurchaseOrderItem.product_id == product_id,
            )
            .order_by(PurchaseOrder.created_at.desc())
        ).unique()
    )
    ordered = received = 0
    for order in orders:
        if order.status == PurchaseOrderStatus.CANCELLED:
            continue
        for item in order.items:
            if item.product_id == product_id:
                ordered += item.quantity_ordered
                received += item.quantity_received
    return ProductProcurementSummary(
        open_requests=open_requests,
        units_ordered=ordered,
        units_received=received,
        orders=[_order_summary(o) for o in orders],
    )
