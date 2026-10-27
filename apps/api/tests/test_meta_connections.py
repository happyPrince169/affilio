"""Facebook Page connection lifecycle through the deterministic mock provider.

Needs TEST_DATABASE_URL. Every response is checked for leaked tokens.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from affilio.app import create_app
from affilio.core.config import get_settings
from affilio.core.enums import MembershipRole
from affilio.db.session import dispose_engine
from affilio.modules.accounts.models import AccountCredential, OAuthState
from affilio.modules.accounts.providers.mock import MockMetaProvider
from tests.auth_helpers import create_member, login, sign_in
from tests.connection_helpers import (
    CALLBACK,
    assert_no_secret,
    callback,
    connect,
    link,
    live_env,
    location_of,
    mock_env,
    start,
)
from tests.media_helpers import create_organization
from tests.publishing_helpers import register_page

pytestmark = pytest.mark.database

ALPHA, BETA, GAMMA = "100000000000001", "100000000000002", "100000000000003"


@pytest.fixture
def api(
    monkeypatch: pytest.MonkeyPatch, media_dirs, organization, schema, db_session: Session
) -> Iterator[TestClient]:
    for key, value in media_dirs.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DEFAULT_ORGANIZATION_SLUG", organization.slug)
    monkeypatch.delenv("MEDIA_ADMIN_TOKEN", raising=False)
    mock_env(monkeypatch)
    MockMetaProvider.reset()
    get_settings.cache_clear()
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        sign_in(client, db_session, organization, role=MembershipRole.ADMIN)
        yield client
    dispose_engine()
    MockMetaProvider.reset()
    get_settings.cache_clear()


def _accounts(api: TestClient) -> dict[str, dict]:
    response = api.get("/api/v1/accounts")
    assert response.status_code == 200
    assert_no_secret(response.json())
    return {a["external_account_id"]: a for a in response.json() if a["external_account_id"]}


# ------------------------------------------------------------------ configuration


def test_config_reports_mock_mode_and_scenarios(api: TestClient) -> None:
    config = api.get("/api/v1/accounts/connect/facebook/config").json()
    assert config["mode"] == "mock" and config["configured"] and config["vault_ready"]
    assert "success" in config["mock_scenarios"] and config["missing"] == []
    info = api.get("/api/v1/system/info").json()
    assert info["integrations"]["meta"] == {
        "mode": "mock",
        "configured": True,
        "graph_api_version": "v26.0",
    }


def test_live_mode_without_app_settings_is_a_clear_configuration_error(
    monkeypatch: pytest.MonkeyPatch, media_dirs, organization, schema, db_session: Session
) -> None:
    for key, value in media_dirs.env().items():
        monkeypatch.setenv(key, value)
    live_env(monkeypatch, configured=False)
    get_settings.cache_clear()
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        sign_in(client, db_session, organization, role=MembershipRole.ADMIN)
        config = client.get("/api/v1/accounts/connect/facebook/config").json()
        assert config["mode"] == "live" and config["configured"] is False
        assert config["missing"] == ["META_APP_ID", "META_APP_SECRET", "META_OAUTH_REDIRECT_URI"]
        started = client.post("/api/v1/accounts/connect/facebook", json={})
        assert started.status_code == 503
        body = started.json()["error"]
        assert body["code"] == "meta_not_configured"
        assert body["details"]["missing"] == config["missing"]
        # Mock scenarios and mock actions are not reachable in live mode.
        mock = client.post("/api/v1/accounts/connect/facebook", json={"mock_scenario": "success"})
        assert mock.status_code == 404 and mock.json()["error"]["code"] == "mock_mode_disabled"
        action = client.post(
            "/api/v1/accounts/connect/facebook/mock/actions", json={"action": "reset"}
        )
        assert action.status_code == 404
        assert client.get("/api/v1/system/info").json()["integrations"]["meta"]["mode"] == "live"
    dispose_engine()
    get_settings.cache_clear()


def test_missing_encryption_key_fails_closed(
    monkeypatch: pytest.MonkeyPatch, media_dirs, organization, schema, db_session: Session
) -> None:
    for key, value in media_dirs.env().items():
        monkeypatch.setenv(key, value)
    mock_env(monkeypatch, with_key=False)
    get_settings.cache_clear()
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        sign_in(client, db_session, organization, role=MembershipRole.ADMIN)
        config = client.get("/api/v1/accounts/connect/facebook/config").json()
        assert config["vault_ready"] is False and config["configured"] is False
        assert "CREDENTIAL_ENCRYPTION_KEYS" in config["missing"]
        started = client.post(
            "/api/v1/accounts/connect/facebook", json={"mock_scenario": "success"}
        )
        assert started.status_code == 503
        assert started.json()["error"]["code"] == "credential_vault_unavailable"
        assert db_session.scalar(select(OAuthState)) is None
    dispose_engine()
    get_settings.cache_clear()


# ------------------------------------------------------------------ happy path


def test_full_connection_lifecycle_in_mock_mode(api: TestClient, db_session: Session) -> None:
    started = start(api, "success")
    assert started["mode"] == "mock" and started["authorize_url"].startswith(CALLBACK)
    assert "code=mock-success" in started["authorize_url"]

    path, _ = location_of(callback(api, started["authorize_url"]))
    connection_id = path.rsplit("/", 1)[1]

    connections = api.get("/api/v1/accounts/connections").json()
    assert_no_secret(connections)
    assert len(connections) == 1
    conn = connections[0]
    assert conn["id"] == connection_id and conn["is_mock"] is True
    assert conn["status"] == "active" and conn["page_count"] == 3 and conn["missing_scopes"] == []
    assert conn["external_user_name"] == "Mock Meta User" and conn["token_expires_at"]

    pages = api.get(f"/api/v1/accounts/connections/{connection_id}/pages").json()
    assert_no_secret(pages)
    by_id = {p["id"]: p for p in pages}
    assert by_id[ALPHA]["can_publish"] and by_id[GAMMA]["can_publish"]
    assert by_id[BETA]["can_publish"] is False and by_id[BETA]["has_publish_task"] is False
    assert all(p["linked_account_id"] is None for p in pages)

    linked = link(api, connection_id, [ALPHA, GAMMA, ALPHA])
    assert_no_secret(linked)
    assert [a["external_account_id"] for a in linked] == [ALPHA, GAMMA]
    for account in linked:
        assert account["status"] == "connected" and account["is_verified"]
        assert account["source"] == "oauth" and account["is_mock"] is True
        assert account["can_publish"] and account["permission_status"] == "ok"
        assert account["page_tasks"] == [
            "ANALYZE",
            "ADVERTISE",
            "MODERATE",
            "CREATE_CONTENT",
            "MANAGE",
        ]
        assert "Mock" in account["connection_label"] and account["connected_at"]
        assert account["capabilities"]["api_publishing"] is False  # publishing is M3B.1B

    denied = api.post(
        f"/api/v1/accounts/connections/{connection_id}/pages", json={"page_ids": [BETA]}
    )
    assert denied.status_code == 422 and denied.json()["error"]["code"] == "page_cannot_publish"

    pages = api.get(f"/api/v1/accounts/connections/{connection_id}/pages").json()
    alpha = next(p for p in pages if p["id"] == ALPHA)
    assert alpha["linked_account_id"] == linked[0]["id"] and alpha["linked_connected"]

    credentials = api.get(f"/api/v1/accounts/{linked[0]['id']}/credentials").json()
    assert_no_secret(credentials)
    assert len(credentials) == 1
    assert credentials[0]["kind"] == "meta_page" and credentials[0]["version"] == 1
    assert credentials[0]["status"] == "active" and credentials[0]["key_id"] == "v1"
    assert credentials[0]["is_mock"] is True and credentials[0]["expires_at"] is None

    stored = db_session.scalars(select(AccountCredential)).all()
    assert {c.kind.value for c in stored} == {"meta_user_long_lived", "meta_page"}
    for row in stored:
        assert b"mock:" not in row.ciphertext and row.key_id == "v1" and row.is_mock

    attempts = api.get("/api/v1/accounts/connect/facebook/attempts").json()
    assert attempts[0]["status"] == "completed" and attempts[0]["connection_id"] == connection_id
    audit = api.get("/api/v1/auth/audit", params={"entity_type": "platform_connection"}).json()
    assert {e["action"] for e in audit} >= {"account.connect_completed"}
    assert_no_secret(audit)


# ------------------------------------------------------------------ state validation


def test_state_cannot_be_replayed(api: TestClient) -> None:
    started = start(api)
    first = callback(api, started["authorize_url"])
    assert location_of(first)[0].startswith("/accounts/connect/facebook/")
    second = callback(api, started["authorize_url"])
    path, query = location_of(second)
    assert path == "/accounts" and query["connect_error"] == ["oauth_state_invalid"]
    assert len(api.get("/api/v1/accounts/connections").json()) == 1


def test_expired_state_is_refused_and_marked(api: TestClient, db_session: Session) -> None:
    started = start(api)
    attempt = db_session.scalar(select(OAuthState).order_by(OAuthState.created_at.desc()))
    assert attempt is not None
    attempt.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    path, query = location_of(callback(api, started["authorize_url"]))
    assert path == "/accounts" and query["connect_error"] == ["oauth_state_invalid"]
    db_session.refresh(attempt)
    assert attempt.status.value == "expired" and attempt.consumed_at is not None
    assert api.get("/api/v1/accounts/connections").json() == []


def test_tampered_state_or_code_is_refused(api: TestClient) -> None:
    unknown = api.get(
        f"{CALLBACK}?state=not-a-real-state&code=mock-success", follow_redirects=False
    )
    assert location_of(unknown)[1]["connect_error"] == ["oauth_state_invalid"]
    missing = api.get(f"{CALLBACK}?code=mock-success", follow_redirects=False)
    assert location_of(missing)[1]["connect_error"] == ["oauth_state_invalid"]
    # A valid state with a forged code: the provider rejects the code, the attempt is failed.
    started = start(api)
    forged = started["authorize_url"].replace("code=mock-success", "code=EAAstolen")
    assert location_of(callback(api, forged))[1]["connect_error"] == ["mock_credential_rejected"]
    attempts = api.get("/api/v1/accounts/connect/facebook/attempts").json()
    assert attempts[0]["status"] == "failed" and attempts[0]["error"] == "mock_credential_rejected"
    # The consumed state cannot be reused with the real code either.
    assert location_of(callback(api, started["authorize_url"]))[1]["connect_error"] == [
        "oauth_state_invalid"
    ]


def test_state_is_bound_to_the_user_who_started_it(
    api: TestClient, db_session: Session, organization
) -> None:
    started = start(api)
    other_admin = create_member(db_session, organization, role=MembershipRole.ADMIN)
    with TestClient(create_app(), raise_server_exceptions=False) as other:
        login(other, other_admin)
        assert location_of(callback(other, started["authorize_url"]))[1]["connect_error"] == [
            "oauth_state_invalid"
        ]
    # Not consumed by the wrong user: the original admin can still finish.
    assert location_of(callback(api, started["authorize_url"]))[0].startswith(
        "/accounts/connect/facebook/"
    )


def test_callback_requires_a_signed_in_admin(
    api: TestClient, db_session: Session, organization
) -> None:
    started = start(api)
    with TestClient(create_app(), raise_server_exceptions=False) as anonymous:
        response = callback(anonymous, started["authorize_url"])
        assert response.status_code == 401
    editor = create_member(db_session, organization, role=MembershipRole.EDITOR)
    with TestClient(create_app(), raise_server_exceptions=False) as as_editor:
        login(as_editor, editor)
        assert callback(as_editor, started["authorize_url"]).status_code == 403


# ------------------------------------------------------------------ scenarios


def test_denied_consent_records_the_attempt_without_a_connection(api: TestClient) -> None:
    started = start(api, "denied")
    assert "error=access_denied" in started["authorize_url"]
    path, query = location_of(callback(api, started["authorize_url"]))
    assert path == "/accounts" and query["connect_error"] == ["denied"]
    assert api.get("/api/v1/accounts/connections").json() == []
    attempts = api.get("/api/v1/accounts/connect/facebook/attempts").json()
    assert attempts[0]["status"] == "denied" and attempts[0]["error"] == "user_denied"


def test_missing_permissions_are_visible_and_block_publishing(api: TestClient) -> None:
    connection_id = connect(api, "missing_permissions")
    conn = api.get(f"/api/v1/accounts/connections/{connection_id}").json()
    assert conn["missing_scopes"] == ["pages_manage_posts"]
    pages = api.get(f"/api/v1/accounts/connections/{connection_id}/pages").json()
    assert all(p["can_publish"] is False for p in pages)
    (alpha,) = link(api, connection_id, [ALPHA])
    assert alpha["status"] == "connected" and alpha["can_publish"] is False
    assert alpha["permission_status"] == "missing_scopes"


def test_no_pages_scenario(api: TestClient) -> None:
    connection_id = connect(api, "no_pages")
    assert api.get(f"/api/v1/accounts/connections/{connection_id}/pages").json() == []
    assert api.get(f"/api/v1/accounts/connections/{connection_id}").json()["page_count"] == 0


def test_provider_outage_during_exchange_fails_the_attempt(api: TestClient) -> None:
    started = start(api, "outage")
    assert location_of(callback(api, started["authorize_url"]))[1]["connect_error"] == [
        "provider_unavailable"
    ]
    assert api.get("/api/v1/accounts/connections").json() == []
    assert api.get("/api/v1/accounts/connect/facebook/attempts").json()[0]["status"] == "failed"


def test_token_for_another_app_is_rejected(api: TestClient) -> None:
    started = start(api, "invalid_app")
    _, query = location_of(callback(api, started["authorize_url"]))
    assert query["connect_error"] == ["provider_error"]
    assert api.get("/api/v1/accounts/connections").json() == []


# ------------------------------------------------------------------ compatibility & reconnection


def test_manual_page_is_upgraded_in_place_keeping_routing(api: TestClient) -> None:
    manual = register_page(api, "Alpha (manual)", external_account_id=ALPHA)
    assert manual["status"] == "disconnected" and manual["source"] == "manual"
    put = api.put(
        f"/api/v1/accounts/{manual['id']}/channel-profile",
        json={"description": "men's sports", "routing_enabled": True, "rules": []},
    )
    assert put.status_code == 200
    connection_id = connect(api)
    pages = api.get(f"/api/v1/accounts/connections/{connection_id}/pages").json()
    alpha = next(p for p in pages if p["id"] == ALPHA)
    assert alpha["linked_account_id"] == manual["id"] and alpha["linked_source"] == "manual"
    assert alpha["linked_connected"] is False
    (upgraded,) = link(api, connection_id, [ALPHA])
    assert upgraded["id"] == manual["id"]
    assert upgraded["source"] == "oauth" and upgraded["status"] == "connected"
    assert upgraded["display_name"] == "Mock Page Alpha" and upgraded["is_mock"]
    profile = api.get(f"/api/v1/accounts/{manual['id']}/channel-profile").json()
    assert profile["description"] == "men's sports"
    assert len(_accounts(api)) == 1  # no duplicate Page identity
    # A manual Page without an id stays untouched: it is a different registry row.
    other = register_page(api, "Unrelated manual page")
    assert other["status"] == "disconnected"


def test_reconnection_rebinds_pages_and_versions_credentials(
    api: TestClient, db_session: Session
) -> None:
    first = connect(api)
    (alpha,) = link(api, first, [ALPHA])
    second = connect(api)
    assert second == first  # same Meta user → same connection row
    assert len(api.get("/api/v1/accounts/connections").json()) == 1
    accounts = _accounts(api)
    assert accounts[ALPHA]["id"] == alpha["id"] and accounts[ALPHA]["status"] == "connected"
    credentials = api.get(f"/api/v1/accounts/{alpha['id']}/credentials").json()
    assert [(c["version"], c["status"]) for c in credentials] == [(2, "active"), (1, "superseded")]
    # Only one active credential per subject exists in the vault.
    active = [
        c for c in db_session.scalars(select(AccountCredential)).all() if c.status.value == "active"
    ]
    assert len(active) == 4  # user + 3 pages


# ------------------------------------------------------------------ health, revocation, disconnect


def test_verify_disconnect_and_revocation(api: TestClient) -> None:
    connection_id = connect(api)
    alpha, gamma = link(api, connection_id, [ALPHA, GAMMA])

    verified = api.post(f"/api/v1/accounts/{alpha['id']}/verify").json()
    assert verified["status"] == "connected" and verified["last_verified_at"]

    revoked = api.post(
        "/api/v1/accounts/connect/facebook/mock/actions",
        json={"action": "revoke_page_token", "account_id": alpha["id"]},
    )
    assert revoked.status_code == 200 and revoked.json()["revoked_tokens"] == 1
    after = api.post(f"/api/v1/accounts/{alpha['id']}/verify").json()
    assert after["status"] == "disconnected" and after["is_verified"] is False
    assert (
        after["disconnect_reason"] == "token_invalid" and "reconnect" in after["connection_label"]
    )
    assert after["permission_status"] == "not_connected" and after["can_publish"] is False
    credentials = api.get(f"/api/v1/accounts/{alpha['id']}/credentials").json()
    assert (
        credentials[0]["status"] == "invalid"
        and credentials[0]["invalidated_reason"] == "token_invalid"
    )
    again = api.post(f"/api/v1/accounts/{alpha['id']}/verify")
    assert again.status_code == 409 and again.json()["error"]["code"] == "account_not_connected"

    # Outage: nothing can be concluded, health unchanged.
    api.post("/api/v1/accounts/connect/facebook/mock/actions", json={"action": "outage_on"})
    outage = api.post(f"/api/v1/accounts/{gamma['id']}/verify")
    assert outage.status_code == 503 and outage.json()["error"]["code"] == "provider_unavailable"
    assert _accounts(api)[GAMMA]["status"] == "connected"
    api.post("/api/v1/accounts/connect/facebook/mock/actions", json={"action": "outage_off"})

    # Admin disconnect.
    disconnected = api.post(
        f"/api/v1/accounts/{gamma['id']}/disconnect", json={"reason": "handover"}
    )
    assert disconnected.status_code == 200
    assert disconnected.json()["disconnect_reason"] == "disconnected_by_admin"
    assert api.get(f"/api/v1/accounts/{gamma['id']}/credentials").json()[0]["status"] == "revoked"

    # Reconnect restores both Pages (Alpha re-binds automatically, Gamma too: both are oauth rows).
    assert connect(api) == connection_id
    accounts = _accounts(api)
    assert accounts[ALPHA]["status"] == "connected" and accounts[GAMMA]["status"] == "connected"

    # Revoke the whole grant.
    revoked = api.post(f"/api/v1/accounts/connections/{connection_id}/revoke").json()
    assert revoked["status"] == "revoked"
    accounts = _accounts(api)
    assert {accounts[ALPHA]["disconnect_reason"], accounts[GAMMA]["disconnect_reason"]} == {
        "connection_revoked"
    }
    blocked = api.post(
        f"/api/v1/accounts/connections/{connection_id}/pages", json={"page_ids": [ALPHA]}
    )
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "connection_inactive"
    assert api.get("/api/v1/auth/audit", params={"entity_type": "platform_connection"}).json()


def test_verify_connection_detects_user_grant_revocation(api: TestClient) -> None:
    connection_id = connect(api)
    link(api, connection_id, [ALPHA])
    ok = api.post(f"/api/v1/accounts/connections/{connection_id}/verify").json()
    assert ok["status"] == "active" and ok["last_checked_at"]
    api.post(
        "/api/v1/accounts/connect/facebook/mock/actions",
        json={"action": "revoke_user_token", "connection_id": connection_id},
    )
    invalid = api.post(f"/api/v1/accounts/connections/{connection_id}/verify").json()
    assert invalid["status"] == "invalid" and invalid["last_error"] == "token_invalid"
    assert _accounts(api)[ALPHA]["status"] == "disconnected"
    assert _accounts(api)[ALPHA]["disconnect_reason"] == "token_invalid"


# ------------------------------------------------------------------ RBAC and isolation


def test_rbac_for_connection_management(api: TestClient, db_session: Session, organization) -> None:
    connection_id = connect(api)
    editor = create_member(db_session, organization, role=MembershipRole.EDITOR)
    viewer = create_member(db_session, organization, role=MembershipRole.VIEWER)
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        login(client, editor)
        assert client.get("/api/v1/accounts/connect/facebook/config").status_code == 200
        assert client.get("/api/v1/accounts/connections").status_code == 200
        assert client.get(f"/api/v1/accounts/connections/{connection_id}/pages").status_code == 200
        assert client.post("/api/v1/accounts/connect/facebook", json={}).status_code == 403
        assert (
            client.post(
                f"/api/v1/accounts/connections/{connection_id}/pages", json={"page_ids": [ALPHA]}
            ).status_code
            == 403
        )
        assert (
            client.post(f"/api/v1/accounts/connections/{connection_id}/revoke").status_code == 403
        )
        assert (
            client.post(
                "/api/v1/accounts/connect/facebook/mock/actions", json={"action": "reset"}
            ).status_code
            == 403
        )
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        login(client, viewer)
        assert client.get("/api/v1/accounts/connect/facebook/config").status_code == 200
        assert client.post("/api/v1/accounts/connect/facebook", json={}).status_code == 403


def test_connections_are_isolated_per_organization(api: TestClient, db_session: Session) -> None:
    connection_id = connect(api)
    (alpha,) = link(api, connection_id, [ALPHA])
    other_org = create_organization(db_session)
    db_session.commit()
    other_admin = create_member(db_session, other_org, role=MembershipRole.ADMIN)
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        login(client, other_admin)
        assert client.get("/api/v1/accounts/connections").json() == []
        assert client.get(f"/api/v1/accounts/connections/{connection_id}").status_code == 404
        assert client.get(f"/api/v1/accounts/connections/{connection_id}/pages").status_code == 404
        assert (
            client.post(
                f"/api/v1/accounts/connections/{connection_id}/pages", json={"page_ids": [ALPHA]}
            ).status_code
            == 404
        )
        assert client.get(f"/api/v1/accounts/{alpha['id']}/credentials").status_code == 404
        assert client.post(f"/api/v1/accounts/{alpha['id']}/disconnect", json={}).status_code == 404
        assert client.get("/api/v1/accounts").json() == []
        # The same Meta user can connect the other organization independently.
        other_connection = connect(client)
        assert other_connection != connection_id
    db_session.delete(other_org)
    db_session.commit()
