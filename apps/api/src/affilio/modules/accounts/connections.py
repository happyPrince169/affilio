"""Facebook Page connections: OAuth attempts, provider grants, Page linking, health (M3B.1A).

Everything here runs server-side. Plaintext tokens are decrypted by `vault.open_token` only to
be handed to a provider call in the same function; they are never returned, logged or audited.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from fastapi import status
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, selectinload

from affilio.core.audit import record_audit
from affilio.core.config import Settings
from affilio.core.enums import (
    ConnectionProvider,
    ConnectionStatus,
    CredentialKind,
    CredentialStatus,
    OAuthAttemptStatus,
    Platform,
    SocialAccountSource,
    SocialAccountStatus,
    SocialAccountType,
)
from affilio.core.errors import AppError, ConflictError, NotFoundError
from affilio.modules.accounts import vault
from affilio.modules.accounts.crypto import CredentialUndecryptableError, CredentialVaultError
from affilio.modules.accounts.models import (
    PUBLISH_TASK,
    AccountCredential,
    OAuthState,
    PlatformConnection,
    SocialAccount,
)
from affilio.modules.accounts.providers import get_provider
from affilio.modules.accounts.providers.base import (
    MetaProvider,
    PageInfo,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderUnavailableError,
    TokenInfo,
    TokenInvalidError,
)
from affilio.modules.accounts.schemas import (
    ConnectionConfigResponse,
    CredentialSummary,
    DiscoveredPageResponse,
    MockActionRequest,
    MockActionResponse,
    PlatformConnectionResponse,
)
from affilio.modules.accounts.service import PLATFORM_CAPABILITIES

log = structlog.get_logger("affilio.accounts.connections")

PROVIDER = ConnectionProvider.META
DEFAULT_REDIRECT_PATH = "/api/v1/accounts/connect/facebook/callback"


# ---------------------------------------------------------------------------- errors


class OAuthStateInvalidError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "oauth_state_invalid"


class MockModeDisabledError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "mock_mode_disabled"


class ConnectionInactiveError(ConflictError):
    code = "connection_inactive"


class PageNotDiscoveredError(NotFoundError):
    code = "page_not_discovered"


class PageCannotPublishError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "page_cannot_publish"


class AccountNotConnectedError(ConflictError):
    code = "account_not_connected"


# ---------------------------------------------------------------------------- helpers


def _now() -> datetime:
    return datetime.now(UTC)


def _hash_state(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _redirect_uri(settings: Settings) -> str:
    # Mock mode may run on plain http without a configured URI: the callback is relative to
    # the API origin and the browser follows it through the same-origin proxy.
    return settings.meta_oauth_redirect_uri or DEFAULT_REDIRECT_PATH


def _grant_expiry(expires_in: int | None) -> datetime | None:
    return _now() + timedelta(seconds=expires_in) if expires_in else None


# ---------------------------------------------------------------------------- configuration


def config_response(settings: Settings) -> ConnectionConfigResponse:
    vault_ready = True
    try:
        vault.ensure_vault(settings)
    except CredentialVaultError:
        vault_ready = False
    mock = settings.meta_provider_mode == "mock"
    missing = [] if mock else settings.meta_live_missing_settings
    if not vault_ready:
        missing = [*missing, "CREDENTIAL_ENCRYPTION_KEYS"]
    scenarios: list[str] = []
    if mock:
        from affilio.modules.accounts.providers.mock import SCENARIOS

        scenarios = list(SCENARIOS)
    return ConnectionConfigResponse(
        mode=settings.meta_provider_mode,
        configured=settings.is_meta_configured and vault_ready,
        vault_ready=vault_ready,
        missing=missing,
        scopes=list(settings.meta_oauth_scopes),
        mock_scenarios=scenarios,
        graph_api_version=settings.meta_graph_api_version,
    )


def _provider(settings: Settings) -> MetaProvider:
    return get_provider(settings)


# ---------------------------------------------------------------------------- OAuth start


def start_oauth(
    session: Session,
    settings: Settings,
    identity: Any,
    *,
    mock_scenario: str | None,
) -> tuple[OAuthState, str]:
    """Create a one-time state and return (row, authorize URL). Fails closed on config."""
    vault.ensure_vault(settings)  # 503 credential_vault_unavailable before anything else
    if mock_scenario is not None and settings.meta_provider_mode != "mock":
        raise MockModeDisabledError("Mock scenarios are only accepted in mock mode")
    provider = _provider(settings)  # 503 meta_not_configured in live mode without settings
    raw_state = secrets.token_urlsafe(32)
    redirect_uri = _redirect_uri(settings)
    scopes = list(settings.meta_oauth_scopes)
    attempt = OAuthState(
        id=uuid.uuid4(),
        organization_id=identity.organization.id,
        user_id=identity.user.id,
        session_id=identity.session.id if identity.session is not None else None,
        provider=PROVIDER,
        state_hash=_hash_state(raw_state),
        redirect_uri=redirect_uri,
        requested_scopes=scopes,
        mock_scenario=mock_scenario if provider.is_mock else None,
        status=OAuthAttemptStatus.PENDING,
        expires_at=_now() + timedelta(seconds=settings.meta_oauth_state_ttl_seconds),
    )
    session.add(attempt)
    session.flush()
    request = provider.authorize_url(
        state=raw_state, redirect_uri=redirect_uri, scopes=scopes, mock_scenario=mock_scenario
    )
    record_audit(
        session,
        organization_id=identity.organization.id,
        identity=identity,
        action="account.connect_started",
        entity_type="oauth_state",
        entity_id=attempt.id,
        data={"provider": PROVIDER.value, "mock": provider.is_mock, "scopes": scopes},
    )
    return attempt, request.url


# ---------------------------------------------------------------------------- OAuth callback


def _consume_state(session: Session, identity: Any, raw_state: str | None) -> OAuthState:
    """Validate and consume the state exactly once. Every failure is `oauth_state_invalid`."""
    if not raw_state or len(raw_state) > 256:
        raise OAuthStateInvalidError("Missing or malformed state", details={"reason": "missing"})
    attempt = session.scalar(
        select(OAuthState).where(OAuthState.state_hash == _hash_state(raw_state)).with_for_update()
    )
    if attempt is None or attempt.provider != PROVIDER:
        raise OAuthStateInvalidError("Unknown state", details={"reason": "unknown"})
    if attempt.organization_id != identity.organization.id or attempt.user_id != identity.user.id:
        # Do not consume: it belongs to someone else's attempt (or another organization).
        raise OAuthStateInvalidError(
            "This authorization was started by another user", details={"reason": "wrong_user"}
        )
    if attempt.consumed_at is not None:
        raise OAuthStateInvalidError(
            "This authorization was already used", details={"reason": "replayed"}
        )
    now = _now()
    if attempt.expires_at <= now:
        attempt.status = OAuthAttemptStatus.EXPIRED
        attempt.consumed_at = now
        session.flush()
        raise OAuthStateInvalidError(
            "This authorization expired; start again", details={"reason": "expired"}
        )
    consumed = session.execute(
        update(OAuthState)
        .where(OAuthState.id == attempt.id, OAuthState.consumed_at.is_(None))
        .values(consumed_at=now)
    )
    if consumed.rowcount != 1:
        raise OAuthStateInvalidError(
            "This authorization was already used", details={"reason": "replayed"}
        )
    session.refresh(attempt)
    return attempt


def _missing_scopes(settings: Settings, granted: list[str]) -> list[str]:
    return [scope for scope in settings.meta_oauth_scopes if scope not in granted]


def _upsert_connection(
    session: Session,
    *,
    identity: Any,
    provider: MetaProvider,
    info: TokenInfo,
    settings: Settings,
    pages: list[PageInfo],
    user_name: str | None,
) -> PlatformConnection:
    connection = session.scalar(
        select(PlatformConnection)
        .where(
            PlatformConnection.organization_id == identity.organization.id,
            PlatformConnection.provider == PROVIDER,
            PlatformConnection.external_user_id == info.user_id,
        )
        .with_for_update()
    )
    if connection is None:
        connection = PlatformConnection(
            id=uuid.uuid4(),
            organization_id=identity.organization.id,
            provider=PROVIDER,
            external_user_id=info.user_id,
            connected_by_label=identity.label,
        )
        session.add(connection)
    connection.external_user_name = user_name
    connection.status = ConnectionStatus.ACTIVE
    connection.granted_scopes = list(info.scopes)
    connection.missing_scopes = _missing_scopes(settings, info.scopes)
    connection.discovered_pages = [page.public_dict() for page in pages]
    connection.token_expires_at = info.expires_at
    connection.data_access_expires_at = info.data_access_expires_at
    connection.is_mock = provider.is_mock
    connection.connected_by_user_id = identity.user.id
    connection.connected_by_label = identity.label
    connection.connected_at = _now()
    connection.last_checked_at = _now()
    connection.last_error = None
    session.flush()
    return connection


def _relink_existing_pages(
    session: Session, connection: PlatformConnection, pages: list[PageInfo]
) -> list[uuid.UUID]:
    """Pages previously connected through OAuth re-bind automatically on reconnection."""
    by_id = {page.id: page for page in pages}
    relinked: list[uuid.UUID] = []
    for account in session.scalars(
        select(SocialAccount).where(
            SocialAccount.organization_id == connection.organization_id,
            SocialAccount.platform == Platform.FACEBOOK,
            SocialAccount.source == SocialAccountSource.OAUTH,
            SocialAccount.external_account_id.in_(list(by_id)),
        )
    ):
        if account.status == SocialAccountStatus.DISABLED:
            continue
        page = by_id[account.external_account_id or ""]
        _bind_account(account, connection, page)
        credential = vault.active_credential(
            session, connection=connection, subject=page.id, kind=CredentialKind.META_PAGE
        )
        if credential is not None:
            credential.social_account_id = account.id
        relinked.append(account.id)
    session.flush()
    return relinked


def complete_oauth(
    session: Session,
    settings: Settings,
    identity: Any,
    *,
    state: str | None,
    code: str | None,
    error: str | None,
    error_reason: str | None,
) -> OAuthState:
    """Consume the state, exchange the code, discover Pages, store encrypted tokens."""
    cipher = vault.ensure_vault(settings)
    attempt = _consume_state(session, identity, state)
    if error or not code:
        attempt.status = OAuthAttemptStatus.DENIED
        attempt.error = (error_reason or error or "no_code")[:255]
        record_audit(
            session,
            organization_id=identity.organization.id,
            identity=identity,
            action="account.connect_denied",
            entity_type="oauth_state",
            entity_id=attempt.id,
            data={"error": attempt.error},
        )
        session.flush()
        return attempt

    provider = _provider(settings)
    try:
        short = provider.exchange_code(code=code, redirect_uri=attempt.redirect_uri)
        long_lived = provider.exchange_long_lived(token=short.access_token)
        info = provider.debug_token(token=long_lived.access_token)
        if not info.is_valid or info.app_id != provider.expected_app_id:
            raise ProviderError(
                "The token was not issued for this app", details={"reason": "app_mismatch"}
            )
        pages = provider.list_pages(user_token=long_lived.access_token)
    except AppError as exc:
        attempt.status = OAuthAttemptStatus.FAILED
        attempt.error = exc.code[:255]
        record_audit(
            session,
            organization_id=identity.organization.id,
            identity=identity,
            action="account.connect_failed",
            entity_type="oauth_state",
            entity_id=attempt.id,
            data={"error": exc.code, "message": exc.message[:255]},
        )
        session.flush()
        raise

    connection = _upsert_connection(
        session,
        identity=identity,
        provider=provider,
        info=info,
        settings=settings,
        pages=pages,
        user_name=info.user_name,
    )
    expires_at = info.expires_at or _grant_expiry(long_lived.expires_in)
    vault.store_token(
        session,
        cipher,
        connection=connection,
        subject=info.user_id,
        kind=CredentialKind.META_USER,
        plaintext=long_lived.access_token,
        scopes=info.scopes,
        page_tasks=None,
        expires_at=expires_at,
        data_access_expires_at=info.data_access_expires_at,
        social_account_id=None,
        actor=identity.label,
        is_mock=provider.is_mock,
    )
    for page in pages:
        vault.store_token(
            session,
            cipher,
            connection=connection,
            subject=page.id,
            kind=CredentialKind.META_PAGE,
            plaintext=page.access_token,
            scopes=info.scopes,
            page_tasks=page.tasks,
            expires_at=None,  # Page tokens from a long-lived user token carry no expiry (A3)
            data_access_expires_at=info.data_access_expires_at,
            social_account_id=None,
            actor=identity.label,
            is_mock=provider.is_mock,
        )
    relinked = _relink_existing_pages(session, connection, pages)
    attempt.status = OAuthAttemptStatus.COMPLETED
    attempt.connection_id = connection.id
    record_audit(
        session,
        organization_id=identity.organization.id,
        identity=identity,
        action="account.connect_completed",
        entity_type="platform_connection",
        entity_id=connection.id,
        data={
            "external_user_id": info.user_id,
            "scopes": info.scopes,
            "missing_scopes": connection.missing_scopes,
            "pages_discovered": len(pages),
            "pages_relinked": [str(a) for a in relinked],
            "mock": provider.is_mock,
        },
    )
    session.flush()
    return attempt


# ---------------------------------------------------------------------------- connections


def _connection_query():
    return select(PlatformConnection).options(selectinload(PlatformConnection.accounts))


def list_connections(session: Session, organization_id: uuid.UUID) -> list[PlatformConnection]:
    return list(
        session.scalars(
            _connection_query()
            .where(PlatformConnection.organization_id == organization_id)
            .order_by(PlatformConnection.connected_at.desc())
        )
    )


def get_connection(
    session: Session, organization_id: uuid.UUID, connection_id: uuid.UUID
) -> PlatformConnection:
    connection = session.scalar(
        _connection_query().where(
            PlatformConnection.organization_id == organization_id,
            PlatformConnection.id == connection_id,
        )
    )
    if connection is None:
        raise NotFoundError("Connection not found")
    return connection


def connection_response(connection: PlatformConnection) -> PlatformConnectionResponse:
    linked = [a for a in connection.accounts if a.is_connected]
    return PlatformConnectionResponse(
        id=connection.id,
        provider=connection.provider,
        external_user_id=connection.external_user_id,
        external_user_name=connection.external_user_name,
        status=connection.status,
        granted_scopes=list(connection.granted_scopes),
        missing_scopes=list(connection.missing_scopes),
        token_expires_at=connection.token_expires_at,
        data_access_expires_at=connection.data_access_expires_at,
        is_mock=connection.is_mock,
        connected_by_label=connection.connected_by_label,
        connected_at=connection.connected_at,
        last_checked_at=connection.last_checked_at,
        last_error=connection.last_error,
        page_count=len(connection.discovered_pages),
        linked_account_count=len(linked),
        created_at=connection.created_at,
        updated_at=connection.updated_at,
    )


def discovered_pages(
    session: Session, connection: PlatformConnection
) -> list[DiscoveredPageResponse]:
    ids = [str(p.get("id")) for p in connection.discovered_pages if p.get("id")]
    existing = {
        a.external_account_id: a
        for a in session.scalars(
            select(SocialAccount).where(
                SocialAccount.organization_id == connection.organization_id,
                SocialAccount.platform == Platform.FACEBOOK,
                SocialAccount.external_account_id.in_(ids),
            )
        )
        if a.external_account_id
    }
    result: list[DiscoveredPageResponse] = []
    for page in connection.discovered_pages:
        page_id = str(page.get("id") or "")
        tasks = [str(t) for t in page.get("tasks") or []]
        linked = existing.get(page_id)
        result.append(
            DiscoveredPageResponse(
                id=page_id,
                name=str(page.get("name") or page_id),
                category=page.get("category"),
                tasks=tasks,
                can_publish=PUBLISH_TASK in tasks and not connection.missing_scopes,
                has_publish_task=PUBLISH_TASK in tasks,
                linked_account_id=linked.id if linked else None,
                linked_status=linked.status.value if linked else None,
                linked_source=linked.source.value if linked else None,
                linked_connected=linked.is_connected if linked else False,
            )
        )
    return result


def _bind_account(account: SocialAccount, connection: PlatformConnection, page: PageInfo | dict):
    tasks = page.tasks if isinstance(page, PageInfo) else [str(t) for t in page.get("tasks") or []]
    name = page.name if isinstance(page, PageInfo) else str(page.get("name") or "")
    account.display_name = name or account.display_name
    account.connection_id = connection.id
    account.source = SocialAccountSource.OAUTH
    account.is_verified = True
    account.status = SocialAccountStatus.CONNECTED
    account.is_mock = connection.is_mock
    account.page_tasks = list(tasks)
    account.connected_at = _now()
    account.last_verified_at = _now()
    account.disconnect_reason = None


def link_pages(
    session: Session, connection: PlatformConnection, page_ids: list[str], *, actor: str, identity
) -> list[SocialAccount]:
    """Create or upgrade one `social_accounts` row per selected discovered Page."""
    if connection.status != ConnectionStatus.ACTIVE:
        raise ConnectionInactiveError(
            "This connection is no longer active; reconnect first",
            details={"status": connection.status.value},
        )
    by_id = {str(p.get("id")): p for p in connection.discovered_pages if p.get("id")}
    linked: list[SocialAccount] = []
    for page_id in dict.fromkeys(page_ids):  # preserve order, drop duplicates
        page = by_id.get(page_id)
        if page is None:
            raise PageNotDiscoveredError(
                f"Page {page_id} was not listed by the provider for this connection",
                details={"page_id": page_id},
            )
        tasks = [str(t) for t in page.get("tasks") or []]
        if PUBLISH_TASK not in tasks:
            raise PageCannotPublishError(
                f"The authorizing user cannot create content on '{page.get('name')}'",
                details={"page_id": page_id, "tasks": tasks, "required_task": PUBLISH_TASK},
            )
        account = session.scalar(
            select(SocialAccount)
            .where(
                SocialAccount.organization_id == connection.organization_id,
                SocialAccount.platform == Platform.FACEBOOK,
                SocialAccount.external_account_id == page_id,
            )
            .with_for_update()
        )
        created = account is None
        if account is None:
            account = SocialAccount(
                id=uuid.uuid4(),
                organization_id=connection.organization_id,
                platform=Platform.FACEBOOK,
                account_type=SocialAccountType.PAGE,
                display_name=str(page.get("name") or page_id),
                external_account_id=page_id,
                capabilities=dict(PLATFORM_CAPABILITIES.get(Platform.FACEBOOK, {})),
                registered_by=actor,
            )
            session.add(account)
        if account.status == SocialAccountStatus.DISABLED:
            raise ConflictError(
                "This Page is disabled in AFFILIO; re-enable it before connecting",
                details={"account_id": str(account.id)},
            )
        _bind_account(account, connection, page)
        session.flush()
        credential = vault.active_credential(
            session, connection=connection, subject=page_id, kind=CredentialKind.META_PAGE
        )
        if credential is None:
            raise ConflictError(
                "No stored credential for this Page; reconnect to refresh it",
                details={"page_id": page_id},
            )
        credential.social_account_id = account.id
        record_audit(
            session,
            organization_id=connection.organization_id,
            identity=identity,
            action="account.page_linked",
            entity_type="social_account",
            entity_id=account.id,
            data={
                "page_id": page_id,
                "created": created,
                "connection_id": str(connection.id),
                "mock": connection.is_mock,
                "tasks": tasks,
            },
        )
        linked.append(account)
    session.flush()
    return linked


# ---------------------------------------------------------------------------- health


def _page_credential(session: Session, account: SocialAccount) -> AccountCredential | None:
    if account.connection is None or not account.external_account_id:
        return None
    return vault.active_credential(
        session,
        connection=account.connection,
        subject=account.external_account_id,
        kind=CredentialKind.META_PAGE,
    )


def _mark_disconnected(
    session: Session, account: SocialAccount, *, reason: str, credential_status: CredentialStatus
) -> None:
    credential = _page_credential(session, account)
    if credential is not None:
        vault.invalidate(credential, status=credential_status, reason=reason)
    account.status = SocialAccountStatus.DISCONNECTED
    account.is_verified = False
    account.disconnect_reason = reason[:64]


def disconnect_account(
    session: Session, account: SocialAccount, *, reason: str | None, identity
) -> SocialAccount:
    if account.source != SocialAccountSource.OAUTH or account.connection is None:
        raise AccountNotConnectedError("This account was never connected through OAuth")
    _mark_disconnected(
        session,
        account,
        reason="disconnected_by_admin",
        credential_status=CredentialStatus.REVOKED,
    )
    record_audit(
        session,
        organization_id=account.organization_id,
        identity=identity,
        action="account.disconnected",
        entity_type="social_account",
        entity_id=account.id,
        data={"reason": reason, "connection_id": str(account.connection_id)},
    )
    session.flush()
    return account


def verify_account(
    session: Session, settings: Settings, account: SocialAccount, *, identity
) -> SocialAccount:
    """Ask the provider whether the stored Page token still works. Updates health fields."""
    if not account.is_connected:
        raise AccountNotConnectedError("This account is not connected")
    credential = _page_credential(session, account)
    if credential is None:
        _mark_disconnected(
            session,
            account,
            reason="credential_missing",
            credential_status=CredentialStatus.INVALID,
        )
        session.flush()
        return account
    cipher = vault.ensure_vault(settings)
    provider = _provider(settings)
    if credential.is_mock != provider.is_mock:
        # A mock credential must never be validated by the live provider (or vice versa).
        _mark_disconnected(
            session, account, reason="provider_mismatch", credential_status=CredentialStatus.INVALID
        )
        session.flush()
        return account
    try:
        token = vault.open_token(cipher, credential)
    except CredentialUndecryptableError:
        _mark_disconnected(
            session,
            account,
            reason="credential_undecryptable",
            credential_status=CredentialStatus.INVALID,
        )
        session.flush()
        return account
    try:
        provider.validate_page_token(page_token=token, page_id=account.external_account_id or "")
    except TokenInvalidError as exc:
        _mark_disconnected(
            session, account, reason="token_invalid", credential_status=CredentialStatus.INVALID
        )
        record_audit(
            session,
            organization_id=account.organization_id,
            identity=identity,
            action="account.token_invalid",
            entity_type="social_account",
            entity_id=account.id,
            data={"provider_code": exc.provider_code, "subcode": exc.provider_subcode},
        )
        session.flush()
        return account
    except ProviderUnavailableError:
        raise  # nothing can be concluded; the caller reports 503 and health stays as it was
    finally:
        del token
    now = _now()
    credential.last_validated_at = now
    account.last_verified_at = now
    record_audit(
        session,
        organization_id=account.organization_id,
        identity=identity,
        action="account.verified",
        entity_type="social_account",
        entity_id=account.id,
        data={"connection_id": str(account.connection_id)},
    )
    session.flush()
    return account


def _user_credential(session: Session, connection: PlatformConnection) -> AccountCredential | None:
    return vault.active_credential(
        session,
        connection=connection,
        subject=connection.external_user_id,
        kind=CredentialKind.META_USER,
    )


def _invalidate_connection(
    session: Session,
    connection: PlatformConnection,
    *,
    status_value: ConnectionStatus,
    reason: str,
) -> None:
    connection.status = status_value
    connection.last_error = reason[:255]
    for credential in session.scalars(
        select(AccountCredential).where(
            AccountCredential.connection_id == connection.id,
            AccountCredential.status == CredentialStatus.ACTIVE,
        )
    ):
        vault.invalidate(
            credential,
            status=CredentialStatus.REVOKED
            if status_value == ConnectionStatus.REVOKED
            else CredentialStatus.INVALID,
            reason=reason,
        )
    for account in connection.accounts:
        if account.is_connected:
            account.status = SocialAccountStatus.DISCONNECTED
            account.is_verified = False
            account.disconnect_reason = reason[:64]


def verify_connection(
    session: Session, settings: Settings, connection: PlatformConnection, *, identity
) -> PlatformConnection:
    """Re-inspect the user grant (`/debug_token`). Invalid → connection and Pages disconnected."""
    if connection.status != ConnectionStatus.ACTIVE:
        return connection
    credential = _user_credential(session, connection)
    if credential is None:
        _invalidate_connection(
            session, connection, status_value=ConnectionStatus.INVALID, reason="credential_missing"
        )
        session.flush()
        return connection
    cipher = vault.ensure_vault(settings)
    provider = _provider(settings)
    if credential.is_mock != provider.is_mock:
        _invalidate_connection(
            session, connection, status_value=ConnectionStatus.INVALID, reason="provider_mismatch"
        )
        session.flush()
        return connection
    try:
        token = vault.open_token(cipher, credential)
    except CredentialUndecryptableError:
        _invalidate_connection(
            session,
            connection,
            status_value=ConnectionStatus.INVALID,
            reason="credential_undecryptable",
        )
        session.flush()
        return connection
    try:
        info = provider.debug_token(token=token)
    except TokenInvalidError:
        _invalidate_connection(
            session, connection, status_value=ConnectionStatus.INVALID, reason="token_invalid"
        )
        record_audit(
            session,
            organization_id=connection.organization_id,
            identity=identity,
            action="connection.token_invalid",
            entity_type="platform_connection",
            entity_id=connection.id,
            data={},
        )
        session.flush()
        return connection
    finally:
        del token
    now = _now()
    connection.last_checked_at = now
    connection.last_error = None
    if not info.is_valid:
        _invalidate_connection(
            session, connection, status_value=ConnectionStatus.EXPIRED, reason="grant_expired"
        )
    else:
        connection.granted_scopes = list(info.scopes)
        connection.missing_scopes = _missing_scopes(settings, info.scopes)
        connection.token_expires_at = info.expires_at
        connection.data_access_expires_at = info.data_access_expires_at
        credential.last_validated_at = now
    record_audit(
        session,
        organization_id=connection.organization_id,
        identity=identity,
        action="connection.verified",
        entity_type="platform_connection",
        entity_id=connection.id,
        data={"status": connection.status.value, "missing_scopes": connection.missing_scopes},
    )
    session.flush()
    return connection


def revoke_connection(
    session: Session, settings: Settings, connection: PlatformConnection, *, identity
) -> PlatformConnection:
    """Revoke the app grant at the provider (best effort) and locally for every Page."""
    credential = _user_credential(session, connection)
    if credential is not None:
        try:
            cipher = vault.ensure_vault(settings)
            provider = _provider(settings)
            if credential.is_mock == provider.is_mock:
                token = vault.open_token(cipher, credential)
                try:
                    provider.revoke(user_token=token)
                finally:
                    del token
        except (CredentialVaultError, CredentialUndecryptableError, ProviderError) as exc:
            # Local revocation still proceeds: the stored tokens become unusable here even
            # when the provider could not be told. Reported in last_error for the admin.
            log.warning("meta.revoke_remote_failed", code=exc.code)
            connection.last_error = f"remote_revoke_failed:{exc.code}"[:255]
    _invalidate_connection(
        session, connection, status_value=ConnectionStatus.REVOKED, reason="connection_revoked"
    )
    record_audit(
        session,
        organization_id=connection.organization_id,
        identity=identity,
        action="connection.revoked",
        entity_type="platform_connection",
        entity_id=connection.id,
        data={"accounts": [str(a.id) for a in connection.accounts]},
    )
    session.flush()
    return connection


# ---------------------------------------------------------------------------- credentials


def credential_summaries(session: Session, account: SocialAccount) -> list[CredentialSummary]:
    rows = session.scalars(
        select(AccountCredential)
        .where(AccountCredential.social_account_id == account.id)
        .order_by(AccountCredential.version.desc())
    )
    return [
        CredentialSummary(
            id=row.id,
            kind=row.kind,
            version=row.version,
            status=row.status,
            key_id=row.key_id,
            scopes=list(row.scopes),
            page_tasks=row.page_tasks,
            issued_at=row.issued_at,
            expires_at=row.expires_at,
            data_access_expires_at=row.data_access_expires_at,
            last_validated_at=row.last_validated_at,
            last_used_at=row.last_used_at,
            invalidated_reason=row.invalidated_reason,
            is_mock=row.is_mock,
            created_by_label=row.created_by_label,
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------- mock actions


def mock_action(
    session: Session, settings: Settings, organization_id: uuid.UUID, data: MockActionRequest
) -> MockActionResponse:
    if settings.meta_provider_mode != "mock":
        raise MockModeDisabledError("Mock actions are only available in mock mode")
    from affilio.modules.accounts.providers.mock import MockMetaProvider

    cipher = vault.ensure_vault(settings)
    if data.action == "outage_on":
        MockMetaProvider.set_outage(True)
    elif data.action == "outage_off":
        MockMetaProvider.set_outage(False)
    elif data.action == "reset":
        MockMetaProvider.reset()
    elif data.action == "revoke_page_token":
        if data.account_id is None:
            raise AppError("account_id is required", code="validation_error", status_code=422)
        account = session.scalar(
            select(SocialAccount).where(
                SocialAccount.organization_id == organization_id,
                SocialAccount.id == data.account_id,
            )
        )
        credential = _page_credential(session, account) if account else None
        if credential is None:
            raise NotFoundError("No active Page credential for this account")
        MockMetaProvider.revoke_token(vault.open_token(cipher, credential))
    elif data.action == "revoke_user_token":
        if data.connection_id is None:
            raise AppError("connection_id is required", code="validation_error", status_code=422)
        connection = get_connection(session, organization_id, data.connection_id)
        credential = _user_credential(session, connection)
        if credential is None:
            raise NotFoundError("No active user credential for this connection")
        MockMetaProvider.revoke_token(vault.open_token(cipher, credential))
    return MockActionResponse(
        action=data.action,
        outage=MockMetaProvider.outage(),
        revoked_tokens=MockMetaProvider.revoked_count(),
    )


# ---------------------------------------------------------------------------- maintenance


def purge_oauth_states(session: Session, *, retention: timedelta) -> int:
    """Delete finished or expired attempts older than `retention`; pending ones past expiry too."""
    cutoff = _now() - retention
    result = session.execute(
        delete(OAuthState).where(
            (OAuthState.consumed_at.is_not(None) & (OAuthState.consumed_at < cutoff))
            | (OAuthState.expires_at < cutoff)
        )
    )
    return int(result.rowcount or 0)


def oauth_attempts(session: Session, organization_id: uuid.UUID, *, limit: int = 20):
    return list(
        session.scalars(
            select(OAuthState)
            .where(OAuthState.organization_id == organization_id)
            .order_by(OAuthState.created_at.desc())
            .limit(min(max(limit, 1), 100))
        )
    )


__all__ = ["ProviderNotConfiguredError", "complete_oauth", "config_response", "start_oauth"]
