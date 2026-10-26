"""Content category routes (M3A.1). Taxonomy changes require the admin role."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from affilio.api.deps import CurrentIdentity, DbSession, RequireAdmin
from affilio.core.audit import record_audit
from affilio.modules.taxonomy import service
from affilio.modules.taxonomy.schemas import CategoryCreate, CategoryResponse, CategoryUpdate

router = APIRouter(prefix="/content-categories", tags=["taxonomy"])


@router.get("", response_model=list[CategoryResponse], summary="Category tree (depth-first)")
def list_categories(
    identity: CurrentIdentity,
    session: DbSession,
    include_archived: Annotated[bool, Query()] = False,
) -> list[CategoryResponse]:
    return service.list_categories(
        session, identity.organization.id, include_archived=include_archived
    )


@router.post(
    "",
    response_model=CategoryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a category (admin)",
)
def create_category(
    body: CategoryCreate, identity: RequireAdmin, session: DbSession
) -> CategoryResponse:
    category = service.create_category(session, identity.organization.id, body)
    record_audit(
        session,
        organization_id=identity.organization.id,
        identity=identity,
        action="category.created",
        entity_type="content_category",
        entity_id=category.id,
        data={
            "name": category.name,
            "slug": category.slug,
            "parent_id": str(category.parent_id) if category.parent_id else None,
        },
    )
    session.commit()
    return service.to_response(service.load_tree(session, identity.organization.id), category)


@router.post(
    "/ensure-uncategorized",
    response_model=CategoryResponse,
    summary="Create the Uncategorized fallback if missing (admin)",
)
def ensure_uncategorized(identity: RequireAdmin, session: DbSession) -> CategoryResponse:
    category = service.ensure_uncategorized(session, identity.organization.id)
    session.commit()
    return service.to_response(service.load_tree(session, identity.organization.id), category)


@router.get("/{category_id}", response_model=CategoryResponse, summary="Category detail")
def read_category(
    category_id: uuid.UUID, identity: CurrentIdentity, session: DbSession
) -> CategoryResponse:
    category = service.get_category(session, identity.organization.id, category_id)
    return service.to_response(service.load_tree(session, identity.organization.id), category)


@router.patch(
    "/{category_id}",
    response_model=CategoryResponse,
    summary="Edit / move / archive a category (admin)",
)
def update_category(
    category_id: uuid.UUID, body: CategoryUpdate, identity: RequireAdmin, session: DbSession
) -> CategoryResponse:
    category = service.update_category(session, identity.organization.id, category_id, body)
    record_audit(
        session,
        organization_id=identity.organization.id,
        identity=identity,
        action="category.updated",
        entity_type="content_category",
        entity_id=category.id,
        data=body.model_dump(exclude_unset=True, mode="json"),
    )
    session.commit()
    return service.to_response(service.load_tree(session, identity.organization.id), category)


@router.delete(
    "/{category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an unreferenced category (admin)",
)
def delete_category(
    category_id: uuid.UUID, identity: RequireAdmin, session: DbSession, response: Response
) -> Response:
    service.delete_category(session, identity.organization.id, category_id)
    record_audit(
        session,
        organization_id=identity.organization.id,
        identity=identity,
        action="category.deleted",
        entity_type="content_category",
        entity_id=category_id,
    )
    session.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
