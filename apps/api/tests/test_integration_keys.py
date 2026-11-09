"""Integration API keys (ADR 0013): lifecycle, hashing, scopes, allow-list. Needs a test DB."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from affilio.api.deps import integration_key_allows
from affilio.app import create_app
from affilio.core.config import get_settings
from affilio.core.enums import MembershipRole
from affilio.core.security import (
    API_KEY_LENGTH,
    api_key_display_prefix,
    generate_api_key,
    hash_api_key,
    looks_like_api_key,
)
from affilio.db.session import dispose_engine
from affilio.modules.identity import service as identity_service
from affilio.modules.identity.models import AuditEvent, IntegrationApiKey
from affilio.scripts import create_integration_key as cli
from tests.auth_helpers import sign_in

pytestmark = pytest.mark.database


@pytest.fixture
def api(
    monkeypatch: pytest.MonkeyPatch, media_dirs, organization, schema, db_session: Session
) -> Iterator[TestClient]:
    for key, value in media_dirs.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DEFAULT_ORGANIZATION_SLUG", organization.slug)
    monkeypatch.setenv("MEDIA_ADMIN_TOKEN", "svc-token")
    get_settings.cache_clear()
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        yield client
    dispose_engine()
    get_settings.cache_clear()


@pytest.fixture
def machine(api: TestClient) -> TestClient:
    """A second client without the browser session: what the MeoScout relay looks like."""
    return TestClient(api.app, raise_server_exceptions=False)


def bearer(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


def test_key_helpers_shape_and_hash() -> None:
    secret = generate_api_key()
    assert secret.startswith("afk_") and len(secret) == API_KEY_LENGTH
    assert looks_like_api_key(secret)
    assert not looks_like_api_key("afk_short")
    assert not looks_like_api_key("x" * API_KEY_LENGTH)
    assert not looks_like_api_key(secret + "!")
    assert len(hash_api_key(secret)) == 64 and hash_api_key(secret) != secret
    assert api_key_display_prefix(secret) == secret[:12]
    assert generate_api_key() != secret


def test_integration_allow_list_is_explicit() -> None:
    assert integration_key_allows("POST", "/api/v1/products/ingest", "/api/v1")
    assert integration_key_allows("GET", "/api/v1/products/ingest/msb_1", "/api/v1")
    assert integration_key_allows("GET", "/api/v1/integrations/me", "/api/v1")
    for method, path in (
        ("GET", "/api/v1/products"),
        ("POST", "/api/v1/products"),
        ("GET", "/api/v1/products/ingest"),
        ("DELETE", "/api/v1/products/ingest/msb_1"),
        ("GET", "/api/v1/auth/me"),
        ("POST", "/api/v1/integrations/keys"),
        ("GET", "/api/v1/integrations/keys"),
        ("POST", "/api/v1/publishing/queue/release-due"),
        ("POST", "/api/v1/media/scans"),
    ):
        assert not integration_key_allows(method, path, "/api/v1"), (method, path)


def test_admin_manages_keys_and_secret_is_shown_once(
    api: TestClient, machine: TestClient, db_session: Session, organization
) -> None:
    sign_in(api, db_session, organization, role=MembershipRole.ADMIN)
    created = api.post(
        "/api/v1/integrations/keys",
        json={"name": "  MeoScout bridge ", "scopes": ["products:ingest", "products:ingest"]},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    secret = body["secret"]
    assert secret.startswith("afk_") and len(secret) == API_KEY_LENGTH
    assert body["key"]["name"] == "MeoScout bridge"
    assert body["key"]["prefix"] == secret[:12]
    assert body["key"]["scopes"] == ["products:ingest"]
    assert body["key"]["state"] == "active"
    assert body["key"]["created_by_user_id"] is not None
    key_id = body["key"]["id"]

    # At rest: only the SHA-256 and a short prefix; the plaintext is nowhere.
    row = db_session.get(IntegrationApiKey, uuid.UUID(key_id))
    assert row is not None and row.key_hash == hash_api_key(secret)
    assert secret not in (row.key_hash, row.prefix, row.name)

    listed = api.get("/api/v1/integrations/keys")
    assert listed.status_code == 200
    assert [k["id"] for k in listed.json()] == [key_id]
    assert "secret" not in listed.json()[0] and "key_hash" not in listed.json()[0]

    # Unknown scopes are refused; the known set is echoed.
    bad = api.post("/api/v1/integrations/keys", json={"name": "x", "scopes": ["products:write"]})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "unknown_scope"
    assert bad.json()["error"]["details"]["known"] == ["products:ingest"]

    # Rotation: a new secret with the same name and scopes; the old key dies at once.
    rotated = api.post(f"/api/v1/integrations/keys/{key_id}/rotate")
    assert rotated.status_code == 201, rotated.text
    new_secret = rotated.json()["secret"]
    assert new_secret != secret and rotated.json()["key"]["rotated_from_key_id"] == key_id
    assert rotated.json()["key"]["scopes"] == ["products:ingest"]
    assert machine.get("/api/v1/integrations/me", headers=bearer(secret)).status_code == 401
    me = machine.get("/api/v1/integrations/me", headers=bearer(new_secret))
    assert me.status_code == 200, me.text
    assert me.json()["organization"]["slug"] == organization.slug
    assert me.json()["integration"]["name"] == "MeoScout bridge"
    assert "secret" not in me.text and new_secret not in me.text

    states = {k["id"]: k["state"] for k in api.get("/api/v1/integrations/keys").json()}
    assert states[key_id] == "revoked"
    new_id = rotated.json()["key"]["id"]
    assert states[new_id] == "active"

    # Revoke: immediate, idempotency error on a second revoke, rotation of a dead key refused.
    revoked = api.delete(f"/api/v1/integrations/keys/{new_id}")
    assert revoked.status_code == 200 and revoked.json()["state"] == "revoked"
    assert revoked.json()["revoked_by_user_id"] is not None
    assert machine.get("/api/v1/integrations/me", headers=bearer(new_secret)).status_code == 401
    assert (
        api.delete(f"/api/v1/integrations/keys/{new_id}").json()["error"]["code"]
        == "integration_key_not_active"
    )
    assert api.post(f"/api/v1/integrations/keys/{new_id}/rotate").status_code == 409
    assert api.delete(f"/api/v1/integrations/keys/{uuid.uuid4()}").status_code == 404

    actions = [
        e.action
        for e in db_session.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.entity_type == "integration_api_key",
                AuditEvent.organization_id == organization.id,
            )
            .order_by(AuditEvent.created_at)
        )
    ]
    assert actions == [
        "integration_key.created",
        "integration_key.rotated",
        "integration_key.revoked",
    ]
    for event in db_session.scalars(
        select(AuditEvent).where(
            AuditEvent.entity_type == "integration_api_key",
            AuditEvent.organization_id == organization.id,
        )
    ):
        assert secret not in str(event.data) and new_secret not in str(event.data)
        assert event.actor_kind == "user"


def test_editors_and_integration_keys_cannot_manage_keys(
    api: TestClient, db_session: Session, organization
) -> None:
    sign_in(api, db_session, organization, role=MembershipRole.EDITOR)
    denied = api.post("/api/v1/integrations/keys", json={"name": "x", "scopes": []})
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "forbidden"
    assert api.get("/api/v1/integrations/keys").status_code == 403
    # A session cannot pretend to be an integration either.
    assert api.get("/api/v1/integrations/me").status_code == 403

    api.cookies.clear()
    _, secret = identity_service.create_api_key(
        db_session, organization, name="bridge", scopes=["products:ingest"], created_by=None
    )
    db_session.commit()
    # No privilege escalation: a key cannot mint, list, rotate or revoke keys …
    for method, path in (
        ("POST", "/api/v1/integrations/keys"),
        ("GET", "/api/v1/integrations/keys"),
        ("POST", f"/api/v1/integrations/keys/{uuid.uuid4()}/rotate"),
        ("DELETE", f"/api/v1/integrations/keys/{uuid.uuid4()}"),
        # … nor reach anything outside product ingestion.
        ("GET", "/api/v1/products"),
        ("POST", "/api/v1/products"),
        ("GET", "/api/v1/auth/me"),
        ("GET", "/api/v1/auth/members"),
        ("GET", "/api/v1/media/assets"),
        ("POST", "/api/v1/publishing/queue/release-due"),
        ("GET", "/api/v1/procurement/requests"),
    ):
        response = api.request(
            method, path, headers=bearer(secret), json={"name": "x"} if method == "POST" else None
        )
        assert response.status_code == 403, (method, path, response.text)
        assert response.json()["error"]["code"] == "integration_scope_denied"
    # The service token is confined the same way: it cannot ingest.
    svc = api.post(
        "/api/v1/products/ingest", json={}, headers={"X-Affilio-Admin-Token": "svc-token"}
    )
    assert svc.status_code == 403 and svc.json()["error"]["code"] == "service_scope_denied"


def test_missing_invalid_revoked_and_expired_keys_fail_closed(
    api: TestClient, db_session: Session, organization
) -> None:
    key, secret = identity_service.create_api_key(
        db_session, organization, name="bridge", scopes=["products:ingest"], created_by=None
    )
    db_session.commit()
    url = "/api/v1/integrations/me"
    assert api.get(url).status_code == 401
    for header in (
        "Bearer",
        "Bearer not-a-key",
        "Bearer " + generate_api_key(),  # well-formed, unknown
        "Bearer " + secret[:-1] + ("A" if secret[-1] != "A" else "B"),  # one character off
        "Basic " + secret,
        "Token " + secret,
    ):
        response = api.get(url, headers={"Authorization": header})
        assert response.status_code == 401, header
        assert response.json()["error"]["code"] == "authentication_required"
        assert secret not in response.text
    ok = api.get(url, headers=bearer(secret))
    assert ok.status_code == 200
    db_session.refresh(key)
    assert key.last_used_at is not None

    key.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    expired = api.get(url, headers=bearer(secret))
    assert expired.status_code == 401 and "expired" in expired.json()["error"]["message"]
    assert identity_service.api_key_state(key) == "expired"

    key.expires_at = None
    identity_service.revoke_api_key(key, revoked_by=None)
    db_session.commit()
    revoked = api.get(url, headers=bearer(secret))
    assert revoked.status_code == 401 and "revoked" in revoked.json()["error"]["message"]


def test_cli_creates_rotates_and_revokes_keys(
    api: TestClient, db_session: Session, organization, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli.main(
            [
                "create",
                "--org",
                organization.slug,
                "--name",
                "CLI bridge",
                "--scope",
                "products:ingest",
            ]
        )
        == 0
    )
    out, err = capsys.readouterr()
    secret = out.strip()
    assert looks_like_api_key(secret) and "created" in err and secret not in err
    me = api.get("/api/v1/integrations/me", headers=bearer(secret))
    assert me.status_code == 200 and me.json()["integration"]["name"] == "CLI bridge"
    assert me.json()["integration"]["scopes"] == ["products:ingest"]

    assert cli.main(["list", "--org", organization.slug]) == 0
    listed = capsys.readouterr().out
    assert "CLI bridge" in listed and secret not in listed
    key_id = listed.split()[0]

    assert cli.main(["rotate", "--org", organization.slug, "--key", key_id]) == 0
    out, _ = capsys.readouterr()
    new_secret = out.strip()
    assert looks_like_api_key(new_secret) and new_secret != secret
    assert api.get("/api/v1/integrations/me", headers=bearer(secret)).status_code == 401
    assert api.get("/api/v1/integrations/me", headers=bearer(new_secret)).status_code == 200

    assert cli.main(["list", "--org", organization.slug]) == 0
    active = [line for line in capsys.readouterr().out.splitlines() if " active " in line]
    assert len(active) == 1
    assert cli.main(["revoke", "--org", organization.slug, "--key", active[0].split()[0]]) == 0
    capsys.readouterr()
    assert api.get("/api/v1/integrations/me", headers=bearer(new_secret)).status_code == 401
    with pytest.raises(SystemExit):
        cli.main(["create", "--org", "no-such-org", "--name", "x"])
    capsys.readouterr()
    cli_audits = db_session.scalars(
        select(AuditEvent).where(
            AuditEvent.entity_type == "integration_api_key",
            AuditEvent.actor_kind == "system",
            AuditEvent.organization_id == organization.id,
        )
    ).all()
    assert {e.action for e in cli_audits} == {
        "integration_key.created",
        "integration_key.rotated",
        "integration_key.revoked",
    }
