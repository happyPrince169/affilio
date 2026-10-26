"""Worker claim/retry/thumbnail behaviour. Requires TEST_DATABASE_URL."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from affilio.core.enums import MediaVariantKind, ScanJobStatus, ScanTrigger, ThumbnailStatus
from affilio.modules.media.models import MediaAsset
from affilio.modules.media.service import enqueue_scan
from affilio.modules.media.thumbnails import ThumbnailGenerator, seek_position
from affilio.worker.runner import claim_next_scan_job, execute_scan_job
from tests.media_helpers import FakeProber, build_settings, make_video, requires_ffmpeg, write_bytes

pytestmark = pytest.mark.database


def test_claim_runs_and_succeeds(db_session: Session, organization, media_dirs) -> None:
    write_bytes(media_dirs.originals / "clip.mp4", 5_000, seed=1)
    settings = build_settings(media_dirs)
    enqueue_scan(db_session, organization.id, media_dirs.registry, trigger=ScanTrigger.MANUAL)
    db_session.commit()

    job = claim_next_scan_job(db_session)
    assert job is not None and job.status == ScanJobStatus.RUNNING and job.attempt == 1
    db_session.commit()
    job = execute_scan_job(
        db_session, job, settings=settings, registry=media_dirs.registry, prober=FakeProber()
    )

    assert job.status == ScanJobStatus.SUCCEEDED
    assert job.stats["added"] == 1 and job.finished_at is not None
    assert claim_next_scan_job(db_session) is None  # queue drained


def test_failed_scan_retries_with_backoff_then_fails(
    db_session: Session, organization, media_dirs
) -> None:
    settings = build_settings(media_dirs)
    enqueue_scan(
        db_session, organization.id, media_dirs.registry, trigger=ScanTrigger.MANUAL, max_attempts=2
    )
    db_session.commit()
    shutil.rmtree(media_dirs.originals)  # root disappears (e.g. NAS share unmounted)

    job = claim_next_scan_job(db_session)
    assert job is not None
    db_session.commit()
    job = execute_scan_job(db_session, job, settings=settings, registry=media_dirs.registry)
    assert job.status == ScanJobStatus.QUEUED  # re-queued for retry
    assert job.attempt == 1 and job.next_attempt_at > datetime.now(UTC)
    assert "does not exist" in (job.error or "")

    assert claim_next_scan_job(db_session) is None  # not yet due
    job.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    job = claim_next_scan_job(db_session)
    assert job is not None and job.attempt == 2
    db_session.commit()
    job = execute_scan_job(db_session, job, settings=settings, registry=media_dirs.registry)
    assert job.status == ScanJobStatus.FAILED


def test_seek_position_bounds() -> None:
    assert seek_position(None) == 0.0
    assert seek_position(1.0) == 0.1
    assert seek_position(600.0) == 3.0


@requires_ffmpeg
def test_thumbnail_generation_prefers_proxy_and_writes_only_thumbnails_root(
    db_session: Session, organization, media_dirs, schema
) -> None:
    make_video(media_dirs.originals / "clip.mp4", size="36x64")
    make_video(media_dirs.proxies / "clip.mp4", size="18x32")
    settings = build_settings(media_dirs)
    enqueue_scan(db_session, organization.id, media_dirs.registry, trigger=ScanTrigger.MANUAL)
    db_session.commit()
    job = claim_next_scan_job(db_session)
    db_session.commit()
    execute_scan_job(
        db_session, job, settings=settings, registry=media_dirs.registry
    )  # real ffprobe

    asset = db_session.scalar(
        select(MediaAsset).where(MediaAsset.organization_id == organization.id)
    )
    assert asset is not None and asset.thumbnail_status == ThumbnailStatus.PENDING
    generator = ThumbnailGenerator(settings, media_dirs.registry)
    source = generator.pick_source(asset)
    assert source is not None and source[0] == "proxies"

    factory = sessionmaker(bind=schema, expire_on_commit=False)
    processed = generator.process_pending(factory, limit=4)
    assert processed == 1

    db_session.expire_all()
    asset = db_session.get(MediaAsset, asset.id)
    assert asset is not None and asset.thumbnail_status == ThumbnailStatus.READY
    thumb = asset.variant(MediaVariantKind.THUMBNAIL)
    assert thumb is not None
    written = media_dirs.thumbnails / thumb.relative_path
    assert written.is_file() and written.stat().st_size > 0
    assert not any(media_dirs.originals.rglob("*.jpg"))
    assert not any(media_dirs.proxies.rglob("*.jpg"))
    assert not list(media_dirs.thumbnails.rglob(".*.tmp.jpg"))


def test_thumbnail_failure_counts_attempts(db_session: Session, organization, media_dirs) -> None:
    write_bytes(media_dirs.originals / "junk.mp4", 2_000, seed=3)
    settings = build_settings(
        media_dirs, media_ffmpeg_path="/no/such/ffmpeg", media_thumbnail_max_attempts=2
    )
    enqueue_scan(db_session, organization.id, media_dirs.registry, trigger=ScanTrigger.MANUAL)
    db_session.commit()
    job = claim_next_scan_job(db_session)
    db_session.commit()
    execute_scan_job(
        db_session, job, settings=settings, registry=media_dirs.registry, prober=FakeProber()
    )
    asset = db_session.scalar(
        select(MediaAsset).where(MediaAsset.organization_id == organization.id)
    )
    assert asset is not None
    generator = ThumbnailGenerator(settings, media_dirs.registry)

    assert generator.generate(db_session, asset) is False
    assert asset.thumbnail_status == ThumbnailStatus.PENDING and asset.thumbnail_attempts == 1
    assert generator.generate(db_session, asset) is False
    assert asset.thumbnail_status == ThumbnailStatus.FAILED and asset.thumbnail_attempts == 2
    assert "not found" in (asset.thumbnail_error or "")
