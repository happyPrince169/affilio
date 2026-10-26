"""Procurement HTTP routes (M2B)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from affilio.api.deps import CurrentOrganization, DbSession, RequireAdmin, RequireEditor
from affilio.core.enums import PurchaseOrderStatus, PurchaseRequestStatus
from affilio.modules.procurement import service
from affilio.modules.procurement.schemas import (
    OrderItemInput,
    PaginatedOrders,
    ProcurementStats,
    ProductProcurementSummary,
    PurchaseOrderCreate,
    PurchaseOrderDetail,
    PurchaseOrderStatusChange,
    PurchaseOrderUpdate,
    PurchaseRequestCreate,
    PurchaseRequestDecision,
    PurchaseRequestResponse,
    ReceiveRequest,
)
from affilio.modules.procurement.service import OrderFilters

router = APIRouter(prefix="/procurement", tags=["procurement"])


@router.get("/stats", response_model=ProcurementStats, summary="Procurement counters")
def read_stats(session: DbSession, organization: CurrentOrganization) -> ProcurementStats:
    return service.stats(session, organization.id)


@router.get("/requests", response_model=list[PurchaseRequestResponse], summary="Purchase requests")
def list_requests(
    session: DbSession,
    organization: CurrentOrganization,
    status_filter: Annotated[PurchaseRequestStatus | None, Query(alias="status")] = None,
    product_id: uuid.UUID | None = None,
) -> list[PurchaseRequestResponse]:
    return service.list_requests(
        session, organization.id, status=status_filter, product_id=product_id
    )


@router.post(
    "/requests",
    response_model=PurchaseRequestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Request a purchase (editor)",
)
def create_request(
    body: PurchaseRequestCreate,
    session: DbSession,
    organization: CurrentOrganization,
    _editor: RequireEditor,
) -> PurchaseRequestResponse:
    req = service.create_request(session, organization.id, body)
    session.commit()
    return service._request_response(service.get_request(session, organization.id, req.id))


@router.post(
    "/requests/{request_id}/decision",
    response_model=PurchaseRequestResponse,
    summary="Approve / reject / cancel a request (admin)",
)
def decide_request(
    request_id: uuid.UUID,
    body: PurchaseRequestDecision,
    session: DbSession,
    organization: CurrentOrganization,
    identity: RequireAdmin,
) -> PurchaseRequestResponse:
    service.decide_request(session, organization.id, request_id, body)
    session.commit()
    return service._request_response(service.get_request(session, organization.id, request_id))


@router.get("/orders", response_model=PaginatedOrders, summary="Purchase orders")
def list_orders(
    session: DbSession,
    organization: CurrentOrganization,
    status_filter: Annotated[PurchaseOrderStatus | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=service.MAX_PAGE_SIZE)] = 25,
) -> PaginatedOrders:
    return service.list_orders(
        session,
        organization.id,
        OrderFilters(status=status_filter, q=q, page=page, page_size=page_size),
    )


@router.post(
    "/orders",
    response_model=PurchaseOrderDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a purchase order (editor)",
)
def create_order(
    body: PurchaseOrderCreate,
    session: DbSession,
    organization: CurrentOrganization,
    _editor: RequireEditor,
) -> PurchaseOrderDetail:
    order = service.create_order(session, organization.id, body)
    session.commit()
    return service.order_detail(service.get_order(session, organization.id, order.id))


@router.get("/orders/{order_id}", response_model=PurchaseOrderDetail, summary="Order detail")
def read_order(
    order_id: uuid.UUID, session: DbSession, organization: CurrentOrganization
) -> PurchaseOrderDetail:
    return service.order_detail(service.get_order(session, organization.id, order_id))


@router.patch(
    "/orders/{order_id}", response_model=PurchaseOrderDetail, summary="Edit order fields (editor)"
)
def update_order(
    order_id: uuid.UUID,
    body: PurchaseOrderUpdate,
    session: DbSession,
    organization: CurrentOrganization,
    _editor: RequireEditor,
) -> PurchaseOrderDetail:
    service.update_order(session, organization.id, order_id, body)
    session.commit()
    return service.order_detail(service.get_order(session, organization.id, order_id))


@router.post(
    "/orders/{order_id}/status",
    response_model=PurchaseOrderDetail,
    summary="Change order status (editor)",
)
def change_order_status(
    order_id: uuid.UUID,
    body: PurchaseOrderStatusChange,
    session: DbSession,
    organization: CurrentOrganization,
    _editor: RequireEditor,
) -> PurchaseOrderDetail:
    service.change_order_status(session, organization.id, order_id, body)
    session.commit()
    return service.order_detail(service.get_order(session, organization.id, order_id))


@router.post(
    "/orders/{order_id}/items",
    response_model=PurchaseOrderDetail,
    summary="Add an order line (editor)",
)
def add_item(
    order_id: uuid.UUID,
    body: OrderItemInput,
    session: DbSession,
    organization: CurrentOrganization,
    _editor: RequireEditor,
) -> PurchaseOrderDetail:
    service.add_order_item(session, organization.id, order_id, body)
    session.commit()
    return service.order_detail(service.get_order(session, organization.id, order_id))


@router.delete(
    "/orders/{order_id}/items/{item_id}",
    response_model=PurchaseOrderDetail,
    summary="Remove a draft order line (editor)",
)
def remove_item(
    order_id: uuid.UUID,
    item_id: uuid.UUID,
    session: DbSession,
    organization: CurrentOrganization,
    _editor: RequireEditor,
) -> PurchaseOrderDetail:
    service.remove_order_item(session, organization.id, order_id, item_id)
    session.commit()
    return service.order_detail(service.get_order(session, organization.id, order_id))


@router.post(
    "/orders/{order_id}/receive",
    response_model=PurchaseOrderDetail,
    summary="Record received quantities (editor)",
)
def receive(
    order_id: uuid.UUID,
    body: ReceiveRequest,
    session: DbSession,
    organization: CurrentOrganization,
    _editor: RequireEditor,
) -> PurchaseOrderDetail:
    service.receive(session, organization.id, order_id, body)
    session.commit()
    return service.order_detail(service.get_order(session, organization.id, order_id))


@router.get(
    "/products/{product_id}",
    response_model=ProductProcurementSummary,
    summary="Procurement summary for a product",
)
def product_summary(
    product_id: uuid.UUID, session: DbSession, organization: CurrentOrganization
) -> ProductProcurementSummary:
    return service.product_summary(session, organization.id, product_id)
