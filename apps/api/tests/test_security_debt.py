"""Pre-M3B security debt closed in M3B.1A: service-token scope, session purge, admin revoke."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from affilio.api.deps import service_token_allows
from affilio.app import create_app
from affilio.core.config import get_settings
from affilio.core.enums import MembershipRole
from affilio.db.session import dispose_engine
from affilio.modules.accounts import connections as connections_service
from affilio.modules.accounts.models import OAuthState
from affilio.modules.identity import service as identity_service
from affilio.modules.identity.models import UserSession
from affilio.worker.maintenance import run_maintenance
from tests.auth_helpers import create_member, login, sign_in
from tests.connection_helpers import mock_env, start

pytestmark = pytest.mark.database


@pytest.fixture
def api(
    monkeypatch: pytest.MonkeyPatch, media_dirs, organization, schema, db_session: Session
) -> Iterator[TestClient]:
    for key, value in media_dirs.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DEFAULT_ORGANIZATION_SLUG", organization.slug)
    monkeypatch.setenv("MEDIA_ADMIN_TOKEN", "svc-token")
    mock_env(monkeypatch)
    get_settings.cache_clear()
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        yield client
    dispose_engine()
    get_settings.cache_clear()


def test_service_token_allow_list_is_explicit() -> None:
    assert service_token_allows("POST", "/api/v1/media/scans", "/api/v1")
    assert service_token_allows("GET", "/api/v1/media/scans/status", "/api/v1")
    assert service_token_allows("POST", "/api/v1/publishing/queue/release-due", "/api/v1")
    assert service_token_allows("GET", "/api/v1/auth/me", "/api/v1")
    for method, path in (
        ("GET", "/api/v1/accounts"),
        ("POST", "/api/v1/accounts/connect/facebook"),
        ("GET", "/api/v1/accounts/connections"),
        ("POST", "/api/v1/products"),
        ("POST", "/api/v1/publishing/publications"),
        ("DELETE", "/api/v1/media/scans/1"),
        ("POST", "/api/v1/auth/members"),
    ):
        assert not service_token_allows(method, path, "/api/v1"), (method, path)


def test_service_token_cannot_reach_connections_or_registry(api: TestClient) -> None:
    headers = {"X-Affilio-Admin-Token": "svc-token"}
    assert api.get("/api/v1/auth/me", headers=headers).json()["identity_kind"] == "service"
    assert api.post("/api/v1/publishing/queue/release-due", headers=headers).status_code == 200
    for method, path in (
        ("GET", "/api/v1/accounts"),
        ("GET", "/api/v1/accounts/connections"),
        ("POST", "/api/v1/accounts/connect/facebook"),
        ("GET", "/api/v1/accounts/connect/facebook/config"),
        ("GET", "/api/v1/auth/members"),
    ):
        response = api.request(method, path, headers=headers, json={} if method == "POST" else None)
        assert response.status_code == 403, (method, path, response.text)
        assert response.json()["error"]["code"] == "service_scope_denied"


def test_expired_and_revoked_sessions_are_purged(db_session: Session, organization) -> None:
    user = create_member(db_session, organization, role=MembershipRole.EDITOR)
    live, _ = identity_service.create_session(db_session, user, ttl=timedelta(days=1))
    expired, _ = identity_service.create_session(db_session, user, ttl=timedelta(days=1))
    expired.expires_at = datetime.now(UTC) - timedelta(days=40)
    revoked, _ = identity_service.create_session(db_session, user, ttl=timedelta(days=1))
    revoked.revoked_at = datetime.now(UTC) - timedelta(days=40)
    recent, _ = identity_service.create_session(db_session, user, ttl=timedelta(days=1))
    recent.revoked_at = datetime.now(UTC) - timedelta(days=2)
    db_session.commit()

    purged = identity_service.purge_sessions(db_session, retention=timedelta(days=30))
    db_session.commit()
    assert purged == 2
    remaining = {
        s.id for s in db_session.scalars(select(UserSession).where(UserSession.user_id == user.id))
    }
    assert remaining == {live.id, recent.id}


def test_oauth_attempts_are_purged_by_maintenance(
    api: TestClient, db_session: Session, organization
) -> None:
    sign_in(api, db_session, organization, role=MembershipRole.ADMIN)
    start(api)
    start(api)
    old = db_session.scalars(select(OAuthState)).all()
    assert len(old) == 2
    old[0].consumed_at = datetime.now(UTC) - timedelta(days=3)
    old[1].expires_at = datetime.now(UTC) - timedelta(days=3)
    db_session.commit()
    assert connections_service.purge_oauth_states(db_session, retention=timedelta(hours=24)) == 2
    db_session.commit()
    result = run_maintenance(get_settings())
    assert set(result) == {"sessions_purged", "oauth_states_purged"}


def test_admin_can_sign_a_member_out_everywhere(
    api: TestClient, db_session: Session, organization
) -> None:
    admin = sign_in(api, db_session, organization, role=MembershipRole.ADMIN)
    editor = create_member(db_session, organization, role=MembershipRole.EDITOR)
    owner = create_member(db_session, organization, role=MembershipRole.OWNER)
    with TestClient(create_app(), raise_server_exceptions=False) as as_editor:
        login(as_editor, editor)
        assert as_editor.get("/api/v1/auth/me").status_code == 200
        revoked = api.post(f"/api/v1/auth/members/{editor.id}/sessions/revoke")
        assert revoked.status_code == 204
        assert as_editor.get("/api/v1/auth/me").status_code == 401
    # An admin cannot sign out an owner; the admin's own session is untouched.
    assert api.post(f"/api/v1/auth/members/{owner.id}/sessions/revoke").status_code == 404
    assert api.get("/api/v1/auth/me").json()["email"] == admin.email
    with TestClient(create_app(), raise_server_exceptions=False) as as_editor:
        login(as_editor, editor)
        assert as_editor.post(f"/api/v1/auth/members/{admin.id}/sessions/revoke").status_code == 403
    audit = api.get(
        "/api/v1/auth/audit", params={"entity_type": "user", "entity_id": str(editor.id)}
    )
    assert "member.sessions_revoked" in {e["action"] for e in audit.json()}
