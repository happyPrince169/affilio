"""Procurement state machines. Independent of the product research lifecycle."""

from __future__ import annotations

from affilio.core.enums import PurchaseOrderStatus as O
from affilio.core.enums import PurchaseRequestStatus as R

REQUEST_TRANSITIONS: dict[R, frozenset[R]] = {
    R.REQUESTED: frozenset({R.APPROVED, R.REJECTED, R.CANCELLED}),
    R.APPROVED: frozenset({R.ORDERED, R.REJECTED, R.CANCELLED}),
    R.REJECTED: frozenset({R.REQUESTED}),
    R.ORDERED: frozenset(),
    R.CANCELLED: frozenset({R.REQUESTED}),
}

ORDER_TRANSITIONS: dict[O, frozenset[O]] = {
    O.DRAFT: frozenset({O.PLACED, O.CANCELLED}),
    O.PLACED: frozenset({O.SHIPPED, O.DELIVERED, O.CANCELLED}),
    O.SHIPPED: frozenset({O.DELIVERED, O.CANCELLED}),
    O.DELIVERED: frozenset({O.PARTIALLY_RECEIVED, O.RECEIVED}),
    O.PARTIALLY_RECEIVED: frozenset({O.RECEIVED}),
    O.RECEIVED: frozenset(),
    O.CANCELLED: frozenset({O.DRAFT}),
}

# Receiving is allowed once goods can physically arrive.
RECEIVABLE: frozenset[O] = frozenset({O.PLACED, O.SHIPPED, O.DELIVERED, O.PARTIALLY_RECEIVED})
EDITABLE: frozenset[O] = frozenset(
    {O.DRAFT, O.PLACED, O.SHIPPED, O.DELIVERED, O.PARTIALLY_RECEIVED}
)
