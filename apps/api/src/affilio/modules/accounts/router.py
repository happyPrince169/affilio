"""Social account registry routes (M3A). Account management requires the admin role."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from affilio.api.deps import CurrentIdentity, DbSession, RequireAdmin
from affilio.core.audit import record_audit
from affilio.core.enums import Platform
from affilio.modules.accounts import service
from affilio.modules.accounts.schemas import (
    ChannelProfileResponse,
    ChannelProfileUpdate,
    SocialAccountCreate,
    SocialAccountResponse,
    SocialAccountStatusChange,
    SocialAccountUpdate,
)

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("", response_model=list[SocialAccountResponse], summary="Registered social accounts")
def list_accounts(
    identity: CurrentIdentity,
    session: DbSession,
    platform: Platform | None = None,
    include_disabled: Annotated[bool, Query()] = True,
) -> list[SocialAccountResponse]:
    return [
        service.to_response(a)
        for a in service.list_accounts(
            session, identity.organization.id, platform=platform, include_disabled=include_disabled
        )
    ]


@router.post(
    "",
    response_model=SocialAccountResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a Page manually (admin). Unverified until OAuth in M3B.",
)
def register(
    body: SocialAccountCreate, identity: RequireAdmin, session: DbSession
) -> SocialAccountResponse:
    account = service.register_manual(session, identity.organization.id, body, actor=identity.label)
    record_audit(
        session,
        organization_id=identity.organization.id,
        identity=identity,
        action="account.registered",
        entity_type="social_account",
        entity_id=account.id,
        data={
            "platform": body.platform.value,
            "display_name": body.display_name,
            "source": "manual",
        },
    )
    session.commit()
    return service.to_response(account)


@router.get("/{account_id}", response_model=SocialAccountResponse, summary="Account detail")
def read(
    account_id: uuid.UUID, identity: CurrentIdentity, session: DbSession
) -> SocialAccountResponse:
    return service.to_response(service.get_account(session, identity.organization.id, account_id))


@router.patch(
    "/{account_id}", response_model=SocialAccountResponse, summary="Edit account fields (admin)"
)
def update(
    account_id: uuid.UUID, body: SocialAccountUpdate, identity: RequireAdmin, session: DbSession
) -> SocialAccountResponse:
    account = service.update_account(session, identity.organization.id, account_id, body)
    record_audit(
        session,
        organization_id=identity.organization.id,
        identity=identity,
        action="account.updated",
        entity_type="social_account",
        entity_id=account.id,
        data=body.model_dump(exclude_unset=True),
    )
    session.commit()
    return service.to_response(account)


@router.post(
    "/{account_id}/status",
    response_model=SocialAccountResponse,
    summary="Disable / re-enable (admin)",
)
def change_status(
    account_id: uuid.UUID,
    body: SocialAccountStatusChange,
    identity: RequireAdmin,
    session: DbSession,
) -> SocialAccountResponse:
    account = service.change_status(session, identity.organization.id, account_id, body)
    record_audit(
        session,
        organization_id=identity.organization.id,
        identity=identity,
        action="account.status_changed",
        entity_type="social_account",
        entity_id=account.id,
        data={"status": body.status, "note": body.note},
    )
    session.commit()
    return service.to_response(account)


# ---------------------------------------------------------------- channel routing (M3A.1)


@router.get(
    "/{account_id}/channel-profile",
    response_model=ChannelProfileResponse,
    summary="Routing profile and category rules",
)
def read_channel_profile(
    account_id: uuid.UUID, identity: CurrentIdentity, session: DbSession
) -> ChannelProfileResponse:
    account, tree = service.get_channel_profile(session, identity.organization.id, account_id)
    return service.profile_response(account, tree)


@router.put(
    "/{account_id}/channel-profile",
    response_model=ChannelProfileResponse,
    summary="Replace routing profile and category rules (admin)",
)
def replace_channel_profile(
    account_id: uuid.UUID, body: ChannelProfileUpdate, identity: RequireAdmin, session: DbSession
) -> ChannelProfileResponse:
    account, tree = service.replace_channel_profile(
        session, identity.organization.id, account_id, body
    )
    record_audit(
        session,
        organization_id=identity.organization.id,
        identity=identity,
        action="account.channel_profile_replaced",
        entity_type="social_account",
        entity_id=account.id,
        data={
            "primary_category_id": str(body.primary_category_id)
            if body.primary_category_id
            else None,
            "routing_enabled": body.routing_enabled,
            "rules": [
                {
                    "category_id": str(r.category_id),
                    "match_policy": r.match_policy.value,
                    "priority": r.priority,
                }
                for r in body.rules
            ],
        },
    )
    session.commit()
    return service.profile_response(account, tree)


@router.get(
    "/{account_id}/category-rules",
    response_model=ChannelProfileResponse,
    summary="Alias of channel-profile (rules are part of the profile)",
)
def read_category_rules(
    account_id: uuid.UUID, identity: CurrentIdentity, session: DbSession
) -> ChannelProfileResponse:
    account, tree = service.get_channel_profile(session, identity.organization.id, account_id)
    return service.profile_response(account, tree)
