"""Product HTTP API. Requires TEST_DATABASE_URL."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from affilio.app import create_app
from affilio.core.config import get_settings
from affilio.core.enums import MembershipRole, ScanTrigger
from affilio.db.session import dispose_engine
from affilio.modules.identity import service as identity_service
from affilio.modules.identity.models import User
from affilio.modules.media.models import MediaAsset
from affilio.modules.media.scanner import MediaScanner
from affilio.modules.media.service import enqueue_scan
from tests.auth_helpers import sign_in
from tests.media_helpers import FakeProber, build_settings, create_organization, write_bytes

pytestmark = pytest.mark.database

PRODUCT = {
    "name": "Portable Blender",
    "product_url": "https://shopee.vn/portable-blender-i.123.456",
    "affiliate_url": "https://s.shopee.vn/9xyz",
    "platform_product_id": "123.456",
    "seller_name": "Kitchen Shop",
    "category": "kitchen",
    "images": ["https://cf.shopee.vn/file/abc"],
    "currency": "VND",
    "observed_price": "199000",
    "estimated_commission_rate": "0.08",
    "research_notes": "Trending on TikTok",
}


@pytest.fixture
def api(
    monkeypatch: pytest.MonkeyPatch, media_dirs, organization, schema, db_session
) -> Iterator[TestClient]:
    for key, value in media_dirs.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DEFAULT_ORGANIZATION_SLUG", organization.slug)
    monkeypatch.delenv("MEDIA_ADMIN_TOKEN", raising=False)
    get_settings.cache_clear()
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        sign_in(client, db_session, organization)
        yield client
    dispose_engine()
    get_settings.cache_clear()


def _asset(session: Session, organization, media_dirs) -> MediaAsset:
    write_bytes(media_dirs.originals / "promo.mp4", 4_000, seed=1)
    job = enqueue_scan(session, organization.id, media_dirs.registry, trigger=ScanTrigger.CLI)
    MediaScanner(session, media_dirs.registry, build_settings(media_dirs), prober=FakeProber()).run(
        job
    )
    session.commit()
    asset = session.scalar(select(MediaAsset).where(MediaAsset.organization_id == organization.id))
    assert asset is not None
    return asset


def test_crud_roundtrip_and_schema(api: TestClient) -> None:
    created = api.post("/api/v1/products", json=PRODUCT)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "discovered"
    assert body["observed_price"] == "199000.00"  # Decimal serialised as string, 2 dp
    assert body["estimated_commission_rate"] == "0.0800"
    assert body["estimated_commission_amount"] is None
    assert body["media_count"] == 0
    assert body["allowed_transitions"] == ["archived", "shortlisted"]
    assert body["status_events"][0]["to_status"] == "discovered"
    product_id = body["id"]

    listed = api.get("/api/v1/products", params={"q": "blender"}).json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == product_id
    assert "research_notes" not in listed["items"][0]  # summary omits long text

    patched = api.patch(
        f"/api/v1/products/{product_id}", json={"research_notes": "Updated", "category": None}
    )
    assert patched.status_code == 200 and patched.json()["research_notes"] == "Updated"
    assert patched.json()["category"] is None

    detail = api.get(f"/api/v1/products/{product_id}")
    assert detail.status_code == 200 and detail.json()["name"] == "Portable Blender"
    assert api.get(f"/api/v1/products/{uuid.uuid4()}").status_code == 404

    stats = api.get("/api/v1/products/stats").json()
    assert (
        stats["total"] == 1
        and stats["by_status"]["discovered"] == 1
        and stats["with_affiliate_url"] == 1
    )


def test_validation_errors_use_envelope(api: TestClient) -> None:
    bad_url = api.post("/api/v1/products", json={"name": "x", "product_url": "javascript:alert(1)"})
    assert bad_url.status_code == 422
    assert bad_url.json()["error"]["code"] == "invalid_product_url"

    blank = api.post("/api/v1/products", json={"name": "   "})
    assert blank.status_code == 422 and blank.json()["error"]["code"] == "validation_error"

    unknown_field = api.post("/api/v1/products", json={"name": "x", "price": 1})
    assert unknown_field.status_code == 422

    winner = api.post("/api/v1/products", json={"name": "x", "status": "winner"})
    assert winner.status_code == 422


def test_duplicate_detection_via_api(api: TestClient) -> None:
    assert api.post("/api/v1/products", json=PRODUCT).status_code == 201
    dup = api.post("/api/v1/products", json={"name": "Again", "platform_product_id": "123.456"})
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "duplicate_product"
    assert "product_id" in dup.json()["error"]["details"]


def test_status_change_flow(api: TestClient) -> None:
    product_id = api.post("/api/v1/products", json={"name": "Lamp"}).json()["id"]
    url = f"/api/v1/products/{product_id}/status"

    invalid = api.post(url, json={"status": "winner", "confirmed": True, "note": "nope"})
    assert (
        invalid.status_code == 409
        and invalid.json()["error"]["code"] == "invalid_status_transition"
    )
    assert invalid.json()["error"]["details"]["allowed"] == ["archived", "shortlisted"]

    assert api.post(url, json={"status": "shortlisted"}).status_code == 200
    assert api.post(url, json={"status": "testing", "note": "2 videos live"}).status_code == 200
    needs_confirm = api.post(url, json={"status": "winner"})
    assert needs_confirm.status_code == 422
    assert needs_confirm.json()["error"]["code"] == "winner_confirmation_required"
    won = api.post(url, json={"status": "winner", "confirmed": True, "note": "Converted 3.2 %"})
    assert won.status_code == 200 and won.json()["status"] == "winner"

    history = api.get(f"/api/v1/products/{product_id}/status-history").json()
    assert [h["to_status"] for h in history] == ["winner", "testing", "shortlisted", "discovered"]
    assert history[0]["confirmed"] is True and history[0]["note"] == "Converted 3.2 %"


def test_media_links_via_api(
    api: TestClient, db_session: Session, organization, media_dirs
) -> None:
    asset = _asset(db_session, organization, media_dirs)
    product_id = api.post("/api/v1/products", json={"name": "Lamp"}).json()["id"]

    created = api.post(
        f"/api/v1/products/{product_id}/media",
        json={"media_asset_id": str(asset.id), "is_primary": True, "notes": "intro"},
    )
    assert created.status_code == 201, created.text
    link = created.json()
    assert link["is_primary"] is True and link["asset"]["id"] == str(asset.id)
    assert link["asset"]["thumbnail_status"] == "pending"

    dup = api.post(f"/api/v1/products/{product_id}/media", json={"media_asset_id": str(asset.id)})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "duplicate_media_link"

    missing = api.post(
        f"/api/v1/products/{product_id}/media", json={"media_asset_id": str(uuid.uuid4())}
    )
    assert missing.status_code == 404

    links = api.get(f"/api/v1/products/{product_id}/media").json()
    assert len(links) == 1 and links[0]["notes"] == "intro"
    reverse = api.get(f"/api/v1/products/media/{asset.id}").json()
    assert reverse[0]["product"]["id"] == product_id and reverse[0]["is_primary"] is True

    updated = api.patch(
        f"/api/v1/products/{product_id}/media/{asset.id}",
        json={"is_primary": False, "notes": "b-roll"},
    )
    assert updated.status_code == 200 and updated.json()["is_primary"] is False

    assert api.get("/api/v1/products", params={"has_media": "true"}).json()["total"] == 1
    assert api.delete(f"/api/v1/products/{product_id}/media/{asset.id}").status_code == 204
    assert api.delete(f"/api/v1/products/{product_id}/media/{asset.id}").status_code == 404
    assert api.get(f"/api/v1/products/{product_id}/media").json() == []


def test_products_scoped_by_organization_header(api: TestClient, db_session: Session) -> None:
    other = create_organization(db_session)
    db_session.commit()
    product_id = api.post("/api/v1/products", json={"name": "Lamp"}).json()["id"]
    # Not a member of `other`: selecting it is refused outright.
    refused = api.get(f"/api/v1/products/{product_id}", headers={"X-Organization-Slug": other.slug})
    assert refused.status_code == 403
    # A member of both organizations sees strict tenant isolation.
    me = api.get("/api/v1/auth/me").json()
    user = db_session.get(User, uuid.UUID(me["id"]))
    identity_service.add_membership(db_session, other, user, MembershipRole.ADMIN)
    db_session.commit()
    foreign = api.get(f"/api/v1/products/{product_id}", headers={"X-Organization-Slug": other.slug})
    assert foreign.status_code == 404
    assert (
        api.get("/api/v1/products", headers={"X-Organization-Slug": other.slug}).json()["total"]
        == 0
    )


def test_writes_require_admin_token_when_configured(
    monkeypatch: pytest.MonkeyPatch, media_dirs, organization, schema
) -> None:
    for key, value in media_dirs.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DEFAULT_ORGANIZATION_SLUG", organization.slug)
    monkeypatch.setenv("MEDIA_ADMIN_TOKEN", "tok")
    get_settings.cache_clear()
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        assert client.post("/api/v1/products", json={"name": "x"}).status_code == 401
        assert client.get("/api/v1/products").status_code == 401
        ok = client.post(
            "/api/v1/products", json={"name": "x"}, headers={"X-Affilio-Admin-Token": "tok"}
        )
        assert ok.status_code == 201
    dispose_engine()
    get_settings.cache_clear()
