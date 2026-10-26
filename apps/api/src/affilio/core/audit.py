"""Audit trail helper. Every security-relevant or state-changing action records who did what."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy.orm import Session

from affilio.core.enums import AuditActorKind
from affilio.modules.identity.models import AuditEvent

if TYPE_CHECKING:
    from affilio.api.deps import Identity


def record_audit(
    session: Session,
    *,
    organization_id: uuid.UUID | None,
    identity: Identity | None,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None = None,
    data: dict[str, Any] | None = None,
) -> AuditEvent:
    request_id = structlog.contextvars.get_contextvars().get("request_id")
    if identity is None:
        actor_kind, actor_user_id, actor_label = AuditActorKind.SYSTEM, None, "system"
    elif identity.user is not None:
        actor_kind, actor_user_id, actor_label = (
            AuditActorKind.USER,
            identity.user.id,
            identity.label,
        )
    else:
        actor_kind, actor_user_id, actor_label = AuditActorKind.SERVICE, None, identity.label
    event = AuditEvent(
        id=uuid.uuid4(),
        organization_id=organization_id,
        actor_kind=actor_kind,
        actor_user_id=actor_user_id,
        actor_label=actor_label[:255],
        action=action[:80],
        entity_type=entity_type[:60],
        entity_id=entity_id,
        data=data or {},
        request_id=str(request_id)[:128] if request_id else None,
    )
    session.add(event)
    return event
