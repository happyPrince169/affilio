"""Authentication, sessions, RBAC, CSRF and tenant isolation. Needs TEST_DATABASE_URL."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from affilio.app import create_app
from affilio.core.config import get_settings
from affilio.core.enums import MembershipRole
from affilio.core.security import hash_password, hash_session_token, verify_password
from affilio.db.session import dispose_engine
from affilio.modules.identity.models import AuditEvent, UserSession
from tests.auth_helpers import TEST_PASSWORD, create_member, login, sign_in
from tests.media_helpers import create_organization

pytestmark = pytest.mark.database


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, media_dirs, organization, schema) -> Iterator[TestClient]:
    for key, value in media_dirs.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DEFAULT_ORGANIZATION_SLUG", organization.slug)
    monkeypatch.delenv("MEDIA_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    get_settings.cache_clear()
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        yield client
    dispose_engine()
    get_settings.cache_clear()


def test_password_hashing_is_salted_and_verifies() -> None:
    first, second = hash_password("s3cret-password"), hash_password("s3cret-password")
    assert first != second and first.startswith("$argon2id$")
    assert verify_password("s3cret-password", first)
    assert not verify_password("wrong", first)
    assert not verify_password("anything", None)


def test_unauthenticated_requests_fail_closed(api: TestClient) -> None:
    for path in (
        "/api/v1/auth/me",
        "/api/v1/media/assets",
        "/api/v1/products",
        "/api/v1/publishing/queue",
    ):
        response = api.get(path)
        assert response.status_code == 401, path
        assert response.json()["error"]["code"] == "authentication_required"
    assert api.post("/api/v1/publishing/campaigns", json={"name": "x"}).status_code == 401
    # Public endpoints keep working.
    assert api.get("/api/v1/health").status_code == 200
    assert api.get("/api/v1/system/info").status_code == 200


def test_login_sets_httponly_cookie_and_me_reports_identity(
    api: TestClient, db_session: Session, organization
) -> None:
    user = create_member(db_session, organization, role=MembershipRole.ADMIN)
    response = api.post(
        "/api/v1/auth/login", json={"email": user.email.upper(), "password": TEST_PASSWORD}
    )
    assert response.status_code == 200, response.text
    cookie = response.headers["set-cookie"]
    assert (
        "affilio_session=" in cookie
        and "HttpOnly" in cookie
        and "SameSite=lax" in cookie.replace("Lax", "lax")
    )
    body = response.json()
    assert body["email"] == user.email and body["identity_kind"] == "user"
    assert body["organization"]["organization_slug"] == organization.slug
    assert body["organization"]["role"] == "admin"
    assert body["permissions"] == {
        "view": True,
        "edit": True,
        "approve": True,
        "manage_accounts": True,
        "manage_members": True,
    }
    assert "password" not in response.text and "token" not in response.text

    me = api.get("/api/v1/auth/me")
    assert me.status_code == 200 and me.json()["id"] == body["id"]

    stored = db_session.scalars(select(UserSession).where(UserSession.user_id == user.id)).all()
    raw = api.cookies.get("affilio_session")
    assert raw and len(stored) == 1
    assert stored[0].token_hash == hash_session_token(raw) and raw not in stored[0].token_hash


def test_invalid_credentials_and_inactive_users(
    api: TestClient, db_session: Session, organization
) -> None:
    user = create_member(db_session, organization, role=MembershipRole.EDITOR)
    wrong = api.post("/api/v1/auth/login", json={"email": user.email, "password": "nope-nope-nope"})
    assert wrong.status_code == 401 and wrong.json()["error"]["code"] == "invalid_credentials"
    unknown = api.post(
        "/api/v1/auth/login", json={"email": "ghost@example.com", "password": TEST_PASSWORD}
    )
    assert unknown.status_code == 401 and unknown.json()["error"]["code"] == "invalid_credentials"
    user.status = "suspended"
    db_session.commit()
    disabled = api.post("/api/v1/auth/login", json={"email": user.email, "password": TEST_PASSWORD})
    assert disabled.status_code == 401
    assert "set-cookie" not in disabled.headers


def test_logout_revokes_session_and_clears_cookie(
    api: TestClient, db_session: Session, organization
) -> None:
    sign_in(api, db_session, organization)
    assert api.get("/api/v1/auth/me").status_code == 200
    out = api.post("/api/v1/auth/logout")
    assert out.status_code == 204
    assert (
        'affilio_session=""' in out.headers["set-cookie"]
        or "Max-Age=0" in out.headers["set-cookie"]
    )
    api.cookies.clear()
    assert api.get("/api/v1/auth/me").status_code == 401


def test_revoked_or_tampered_cookie_is_rejected(
    api: TestClient, db_session: Session, organization
) -> None:
    sign_in(api, db_session, organization)
    raw = api.cookies.get("affilio_session")
    api.cookies.clear()
    tampered = api.get("/api/v1/auth/me", headers={"Cookie": f"affilio_session={raw[:-2]}zz"})
    assert tampered.status_code == 401
    valid = api.get("/api/v1/auth/me", headers={"Cookie": f"affilio_session={raw}"})
    assert valid.status_code == 200
    record = db_session.scalar(
        select(UserSession).where(UserSession.token_hash == hash_session_token(raw))
    )
    record.revoked_at = record.expires_at
    db_session.commit()
    revoked = api.get("/api/v1/auth/me", headers={"Cookie": f"affilio_session={raw}"})
    assert revoked.status_code == 401


def test_role_hierarchy_enforced_at_api(api: TestClient, db_session: Session, organization) -> None:
    viewer = create_member(db_session, organization, role=MembershipRole.VIEWER)
    editor = create_member(db_session, organization, role=MembershipRole.EDITOR)
    admin = create_member(db_session, organization, role=MembershipRole.ADMIN)

    login(api, viewer)
    assert api.get("/api/v1/publishing/campaigns").status_code == 200
    denied = api.post("/api/v1/publishing/campaigns", json={"name": "Nope"})
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "forbidden"
    assert denied.json()["error"]["details"] == {"required": "editor", "role": "viewer"}
    assert api.post("/api/v1/accounts", json={"display_name": "Page"}).status_code == 403
    assert api.get("/api/v1/auth/audit").status_code == 403

    login(api, editor)
    assert (
        api.post("/api/v1/publishing/campaigns", json={"name": "Editor campaign"}).status_code
        == 201
    )
    assert api.post("/api/v1/accounts", json={"display_name": "Page"}).status_code == 403
    assert (
        api.post("/api/v1/auth/members", json={"email": "x@y.io", "password": "p" * 12}).status_code
        == 403
    )

    login(api, admin)
    new_email = f"new.editor-{uuid.uuid4().hex[:6]}@team.local"
    assert api.post("/api/v1/accounts", json={"display_name": "Page"}).status_code == 201
    created = api.post(
        "/api/v1/auth/members",
        json={"email": new_email, "password": "long-enough-pw", "role": "editor"},
    )
    assert created.status_code == 201 and created.json()["role"] == "editor"
    assert (
        api.post(
            "/api/v1/auth/members",
            json={
                "email": f"owner-{uuid.uuid4().hex[:6]}@team.local",
                "password": "long-enough-pw",
                "role": "owner",
            },
        ).status_code
        == 422
    )
    members = api.get("/api/v1/auth/members").json()
    assert {m["email"] for m in members} >= {
        viewer.email,
        editor.email,
        admin.email,
        new_email,
    }


def test_cross_organization_access_is_refused(
    api: TestClient, db_session: Session, organization
) -> None:
    other = create_organization(db_session)
    db_session.commit()
    outsider = create_member(db_session, other, role=MembershipRole.OWNER)
    insider = create_member(db_session, organization, role=MembershipRole.OWNER)

    login(api, insider)
    campaign = api.post("/api/v1/publishing/campaigns", json={"name": "Private"}).json()
    account = api.post("/api/v1/accounts", json={"display_name": "Private Page"}).json()

    login(api, outsider)
    assert api.get("/api/v1/auth/me").json()["organization"]["organization_slug"] == other.slug
    assert api.get(f"/api/v1/publishing/campaigns/{campaign['id']}").status_code == 404
    assert api.get(f"/api/v1/accounts/{account['id']}").status_code == 404
    assert api.get("/api/v1/publishing/campaigns").json()["total"] == 0
    # Cannot hop into an organization without membership, even by naming it.
    hop = api.get(
        "/api/v1/publishing/campaigns", headers={"X-Organization-Slug": organization.slug}
    )
    assert hop.status_code == 403
    # Login scoped to a foreign organization fails too.
    bad = api.post(
        "/api/v1/auth/login",
        json={
            "email": outsider.email,
            "password": TEST_PASSWORD,
            "organization_slug": organization.slug,
        },
    )
    assert bad.status_code == 401


def test_cross_site_writes_with_cookie_are_rejected(
    api: TestClient, db_session: Session, organization
) -> None:
    sign_in(api, db_session, organization)
    evil = api.post(
        "/api/v1/publishing/campaigns",
        json={"name": "csrf"},
        headers={"Origin": "https://evil.example"},
    )
    assert evil.status_code == 403 and evil.json()["error"]["code"] == "csrf_rejected"
    fetch = api.post(
        "/api/v1/publishing/campaigns",
        json={"name": "csrf"},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert fetch.status_code == 403
    allowed = api.post(
        "/api/v1/publishing/campaigns",
        json={"name": "ok"},
        headers={"Origin": "http://localhost:3000"},
    )
    assert allowed.status_code == 201
    same_host = api.post(
        "/api/v1/publishing/campaigns",
        json={"name": "ok2"},
        headers={"Origin": "http://testserver"},
    )
    assert same_host.status_code == 201
    # Reads are never blocked by the origin check.
    assert (
        api.get(
            "/api/v1/publishing/campaigns", headers={"Origin": "https://evil.example"}
        ).status_code
        == 200
    )


def test_password_change_revokes_other_sessions(
    api: TestClient, db_session: Session, organization
) -> None:
    user = create_member(db_session, organization, role=MembershipRole.EDITOR)
    login(api, user)
    first_cookie = api.cookies.get("affilio_session")
    api.cookies.clear()
    login(api, user)
    second_cookie = api.cookies.get("affilio_session")
    assert first_cookie != second_cookie

    short = api.post(
        "/api/v1/auth/password", json={"current_password": TEST_PASSWORD, "new_password": "short"}
    )
    assert short.status_code == 422
    wrong = api.post(
        "/api/v1/auth/password",
        json={"current_password": "not-it-at-all", "new_password": "a-brand-new-password"},
    )
    assert wrong.status_code == 401
    ok = api.post(
        "/api/v1/auth/password",
        json={"current_password": TEST_PASSWORD, "new_password": "a-brand-new-password"},
    )
    assert ok.status_code == 204
    assert api.get("/api/v1/auth/me").status_code == 200  # current session survives
    api.cookies.clear()
    other = api.get("/api/v1/auth/me", headers={"Cookie": f"affilio_session={first_cookie}"})
    assert other.status_code == 401  # other session revoked
    assert (
        api.post(
            "/api/v1/auth/login", json={"email": user.email, "password": "a-brand-new-password"}
        ).status_code
        == 200
    )


def test_audit_trail_records_user_ids(api: TestClient, db_session: Session, organization) -> None:
    user = sign_in(api, db_session, organization, role=MembershipRole.ADMIN)
    api.post("/api/v1/accounts", json={"display_name": "Audited Page"})
    api.patch("/api/v1/auth/organization", json={"timezone": "Asia/Ho_Chi_Minh", "name": "Renamed"})
    assert (
        api.patch("/api/v1/auth/organization", json={"timezone": "Mars/Olympus"}).status_code == 422
    )
    events = api.get("/api/v1/auth/audit").json()
    actions = [e["action"] for e in events]
    assert (
        "auth.login" in actions
        and "account.registered" in actions
        and "organization.updated" in actions
    )
    assert all(e["actor_user_id"] == str(user.id) for e in events if e["actor_kind"] == "user")
    assert all(e["request_id"] for e in events)
    rows = db_session.scalars(
        select(AuditEvent).where(AuditEvent.organization_id == organization.id)
    ).all()
    assert len(rows) >= 3
    assert api.get("/api/v1/auth/me").json()["organization"]["timezone"] == "Asia/Ho_Chi_Minh"
