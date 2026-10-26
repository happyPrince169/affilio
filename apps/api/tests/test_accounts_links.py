"""Social account registry and affiliate link library. Needs TEST_DATABASE_URL."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from affilio.app import create_app
from affilio.core.config import get_settings
from affilio.core.enums import MembershipRole
from affilio.db.session import dispose_engine
from tests.auth_helpers import create_member, login, sign_in
from tests.publishing_helpers import add_link, register_page

pytestmark = pytest.mark.database


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
        sign_in(client, db_session, organization, role=MembershipRole.ADMIN)
        yield client
    dispose_engine()
    get_settings.cache_clear()


def test_manual_page_is_unverified_and_disconnected(api: TestClient) -> None:
    page = register_page(
        api,
        "Deals Page",
        profile_url="https://www.facebook.com/DealsPage",
        external_account_id="1234567890",
    )
    assert (
        page["status"] == "disconnected"
        and page["is_verified"] is False
        and page["source"] == "manual"
    )
    assert page["connection_label"] == "Manual (unverified, not connected)"
    assert (
        page["capabilities"]["api_publishing"] is False
        and page["capabilities"]["assisted_publishing"] is True
    )
    assert "token" not in page and "access_token" not in str(page)

    dup = api.post(
        "/api/v1/accounts", json={"display_name": "Other", "external_account_id": "1234567890"}
    )
    assert dup.status_code == 409

    bad_url = api.post(
        "/api/v1/accounts", json={"display_name": "X", "profile_url": "javascript:alert(1)"}
    )
    assert bad_url.status_code == 422

    # Status can only be toggled between disabled/disconnected; CONNECTED needs OAuth (M3B).
    assert (
        api.post(f"/api/v1/accounts/{page['id']}/status", json={"status": "connected"}).status_code
        == 422
    )
    disabled = api.post(
        f"/api/v1/accounts/{page['id']}/status", json={"status": "disabled", "note": "paused"}
    ).json()
    assert disabled["status"] == "disabled" and "paused" in disabled["notes"]
    assert len(api.get("/api/v1/accounts", params={"include_disabled": "false"}).json()) == 0
    back = api.post(f"/api/v1/accounts/{page['id']}/status", json={"status": "disconnected"}).json()
    assert back["status"] == "disconnected" and back["is_verified"] is False

    edited = api.patch(
        f"/api/v1/accounts/{page['id']}", json={"handle": "@deals.page", "notes": "main page"}
    ).json()
    assert edited["handle"] == "deals.page"
    assert (
        api.patch(f"/api/v1/accounts/{page['id']}", json={"is_verified": True}).status_code == 422
    )


def test_link_library_validation_and_duplicates(api: TestClient) -> None:
    link = add_link(api, "Blender", "https://shopee.vn/blender-123?aff=abc")
    assert link["status"] == "active" and link["source"] == "manual"
    same = api.post(
        "/api/v1/content/links",
        json={
            "platform": "shopee",
            "label": "Blender again",
            "url": "HTTPS://SHOPEE.VN/blender-123/?aff=abc#x",
        },
    )
    assert same.status_code == 409
    for bad in (
        "javascript:alert(1)",
        "ftp://shopee.vn/x",
        "https://user:pw@shopee.vn/x",
        "not a url",
        "https://shopee.vn/a b",
    ):
        response = api.post(
            "/api/v1/content/links", json={"platform": "shopee", "label": "bad", "url": bad}
        )
        assert response.status_code == 422, bad
    listed = api.get("/api/v1/content/links", params={"q": "blend"}).json()
    assert [item["id"] for item in listed] == [link["id"]]
    archived = api.patch(f"/api/v1/content/links/{link['id']}", json={"status": "archived"}).json()
    assert archived["status"] == "archived"
    assert api.get("/api/v1/content/links").json() == []
    # An archived URL can be re-added as a fresh active link.
    assert (
        api.post(
            "/api/v1/content/links",
            json={
                "platform": "shopee",
                "label": "Blender v2",
                "url": "https://shopee.vn/blender-123?aff=abc",
            },
        ).status_code
        == 201
    )


def test_viewer_cannot_mutate_links_or_accounts(
    api: TestClient, db_session: Session, organization
) -> None:
    viewer = create_member(db_session, organization, role=MembershipRole.VIEWER)
    login(api, viewer)
    assert api.get("/api/v1/content/links").status_code == 200
    assert (
        api.post(
            "/api/v1/content/links", json={"label": "x", "url": "https://shopee.vn/x"}
        ).status_code
        == 403
    )
    assert api.get("/api/v1/accounts").status_code == 200
    assert api.post("/api/v1/accounts", json={"display_name": "x"}).status_code == 403
