"""Publication state machine. See docs/architecture/distribution.md#state-machine.

Only the service layer calls `assert_transition`. Nothing moves to PUBLISHED without
evidence (operator-reported in M3A, API-verified from M3B), and nothing is retried
automatically.
"""

from __future__ import annotations

from affilio.core.enums import MembershipRole, PublicationStatus
from affilio.modules.publishing.errors import InvalidPublicationTransitionError

S = PublicationStatus

# action -> (allowed source statuses, target status, minimum role)
ACTIONS: dict[str, tuple[frozenset[PublicationStatus], PublicationStatus, MembershipRole]] = {
    "submit": (frozenset({S.DRAFT}), S.READY_FOR_REVIEW, MembershipRole.EDITOR),
    "return_to_draft": (frozenset({S.READY_FOR_REVIEW}), S.DRAFT, MembershipRole.EDITOR),
    "approve": (frozenset({S.READY_FOR_REVIEW}), S.APPROVED, MembershipRole.ADMIN),
    "revoke_approval": (
        frozenset({S.APPROVED, S.SCHEDULED, S.READY_TO_PUBLISH}),
        S.DRAFT,
        MembershipRole.ADMIN,
    ),
    "schedule": (frozenset({S.APPROVED, S.SCHEDULED}), S.SCHEDULED, MembershipRole.EDITOR),
    "unschedule": (frozenset({S.SCHEDULED, S.READY_TO_PUBLISH}), S.APPROVED, MembershipRole.EDITOR),
    "release": (frozenset({S.SCHEDULED}), S.READY_TO_PUBLISH, MembershipRole.EDITOR),
    "start": (
        frozenset({S.APPROVED, S.SCHEDULED, S.READY_TO_PUBLISH}),
        S.PUBLISHING,
        MembershipRole.EDITOR,
    ),
    "confirm": (frozenset({S.PUBLISHING}), S.PUBLISHED, MembershipRole.EDITOR),
    "fail": (frozenset({S.PUBLISHING}), S.FAILED, MembershipRole.EDITOR),
    "report_uncertain": (frozenset({S.PUBLISHING}), S.NEEDS_RECONCILIATION, MembershipRole.EDITOR),
    "retry": (frozenset({S.FAILED}), S.READY_TO_PUBLISH, MembershipRole.EDITOR),
    "revise": (frozenset({S.FAILED}), S.DRAFT, MembershipRole.EDITOR),
    "reconcile": (
        frozenset({S.NEEDS_RECONCILIATION}),
        S.NEEDS_RECONCILIATION,  # actual target depends on the outcome
        MembershipRole.ADMIN,
    ),
    "revalidate": (
        frozenset({S.APPROVED, S.SCHEDULED, S.READY_TO_PUBLISH, S.FAILED}),
        S.APPROVED,  # actual target = current status (no change)
        MembershipRole.ADMIN,
    ),
    "cancel": (
        frozenset(
            {S.DRAFT, S.READY_FOR_REVIEW, S.APPROVED, S.SCHEDULED, S.READY_TO_PUBLISH, S.FAILED}
        ),
        S.CANCELLED,
        MembershipRole.EDITOR,
    ),
}

EDITABLE = frozenset({S.DRAFT, S.READY_FOR_REVIEW, S.APPROVED, S.SCHEDULED, S.READY_TO_PUBLISH})
# Content edits in these states invalidate the approval (back to DRAFT).
APPROVAL_STATES = frozenset({S.APPROVED, S.SCHEDULED, S.READY_TO_PUBLISH})
TERMINAL = frozenset({S.PUBLISHED, S.CANCELLED})
QUEUE_STATES = (
    S.READY_TO_PUBLISH,
    S.PUBLISHING,
    S.NEEDS_RECONCILIATION,
    S.SCHEDULED,
    S.APPROVED,
    S.FAILED,
)
ATTENTION_STATES = frozenset({S.FAILED, S.NEEDS_RECONCILIATION})


def assert_transition(action: str, current: PublicationStatus) -> PublicationStatus:
    try:
        sources, target, _ = ACTIONS[action]
    except KeyError as exc:
        raise InvalidPublicationTransitionError(f"Unknown action '{action}'") from exc
    if current not in sources:
        raise InvalidPublicationTransitionError(
            f"Cannot {action.replace('_', ' ')} a publication in status '{current.value}'",
            details={
                "action": action,
                "status": current.value,
                "allowed_from": sorted(s.value for s in sources),
            },
        )
    return target


def allowed_actions(current: PublicationStatus, role: MembershipRole) -> list[str]:
    from affilio.modules.identity.service import role_at_least

    return [
        action
        for action, (sources, _, minimum) in ACTIONS.items()
        if current in sources and role_at_least(role, minimum)
    ]
