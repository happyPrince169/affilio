from __future__ import annotations

from fastapi import status

from affilio.core.errors import AppError


class InvalidProcurementTransitionError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "invalid_procurement_transition"


class RequestNotApprovedError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "request_not_approved"


class DuplicateOrderError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "duplicate_order"


class OrderNotEditableError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "order_not_editable"


class ReceiveQuantityError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "invalid_receive_quantity"
