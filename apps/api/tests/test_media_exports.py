"""Publish-ready export pipeline E1: real ffmpeg, real ffprobe, PostgreSQL.

Requires TEST_DATABASE_URL and an ffmpeg with libx265 (10-bit) + libx264 + aac.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from affilio.core.config import Settings, get_settings
from affilio.core.enums import (
    ExportJobStatus,
    ExportTrigger,
    MediaAssetKind,
    MediaVariantKind,
    MediaVariantStatus,
    ScanJobStatus,
    ScanTrigger,
)
from affilio.core.redaction import PATH_PLACEHOLDER
from affilio.db.session import dispose_engine
from affilio.modules.media import exports
from affilio.modules.media.errors import ExportAlreadyActiveError, ExportNotEligibleError
from affilio.modules.media.exports import (
    ExportGenerator,
    cancel_export,
    claim_next_export_job,
    enqueue_export,
    export_is_current,
    recover_stale_export_jobs,
)
from affilio.modules.media.models import MediaAsset, MediaExportJob, MediaFileVariant
from affilio.modules.media.probe import FFprobe
from affilio.modules.media.scanner import MediaScanner
from affilio.modules.media.service import enqueue_scan
from affilio.scripts import media_export
from tests.media_helpers import FFMPEG, FFPROBE, build_settings, requires_ffmpeg

pytestmark = [pytest.mark.database, requires_ffmpeg]


def _supports(encoder: str) -> bool:
    if not FFMPEG:
        return False
    out = subprocess.run(  # noqa: S603
        [FFMPEG, "-hide_banner", "-encoders"], capture_output=True, text=True, check=False
    ).stdout
    return f" {encoder} " in out


requires_x265 = pytest.mark.skipif(not _supports("libx265"), reason="libx265 encoder missing")


def make_clip(
    path: Path,
    *,
    size: str = "540x960",
    seconds: float = 4.0,
    rate: int = 30,
    codec: str = "libx265",
    pix_fmt: str = "yuv420p10le",
    audio: bool = True,
    hdr: bool = False,
) -> Path:
    """Render a synthetic clip the way an editor's export would look (default: 10-bit HEVC)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        FFMPEG,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={seconds}:size={size}:rate={rate}",
    ]
    if audio:
        command += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}:sample_rate=44100"]
    command += ["-c:v", codec, "-preset", "ultrafast", "-pix_fmt", pix_fmt]
    if codec == "libx265":
        command += ["-x265-params", "log-level=error", "-tag:v", "hvc1"]
    if hdr:
        command += [
            "-color_primaries",
            "bt2020",
            "-color_trc",
            "smpte2084",
            "-colorspace",
            "bt2020nc",
        ]
    if audio:
        command += ["-c:a", "aac", "-ar", "44100", "-shortest"]
    command += [str(path)]
    subprocess.run(command, check=True, timeout=120)  # noqa: S603
    return path


def _settings(media_dirs, **overrides) -> Settings:
    values = {
        "media_final_settle_seconds": 0,
        "export_preset": "ultrafast",
        "export_threads": 2,
        "export_heartbeat_seconds": 0.1,
        "export_stale_after_seconds": 1,
        "export_timeout_min_seconds": 120,
    }
    values.update(overrides)
    return build_settings(media_dirs, **values)


def _index_finals(
    session: Session, organization, media_dirs, settings: Settings
) -> list[MediaAsset]:
    job = enqueue_scan(
        session, organization.id, media_dirs.registry, root_key="finals", trigger=ScanTrigger.CLI
    )
    job.status = ScanJobStatus.RUNNING
    prober = FFprobe(settings.media_ffprobe_path, settings.media_probe_timeout_seconds)
    MediaScanner(session, media_dirs.registry, settings, prober=prober).run(job)
    job.status = ScanJobStatus.SUCCEEDED
    session.commit()
    assets = session.scalars(
        select(MediaAsset)
        .options(selectinload(MediaAsset.variants))
        .where(
            MediaAsset.organization_id == organization.id,
            MediaAsset.asset_kind == MediaAssetKind.FINAL,
        )
        .order_by(MediaAsset.title)
    ).all()
    for asset in assets:
        session.refresh(asset)
    return assets


def _run_export(
    session: Session,
    organization,
    media_dirs,
    settings: Settings,
    asset: MediaAsset,
    *,
    worker="w1",
    **kw,
) -> MediaExportJob:
    job = enqueue_export(session, organization.id, asset.id, trigger=ExportTrigger.CLI, **kw)
    session.commit()
    claimed = claim_next_export_job(session, worker)
    assert claimed is not None and claimed.id == job.id
    session.commit()
    return ExportGenerator(settings, media_dirs.registry, worker_id=worker).run(session, claimed)


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file()
    }


def _probe(path: Path):
    return FFprobe(FFPROBE, 60).probe(path)


# ---------------------------------------------------------------------------- the happy path


@requires_x265
def test_export_renders_10bit_hevc_final_into_publish_ready_h264(
    db_session: Session, organization, media_dirs
) -> None:
    source = make_clip(media_dirs.finals / "Hang" / "T9" / "Couple áo bé.mp4")
    settings = _settings(media_dirs)
    (asset,) = _index_finals(db_session, organization, media_dirs, settings)
    final = asset.variant(MediaVariantKind.FINAL)
    assert final is not None and final.pixel_format == "yuv420p10le"
    finals_before = _snapshot(media_dirs.finals)
    assets_before = db_session.scalar(select(func.count(MediaAsset.id)))

    job = _run_export(db_session, organization, media_dirs, settings, asset)

    assert job.status == ExportJobStatus.READY, (job.error_code, job.error)
    assert job.output_variant_id is not None and job.stats["reused"] is False
    assert job.stats["source_problems"] == ["pixel format yuv420p10le is not 8-bit yuv420p"]
    assert job.recipe["key"] == exports.RECIPE_KEY and job.recipe["ffmpeg_version"]
    assert not any("/" in a and a.endswith(".mp4") for a in job.recipe["args"])  # placeholders only
    assert job.source_fingerprint == final.fingerprint and job.source_file_size == final.file_size

    db_session.refresh(asset)
    export = asset.variant(MediaVariantKind.EXPORT)
    assert export is not None and export.id == job.output_variant_id
    assert export.derived_from_variant_id == final.id and export.recipe_key == exports.RECIPE_KEY
    assert (
        export.source_fingerprint == final.fingerprint
        and export.source_file_size == final.file_size
    )
    assert export.root.key == "exports" and export.status == MediaVariantStatus.PRESENT
    assert export.relative_path == exports.export_relative_path(asset.id)
    assert export_is_current(export, final)
    out = media_dirs.exports / export.relative_path
    assert out.is_file() and out.stat().st_size == export.file_size == job.stats["output_bytes"]
    probe = _probe(out)
    assert probe.video_codec == "h264" and probe.pixel_format == "yuv420p" and probe.bit_depth == 8
    assert probe.audio_codec == "aac" and probe.width == 540 and probe.height == 960
    assert probe.frame_rate == 30.0 and abs((probe.duration_seconds or 0) - 4.0) < 0.6
    assert export.video_codec == "h264" and export.probe and export.probed_at
    # Immutable sources, one asset, no temp files.
    assert _snapshot(media_dirs.finals) == finals_before and source.exists()
    assert db_session.scalar(select(func.count(MediaAsset.id))) == assets_before
    assert not list(media_dirs.exports.rglob(".*tmp*"))
    assert not list(media_dirs.finals.rglob("*.tmp*"))


@requires_x265
def test_existing_export_is_reused_and_stale_after_source_change(
    db_session: Session, organization, media_dirs, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_clip(media_dirs.finals / "a.mp4")
    settings = _settings(media_dirs)
    (asset,) = _index_finals(db_session, organization, media_dirs, settings)
    first = _run_export(db_session, organization, media_dirs, settings, asset)
    assert first.status == ExportJobStatus.READY

    def explode(*_args, **_kwargs):
        raise AssertionError("ffmpeg must not run for a reusable export")

    monkeypatch.setattr(ExportGenerator, "_run_ffmpeg", explode)
    second = _run_export(db_session, organization, media_dirs, settings, asset)
    assert second.status == ExportJobStatus.READY and second.stats["reused"] is True
    assert second.output_variant_id == first.output_variant_id
    assert (
        db_session.scalar(
            select(func.count(MediaFileVariant.id)).where(
                MediaFileVariant.kind == MediaVariantKind.EXPORT
            )
        )
        == 1
    )

    # The editor replaces the final: the recorded identity no longer matches → export is stale
    # for consumers, and a new job refuses to run against a changed, un-rescanned source.
    make_clip(source, seconds=5.0)
    db_session.refresh(asset)
    final = asset.variant(MediaVariantKind.FINAL)
    export = asset.variant(MediaVariantKind.EXPORT)
    db_session.refresh(final)
    assert export_is_current(export, final)  # DB still describes the old file …
    monkeypatch.undo()
    third = _run_export(db_session, organization, media_dirs, settings, asset)
    assert third.status == ExportJobStatus.FAILED and third.error_code == "source_changed"
    # … and after a rescan the identity differs, so the old export is reported stale.
    _index_finals(db_session, organization, media_dirs, settings)
    db_session.refresh(final)
    assert not export_is_current(export, final)


def test_compliant_source_is_skipped_without_a_file(
    db_session: Session, organization, media_dirs
) -> None:
    make_clip(media_dirs.finals / "ok.mp4", codec="libx264", pix_fmt="yuv420p")
    settings = _settings(media_dirs)
    (asset,) = _index_finals(db_session, organization, media_dirs, settings)

    job = _run_export(db_session, organization, media_dirs, settings, asset)

    assert job.status == ExportJobStatus.SKIPPED and job.error_code == "source_compliant"
    assert job.output_variant_id is None
    assert not list(media_dirs.exports.rglob("*.mp4"))
    db_session.refresh(asset)
    assert asset.variant(MediaVariantKind.EXPORT) is None


@requires_x265
def test_hdr_source_is_blocked_not_converted(db_session: Session, organization, media_dirs) -> None:
    make_clip(media_dirs.finals / "hdr.mp4", hdr=True)
    settings = _settings(media_dirs)
    (asset,) = _index_finals(db_session, organization, media_dirs, settings)
    final = asset.variant(MediaVariantKind.FINAL)
    assert (final.probe["streams"][0].get("color_transfer") or "") == "smpte2084"

    job = _run_export(db_session, organization, media_dirs, settings, asset)

    assert job.status == ExportJobStatus.BLOCKED and job.error_code == "hdr_source"
    assert "smpte2084" in (job.error or "") and job.stats["color"]["transfer"] == "smpte2084"
    assert not list(media_dirs.exports.rglob("*.mp4"))


@requires_x265
def test_eligibility_rules(db_session: Session, organization, media_dirs) -> None:
    make_clip(media_dirs.finals / "b.mp4")
    settings = _settings(media_dirs)
    (asset,) = _index_finals(db_session, organization, media_dirs, settings)
    first = enqueue_export(db_session, organization.id, asset.id)
    db_session.commit()
    with pytest.raises(ExportAlreadyActiveError):
        enqueue_export(db_session, organization.id, asset.id)
    cancel_export(db_session, organization.id, first.id, actor="t")
    db_session.commit()
    assert enqueue_export(db_session, organization.id, asset.id).status == ExportJobStatus.QUEUED
    # footage is never exported for publishing
    footage = MediaAsset(
        organization_id=organization.id, title="raw", asset_kind=MediaAssetKind.FOOTAGE
    )
    db_session.add(footage)
    db_session.flush()
    with pytest.raises(ExportNotEligibleError):
        enqueue_export(db_session, organization.id, footage.id)


# ---------------------------------------------------------------------------- safety


@requires_x265
def test_cancellation_kills_ffmpeg_and_leaves_no_output(
    db_session: Session, organization, media_dirs, schema: Engine
) -> None:
    make_clip(media_dirs.finals / "long.mp4", size="1080x1920", seconds=8.0)
    settings = _settings(media_dirs, export_preset="medium", export_heartbeat_seconds=0.05)
    (asset,) = _index_finals(db_session, organization, media_dirs, settings)
    job = enqueue_export(db_session, organization.id, asset.id)
    db_session.commit()
    claimed = claim_next_export_job(db_session, "w1")
    db_session.commit()

    other = sessionmaker(bind=schema, autoflush=False, expire_on_commit=False)
    generator = ExportGenerator(settings, media_dirs.registry, worker_id="w1")
    original_run = generator._run_ffmpeg

    def cancel_then_run(session, fence, plan, source, tmp, budget):
        with other() as admin:  # the operator cancels while ffmpeg is about to run
            cancel_export(admin, organization.id, job.id, actor="admin")
            admin.commit()
        original_run(session, fence, plan, source, tmp, budget)

    generator._run_ffmpeg = cancel_then_run  # type: ignore[method-assign]
    result = generator.run(db_session, claimed)

    assert result.status == ExportJobStatus.CANCELLED
    assert not [p for p in media_dirs.exports.rglob("*") if p.is_file()]  # no output, no temp
    db_session.refresh(asset)
    assert asset.variant(MediaVariantKind.EXPORT) is None


@requires_x265
def test_timeout_is_retried_then_failed(db_session: Session, organization, media_dirs) -> None:
    make_clip(media_dirs.finals / "slow.mp4", size="1080x1920", seconds=8.0)
    settings = _settings(
        media_dirs, export_preset="medium", export_timeout_factor=0.0, export_timeout_min_seconds=1
    )
    (asset,) = _index_finals(db_session, organization, media_dirs, settings)

    first = _run_export(db_session, organization, media_dirs, settings, asset, max_attempts=2)
    assert first.status == ExportJobStatus.QUEUED and first.error_code == "timeout"
    assert first.next_attempt_at is not None and first.next_attempt_at > datetime.now(UTC)
    first.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    claimed = claim_next_export_job(db_session, "w1")
    assert claimed is not None and claimed.attempt == 2
    db_session.commit()
    second = ExportGenerator(settings, media_dirs.registry, worker_id="w1").run(db_session, claimed)
    assert second.status == ExportJobStatus.FAILED and "no attempts left" in (second.error or "")
    assert not [p for p in media_dirs.exports.rglob("*") if p.is_file()]


@requires_x265
def test_recovered_zombie_cannot_finalize(
    db_session: Session, organization, media_dirs, schema: Engine
) -> None:
    make_clip(media_dirs.finals / "z.mp4")
    settings = _settings(
        media_dirs, export_heartbeat_seconds=3600, export_stale_after_seconds=36000
    )
    (asset,) = _index_finals(db_session, organization, media_dirs, settings)
    job = enqueue_export(db_session, organization.id, asset.id, max_attempts=3)
    db_session.commit()
    claimed = claim_next_export_job(db_session, "zombie")
    db_session.commit()

    other = sessionmaker(bind=schema, autoflush=False, expire_on_commit=False)
    with other() as watchdog:
        stale = watchdog.get(MediaExportJob, job.id)
        stale.heartbeat_at = datetime.now(UTC) - timedelta(hours=2)
        watchdog.commit()
        recovered = recover_stale_export_jobs(watchdog, stale_after=timedelta(minutes=10))
        assert [r.id for r in recovered] == [job.id]
        watchdog.commit()

    # The zombie encodes the whole file (no mid-run heartbeat) and must fail at the fence.
    result = ExportGenerator(settings, media_dirs.registry, worker_id="zombie").run(
        db_session, claimed
    )
    assert result.status == ExportJobStatus.QUEUED and result.error_code == "worker_lost"
    assert result.output_variant_id is None
    assert not list(media_dirs.exports.rglob("*.mp4"))
    db_session.refresh(asset)
    assert asset.variant(MediaVariantKind.EXPORT) is None

    # A live worker then processes it exactly once.
    result.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    live = claim_next_export_job(db_session, "live")
    assert live is not None and live.attempt == 2
    db_session.commit()
    done = ExportGenerator(settings, media_dirs.registry, worker_id="live").run(db_session, live)
    assert done.status == ExportJobStatus.READY
    assert claim_next_export_job(db_session, "live") is None


def test_watchdog_exhausts_attempts(db_session: Session, organization, media_dirs) -> None:
    make_clip(media_dirs.finals / "w.mp4", codec="libx264", pix_fmt="yuv420p")
    settings = _settings(media_dirs)
    (asset,) = _index_finals(db_session, organization, media_dirs, settings)
    job = enqueue_export(db_session, organization.id, asset.id, max_attempts=1)
    db_session.commit()
    claimed = claim_next_export_job(db_session, "dead")
    claimed.heartbeat_at = datetime.now(UTC) - timedelta(hours=1)
    db_session.commit()
    assert recover_stale_export_jobs(db_session, stale_after=timedelta(minutes=15)) == [job]
    db_session.commit()
    assert job.status == ExportJobStatus.FAILED and job.error_code == "worker_lost"
    assert recover_stale_export_jobs(db_session, stale_after=timedelta(minutes=15)) == []


def test_missing_ffmpeg_error_is_redacted(db_session: Session, organization, media_dirs) -> None:
    make_clip(
        media_dirs.finals / "r.mp4", codec="libx264", pix_fmt="yuv420p", rate=10
    )  # 10 fps → needs export
    settings = _settings(media_dirs, media_ffmpeg_path="/no/such/ffmpeg")
    (asset,) = _index_finals(db_session, organization, media_dirs, settings)

    job = _run_export(db_session, organization, media_dirs, settings, asset)

    assert job.status == ExportJobStatus.FAILED and job.error_code == "ffmpeg_missing"
    assert job.error == f"{PATH_PLACEHOLDER}/ffmpeg not found"


# ---------------------------------------------------------------------------- CLI


@requires_x265
def test_cli_queues_runs_and_refuses_production_runs(
    db_session: Session, organization, media_dirs, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    make_clip(media_dirs.finals / "cli.mp4")
    settings = _settings(media_dirs)
    (asset,) = _index_finals(db_session, organization, media_dirs, settings)
    for key, value in media_dirs.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DEFAULT_ORGANIZATION_SLUG", organization.slug)
    monkeypatch.setenv("MEDIA_FINAL_SETTLE_SECONDS", "0")
    monkeypatch.setenv("EXPORT_PRESET", "ultrafast")
    get_settings.cache_clear()
    try:
        assert media_export.main(["queue", "--asset", str(asset.id)]) == 0
        queued = db_session.scalar(
            select(MediaExportJob).where(MediaExportJob.media_asset_id == asset.id)
        )
        assert queued is not None and queued.status == ExportJobStatus.QUEUED
        assert queued.trigger == ExportTrigger.CLI and queued.requested_by == "cli"
        assert media_export.main(["queue", "--asset", str(asset.id)]) == 1  # already active
        assert "export_already_active" in capsys.readouterr().err
        assert media_export.main(["cancel", "--job", str(queued.id)]) == 0
        assert media_export.main(["status", "--asset", str(asset.id)]) == 0
        assert '"cancelled"' in capsys.readouterr().out

        assert media_export.main(["queue", "--asset", str(asset.id), "--run"]) == 0
        out = capsys.readouterr().out
        assert '"status": "ready"' in out

        # production guard: a bare --run is refused before anything is queued
        monkeypatch.setattr(
            media_export,
            "get_settings",
            lambda: settings.model_copy(update={"app_env": "production"}),
        )
        assert media_export.main(["queue", "--asset", str(asset.id), "--run"]) == 2
        assert "Refusing --run in production" in capsys.readouterr().err
    finally:
        dispose_engine()
        get_settings.cache_clear()


def test_unknown_asset_and_org(db_session: Session, organization, media_dirs) -> None:
    with pytest.raises(Exception, match="not found"):
        enqueue_export(db_session, organization.id, uuid.uuid4())
    assert os.environ.get("APP_ENV") == "test"
    assert shutil.which("ffmpeg")  # fixture sanity
