"""Editor → production-owner mappings (upload track U2, ADR 0012).

A mapping authorizes a member to submit finished videos on behalf of one production owner
(a value under the organization's `production_owner` facet root). A member may hold several
active mappings. Revoking keeps the row (status `revoked`) so past attribution stays
explainable; re-assigning the same owner reactivates that row instead of creating another.
The owner category cannot be deleted while a mapping references it (taxonomy reference
registry); archiving the category keeps the mapping but removes the owner from the member's
submittable set until the category is active again.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from affilio.core.enums import CategoryFacet, CategoryStatus, OwnerMappingStatus
from affilio.core.errors import NotFoundError
from affilio.modules.identity import service as identity_service
from affilio.modules.media.errors import OwnerMappingConflictError, OwnerMappingInvalidCategoryError
from affilio.modules.media.models import MediaOwnerMapping
from affilio.modules.taxonomy import service as taxonomy_service
from affilio.modules.taxonomy.models import ContentCategory


@dataclass(slots=True)
class MappingChange:
    mapping: MediaOwnerMapping
    reactivated: bool


def _stmt(organization_id: uuid.UUID):
    return (
        select(MediaOwnerMapping)
        .options(selectinload(MediaOwnerMapping.user), selectinload(MediaOwnerMapping.category))
        .where(MediaOwnerMapping.organization_id == organization_id)
    )


def list_mappings(
    session: Session,
    organization_id: uuid.UUID,
    *,
    user_id: uuid.UUID | None = None,
    include_revoked: bool = False,
) -> list[MediaOwnerMapping]:
    stmt = _stmt(organization_id)
    if user_id is not None:
        stmt = stmt.where(MediaOwnerMapping.user_id == user_id)
    if not include_revoked:
        stmt = stmt.where(MediaOwnerMapping.status == OwnerMappingStatus.ACTIVE)
    stmt = stmt.order_by(MediaOwnerMapping.created_at, MediaOwnerMapping.id)
    return list(session.scalars(stmt))


def get_mapping(
    session: Session, organization_id: uuid.UUID, mapping_id: uuid.UUID
) -> MediaOwnerMapping:
    mapping = session.scalar(_stmt(organization_id).where(MediaOwnerMapping.id == mapping_id))
    if mapping is None:
        raise NotFoundError("Owner mapping not found")
    return mapping


def submittable_owners(
    session: Session, organization_id: uuid.UUID, user_id: uuid.UUID
) -> list[ContentCategory]:
    """Active owner categories the member may submit for: active mapping and active category
    that still sits under the production-owner root. Archived owners drop out until restored."""
    out: list[ContentCategory] = []
    for mapping in list_mappings(session, organization_id, user_id=user_id):
        category = mapping.category
        if (
            category.status == CategoryStatus.ACTIVE
            and category.facet == CategoryFacet.PRODUCTION_OWNER
            and category.parent_id is not None
        ):
            out.append(category)
    out.sort(key=lambda c: c.name.lower())
    return out


def validate_owner_category(
    session: Session, organization_id: uuid.UUID, category_id: uuid.UUID
) -> ContentCategory:
    """The category must be an active value under this organization's production-owner root.
    Other organizations' ids are a 404 (never confirmed), wrong facet, root or archived → 422."""
    category = taxonomy_service.get_category(session, organization_id, category_id)
    if category.facet != CategoryFacet.PRODUCTION_OWNER or category.parent_id is None:
        raise OwnerMappingInvalidCategoryError(
            "The category is not a production owner value",
            details={"category_id": str(category_id), "facet": category.facet},
        )
    if category.status != CategoryStatus.ACTIVE:
        raise OwnerMappingInvalidCategoryError(
            "An archived production owner cannot be assigned",
            details={"category_id": str(category_id), "status": category.status.value},
        )
    return category


def create_mapping(
    session: Session,
    organization_id: uuid.UUID,
    *,
    user_id: uuid.UUID,
    category_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
) -> MappingChange:
    identity_service.get_member(session, organization_id, user_id)  # 404 unless a member
    category = validate_owner_category(session, organization_id, category_id)
    existing = session.scalar(
        _stmt(organization_id).where(
            MediaOwnerMapping.user_id == user_id,
            MediaOwnerMapping.production_owner_category_id == category.id,
        )
    )
    if existing is not None:
        if existing.status == OwnerMappingStatus.ACTIVE:
            raise OwnerMappingConflictError(
                "This member is already mapped to that production owner",
                details={"mapping_id": str(existing.id)},
            )
        existing.status = OwnerMappingStatus.ACTIVE
        existing.revoked_at = None
        existing.revoked_by_user_id = None
        existing.created_by_user_id = actor_user_id
        session.flush()
        return MappingChange(mapping=existing, reactivated=True)
    mapping = MediaOwnerMapping(
        id=uuid.uuid4(),
        organization_id=organization_id,
        user_id=user_id,
        production_owner_category_id=category.id,
        status=OwnerMappingStatus.ACTIVE,
        created_by_user_id=actor_user_id,
    )
    session.add(mapping)
    session.flush()
    return MappingChange(
        mapping=get_mapping(session, organization_id, mapping.id), reactivated=False
    )


def revoke_mapping(
    session: Session,
    organization_id: uuid.UUID,
    mapping_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID | None,
    now: datetime | None = None,
) -> tuple[MediaOwnerMapping, bool]:
    """Returns (mapping, changed). Revoking twice is a no-op."""
    mapping = get_mapping(session, organization_id, mapping_id)
    if mapping.status == OwnerMappingStatus.REVOKED:
        return mapping, False
    mapping.status = OwnerMappingStatus.REVOKED
    mapping.revoked_at = now or datetime.now(UTC)
    mapping.revoked_by_user_id = actor_user_id
    session.flush()
    return mapping, True


__all__ = [
    "MappingChange",
    "create_mapping",
    "get_mapping",
    "list_mappings",
    "revoke_mapping",
    "submittable_owners",
    "validate_owner_category",
]
