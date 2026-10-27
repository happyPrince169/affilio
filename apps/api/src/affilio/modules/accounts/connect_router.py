"""Facebook Page connection routes (M3B.1A). Included by `accounts.router` *before* the
`/{account_id}` routes so the static paths are never captured by the UUID parameter."""

from __future__ import annotations

import uuid
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Query, status
from fastapi.responses import RedirectResponse

from affilio.api.deps import CurrentIdentity, DbSession, RequireAdmin, SettingsDep
from affilio.core.audit import record_audit
from affilio.core.errors import AppError
from affilio.modules.accounts import connections, service
from affilio.modules.accounts.schemas import (
    ConnectionConfigResponse,
    ConnectStartRequest,
    ConnectStartResponse,
    CredentialSummary,
    DisconnectRequest,
    DiscoveredPageResponse,
    LinkPagesRequest,
    MockActionRequest,
    MockActionResponse,
    OAuthAttemptResponse,
    PlatformConnectionResponse,
    SocialAccountResponse,
)

router = APIRouter(tags=["accounts:connections"])


def _web(settings, path: str) -> str:
    base = (settings.web_base_url or "").rstrip("/")
    return f"{base}{path}"


# ------------------------------------------------------------------ configuration & OAuth


@router.get(
    "/connect/facebook/config",
    response_model=ConnectionConfigResponse,
    summary="Is Facebook connection available? (names of missing settings only)",
)
def connect_config(identity: CurrentIdentity, settings: SettingsDep) -> ConnectionConfigResponse:
    return connections.config_response(settings)


@router.post(
    "/connect/facebook",
    response_model=ConnectStartResponse,
    summary="Start the Facebook authorization (admin). Returns the URL to navigate to.",
)
def connect_start(
    body: ConnectStartRequest, identity: RequireAdmin, session: DbSession, settings: SettingsDep
) -> ConnectStartResponse:
    attempt, url = connections.start_oauth(
        session, settings, identity, mock_scenario=body.mock_scenario
    )
    session.commit()
    return ConnectStartResponse(
        authorize_url=url,
        state_expires_at=attempt.expires_at,
        mode=settings.meta_provider_mode,
        mock_scenario=attempt.mock_scenario,
    )


@router.get(
    "/connect/facebook/callback",
    summary="OAuth redirect target. Answers with a redirect to the web app only.",
    response_class=RedirectResponse,
    status_code=status.HTTP_303_SEE_OTHER,
)
def connect_callback(
    identity: RequireAdmin,
    session: DbSession,
    settings: SettingsDep,
    state: Annotated[str | None, Query(max_length=256)] = None,
    code: Annotated[str | None, Query(max_length=2048)] = None,
    error: Annotated[str | None, Query(max_length=128)] = None,
    error_reason: Annotated[str | None, Query(max_length=128)] = None,
) -> RedirectResponse:
    try:
        attempt = connections.complete_oauth(
            session,
            settings,
            identity,
            state=state,
            code=code,
            error=error,
            error_reason=error_reason,
        )
    except AppError as exc:
        session.commit()  # the attempt row (failed/expired) is kept for the audit trail
        query = urlencode({"connect_error": exc.code})
        return RedirectResponse(
            _web(settings, f"/accounts?{query}"), status_code=status.HTTP_303_SEE_OTHER
        )
    session.commit()
    if attempt.connection_id is None:
        query = urlencode({"connect_error": attempt.status.value})
        return RedirectResponse(
            _web(settings, f"/accounts?{query}"), status_code=status.HTTP_303_SEE_OTHER
        )
    return RedirectResponse(
        _web(settings, f"/accounts/connect/facebook/{attempt.connection_id}"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get(
    "/connect/facebook/attempts",
    response_model=list[OAuthAttemptResponse],
    summary="Recent authorization attempts (admin)",
)
def connect_attempts(
    identity: RequireAdmin, session: DbSession, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[OAuthAttemptResponse]:
    return [
        OAuthAttemptResponse.model_validate(a)
        for a in connections.oauth_attempts(session, identity.organization.id, limit=limit)
    ]


@router.post(
    "/connect/facebook/mock/actions",
    response_model=MockActionResponse,
    summary="Mock provider switches (admin; 404 unless META_PROVIDER_MODE=mock)",
)
def mock_actions(
    body: MockActionRequest, identity: RequireAdmin, session: DbSession, settings: SettingsDep
) -> MockActionResponse:
    result = connections.mock_action(session, settings, identity.organization.id, body)
    record_audit(
        session,
        organization_id=identity.organization.id,
        identity=identity,
        action="mock.action",
        entity_type="platform_connection",
        entity_id=body.connection_id,
        data={
            "action": body.action,
            "account_id": str(body.account_id) if body.account_id else None,
        },
    )
    session.commit()
    return result


# ------------------------------------------------------------------ connections


@router.get(
    "/connections",
    response_model=list[PlatformConnectionResponse],
    summary="Provider connections (user grants) of this organization",
)
def list_connections(
    identity: CurrentIdentity, session: DbSession
) -> list[PlatformConnectionResponse]:
    return [
        connections.connection_response(c)
        for c in connections.list_connections(session, identity.organization.id)
    ]


@router.get(
    "/connections/{connection_id}",
    response_model=PlatformConnectionResponse,
    summary="Connection detail",
)
def read_connection(
    connection_id: uuid.UUID, identity: CurrentIdentity, session: DbSession
) -> PlatformConnectionResponse:
    return connections.connection_response(
        connections.get_connection(session, identity.organization.id, connection_id)
    )


@router.get(
    "/connections/{connection_id}/pages",
    response_model=list[DiscoveredPageResponse],
    summary="Pages the provider listed for this connection (no tokens)",
)
def read_connection_pages(
    connection_id: uuid.UUID, identity: CurrentIdentity, session: DbSession
) -> list[DiscoveredPageResponse]:
    connection = connections.get_connection(session, identity.organization.id, connection_id)
    return connections.discovered_pages(session, connection)


@router.post(
    "/connections/{connection_id}/pages",
    response_model=list[SocialAccountResponse],
    summary="Link selected Pages as connected accounts (admin)",
)
def link_connection_pages(
    connection_id: uuid.UUID, body: LinkPagesRequest, identity: RequireAdmin, session: DbSession
) -> list[SocialAccountResponse]:
    connection = connections.get_connection(session, identity.organization.id, connection_id)
    linked = connections.link_pages(
        session, connection, body.page_ids, actor=identity.label, identity=identity
    )
    session.commit()
    return [service.to_response(a) for a in linked]


@router.post(
    "/connections/{connection_id}/verify",
    response_model=PlatformConnectionResponse,
    summary="Re-check the user grant with the provider (admin)",
)
def verify_connection(
    connection_id: uuid.UUID, identity: RequireAdmin, session: DbSession, settings: SettingsDep
) -> PlatformConnectionResponse:
    connection = connections.get_connection(session, identity.organization.id, connection_id)
    connections.verify_connection(session, settings, connection, identity=identity)
    session.commit()
    return connections.connection_response(connection)


@router.post(
    "/connections/{connection_id}/revoke",
    response_model=PlatformConnectionResponse,
    summary="Revoke the grant at the provider and disconnect every Page (admin)",
)
def revoke_connection(
    connection_id: uuid.UUID, identity: RequireAdmin, session: DbSession, settings: SettingsDep
) -> PlatformConnectionResponse:
    connection = connections.get_connection(session, identity.organization.id, connection_id)
    connections.revoke_connection(session, settings, connection, identity=identity)
    session.commit()
    return connections.connection_response(connection)


# ------------------------------------------------------------------ per-account


@router.post(
    "/{account_id}/disconnect",
    response_model=SocialAccountResponse,
    summary="Disconnect a Page: revoke its stored token locally (admin)",
)
def disconnect_account(
    account_id: uuid.UUID, body: DisconnectRequest, identity: RequireAdmin, session: DbSession
) -> SocialAccountResponse:
    account = service.get_account(session, identity.organization.id, account_id)
    connections.disconnect_account(session, account, reason=body.reason, identity=identity)
    session.commit()
    return service.to_response(account)


@router.post(
    "/{account_id}/verify",
    response_model=SocialAccountResponse,
    summary="Check the Page token with the provider; disconnects the Page if invalid (admin)",
)
def verify_account(
    account_id: uuid.UUID, identity: RequireAdmin, session: DbSession, settings: SettingsDep
) -> SocialAccountResponse:
    account = service.get_account(session, identity.organization.id, account_id)
    connections.verify_account(session, settings, account, identity=identity)
    session.commit()
    return service.to_response(account)


@router.get(
    "/{account_id}/credentials",
    response_model=list[CredentialSummary],
    summary="Stored credential metadata for a Page (admin). Never the token.",
)
def account_credentials(
    account_id: uuid.UUID, identity: RequireAdmin, session: DbSession
) -> list[CredentialSummary]:
    account = service.get_account(session, identity.organization.id, account_id)
    return connections.credential_summaries(session, account)
