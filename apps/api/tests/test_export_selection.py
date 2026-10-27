"""E2: which file the publisher streams (export vs final), API surface, isolation.

Requires TEST_DATABASE_URL. Files are synthetic (write_bytes) with FakeProber facts, so the
selection logic is exercised without ffmpeg.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from affilio.app import create_app
from affilio.core.config import get_settings
from affilio.core.enums import ExportJobStatus, MediaVariantKind, MediaVariantStatus, MembershipRole
from affilio.db.session import dispose_engine
from affilio.modules.identity.models import AuditEvent
from affilio.modules.media import exports
from affilio.modules.media.fingerprint import quick_fingerprint
from affilio.modules.media.models import MediaAsset, MediaExportJob, MediaFileVariant, MediaRoot
from affilio.modules.publishing import engine
from affilio.modules.publishing.media_checks import check_reel_media
from tests.auth_helpers import sign_in
from tests.media_helpers import build_settings, create_organization, write_bytes
from tests.publishing_helpers import make_final

pytestmark = pytest.mark.database


def _settings(media_dirs):
    return build_settings(media_dirs, media_final_settle_seconds=0)


def _final(asset: MediaAsset) -> MediaFileVariant:
    variant = asset.variant(MediaVariantKind.FINAL)
    assert variant is not None
    return variant


def make_export(
    session: Session,
    media_dirs,
    asset: MediaAsset,
    *,
    current: bool = True,
    recipe_key: str = exports.RECIPE_KEY,
    write_file: bool = True,
    pixel_format: str = "yuv420p",
    bit_depth: int = 8,
    size: int = 5_000,
) -> MediaFileVariant:
    """An export variant as E1 would have written it (file + provenance + probe facts)."""
    final = _final(asset)
    rel = exports.export_relative_path(asset.id, recipe_key)
    path = media_dirs.exports / rel
    if write_file:
        write_bytes(path, size, seed=99)
    root = session.scalar(
        select(MediaRoot).where(
            MediaRoot.organization_id == asset.organization_id, MediaRoot.key == "exports"
        )
    )
    assert root is not None
    now = datetime.now(UTC)
    variant = MediaFileVariant(
        asset=asset,
        media_root_id=root.id,
        kind=MediaVariantKind.EXPORT,
        status=MediaVariantStatus.PRESENT,
        relative_path=rel,
        file_name=rel.rsplit("/", 1)[-1],
        extension=".mp4",
        file_size=path.stat().st_size if write_file else size,
        modified_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC) if write_file else now,
        fingerprint=quick_fingerprint(path, path.stat().st_size) if write_file else "exportfp",
        duration_seconds=12.5,
        width=1080,
        height=1920,
        frame_rate=30.0,
        video_codec="h264",
        audio_codec="aac",
        pixel_format=pixel_format,
        bit_depth=bit_depth,
        container_format="mov,mp4,m4a,3gp,3g2,mj2",
        probed_at=now,
        last_seen_at=now,
        derived_from_variant_id=final.id,
        recipe_key=recipe_key,
        source_fingerprint=final.fingerprint if current else "0000deadbeef",
        source_file_size=final.file_size,
        source_modified_at=final.modified_at,
    )
    session.add(variant)
    session.commit()
    session.refresh(asset)
    return variant


def make_final_incompatible(session: Session, asset: MediaAsset) -> None:
    """Turn the indexed facts into what the NAS finals really are: HEVC 10-bit."""
    final = _final(asset)
    for target in (asset, final):
        target.video_codec = "hevc"
        target.pixel_format = "yuv420p10le"
        target.bit_depth = 10
    session.commit()
    session.refresh(asset)


# ---------------------------------------------------------------------------- selection


def test_compatible_final_is_streamed_directly(db_session, organization, media_dirs) -> None:
    asset = make_final(db_session, organization, media_dirs, "ok.mp4")
    check = check_reel_media(asset, media_dirs.registry, _settings(media_dirs))
    assert check.ok and check.source == "final" and check.root_key == "finals"
    assert check.export_state == "absent" and check.variant_id == _final(asset).id
    assert check.fingerprint == _final(asset).fingerprint


def test_current_export_is_preferred_over_an_incompatible_final(
    db_session, organization, media_dirs
) -> None:
    asset = make_final(db_session, organization, media_dirs, "ten_bit.mp4")
    make_final_incompatible(db_session, asset)
    rejected = check_reel_media(asset, media_dirs.registry, _settings(media_dirs))
    assert not rejected.ok and "8-bit" in rejected.summary()
    assert "Queue a new publish-ready export" in rejected.summary()

    export = make_export(db_session, media_dirs, asset)
    check = check_reel_media(asset, media_dirs.registry, _settings(media_dirs))
    assert check.ok, check.summary()
    assert check.source == "export" and check.export_state == "used"
    assert check.root_key == "exports" and check.variant_id == export.id
    assert check.relative_path == export.relative_path
    assert check.fingerprint == export.fingerprint == quick_fingerprint(check.path)
    assert check.path == media_dirs.exports / export.relative_path


def test_stale_export_is_rejected_and_final_must_stand_on_its_own(
    db_session, organization, media_dirs
) -> None:
    asset = make_final(db_session, organization, media_dirs, "stale.mp4")
    make_final_incompatible(db_session, asset)
    make_export(db_session, media_dirs, asset, current=False)
    check = check_reel_media(asset, media_dirs.registry, _settings(media_dirs))
    assert not check.ok and check.export_state == "stale"
    assert check.errors[0].startswith("Export unusable (stale: the final changed")
    assert any("8-bit" in e for e in check.errors)

    # A compatible final still publishes itself even when its export is stale.
    compatible = make_final(db_session, organization, media_dirs, "stale_but_ok.mp4")
    make_export(db_session, media_dirs, compatible, current=False)
    check = check_reel_media(compatible, media_dirs.registry, _settings(media_dirs))
    assert check.ok and check.source == "final" and check.export_state == "stale"


def test_missing_or_changed_export_file_is_never_used(db_session, organization, media_dirs) -> None:
    asset = make_final(db_session, organization, media_dirs, "gone.mp4")
    make_final_incompatible(db_session, asset)
    export = make_export(db_session, media_dirs, asset)
    (media_dirs.exports / export.relative_path).unlink()
    check = check_reel_media(asset, media_dirs.registry, _settings(media_dirs))
    assert not check.ok and check.export_state == "missing"
    assert "missing on the exports mount" in check.errors[0]

    write_bytes(media_dirs.exports / export.relative_path, 7_777, seed=3)  # replaced by hand
    check = check_reel_media(asset, media_dirs.registry, _settings(media_dirs))
    assert not check.ok and check.export_state == "invalid"
    assert "size changed" in check.errors[0]


def test_export_with_bad_facts_or_unsupported_recipe_is_rejected(
    db_session, organization, media_dirs
) -> None:
    asset = make_final(db_session, organization, media_dirs, "facts.mp4")
    make_final_incompatible(db_session, asset)
    make_export(db_session, media_dirs, asset, pixel_format="yuv420p10le", bit_depth=10)
    check = check_reel_media(asset, media_dirs.registry, _settings(media_dirs))
    assert not check.ok and check.export_state == "invalid"

    other = make_final(db_session, organization, media_dirs, "recipe.mp4")
    make_final_incompatible(db_session, other)
    make_export(db_session, media_dirs, other, recipe_key="reels-h264-v9")
    check = check_reel_media(other, media_dirs.registry, _settings(media_dirs))
    assert not check.ok and check.export_state == "unsupported_recipe"


def test_pinned_file_resolution_refuses_traversal_and_foreign_roots(media_dirs) -> None:
    registry = media_dirs.registry
    write_bytes(media_dirs.exports / "ab" / "x.mp4", 4_000, seed=1)

    def upload(**kw):
        base = {
            "file_root_key": "exports",
            "file_relative_path": "ab/x.mp4",
            "file_size": 4_000,
            "file_mtime": None,
            "file_fingerprint": None,
            "bytes_sent": 0,
        }
        base.update(kw)
        return SimpleNamespace(**base)

    path, stat = engine._resolve_pinned_file(registry, upload())
    assert path == media_dirs.exports / "ab" / "x.mp4" and stat.st_size == 4_000
    for bad in (
        upload(file_relative_path="../originals/raw.mp4"),
        upload(file_relative_path="/etc/passwd"),
        upload(file_root_key="originals"),
        upload(file_root_key="thumbnails"),
        upload(file_relative_path=None),
    ):
        with pytest.raises(engine._PermanentError) as exc_info:
            engine._resolve_pinned_file(registry, bad)
        assert exc_info.value.code == "media_incompatible"
    with pytest.raises(engine._PermanentError) as exc_info:
        engine._resolve_pinned_file(registry, upload(file_size=1))
    assert exc_info.value.code == "media_changed"
    with pytest.raises(engine._PermanentError) as exc_info:
        engine._resolve_pinned_file(registry, upload(file_fingerprint="not-the-file"))
    assert exc_info.value.code == "media_changed"


def test_readiness_and_export_state_from_indexed_facts(
    db_session, organization, media_dirs
) -> None:
    settings = _settings(media_dirs)
    ok = make_final(db_session, organization, media_dirs, "r_ok.mp4")
    assert exports.readiness_from_db(ok, settings) == ("final_ready", None)
    assert exports.asset_export_state(db_session, ok, media_dirs.registry, settings).state == (
        "not_needed"
    )

    ten = make_final(db_session, organization, media_dirs, "r_ten.mp4")
    make_final_incompatible(db_session, ten)
    state, detail = exports.readiness_from_db(ten, settings)
    assert state == "needs_export" and "yuv420p10le" in (detail or "")
    assert exports.asset_export_state(db_session, ten, media_dirs.registry, settings).state == (
        "not_generated"
    )
    job = exports.enqueue_export(db_session, organization.id, ten.id)
    db_session.commit()
    assert exports.asset_export_state(db_session, ten, media_dirs.registry, settings).state == (
        "queued"
    )
    job.status = ExportJobStatus.BLOCKED
    db_session.commit()
    assert exports.asset_export_state(db_session, ten, media_dirs.registry, settings).state == (
        "blocked"
    )
    export = make_export(db_session, media_dirs, ten)
    assert exports.readiness_from_db(ten, settings) == ("export_ready", None)
    assert exports.asset_export_state(db_session, ten, media_dirs.registry, settings).state == (
        "ready"
    )
    export.source_fingerprint = "changed"
    db_session.commit()
    db_session.refresh(ten)
    assert exports.readiness_from_db(ten, settings)[0] == "export_stale"
    assert exports.asset_export_state(db_session, ten, media_dirs.registry, settings).state == (
        "stale"
    )


# ---------------------------------------------------------------------------- API


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch, media_dirs, organization, schema, db_session: Session
) -> Iterator[TestClient]:
    for key, value in media_dirs.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DEFAULT_ORGANIZATION_SLUG", organization.slug)
    monkeypatch.setenv("MEDIA_FINAL_SETTLE_SECONDS", "0")
    monkeypatch.delenv("MEDIA_ADMIN_TOKEN", raising=False)
    get_settings.cache_clear()
    with TestClient(create_app(), raise_server_exceptions=False) as test_client:
        sign_in(test_client, db_session, organization, role=MembershipRole.ADMIN)
        yield test_client
    dispose_engine()
    get_settings.cache_clear()


def test_export_api_lifecycle_rbac_and_isolation(
    client: TestClient, db_session: Session, organization, media_dirs, monkeypatch
) -> None:
    asset = make_final(db_session, organization, media_dirs, "api.mp4")
    make_final_incompatible(db_session, asset)

    detail = client.get(f"/api/v1/media/assets/{asset.id}").json()
    assert detail["export"]["state"] == "not_generated" and detail["export"]["can_export"]
    assert "yuv420p10le" in detail["export"]["detail"]

    queued = client.post(f"/api/v1/media/assets/{asset.id}/export")
    assert queued.status_code == 202, queued.text
    job = queued.json()
    assert job["status"] == "queued" and job["trigger"] == "manual"
    assert client.post(f"/api/v1/media/assets/{asset.id}/export").status_code == 409
    assert client.get(f"/api/v1/media/assets/{asset.id}/export").json()["state"] == "queued"

    cancelled = client.post(f"/api/v1/media/exports/{job['id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert client.post(f"/api/v1/media/exports/{job['id']}/cancel").status_code == 409
    actions = db_session.scalars(
        select(AuditEvent.action).where(AuditEvent.organization_id == organization.id)
    ).all()
    assert "export.queued" in actions and "export.cancelled" in actions

    # footage cannot be exported; unknown asset is 404
    assert client.post(f"/api/v1/media/assets/{uuid.uuid4()}/export").status_code == 404

    # Another organization sees nothing of it.
    other_org = create_organization(db_session)
    db_session.commit()
    other_asset = make_final(db_session, other_org, media_dirs, "other.mp4")
    assert client.get(f"/api/v1/media/assets/{other_asset.id}/export").status_code == 404
    assert client.post(f"/api/v1/media/assets/{other_asset.id}/export").status_code == 404
    foreign_job = exports.enqueue_export(db_session, other_org.id, other_asset.id)
    db_session.commit()
    assert client.post(f"/api/v1/media/exports/{foreign_job.id}/cancel").status_code == 404
    db_session.delete(other_org)
    db_session.commit()

    # Editors may read the state but not queue or cancel.
    with TestClient(create_app(), raise_server_exceptions=False) as editor:
        sign_in(editor, db_session, organization, role=MembershipRole.EDITOR)
        assert editor.get(f"/api/v1/media/assets/{asset.id}/export").status_code == 200
        assert editor.post(f"/api/v1/media/assets/{asset.id}/export").status_code == 403
        me = editor.get("/api/v1/auth/me").json()
        assert me["permissions"]["manage_exports"] is False
    me = client.get("/api/v1/auth/me").json()
    assert me["permissions"]["manage_exports"] is True
    assert db_session.scalar(
        select(MediaExportJob).where(MediaExportJob.id == uuid.UUID(job["id"]))
    ).status == (ExportJobStatus.CANCELLED)
    assert (datetime.now(UTC) - timedelta(minutes=1)) < datetime.now(UTC)
