"""Manual product lifecycle. Every transition is operator-initiated and audited.

WINNER is a human decision: it requires `confirmed=True` and a note explaining the evidence.
Nothing in the system promotes a product automatically.
"""

from __future__ import annotations

from affilio.core.enums import ProductStatus

S = ProductStatus

ALLOWED_TRANSITIONS: dict[ProductStatus, frozenset[ProductStatus]] = {
    S.DISCOVERED: frozenset({S.SHORTLISTED, S.ARCHIVED}),
    S.SHORTLISTED: frozenset({S.TESTING, S.DISCOVERED, S.PAUSED, S.ARCHIVED}),
    S.TESTING: frozenset({S.WINNER, S.PAUSED, S.SHORTLISTED, S.ARCHIVED}),
    S.WINNER: frozenset({S.PAUSED, S.TESTING, S.ARCHIVED}),
    S.PAUSED: frozenset({S.TESTING, S.SHORTLISTED, S.ARCHIVED}),
    S.ARCHIVED: frozenset({S.DISCOVERED, S.SHORTLISTED}),
}

HUMAN_CONFIRMATION_REQUIRED: frozenset[ProductStatus] = frozenset({S.WINNER})


def can_transition(current: ProductStatus, target: ProductStatus) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def requires_confirmation(target: ProductStatus) -> bool:
    return target in HUMAN_CONFIRMATION_REQUIRED
