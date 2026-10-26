"""Media HTTP API. Requires TEST_DATABASE_URL."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from affilio.app import create_app
from affilio.core.config import get_settings
from affilio.core.enums import ScanTrigger, ThumbnailStatus
from affilio.db.session import dispose_engine
from affilio.modules.media.models import MediaAsset
from affilio.modules.media.scanner import MediaScanner
from affilio.modules.media.service import enqueue_scan
from affilio.modules.media.thumbnails import thumbnail_relative_path
from tests.auth_helpers import sign_in
from tests.media_helpers import FakeProber, build_settings, write_bytes

pytestmark = pytest.mark.database


@pytest.fixture
def media_client(
    monkeypatch: pytest.MonkeyPatch, media_dirs, organization, schema, db_session
) -> Iterator[TestClient]:
    for key, value in media_dirs.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DEFAULT_ORGANIZATION_SLUG", organization.slug)
    monkeypatch.delenv("MEDIA_ADMIN_TOKEN", raising=False)
    get_settings.cache_clear()
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        sign_in(client, db_session, organization)
        yield client
    dispose_engine()
    get_settings.cache_clear()


def _seed_assets(session: Session, organization, media_dirs, count: int = 3) -> list[MediaAsset]:
    for index in range(count):
        write_bytes(media_dirs.originals / f"clip_{index}.mp4", 4_000 + index, seed=index)
    write_bytes(media_dirs.originals / "broken_take.mp4", 4_000, seed=50)
    job = enqueue_scan(session, organization.id, media_dirs.registry, trigger=ScanTrigger.CLI)
    MediaScanner(session, media_dirs.registry, build_settings(media_dirs), prober=FakeProber()).run(
        job
    )
    session.commit()
    return list(session.query(MediaAsset).filter_by(organization_id=organization.id).all())


def test_roots_report_configuration_without_paths(media_client: TestClient, media_dirs) -> None:
    response = media_client.get("/api/v1/media/roots")

    assert response.status_code == 200
    roots = {r["key"]: r for r in response.json()}
    assert set(roots) == {"originals", "proxies", "finals", "thumbnails", "exports"}
    assert roots["originals"]["configured"] and roots["originals"]["readable"]
    assert roots["originals"]["writable"] is False
    assert roots["thumbnails"]["writable"] is True
    assert str(media_dirs.originals) not in response.text  # never leak absolute paths


def test_assets_empty_then_listed_with_filters(
    media_client: TestClient, db_session, organization, media_dirs
) -> None:
    empty = media_client.get("/api/v1/media/assets")
    assert empty.status_code == 200
    assert empty.json() == {"items": [], "page": 1, "page_size": 24, "total": 0, "total_pages": 0}

    _seed_assets(db_session, organization, media_dirs)

    listed = media_client.get("/api/v1/media/assets", params={"sort": "title", "page_size": 2})
    body = listed.json()
    assert body["total"] == 4 and body["total_pages"] == 2 and len(body["items"]) == 2
    assert body["items"][0]["title"] == "broken take"
    assert body["items"][0]["status"] == "unsupported"
    assert body["items"][1]["orientation"] == "vertical"
    assert body["items"][1]["thumbnail_url"] is None  # not generated yet
    assert body["items"][1]["relative_path"] == "clip_0.mp4"

    searched = media_client.get("/api/v1/media/assets", params={"q": "clip_1"})
    assert [item["title"] for item in searched.json()["items"]] == ["clip 1"]

    active = media_client.get("/api/v1/media/assets", params={"status": "active"})
    assert active.json()["total"] == 3
    horizontal = media_client.get("/api/v1/media/assets", params={"orientation": "horizontal"})
    assert horizontal.json()["total"] == 0
    with_proxy = media_client.get("/api/v1/media/assets", params={"has_proxy": "true"})
    assert with_proxy.json()["total"] == 0

    stats = media_client.get("/api/v1/media/stats").json()
    assert (stats["total"], stats["active"], stats["unsupported"]) == (4, 3, 1)
    assert stats["thumbnails_pending"] == 3


def test_asset_detail_and_thumbnail_lifecycle(
    media_client: TestClient, db_session, organization, media_dirs
) -> None:
    assets = _seed_assets(db_session, organization, media_dirs, count=1)
    asset = next(a for a in assets if a.status == "active")

    detail = media_client.get(f"/api/v1/media/assets/{asset.id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["id"] == str(asset.id)
    assert [v["kind"] for v in body["variants"]] == ["original"]
    assert body["variants"][0]["root_key"] == "originals"
    assert body["variants"][0]["relative_path"] == "clip_0.mp4"

    missing = media_client.get(f"/api/v1/media/assets/{asset.id}/thumbnail")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"

    # Simulate the worker having written the thumbnail.
    rel = thumbnail_relative_path(asset.id)
    target = media_dirs.thumbnails / rel
    target.parent.mkdir(parents=True)
    target.write_bytes(b"\xff\xd8\xff\xd9")
    from affilio.core.enums import MediaVariantKind, MediaVariantStatus
    from affilio.modules.media.models import MediaFileVariant, MediaRoot

    thumbs_root = (
        db_session.query(MediaRoot)
        .filter_by(organization_id=organization.id, key="thumbnails")
        .one()
    )
    db_session.add(
        MediaFileVariant(
            asset_id=asset.id,
            media_root_id=thumbs_root.id,
            kind=MediaVariantKind.THUMBNAIL,
            status=MediaVariantStatus.PRESENT,
            relative_path=rel,
            file_name=rel.split("/")[-1],
            extension=".jpg",
            file_size=4,
            modified_at=asset.created_at,
        )
    )
    asset.thumbnail_status = ThumbnailStatus.READY
    db_session.commit()

    served = media_client.get(f"/api/v1/media/assets/{asset.id}/thumbnail")
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/jpeg"
    assert served.content == b"\xff\xd8\xff\xd9"
    listed = media_client.get("/api/v1/media/assets", params={"status": "active"}).json()
    assert listed["items"][0]["thumbnail_url"].endswith(
        f"/api/v1/media/assets/{asset.id}/thumbnail"
    )


def test_unknown_asset_and_invalid_ids(media_client: TestClient) -> None:
    assert media_client.get(f"/api/v1/media/assets/{uuid.uuid4()}").status_code == 404
    bad = media_client.get("/api/v1/media/assets/not-a-uuid")
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "validation_error"
    traversal = media_client.get("/api/v1/media/assets/../../etc/passwd")
    assert traversal.status_code in {404, 422}


def test_unknown_organization_header(media_client: TestClient) -> None:
    # A signed-in user may only select organizations they belong to (fail closed).
    response = media_client.get("/api/v1/media/assets", headers={"X-Organization-Slug": "nope"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert response.json()["error"]["details"] == {"slug": "nope"}


def test_scan_queue_and_history(media_client: TestClient) -> None:
    status_before = media_client.get("/api/v1/media/scans/status").json()
    assert status_before["current"] is None and status_before["last_completed"] is None

    created = media_client.post("/api/v1/media/scans", json={})
    assert created.status_code == 202, created.text
    job = created.json()
    assert job["status"] == "queued" and job["root_key"] == "originals"

    again = media_client.post("/api/v1/media/scans", json={"full_rescan": True})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "scan_already_active"

    status_after = media_client.get("/api/v1/media/scans/status").json()
    assert status_after["current"]["id"] == job["id"]
    history = media_client.get("/api/v1/media/scans").json()
    assert [h["id"] for h in history] == [job["id"]]
    assert media_client.get(f"/api/v1/media/scans/{job['id']}").status_code == 200
    assert media_client.get(f"/api/v1/media/scans/{uuid.uuid4()}").status_code == 404

    unknown_root = media_client.post("/api/v1/media/scans", json={"root_key": "proxies"})
    assert unknown_root.status_code == 404


def test_scan_requires_admin_token_when_configured(
    monkeypatch: pytest.MonkeyPatch, media_dirs, organization, schema
) -> None:
    for key, value in media_dirs.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DEFAULT_ORGANIZATION_SLUG", organization.slug)
    monkeypatch.setenv("MEDIA_ADMIN_TOKEN", "s3cret-token")
    get_settings.cache_clear()
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        denied = client.post("/api/v1/media/scans", json={})
        assert denied.status_code == 401
        assert denied.json()["error"]["code"] == "authentication_required"
        wrong = client.post(
            "/api/v1/media/scans", json={}, headers={"X-Affilio-Admin-Token": "nope"}
        )
        assert wrong.status_code == 401
        ok = client.post(
            "/api/v1/media/scans", json={}, headers={"X-Affilio-Admin-Token": "s3cret-token"}
        )
        assert ok.status_code == 202
        # The service token is a narrow service identity, visible as such.
        me = client.get("/api/v1/auth/me", headers={"X-Affilio-Admin-Token": "s3cret-token"})
        assert me.status_code == 200 and me.json()["identity_kind"] == "service"
        # Reads are NOT open without identity (fail closed).
        assert client.get("/api/v1/media/assets").status_code == 401
    dispose_engine()
    get_settings.cache_clear()


def test_scan_refused_in_production_without_token(
    monkeypatch: pytest.MonkeyPatch, media_dirs, organization, schema
) -> None:
    for key, value in media_dirs.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DEFAULT_ORGANIZATION_SLUG", organization.slug)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("MEDIA_ADMIN_TOKEN", raising=False)
    get_settings.cache_clear()
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        anonymous = client.post("/api/v1/media/scans", json={})
        assert anonymous.status_code == 401
        response = client.post(
            "/api/v1/media/scans", json={}, headers={"X-Affilio-Admin-Token": "anything"}
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "admin_endpoints_disabled"
    dispose_engine()
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()


def test_scan_without_configured_root(
    monkeypatch: pytest.MonkeyPatch, organization, schema, db_session
) -> None:
    for key in (
        "MEDIA_ORIGINALS_PATH",
        "MEDIA_PROXIES_PATH",
        "MEDIA_THUMBNAILS_PATH",
        "MEDIA_EXPORTS_PATH",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DEFAULT_ORGANIZATION_SLUG", organization.slug)
    get_settings.cache_clear()
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        sign_in(client, db_session, organization)
        roots = client.get("/api/v1/media/roots").json()
        assert all(r["configured"] is False for r in roots)
        response = client.post("/api/v1/media/scans", json={})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "media_root_not_configured"
    dispose_engine()
    get_settings.cache_clear()
