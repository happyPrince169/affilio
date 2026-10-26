"""Affiliate link library routes (M3A)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from affilio.api.deps import CurrentIdentity, DbSession, RequireEditor
from affilio.core.audit import record_audit
from affilio.core.enums import AffiliateLinkStatus, Platform
from affilio.modules.content import service
from affilio.modules.content.schemas import (
    AffiliateLinkCreate,
    AffiliateLinkResponse,
    AffiliateLinkUpdate,
)

router = APIRouter(prefix="/content", tags=["content"])


@router.get("/links", response_model=list[AffiliateLinkResponse], summary="Affiliate link library")
def list_links(
    identity: CurrentIdentity,
    session: DbSession,
    status_filter: Annotated[
        AffiliateLinkStatus | None, Query(alias="status")
    ] = AffiliateLinkStatus.ACTIVE,
    platform: Platform | None = None,
    product_id: uuid.UUID | None = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
) -> list[AffiliateLinkResponse]:
    return [
        service.to_response(link)
        for link in service.list_links(
            session,
            identity.organization.id,
            status=status_filter,
            platform=platform,
            product_id=product_id,
            q=q,
        )
    ]


@router.post(
    "/links",
    response_model=AffiliateLinkResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a link (editor)",
)
def create_link(
    body: AffiliateLinkCreate, identity: RequireEditor, session: DbSession
) -> AffiliateLinkResponse:
    link = service.create_link(session, identity.organization.id, body, actor=identity.label)
    record_audit(
        session,
        organization_id=identity.organization.id,
        identity=identity,
        action="affiliate_link.created",
        entity_type="affiliate_link",
        entity_id=link.id,
        data={"label": body.label, "url": body.url},
    )
    session.commit()
    return service.to_response(link)


@router.get("/links/{link_id}", response_model=AffiliateLinkResponse, summary="Link detail")
def read_link(
    link_id: uuid.UUID, identity: CurrentIdentity, session: DbSession
) -> AffiliateLinkResponse:
    return service.to_response(service.get_link(session, identity.organization.id, link_id))


@router.patch(
    "/links/{link_id}",
    response_model=AffiliateLinkResponse,
    summary="Edit / archive a link (editor)",
)
def update_link(
    link_id: uuid.UUID, body: AffiliateLinkUpdate, identity: RequireEditor, session: DbSession
) -> AffiliateLinkResponse:
    link = service.update_link(session, identity.organization.id, link_id, body)
    record_audit(
        session,
        organization_id=identity.organization.id,
        identity=identity,
        action="affiliate_link.updated",
        entity_type="affiliate_link",
        entity_id=link.id,
        data=body.model_dump(exclude_unset=True, mode="json"),
    )
    session.commit()
    return service.to_response(link)
