"""Editor → production-owner mappings (upload track U2): RBAC and CSRF, several owners per
member, duplicate and reactivation handling, organization isolation, taxonomy validation,
archived owners, category deletion protection, audit trail. Needs TEST_DATABASE_URL."""

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
from affilio.db.session import dispose_engine
from affilio.modules.identity.models import AuditEvent
from affilio.modules.media.models import MediaOwnerMapping
from tests.auth_helpers import create_member, login, sign_in
from tests.media_helpers import create_organization

BASE = "/api/v1/media/owner-mappings"


@pytest.fixture
def api(
    monkeypatch: pytest.MonkeyPatch, media_dirs, organization, schema, db_session: Session
) -> Iterator[TestClient]:
    for key, value in media_dirs.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DEFAULT_ORGANIZATION_SLUG", organization.slug)
    monkeypatch.delenv("MEDIA_ADMIN_TOKEN", raising=False)
    get_settings.cache_clear()
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        yield client
    dispose_engine()
    get_settings.cache_clear()


def cat(client: TestClient, name: str, parent_id: str | None = None, **extra) -> dict:
    response = client.post(
        "/api/v1/content-categories", json={"name": name, "parent_id": parent_id, **extra}
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def owners(api: TestClient, db_session: Session, organization) -> dict:
    """Admin signed in; facet roots with owners Hang / Hao / Minh Anh, product Sports, and an
    archived owner Old; editor member to map."""
    admin = sign_in(api, db_session, organization, role=MembershipRole.ADMIN)
    response = api.post("/api/v1/content-categories/ensure-facets")
    assert response.status_code == 200, response.text
    roots = {r["facet"]: r for r in response.json()["roots"]}
    t = {"admin": admin, "roots": roots}
    t["hang"] = cat(api, "Hang", roots["production_owner"]["id"])
    t["hao"] = cat(api, "Hao", roots["production_owner"]["id"])
    t["minh_anh"] = cat(api, "Minh Anh", roots["production_owner"]["id"])
    t["sports"] = cat(api, "Sports", roots["product_category"]["id"])
    old = cat(api, "Old", roots["production_owner"]["id"])
    archived = api.patch(f"/api/v1/content-categories/{old['id']}", json={"status": "archived"})
    assert archived.status_code == 200, archived.text
    t["old"] = archived.json()
    t["editor"] = create_member(db_session, organization, role=MembershipRole.EDITOR)
    t["viewer"] = create_member(db_session, organization, role=MembershipRole.VIEWER)
    return t


def assign(client: TestClient, user_id, category_id, expect: int = 201) -> dict:
    response = client.post(
        BASE, json={"user_id": str(user_id), "production_owner_category_id": category_id}
    )
    assert response.status_code == expect, response.text
    return response.json()


def audit_actions(session: Session, organization) -> list[str]:
    session.expire_all()
    return list(
        session.scalars(
            select(AuditEvent.action)
            .where(
                AuditEvent.organization_id == organization.id,
                AuditEvent.action.like("owner_mapping.%"),
            )
            .order_by(AuditEvent.created_at)
        )
    )


# ---------------------------------------------------------------- admin API


@pytest.mark.database
def test_admin_assigns_several_owners_and_duplicates_conflict(
    api: TestClient, owners: dict, db_session: Session, organization
) -> None:
    editor = owners["editor"]
    first = assign(api, editor.id, owners["hang"]["id"])
    assert first["user_id"] == str(editor.id) and first["user_email"] == editor.email
    assert first["production_owner"] == {
        "id": owners["hang"]["id"],
        "slug": "hang",
        "name": "Hang",
        "status": "active",
    }
    assert first["status"] == "active" and first["created_by_user_id"] == str(owners["admin"].id)
    second = assign(api, editor.id, owners["hao"]["id"])
    assert second["id"] != first["id"]

    duplicate = api.post(
        BASE,
        json={"user_id": str(editor.id), "production_owner_category_id": owners["hang"]["id"]},
    )
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["error"]["code"] == "owner_mapping_conflict"
    assert duplicate.json()["error"]["details"]["mapping_id"] == first["id"]

    listing = api.get(BASE, params={"user_id": str(editor.id)})
    assert listing.status_code == 200
    assert [m["production_owner"]["slug"] for m in listing.json()] == ["hang", "hao"]
    everyone = api.get(BASE).json()
    assert {m["user_id"] for m in everyone} == {str(editor.id)}
    assert audit_actions(db_session, organization) == [
        "owner_mapping.created",
        "owner_mapping.created",
    ]


@pytest.mark.database
def test_revoke_keeps_history_and_reassign_reactivates(
    api: TestClient, owners: dict, db_session: Session, organization
) -> None:
    editor = owners["editor"]
    mapping = assign(api, editor.id, owners["hang"]["id"])
    revoked = api.delete(f"{BASE}/{mapping['id']}")
    assert revoked.status_code == 204
    again = api.delete(f"{BASE}/{mapping['id']}")  # idempotent, no second audit row
    assert again.status_code == 204

    assert api.get(BASE).json() == []
    history = api.get(BASE, params={"include_revoked": "true"}).json()
    assert len(history) == 1 and history[0]["status"] == "revoked"
    assert history[0]["revoked_at"] is not None
    assert history[0]["revoked_by_user_id"] == str(owners["admin"].id)

    reactivated = assign(api, editor.id, owners["hang"]["id"])
    assert reactivated["id"] == mapping["id"] and reactivated["status"] == "active"
    assert reactivated["revoked_at"] is None
    rows = list(db_session.scalars(select(MediaOwnerMapping)))
    assert len(rows) == 1
    assert audit_actions(db_session, organization) == [
        "owner_mapping.created",
        "owner_mapping.revoked",
        "owner_mapping.created",
    ]
    db_session.expire_all()
    last = db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.action == "owner_mapping.created")
        .order_by(AuditEvent.created_at.desc())
    )
    assert last is not None and last.data["reactivated"] is True
    assert last.data["production_owner_slug"] == "hang"


@pytest.mark.database
def test_invalid_categories_and_members_are_refused(
    api: TestClient, owners: dict, db_session: Session, organization
) -> None:
    editor = owners["editor"]
    for category_id, reason in (
        (owners["sports"]["id"], "product category, wrong facet"),
        (owners["roots"]["production_owner"]["id"], "the facet root itself"),
        (owners["old"]["id"], "archived owner"),
    ):
        response = api.post(
            BASE, json={"user_id": str(editor.id), "production_owner_category_id": category_id}
        )
        assert response.status_code == 422, reason
        assert response.json()["error"]["code"] == "owner_mapping_invalid_category", reason

    unknown_category = api.post(
        BASE, json={"user_id": str(editor.id), "production_owner_category_id": str(uuid.uuid4())}
    )
    assert unknown_category.status_code == 404

    stranger = create_member(
        db_session, create_organization(db_session), role=MembershipRole.EDITOR
    )
    db_session.commit()
    not_a_member = api.post(
        BASE,
        json={"user_id": str(stranger.id), "production_owner_category_id": owners["hang"]["id"]},
    )
    assert not_a_member.status_code == 404, not_a_member.text
    assert api.get(BASE).json() == []
    assert audit_actions(db_session, organization) == []


@pytest.mark.database
def test_mapped_owner_cannot_be_deleted_but_can_be_archived(
    api: TestClient, owners: dict, db_session: Session, organization
) -> None:
    editor = owners["editor"]
    assign(api, editor.id, owners["hang"]["id"])
    delete = api.delete(f"/api/v1/content-categories/{owners['hang']['id']}")
    assert delete.status_code == 409, delete.text
    assert delete.json()["error"]["details"]["references"] == {"media_owner_mappings": 1}

    archived = api.patch(
        f"/api/v1/content-categories/{owners['hang']['id']}", json={"status": "archived"}
    )
    assert archived.status_code == 200, archived.text
    listing = api.get(BASE, params={"user_id": str(editor.id)}).json()
    assert listing[0]["production_owner"]["status"] == "archived"  # still listed, flagged

    # The archived owner is no longer submittable for the editor.
    login(api, editor)
    assert api.get(f"{BASE}/me").json() == []


# ---------------------------------------------------------------- RBAC, CSRF, isolation


@pytest.mark.database
def test_editor_reads_only_own_owners_and_cannot_manage(
    api: TestClient, owners: dict, db_session: Session, organization
) -> None:
    editor, viewer = owners["editor"], owners["viewer"]
    assign(api, editor.id, owners["minh_anh"]["id"])
    assign(api, editor.id, owners["hang"]["id"])
    assign(api, viewer.id, owners["hao"]["id"])

    login(api, editor)
    mine = api.get(f"{BASE}/me")
    assert mine.status_code == 200
    assert [o["slug"] for o in mine.json()] == ["hang", "minh-anh"]  # sorted by name
    assert api.get(BASE).status_code == 403
    assert (
        api.post(
            BASE,
            json={"user_id": str(editor.id), "production_owner_category_id": owners["hao"]["id"]},
        ).status_code
        == 403
    )
    mapping_id = db_session.scalar(select(MediaOwnerMapping.id))
    assert api.delete(f"{BASE}/{mapping_id}").status_code == 403

    login(api, viewer)
    assert [o["slug"] for o in api.get(f"{BASE}/me").json()] == ["hao"]
    assert api.get(BASE).status_code == 403


@pytest.mark.database
def test_cross_site_and_cross_organization_requests_are_refused(
    api: TestClient, owners: dict, db_session: Session, organization
) -> None:
    editor = owners["editor"]
    evil = api.post(
        BASE,
        json={"user_id": str(editor.id), "production_owner_category_id": owners["hang"]["id"]},
        headers={"Origin": "https://evil.example"},
    )
    assert evil.status_code == 403 and evil.json()["error"]["code"] == "csrf_rejected"
    mapping = assign(api, editor.id, owners["hang"]["id"])

    other_org = create_organization(db_session)
    db_session.commit()
    other_admin = create_member(db_session, other_org, role=MembershipRole.ADMIN)
    login(api, other_admin, organization_slug=other_org.slug)
    assert api.get(BASE).json() == []
    assert api.get(BASE, params={"user_id": str(editor.id)}).json() == []
    assert api.delete(f"{BASE}/{mapping['id']}").status_code == 404
    # Neither this organization's member nor its category is visible from the other side.
    foreign_user = api.post(
        BASE, json={"user_id": str(editor.id), "production_owner_category_id": owners["hang"]["id"]}
    )
    assert foreign_user.status_code == 404
    db_session.expire_all()
    row = db_session.get(MediaOwnerMapping, uuid.UUID(mapping["id"]))
    assert row is not None and row.status == "active"


@pytest.mark.database
def test_unauthenticated_requests_are_refused(api: TestClient, owners: dict) -> None:
    api.cookies.clear()
    assert api.get(BASE).status_code == 401
    assert api.get(f"{BASE}/me").status_code == 401
    assert api.post(BASE, json={}).status_code == 401
