"""Production and editing state machines."""

from __future__ import annotations

from affilio.core.enums import EditingStatus as E
from affilio.core.enums import ProductionStatus as P

PRODUCTION_TRANSITIONS: dict[P, frozenset[P]] = {
    P.PLANNED: frozenset({P.SCHEDULED, P.CANCELLED}),
    P.SCHEDULED: frozenset({P.FILMING, P.PLANNED, P.CANCELLED}),
    P.FILMING: frozenset({P.FILMED, P.SCHEDULED, P.CANCELLED}),
    P.FILMED: frozenset({P.FILMING}),
    P.CANCELLED: frozenset({P.PLANNED}),
}

EDITING_TRANSITIONS: dict[E, frozenset[E]] = {
    E.NOT_STARTED: frozenset({E.IN_PROGRESS}),
    E.IN_PROGRESS: frozenset({E.REVIEW, E.NOT_STARTED}),
    E.REVIEW: frozenset({E.DONE, E.IN_PROGRESS}),
    E.DONE: frozenset({E.REVIEW}),
}
