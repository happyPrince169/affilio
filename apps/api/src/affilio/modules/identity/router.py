"""Authentication, current user, organization members, audit log."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response, status

from affilio.api.deps import (
    AuthenticationRequiredError,
    CurrentIdentity,
    DbSession,
    Identity,
    RequireAdmin,
    SettingsDep,
)
from affilio.core.audit import record_audit
from affilio.core.config import Settings
from affilio.core.errors import NotFoundError
from affilio.modules.identity import service
from affilio.modules.identity.models import OrganizationMembership
from affilio.modules.identity.schemas import (
    AuditEventResponse,
    ChangePasswordRequest,
    CurrentUserResponse,
    LoginRequest,
    MemberCreate,
    MemberResponse,
    MemberRoleUpdate,
    MembershipResponse,
    OrganizationUpdate,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _membership_response(m: OrganizationMembership) -> MembershipResponse:
    return MembershipResponse(
        organization_id=m.organization_id,
        organization_slug=m.organization.slug,
        organization_name=m.organization.name,
        timezone=m.organization.timezone,
        role=m.role,
    )


def _me(identity: Identity) -> CurrentUserResponse:
    if identity.user is None:
        org = identity.organization
        current = MembershipResponse(
            organization_id=org.id,
            organization_slug=org.slug,
            organization_name=org.name,
            timezone=org.timezone,
            role=identity.role,
        )
        return CurrentUserResponse(
            id=uuid.UUID(int=0),
            email="service@affilio.local",
            display_name="Service token",
            status="active",  # type: ignore[arg-type]
            identity_kind="service",
            organization=current,
            memberships=[current],
            permissions=identity.permissions(),
        )
    memberships = [_membership_response(m) for m in service.memberships_for(identity.user)]
    current = next(m for m in memberships if m.organization_id == identity.organization.id)
    return CurrentUserResponse(
        id=identity.user.id,
        email=identity.user.email,
        display_name=identity.user.display_name,
        status=identity.user.status,
        identity_kind="user",
        organization=current,
        memberships=memberships,
        permissions=identity.permissions(),
        session_expires_at=identity.session.expires_at if identity.session else None,
    )


def _set_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_ttl_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        domain=settings.session_cookie_domain,
        path="/",
    )


def _clear_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        domain=settings.session_cookie_domain,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


@router.post("/login", response_model=CurrentUserResponse, summary="Sign in with email + password")
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
) -> CurrentUserResponse:
    user = service.authenticate(session, body.email, body.password)
    membership = service.membership_for(user, body.organization_slug)
    if membership is None:
        raise AuthenticationRequiredError("This account has no organization membership")
    user_session, token = service.create_session(
        session,
        user,
        ttl=timedelta(days=settings.session_ttl_days),
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    identity = Identity(
        kind="user",
        organization=membership.organization,
        role=membership.role,
        user=user,
        session=user_session,
    )
    record_audit(
        session,
        organization_id=membership.organization_id,
        identity=identity,
        action="auth.login",
        entity_type="user",
        entity_id=user.id,
    )
    session.commit()
    _set_cookie(response, settings, token)
    return _me(identity)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Sign out")
def logout(
    request: Request, response: Response, session: DbSession, settings: SettingsDep
) -> Response:
    raw = request.cookies.get(settings.session_cookie_name)
    if raw:
        resolved = service.resolve_session(
            session, raw, ttl=timedelta(days=settings.session_ttl_days)
        )
        if resolved is not None:
            user_session, user = resolved
            service.revoke_session(user_session)
            record_audit(
                session,
                organization_id=None,
                identity=None,
                action="auth.logout",
                entity_type="user",
                entity_id=user.id,
            )
            session.commit()
    _clear_cookie(response, settings)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=CurrentUserResponse, summary="Current identity")
def me(identity: CurrentIdentity) -> CurrentUserResponse:
    return _me(identity)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT, summary="Change own password")
def change_password(
    body: ChangePasswordRequest, identity: CurrentIdentity, session: DbSession, response: Response
) -> Response:
    if identity.user is None:
        raise AuthenticationRequiredError("Service identities have no password")
    service.authenticate(session, identity.user.email, body.current_password)
    service.set_password(identity.user, body.new_password)
    # Other sessions are revoked; the current one stays valid.
    for record in identity.user.sessions:
        if (
            identity.session is not None
            and record.id != identity.session.id
            and record.revoked_at is None
        ):
            service.revoke_session(record)
    record_audit(
        session,
        organization_id=identity.organization.id,
        identity=identity,
        action="auth.password_changed",
        entity_type="user",
        entity_id=identity.user.id,
    )
    session.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/members", response_model=list[MemberResponse], summary="Organization members")
def list_members(identity: CurrentIdentity, session: DbSession) -> list[MemberResponse]:
    return [
        MemberResponse(
            user_id=m.user_id,
            email=m.user.email,
            display_name=m.user.display_name,
            status=m.user.status,
            role=m.role,
            last_login_at=m.user.last_login_at,
            created_at=m.created_at,
        )
        for m in service.list_members(session, identity.organization.id)
    ]


@router.post(
    "/members",
    response_model=MemberResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a member (admin)",
)
def create_member(body: MemberCreate, identity: RequireAdmin, session: DbSession) -> MemberResponse:
    user = service.create_user(
        session, email=body.email, password=body.password, display_name=body.display_name
    )
    membership = service.add_membership(session, identity.organization, user, body.role)
    record_audit(
        session,
        organization_id=identity.organization.id,
        identity=identity,
        action="member.created",
        entity_type="user",
        entity_id=user.id,
        data={"role": body.role.value},
    )
    session.commit()
    return MemberResponse(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        status=user.status,
        role=membership.role,
        last_login_at=user.last_login_at,
        created_at=membership.created_at,
    )


@router.patch(
    "/members/{user_id}", response_model=MemberResponse, summary="Change a member's role (admin)"
)
def update_member(
    user_id: uuid.UUID, body: MemberRoleUpdate, identity: RequireAdmin, session: DbSession
) -> MemberResponse:
    membership = service.get_member(session, identity.organization.id, user_id)
    if membership.role == "owner":
        raise NotFoundError("Owner role cannot be changed here")
    membership.role = body.role
    record_audit(
        session,
        organization_id=identity.organization.id,
        identity=identity,
        action="member.role_changed",
        entity_type="user",
        entity_id=user_id,
        data={"role": body.role.value},
    )
    session.commit()
    return MemberResponse(
        user_id=membership.user_id,
        email=membership.user.email,
        display_name=membership.user.display_name,
        status=membership.user.status,
        role=membership.role,
        last_login_at=membership.user.last_login_at,
        created_at=membership.created_at,
    )


@router.patch(
    "/organization", response_model=MembershipResponse, summary="Update organization (admin)"
)
def update_organization(
    body: OrganizationUpdate, identity: RequireAdmin, session: DbSession
) -> MembershipResponse:
    service.update_organization(identity.organization, name=body.name, timezone=body.timezone)
    record_audit(
        session,
        organization_id=identity.organization.id,
        identity=identity,
        action="organization.updated",
        entity_type="organization",
        entity_id=identity.organization.id,
        data=body.model_dump(exclude_unset=True),
    )
    session.commit()
    org = identity.organization
    return MembershipResponse(
        organization_id=org.id,
        organization_slug=org.slug,
        organization_name=org.name,
        timezone=org.timezone,
        role=identity.role,
    )


@router.get("/audit", response_model=list[AuditEventResponse], summary="Audit log (admin)")
def audit_log(
    identity: RequireAdmin,
    session: DbSession,
    entity_type: Annotated[str | None, Query(max_length=60)] = None,
    entity_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[AuditEventResponse]:
    return [
        AuditEventResponse.model_validate(e)
        for e in service.list_audit(
            session,
            identity.organization.id,
            entity_type=entity_type,
            entity_id=entity_id,
            limit=limit,
        )
    ]
