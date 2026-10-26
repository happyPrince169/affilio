"""Scanner behaviour against a temp directory tree. Requires TEST_DATABASE_URL."""

from __future__ import annotations

import os
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from affilio.core.enums import (
    MediaAssetStatus,
    MediaVariantKind,
    MediaVariantStatus,
    ScanJobStatus,
    ScanTrigger,
    ThumbnailStatus,
)
from affilio.modules.media.errors import ScanConfigurationError
from affilio.modules.media.models import MediaAsset, MediaFileVariant, MediaScanJob
from affilio.modules.media.scanner import MediaScanner, ScanStats
from affilio.modules.media.service import enqueue_scan
from tests.media_helpers import (
    FakeProber,
    MediaDirs,
    build_settings,
    can_test_permissions,
    write_bytes,
)

pytestmark = pytest.mark.database


def _job(session: Session, org_id: uuid.UUID, dirs: MediaDirs, **kwargs) -> MediaScanJob:
    job = enqueue_scan(session, org_id, dirs.registry, trigger=ScanTrigger.CLI, **kwargs)
    job.status = ScanJobStatus.RUNNING
    session.flush()
    return job


def _scan(
    session: Session, org_id: uuid.UUID, dirs: MediaDirs, prober: FakeProber, **kwargs
) -> ScanStats:
    settings = build_settings(dirs)
    job = _job(session, org_id, dirs, **kwargs)
    stats = MediaScanner(session, dirs.registry, settings, prober=prober).run(job)
    job.status = ScanJobStatus.SUCCEEDED
    session.commit()
    return stats


def _assets(session: Session, org_id: uuid.UUID) -> list[MediaAsset]:
    return list(
        session.scalars(
            select(MediaAsset)
            .where(MediaAsset.organization_id == org_id)
            .order_by(MediaAsset.title)
        )
    )


def _bump_mtime(path: Path, seconds: int = 120) -> None:
    future = datetime.now(UTC) + timedelta(seconds=seconds)
    os.utime(path, (future.timestamp(), future.timestamp()))


def test_incremental_scan_adds_then_skips_then_updates(
    db_session, organization, media_dirs
) -> None:
    clip = write_bytes(media_dirs.originals / "2026" / "clip_one.mp4", 50_000, seed=1)
    write_bytes(media_dirs.originals / "2026" / "clip_two.MOV", 60_000, seed=2)
    (media_dirs.originals / "notes.txt").write_text("not a video")
    prober = FakeProber()

    first = _scan(db_session, organization.id, media_dirs, prober)
    assert (first.added, first.unchanged, first.skipped) == (2, 0, 1)
    assert len(prober.calls) == 2
    assets = _assets(db_session, organization.id)
    assert [a.title for a in assets] == ["clip one", "clip two"]
    assert assets[0].orientation == "vertical"
    assert assets[0].thumbnail_status == ThumbnailStatus.PENDING
    original = assets[0].variant(MediaVariantKind.ORIGINAL)
    assert original is not None and original.relative_path == "2026/clip_one.mp4"

    second = _scan(db_session, organization.id, media_dirs, prober)
    assert (second.added, second.unchanged, second.updated) == (0, 2, 0)
    assert len(prober.calls) == 2  # nothing re-probed: size+mtime unchanged → zero reads

    # Same bytes, newer mtime → fingerprint equal → treated as unchanged, no re-probe.
    _bump_mtime(clip)
    third = _scan(db_session, organization.id, media_dirs, prober)
    assert (third.unchanged, third.updated) == (2, 0)
    assert len(prober.calls) == 2

    # Content changes → re-probed, thumbnail reset.
    write_bytes(clip, 50_000, seed=99)
    _bump_mtime(clip, 240)
    fourth = _scan(db_session, organization.id, media_dirs, prober)
    assert fourth.updated == 1
    assert len(prober.calls) == 3


def test_missing_rediscovered_and_moved(db_session, organization, media_dirs) -> None:
    clip = write_bytes(media_dirs.originals / "a" / "clip.mp4", 40_000, seed=3)
    prober = FakeProber()
    _scan(db_session, organization.id, media_dirs, prober)
    asset = _assets(db_session, organization.id)[0]

    # Missing
    hidden = media_dirs.originals.parent / "hidden.mp4"
    shutil.move(clip, hidden)
    stats = _scan(db_session, organization.id, media_dirs, prober)
    db_session.refresh(asset)
    assert stats.missing == 1
    assert asset.status == MediaAssetStatus.MISSING and asset.missing_since is not None

    # Rediscovered at the same path
    shutil.move(hidden, clip)
    stats = _scan(db_session, organization.id, media_dirs, prober)
    db_session.refresh(asset)
    assert stats.rediscovered == 1
    assert asset.status == MediaAssetStatus.ACTIVE and asset.missing_since is None

    # Moved to a new folder → same asset re-pointed, no new asset, no re-probe
    probes_before = len(prober.calls)
    target = media_dirs.originals / "b" / "renamed.mp4"
    target.parent.mkdir()
    shutil.move(clip, target)
    stats = _scan(db_session, organization.id, media_dirs, prober)
    db_session.refresh(asset)
    assert (stats.moved, stats.added, stats.missing) == (1, 0, 0)
    assert len(prober.calls) == probes_before
    assert len(_assets(db_session, organization.id)) == 1
    original = asset.variant(MediaVariantKind.ORIGINAL)
    assert original is not None and original.relative_path == "b/renamed.mp4"
    assert asset.title == "renamed"


def test_duplicate_detection_confirms_with_full_hash(db_session, organization, media_dirs) -> None:
    write_bytes(media_dirs.originals / "one.mp4", 30_000, seed=5)
    write_bytes(media_dirs.originals / "copy_of_one.mp4", 30_000, seed=5)
    write_bytes(media_dirs.originals / "other.mp4", 30_000, seed=6)

    stats = _scan(db_session, organization.id, media_dirs, FakeProber())

    assert stats.duplicates == 1
    assets = {a.title: a for a in _assets(db_session, organization.id)}
    canonical = assets["one"] if assets["one"].duplicate_of_id is None else assets["copy of one"]
    duplicate = assets["copy of one"] if canonical is assets["one"] else assets["one"]
    assert duplicate.duplicate_of_id == canonical.id
    assert assets["other"].duplicate_of_id is None
    hashes = {
        v.relative_path: v.content_hash
        for v in db_session.scalars(
            select(MediaFileVariant).where(MediaFileVariant.kind == MediaVariantKind.ORIGINAL)
        )
        if v.asset.organization_id == organization.id
    }
    assert hashes["one.mp4"] == hashes["copy_of_one.mp4"] is not None
    assert hashes["other.mp4"] is None  # full hash only computed when fingerprints collide


def test_unsupported_media_is_recorded_not_fatal(db_session, organization, media_dirs) -> None:
    write_bytes(media_dirs.originals / "broken_file.mp4", 10_000, seed=8)
    write_bytes(media_dirs.originals / "good.mp4", 10_000, seed=9)

    stats = _scan(db_session, organization.id, media_dirs, FakeProber())

    assert (stats.added, stats.unsupported, stats.errors) == (2, 1, 1)
    assets = {a.title: a for a in _assets(db_session, organization.id)}
    assert assets["broken file"].status == MediaAssetStatus.UNSUPPORTED
    assert assets["broken file"].thumbnail_status == ThumbnailStatus.SKIPPED
    assert "unsupported" in (assets["broken file"].probe_error or "")
    assert assets["good"].status == MediaAssetStatus.ACTIVE


def test_ignores_synology_service_dirs_and_symlinks(db_session, organization, media_dirs) -> None:
    write_bytes(media_dirs.originals / "@eaDir" / "clip.mp4" / "SYNOPHOTO_THUMB.mp4", 1000, seed=1)
    write_bytes(media_dirs.originals / "#recycle" / "old.mp4", 1000, seed=2)
    write_bytes(media_dirs.originals / "._clip.mp4", 1000, seed=3)
    real = write_bytes(media_dirs.originals / "real.mp4", 1000, seed=4)
    os.symlink(real, media_dirs.originals / "link.mp4")

    stats = _scan(db_session, organization.id, media_dirs, FakeProber())

    assert stats.added == 1
    assert [a.title for a in _assets(db_session, organization.id)] == ["real"]


@pytest.mark.skipif(not can_test_permissions(), reason="permission test needs non-root POSIX")
def test_permission_denied_directory_is_reported(db_session, organization, media_dirs) -> None:
    locked = media_dirs.originals / "locked"
    write_bytes(locked / "secret.mp4", 1000, seed=1)
    write_bytes(media_dirs.originals / "open.mp4", 1000, seed=2)
    locked.chmod(0o000)
    try:
        settings = build_settings(media_dirs)
        job = _job(db_session, organization.id, media_dirs)
        stats = MediaScanner(db_session, media_dirs.registry, settings, prober=FakeProber()).run(
            job
        )
    finally:
        locked.chmod(0o755)

    assert stats.added == 1
    assert stats.errors == 1
    assert job.errors[0]["kind"] == "permission_denied"
    assert job.errors[0]["path"] == "locked"


def test_scan_refuses_missing_root(db_session, organization, media_dirs) -> None:
    job = _job(db_session, organization.id, media_dirs)
    shutil.rmtree(media_dirs.originals)

    with pytest.raises(ScanConfigurationError, match="does not exist"):
        MediaScanner(
            db_session, media_dirs.registry, build_settings(media_dirs), prober=FakeProber()
        ).run(job)


def test_proxy_linking_by_pattern(db_session, organization, media_dirs) -> None:
    write_bytes(media_dirs.originals / "shoot" / "A001.MOV", 20_000, seed=1)
    write_bytes(media_dirs.originals / "shoot" / "A002.MOV", 20_000, seed=2)
    write_bytes(media_dirs.proxies / "shoot" / "A001.mp4", 5_000, seed=11)  # matches {stem}.mp4
    write_bytes(media_dirs.proxies / "shoot" / "A002_proxy.mp4", 5_000, seed=12)  # {stem}_proxy.mp4
    prober = FakeProber()

    stats = _scan(db_session, organization.id, media_dirs, prober)

    assert stats.proxies_linked == 2
    for asset in _assets(db_session, organization.id):
        proxy = asset.variant(MediaVariantKind.PROXY)
        assert proxy is not None and proxy.status == MediaVariantStatus.PRESENT
        assert proxy.bit_depth == 10 and proxy.video_codec == "hevc"  # probed separately
        assert proxy.root.key == "proxies"

    # Proxy removed → variant marked missing on next scan that touches the asset.
    (media_dirs.proxies / "shoot" / "A001.mp4").unlink()
    write_bytes(media_dirs.originals / "shoot" / "A001.MOV", 20_001, seed=1)  # touch asset
    _scan(db_session, organization.id, media_dirs, prober)
    a001 = next(a for a in _assets(db_session, organization.id) if a.title == "A001")
    db_session.refresh(a001)
    proxy = a001.variant(MediaVariantKind.PROXY)
    assert proxy is not None and proxy.status == MediaVariantStatus.MISSING


def test_full_rescan_reprobes_everything(db_session, organization, media_dirs) -> None:
    write_bytes(media_dirs.originals / "x.mp4", 10_000, seed=1)
    prober = FakeProber()
    _scan(db_session, organization.id, media_dirs, prober)
    _scan(db_session, organization.id, media_dirs, prober, full_rescan=True)
    assert len(prober.calls) == 2
