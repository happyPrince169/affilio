"""Publish-ready exports of final videos (Export pipeline E1).

An export is a `media_file_variants` row of kind `export` on the SAME asset as the final it was
rendered from (never a new asset), written under the read-write `exports` root, with full
provenance: the source variant, the fixed recipe key, and the source's size/mtime/fingerprint
at render time. `media_export_jobs` is the export worker's queue and audit trail.

Guarantees:
- Sources are never opened for writing; the finals mount is read-only by policy and by mount.
- The recipe is fixed and versioned in code (`RECIPE_KEY`); settings only bound CPU and time.
  No user-supplied ffmpeg arguments exist.
- ffmpeg streams from disk to disk; nothing loads a video into application memory.
- Output goes to a temp file next to the target and is renamed into place only after ffprobe
  confirms it satisfies the publishing profile (`export_profile`).
- A source that already satisfies the profile is `skipped` (no file); an HDR / wide-gamut
  source is `blocked` for a separate tone-mapping policy instead of being converted silently.
- Idempotent: a ready export whose recorded source identity still matches the current source
  is reused; a changed source makes every older export stale (consumers compare identity).
- Fenced: every write by a run is conditioned on the job still being `running` for this
  worker and attempt, with heartbeats while ffmpeg runs, so cancellation and the watchdog
  stop a run at the next heartbeat and a zombie can never finalize.
- Errors are redacted before they are stored.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

import structlog
from sqlalchemy import ColumnElement, func, or_, select, update
from sqlalchemy.orm import Session, selectinload

from affilio.core.config import Settings
from affilio.core.enums import (
    ExportJobStatus,
    ExportTrigger,
    MediaAssetKind,
    MediaAssetStatus,
    MediaVariantKind,
    MediaVariantStatus,
)
from affilio.core.errors import NotFoundError
from affilio.core.redaction import Redactor
from affilio.modules.media import export_profile
from affilio.modules.media.errors import (
    ExportAbortedError,
    ExportAlreadyActiveError,
    ExportNotCancellableError,
    ExportNotEligibleError,
    InvalidMediaPathError,
)
from affilio.modules.media.fingerprint import quick_fingerprint
from affilio.modules.media.models import MediaAsset, MediaExportJob, MediaFileVariant, MediaRoot
from affilio.modules.media.probe import (
    FFprobe,
    ProbeError,
    Prober,
    ProbeResult,
    parse_ffprobe_output,
)
from affilio.modules.media.roots import MediaRootRegistry, RootBinding
from affilio.modules.media.service import scan_retry_backoff

log = structlog.get_logger(__name__)

RECIPE_KEY = "reels-h264-v1"
# Recipes the publisher may stream (E2). A future recipe is added here only once validated.
SUPPORTED_RECIPES = frozenset({RECIPE_KEY})
TEMP_PATTERN = ".*.tmp.mp4"
EXPORT_EXTENSION = ".mp4"
MTIME_TOLERANCE_SECONDS = 2.0
TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
STDERR_TAIL_BYTES = 2000


# ------------------------------------------------------------------------------ recipe


@dataclass(frozen=True, slots=True)
class RecipePlan:
    """The exact ffmpeg invocation for one source. Built only from the source probe and the
    bounded settings; paths are substituted at run time and recorded as placeholders."""

    key: str
    fps: int
    audio: bool
    threads: int
    nice: int
    preset: str
    crf: int
    max_bitrate_kbps: int
    audio_bitrate_kbps: int

    def ffmpeg_args(self, ffmpeg: str, source: Path | str, target: Path | str) -> list[str]:
        gop = 2 * self.fps  # closed GOP of two seconds, as the Reels spec documents
        args = [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-threads",
            str(self.threads),
            "-i",
            str(source),
            "-map",
            "0:v:0",
            *(["-map", "0:a:0"] if self.audio else ["-an"]),
            # Never upscale; 4K portrait becomes 1080x1920, 1080-wide sources pass through.
            # Even dimensions are guaranteed by h=-2.
            "-vf",
            f"scale=w='min(iw,{TARGET_WIDTH})':h=-2:flags=lanczos",
            "-c:v",
            "libx264",
            "-profile:v",
            "high",
            "-level",
            "4.1",
            "-preset",
            self.preset,
            "-crf",
            str(self.crf),
            "-maxrate",
            f"{self.max_bitrate_kbps}k",
            "-bufsize",
            f"{2 * self.max_bitrate_kbps}k",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(self.fps),
            "-vsync",
            "cfr",
            "-g",
            str(gop),
            "-keyint_min",
            str(gop),
            "-sc_threshold",
            "0",
            "-threads",
            str(self.threads),
        ]
        if self.audio:
            args += [
                "-c:a",
                "aac",
                "-b:a",
                f"{self.audio_bitrate_kbps}k",
                "-ar",
                "48000",
                "-ac",
                "2",
            ]
        args += ["-movflags", "+faststart", "-f", "mp4", str(target)]
        return args

    def describe(self, ffmpeg_version: str | None) -> dict[str, Any]:
        return {
            "key": self.key,
            "ffmpeg_version": ffmpeg_version,
            "args": self.ffmpeg_args("ffmpeg", "<source>", "<target>")[1:],
            "fps": self.fps,
            "audio": self.audio,
            "threads": self.threads,
            "nice": self.nice,
            "preset": self.preset,
            "crf": self.crf,
        }


def plan_recipe(source: ProbeResult, settings: Settings) -> RecipePlan:
    fps = source.frame_rate or 30.0
    fixed_fps = max(24, min(60, round(fps)))
    return RecipePlan(
        key=RECIPE_KEY,
        fps=fixed_fps,
        audio=export_profile.has_audio(source),
        threads=settings.export_threads,
        nice=settings.export_nice,
        preset=settings.export_preset,
        crf=settings.export_crf,
        max_bitrate_kbps=settings.export_max_bitrate_kbps,
        audio_bitrate_kbps=settings.export_audio_bitrate_kbps,
    )


def export_relative_path(asset_id: uuid.UUID, recipe_key: str = RECIPE_KEY) -> str:
    """`<aa>/<asset hex>-<recipe>.mp4` under the exports root (same layout as thumbnails)."""
    digest = asset_id.hex
    return f"{digest[:2]}/{digest}-{recipe_key}{EXPORT_EXTENSION}"


# ------------------------------------------------------------------------------ queue


def enqueue_export(
    session: Session,
    organization_id: uuid.UUID,
    asset_id: uuid.UUID,
    *,
    trigger: ExportTrigger = ExportTrigger.MANUAL,
    requested_by: str | None = None,
    recipe_key: str = RECIPE_KEY,
    max_attempts: int = 3,
) -> MediaExportJob:
    """Queue one export for an active final with a present file. Does not commit."""
    asset = session.get(MediaAsset, asset_id, options=[selectinload(MediaAsset.variants)])
    if asset is None or asset.organization_id != organization_id:
        raise NotFoundError("Media asset not found")
    if asset.asset_kind != MediaAssetKind.FINAL:
        raise ExportNotEligibleError("Only final videos can be exported for publishing")
    if asset.status != MediaAssetStatus.ACTIVE:
        raise ExportNotEligibleError("The final is missing or unsupported")
    source = asset.variant(MediaVariantKind.FINAL)
    if source is None or source.status != MediaVariantStatus.PRESENT:
        raise ExportNotEligibleError("No present final file is recorded for this asset")
    active = session.scalar(
        select(MediaExportJob).where(
            MediaExportJob.media_asset_id == asset.id,
            MediaExportJob.recipe_key == recipe_key,
            MediaExportJob.status.in_([ExportJobStatus.QUEUED, ExportJobStatus.RUNNING]),
        )
    )
    if active is not None:
        raise ExportAlreadyActiveError(
            "An export is already queued or running for this final",
            details={"job_id": str(active.id), "status": active.status.value},
        )
    job = MediaExportJob(
        organization_id=organization_id,
        media_asset_id=asset.id,
        source_variant_id=source.id,
        status=ExportJobStatus.QUEUED,
        trigger=trigger,
        recipe_key=recipe_key,
        requested_by=requested_by,
        max_attempts=max(1, max_attempts),
        next_attempt_at=datetime.now(UTC),
    )
    session.add(job)
    session.flush()
    return job


def get_export_job(
    session: Session, organization_id: uuid.UUID, job_id: uuid.UUID
) -> MediaExportJob:
    job = session.scalar(
        select(MediaExportJob).where(
            MediaExportJob.organization_id == organization_id, MediaExportJob.id == job_id
        )
    )
    if job is None:
        raise NotFoundError("Export job not found")
    return job


def list_export_jobs(
    session: Session,
    organization_id: uuid.UUID,
    *,
    asset_id: uuid.UUID | None = None,
    limit: int = 20,
) -> list[MediaExportJob]:
    stmt = select(MediaExportJob).where(MediaExportJob.organization_id == organization_id)
    if asset_id is not None:
        stmt = stmt.where(MediaExportJob.media_asset_id == asset_id)
    stmt = stmt.order_by(MediaExportJob.created_at.desc()).limit(min(max(limit, 1), 100))
    return list(session.scalars(stmt))


def cancel_export(
    session: Session, organization_id: uuid.UUID, job_id: uuid.UUID, *, actor: str | None = None
) -> MediaExportJob:
    """Cancel a queued or running export. A running ffmpeg is killed at the worker's next
    heartbeat (<= EXPORT_HEARTBEAT_SECONDS) and its temp output removed. Does not commit."""
    job = session.scalar(
        select(MediaExportJob)
        .where(MediaExportJob.organization_id == organization_id, MediaExportJob.id == job_id)
        .with_for_update(of=MediaExportJob)
    )
    if job is None:
        raise NotFoundError("Export job not found")
    if job.status not in {ExportJobStatus.QUEUED, ExportJobStatus.RUNNING}:
        raise ExportNotCancellableError(
            f"Export job is already {job.status.value}", details={"status": job.status.value}
        )
    was_running = job.status == ExportJobStatus.RUNNING
    now = datetime.now(UTC)
    job.status = ExportJobStatus.CANCELLED
    job.finished_at = now
    job.next_attempt_at = None
    job.error_code = "cancelled"
    note = f"Cancelled by {actor or 'operator'}"
    if was_running:
        note += "; the worker stops ffmpeg at its next heartbeat and discards the temp output"
    job.error = note
    session.flush()
    return job


def recover_stale_export_jobs(
    session: Session, *, stale_after: timedelta, now: datetime | None = None
) -> list[MediaExportJob]:
    """Watchdog: re-queue running jobs whose worker stopped heartbeating (SKIP LOCKED, so
    concurrent watchdogs never recover the same job twice). Does not commit."""
    now = now or datetime.now(UTC)
    threshold = now - stale_after
    last_sign_of_life = func.coalesce(
        MediaExportJob.heartbeat_at, MediaExportJob.started_at, MediaExportJob.created_at
    )
    stmt = (
        select(MediaExportJob)
        .where(MediaExportJob.status == ExportJobStatus.RUNNING, last_sign_of_life < threshold)
        .order_by(MediaExportJob.created_at)
        .with_for_update(skip_locked=True, of=MediaExportJob)
    )
    recovered: list[MediaExportJob] = []
    for job in session.scalars(stmt):
        last = job.heartbeat_at or job.started_at or job.created_at
        age = int((now - last).total_seconds())
        note = (
            f"Recovered by watchdog: no heartbeat for {age}s from worker "
            f"{job.worker_id or 'unknown'} (attempt {job.attempt}/{job.max_attempts})"
        )
        if job.attempt >= job.max_attempts:
            job.status = ExportJobStatus.FAILED
            job.finished_at = now
            job.next_attempt_at = None
            job.error_code = "worker_lost"
            note += "; no attempts left"
        else:
            job.status = ExportJobStatus.QUEUED
            job.finished_at = None
            job.next_attempt_at = now + scan_retry_backoff(job.attempt)
            job.error_code = "worker_lost"
        job.error = note
        recovered.append(job)
    session.flush()
    return recovered


def claim_next_export_job(session: Session, worker_id: str) -> MediaExportJob | None:
    """Claim the oldest due job (FOR UPDATE SKIP LOCKED). Does not commit."""
    now = datetime.now(UTC)
    stmt = (
        select(MediaExportJob)
        .where(
            MediaExportJob.status == ExportJobStatus.QUEUED,
            or_(MediaExportJob.next_attempt_at.is_(None), MediaExportJob.next_attempt_at <= now),
        )
        .order_by(MediaExportJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True, of=MediaExportJob)
    )
    job = session.scalar(stmt)
    if job is None:
        return None
    job.status = ExportJobStatus.RUNNING
    job.attempt += 1
    job.started_at = now
    job.heartbeat_at = now
    job.worker_id = worker_id
    job.error = None
    job.error_code = None
    session.flush()
    return job


# ------------------------------------------------------------------------------ generator


class _PermanentExportError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _TransientExportError(_PermanentExportError):
    """Retried with backoff until `max_attempts`."""


@dataclass(frozen=True, slots=True)
class _Fence:
    job_id: uuid.UUID
    worker_id: str
    attempt: int

    def clauses(self) -> list[ColumnElement[bool]]:
        return [
            MediaExportJob.id == self.job_id,
            MediaExportJob.status == ExportJobStatus.RUNNING,
            MediaExportJob.worker_id == self.worker_id,
            MediaExportJob.attempt == self.attempt,
        ]


@dataclass(slots=True)
class _SourceIdentity:
    file_size: int
    modified_at: datetime
    fingerprint: str

    def matches_variant(self, variant: MediaFileVariant) -> bool:
        return (
            variant.source_file_size == self.file_size
            and variant.source_fingerprint == self.fingerprint
            and variant.source_modified_at is not None
            and abs((variant.source_modified_at - self.modified_at).total_seconds())
            <= MTIME_TOLERANCE_SECONDS
        )


class ExportGenerator:
    """Runs one claimed export job to completion. One job at a time per worker process."""

    def __init__(
        self,
        settings: Settings,
        registry: MediaRootRegistry,
        *,
        worker_id: str,
        prober: Prober | None = None,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.worker_id = worker_id
        self.prober = prober or FFprobe(
            settings.media_ffprobe_path, settings.media_probe_timeout_seconds
        )
        self.redactor = Redactor.from_settings(settings)
        self._ffmpeg_version: str | None = None
        self._active_tmp: Path | None = None

    # ------------------------------------------------------------------ public API
    def run(self, session: Session, job: MediaExportJob) -> MediaExportJob:
        """`job` must have been claimed (status running, worker_id == self.worker_id). Commits."""
        fence = _Fence(job_id=job.id, worker_id=self.worker_id, attempt=job.attempt)
        started = time.monotonic()
        self._active_tmp = None
        try:
            self._execute(session, job, fence)
        except ExportAbortedError as exc:
            session.rollback()
            self._discard(self._active_tmp)
            log.warning("media.export.aborted", job_id=str(fence.job_id), reason=str(exc))
            return self._reload(session, fence.job_id)
        except _TransientExportError as exc:
            session.rollback()
            self._discard(self._active_tmp)
            return self._retry_or_fail(session, fence, exc, started)
        except _PermanentExportError as exc:
            session.rollback()
            self._discard(self._active_tmp)
            self._finalize(
                session,
                fence,
                status=ExportJobStatus.FAILED,
                error_code=exc.code,
                error=exc.message,
                stats={"elapsed_seconds": round(time.monotonic() - started, 2)},
            )
            return self._reload(session, fence.job_id)
        except Exception as exc:  # unexpected: treat as transient, never swallow silently
            session.rollback()
            self._discard(self._active_tmp)
            log.exception("media.export.unexpected", job_id=str(fence.job_id))
            wrapped = _TransientExportError("unexpected", f"{exc.__class__.__name__}: {exc}")
            return self._retry_or_fail(session, fence, wrapped, started)
        return self._reload(session, fence.job_id)

    # ------------------------------------------------------------------ the run itself
    def _execute(self, session: Session, job: MediaExportJob, fence: _Fence) -> None:
        asset = session.get(
            MediaAsset, job.media_asset_id, options=[selectinload(MediaAsset.variants)]
        )
        source = session.get(MediaFileVariant, job.source_variant_id)
        if asset is None or source is None:
            raise _PermanentExportError("source_missing", "The final asset or its file row is gone")
        # The source final may live under the legacy `finals` tree or the `uploads` root;
        # the registry resolves either through the stored variant's own root.
        source_root, exports = self.registry.binding_for(source), self.registry.exports
        if source_root is None or not source_root.availability().readable:
            raise _TransientExportError(
                "roots_unavailable", "Source root not readable on this worker"
            )
        if exports is None or not exports.availability().writable:
            raise _TransientExportError(
                "roots_unavailable", "Exports root not writable on this worker"
            )

        source_path = source_root.resolve(source.relative_path)
        identity = self._verify_source(source, source_path)
        job.source_file_size = identity.file_size
        job.source_modified_at = identity.modified_at
        job.source_fingerprint = identity.fingerprint
        probe = self._source_probe(source, source_path)

        color = export_profile.color_info(probe)
        stats: dict[str, Any] = {
            "source_bytes": identity.file_size,
            "source_codec": probe.video_codec,
            "source_pixel_format": probe.pixel_format,
            "source_resolution": f"{probe.width}x{probe.height}",
            "color": color.as_dict(),
        }
        if color.is_hdr_or_wide_gamut:
            self._finalize(
                session,
                fence,
                status=ExportJobStatus.BLOCKED,
                error_code="hdr_source",
                error=(
                    "Source carries HDR / wide-gamut colour metadata "
                    f"(primaries={color.primaries}, transfer={color.transfer}, "
                    f"matrix={color.matrix}); "
                    "a tone-mapping policy is required before conversion"
                ),
                stats=stats,
            )
            return None

        existing = _export_variant(asset, job.recipe_key)
        if existing is not None and self._reusable(existing, identity, exports):
            self._finalize(
                session,
                fence,
                status=ExportJobStatus.READY,
                output_variant_id=existing.id,
                stats={**stats, "reused": True, "output_bytes": existing.file_size},
            )
            return None

        problems = export_profile.profile_problems(
            probe, file_size=identity.file_size, settings=self.settings
        )
        if not problems:
            self._finalize(
                session,
                fence,
                status=ExportJobStatus.SKIPPED,
                error_code="source_compliant",
                error="The final already satisfies the publishing profile; no export needed",
                stats=stats,
            )
            return None

        plan = plan_recipe(probe, self.settings)
        job.recipe = plan.describe(self._ffmpeg_version_string())
        rel = export_relative_path(asset.id, job.recipe_key)
        target = exports.resolve(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.stem}.{os.getpid()}.tmp{EXPORT_EXTENSION}")
        self._discard(tmp)
        self._active_tmp = tmp

        self._heartbeat(session, fence)  # also persists recipe + source identity
        duration = probe.duration_seconds or 0.0
        budget = max(
            float(self.settings.export_timeout_min_seconds),
            self.settings.export_timeout_factor * duration,
        )
        encode_started = time.monotonic()
        self._run_ffmpeg(session, fence, plan, source_path, tmp, budget)
        stats["encode_seconds"] = round(time.monotonic() - encode_started, 2)
        stats["source_problems"] = problems

        output_probe, output_size = self._validate_output(tmp, probe)
        output_identity_fp = quick_fingerprint(tmp, output_size)

        # Finalize: fence first (no commit), then the file, then the rows, one commit.
        result = session.execute(
            update(MediaExportJob)
            .where(*fence.clauses())
            .values(
                status=ExportJobStatus.READY,
                finished_at=datetime.now(UTC),
                error=None,
                error_code=None,
                recipe=job.recipe,
                source_file_size=identity.file_size,
                source_modified_at=identity.modified_at,
                source_fingerprint=identity.fingerprint,
                stats={**stats, "reused": False, "output_bytes": output_size},
            )
        )
        if result.rowcount != 1:
            raise ExportAbortedError("job cancelled, recovered or re-claimed before finalization")
        os.replace(tmp, target)
        self._active_tmp = None
        info = target.stat()
        root_row = _db_root(session, asset.organization_id, "exports")
        if root_row is None:
            raise _TransientExportError(
                "roots_unavailable", "Exports root row missing in the database"
            )
        variant = existing or MediaFileVariant(
            asset=asset,
            media_root_id=root_row.id,
            kind=MediaVariantKind.EXPORT,
            relative_path=rel,
            file_name=PurePosixPath(rel).name,
            extension=EXPORT_EXTENSION,
            file_size=info.st_size,
            modified_at=datetime.fromtimestamp(info.st_mtime, tz=UTC),
        )
        if existing is None:
            session.add(variant)
        variant.media_root_id = root_row.id
        variant.relative_path = rel
        variant.file_name = PurePosixPath(rel).name
        variant.extension = EXPORT_EXTENSION
        variant.status = MediaVariantStatus.PRESENT
        variant.missing_since = None
        variant.file_size = info.st_size
        variant.modified_at = datetime.fromtimestamp(info.st_mtime, tz=UTC)
        variant.fingerprint = output_identity_fp
        variant.content_hash = None
        variant.last_seen_at = datetime.now(UTC)
        variant.derived_from_variant_id = source.id
        variant.recipe_key = job.recipe_key
        variant.source_fingerprint = identity.fingerprint
        variant.source_file_size = identity.file_size
        variant.source_modified_at = identity.modified_at
        for key, value in output_probe.columns().items():
            setattr(variant, key, value)
        variant.probe = output_probe.raw
        variant.probed_at = datetime.now(UTC)
        variant.probe_error = None
        session.flush()
        session.execute(
            update(MediaExportJob)
            .where(MediaExportJob.id == fence.job_id)
            .values(output_variant_id=variant.id)
        )
        session.commit()
        log.info(
            "media.export.ready",
            job_id=str(fence.job_id),
            asset_id=str(asset.id),
            output_bytes=info.st_size,
            encode_seconds=stats["encode_seconds"],
        )
        return None

    # ------------------------------------------------------------------ steps
    def _verify_source(self, source: MediaFileVariant, path: Path) -> _SourceIdentity:
        try:
            stat = path.stat()
        except OSError as exc:
            raise _PermanentExportError(
                "source_missing", f"The final file is not readable: {exc}"
            ) from exc
        if not path.is_file():
            raise _PermanentExportError("source_missing", "The final path is not a regular file")
        modified_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        if stat.st_size != source.file_size or (
            abs((modified_at - source.modified_at).total_seconds()) > MTIME_TOLERANCE_SECONDS
        ):
            raise _PermanentExportError(
                "source_changed",
                "The final changed since it was indexed (size or mtime differ); rescan, then "
                "queue the export again",
            )
        age = (datetime.now(UTC) - modified_at).total_seconds()
        if age < self.settings.media_final_settle_seconds:
            raise _TransientExportError("source_settling", "The final was modified too recently")
        fingerprint = quick_fingerprint(path, stat.st_size)
        if source.fingerprint and source.fingerprint != fingerprint:
            raise _PermanentExportError(
                "source_changed", "The final's content differs from the indexed fingerprint"
            )
        return _SourceIdentity(
            file_size=stat.st_size, modified_at=modified_at, fingerprint=fingerprint
        )

    def _source_probe(self, source: MediaFileVariant, path: Path) -> ProbeResult:
        if source.probe:
            try:
                return parse_ffprobe_output(source.probe)
            except ProbeError:
                pass
        try:
            return self.prober.probe(path)
        except ProbeError as exc:
            raise _PermanentExportError("source_unreadable", f"{exc.kind}: {exc.message}") from exc

    def _reusable(
        self, existing: MediaFileVariant, identity: _SourceIdentity, exports: RootBinding
    ) -> bool:
        if existing.status != MediaVariantStatus.PRESENT or not identity.matches_variant(existing):
            return False
        try:
            info = exports.resolve(existing.relative_path).stat()
        except (OSError, ValueError):
            return False
        return info.st_size == existing.file_size and info.st_size > 0

    def _run_ffmpeg(
        self,
        session: Session,
        fence: _Fence,
        plan: RecipePlan,
        source: Path,
        tmp: Path,
        budget_seconds: float,
    ) -> None:
        args = plan.ffmpeg_args(self.settings.media_ffmpeg_path, source, tmp)
        nice = plan.nice

        def _lower_priority() -> None:
            # Runs in the child before exec; this worker process has no threads.
            with contextlib.suppress(OSError):
                os.nice(nice)

        with tempfile.TemporaryFile() as stderr:
            try:
                proc = subprocess.Popen(  # noqa: S603 - fixed argv built from the recipe, no shell
                    args,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=stderr,
                    preexec_fn=_lower_priority,
                )
            except FileNotFoundError as exc:
                raise _PermanentExportError(
                    "ffmpeg_missing", f"{self.settings.media_ffmpeg_path} not found"
                ) from exc
            deadline = time.monotonic() + budget_seconds
            interval = max(self.settings.export_heartbeat_seconds, 0.05)
            try:
                while True:
                    try:
                        proc.wait(timeout=interval)
                        break
                    except subprocess.TimeoutExpired:
                        if time.monotonic() >= deadline:
                            _kill(proc)
                            raise _TransientExportError(
                                "timeout", f"ffmpeg exceeded the {budget_seconds:.0f}s budget"
                            ) from None
                        try:
                            self._heartbeat(session, fence)
                        except ExportAbortedError:
                            _kill(proc)
                            raise
            finally:
                if proc.poll() is None:
                    _kill(proc)
            if proc.returncode != 0:
                stderr.seek(0, os.SEEK_END)
                size = stderr.tell()
                stderr.seek(max(0, size - STDERR_TAIL_BYTES))
                tail = (
                    stderr.read().decode("utf-8", "replace").strip() or "ffmpeg produced no output"
                )
                raise _PermanentExportError(
                    "encode_failed", f"ffmpeg exit {proc.returncode}: {tail}"
                )

    def _validate_output(self, tmp: Path, source: ProbeResult) -> tuple[ProbeResult, int]:
        try:
            size = tmp.stat().st_size
        except OSError as exc:
            raise _PermanentExportError("output_invalid", f"output missing: {exc}") from exc
        try:
            probe = self.prober.probe(tmp)
        except ProbeError as exc:
            raise _PermanentExportError(
                "output_invalid", f"output not probeable: {exc.kind}: {exc.message}"
            ) from exc
        problems = export_profile.profile_problems(probe, file_size=size, settings=self.settings)
        if (probe.video_codec or "").lower() != "h264":
            problems.append(f"output codec {probe.video_codec} is not h264")
        if (probe.pixel_format or "").lower() != "yuv420p":
            problems.append(f"output pixel format {probe.pixel_format} is not yuv420p")
        if export_profile.has_audio(source) != export_profile.has_audio(probe):
            problems.append("output audio presence differs from the source")
        if export_profile.has_audio(probe) and export_profile.audio_sample_rate(probe) != 48000:
            problems.append("output audio is not 48 kHz")
        if source.duration_seconds and probe.duration_seconds:
            tolerance = max(1.0, 0.02 * source.duration_seconds)
            if abs(probe.duration_seconds - source.duration_seconds) > tolerance:
                problems.append("output duration differs from the source")
        if (
            source.width
            and source.height
            and probe.width
            and probe.height
            and (source.height > source.width) != (probe.height > probe.width)
        ):
            problems.append("output orientation differs from the source")
        if problems:
            raise _PermanentExportError("output_invalid", "; ".join(problems))
        return probe, size

    # ------------------------------------------------------------------ fenced writes
    def _heartbeat(self, session: Session, fence: _Fence) -> None:
        result = session.execute(
            update(MediaExportJob).where(*fence.clauses()).values(heartbeat_at=datetime.now(UTC))
        )
        if result.rowcount != 1:
            session.rollback()
            raise ExportAbortedError(
                f"export job {fence.job_id} is no longer running for {fence.worker_id} "
                f"attempt {fence.attempt} (cancelled, recovered or re-claimed)"
            )
        session.commit()

    def _finalize(
        self,
        session: Session,
        fence: _Fence,
        *,
        status: ExportJobStatus,
        error_code: str | None = None,
        error: str | None = None,
        stats: dict[str, Any] | None = None,
        output_variant_id: uuid.UUID | None = None,
        next_attempt_at: datetime | None = None,
    ) -> bool:
        values: dict[str, Any] = {
            "status": status,
            "error_code": error_code,
            "error": self.redactor.message(error, limit=1000) if error else None,
            "next_attempt_at": next_attempt_at,
        }
        if status != ExportJobStatus.QUEUED:
            values["finished_at"] = datetime.now(UTC)
        if stats is not None:
            values["stats"] = stats
        if output_variant_id is not None:
            values["output_variant_id"] = output_variant_id
        result = session.execute(update(MediaExportJob).where(*fence.clauses()).values(**values))
        if result.rowcount != 1:
            session.rollback()
            log.warning(
                "media.export.aborted", job_id=str(fence.job_id), reason="lost fence at finalize"
            )
            return False
        session.commit()
        return True

    def _retry_or_fail(
        self, session: Session, fence: _Fence, exc: _TransientExportError, started: float
    ) -> MediaExportJob:
        current = self._reload(session, fence.job_id)
        stats = {"elapsed_seconds": round(time.monotonic() - started, 2)}
        if current.attempt >= current.max_attempts:
            self._finalize(
                session,
                fence,
                status=ExportJobStatus.FAILED,
                error_code=exc.code,
                error=f"{exc.message} (no attempts left)",
                stats=stats,
            )
        else:
            self._finalize(
                session,
                fence,
                status=ExportJobStatus.QUEUED,
                error_code=exc.code,
                error=exc.message,
                stats=stats,
                next_attempt_at=datetime.now(UTC) + scan_retry_backoff(current.attempt),
            )
        return self._reload(session, fence.job_id)

    # ------------------------------------------------------------------ helpers
    def _ffmpeg_version_string(self) -> str | None:
        if self._ffmpeg_version is None:
            try:
                completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
                    [self.settings.media_ffmpeg_path, "-version"],
                    capture_output=True,
                    timeout=10,
                    check=False,
                    stdin=subprocess.DEVNULL,
                )
                first = completed.stdout.decode("utf-8", "replace").splitlines()[:1]
                self._ffmpeg_version = (first[0] if first else "unknown")[:120]
            except (OSError, subprocess.TimeoutExpired):
                self._ffmpeg_version = "unknown"
        return self._ffmpeg_version

    @staticmethod
    def _discard(tmp: Path | None) -> None:
        if tmp is not None:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)

    @staticmethod
    def _reload(session: Session, job_id: uuid.UUID) -> MediaExportJob:
        job = session.get(MediaExportJob, job_id)
        assert job is not None  # noqa: S101 - rows are never deleted while a worker holds them
        session.refresh(job)
        return job


def _export_variant(asset: MediaAsset, recipe_key: str) -> MediaFileVariant | None:
    for variant in asset.variants:
        if variant.kind == MediaVariantKind.EXPORT and variant.recipe_key == recipe_key:
            return variant
    return None


def _db_root(session: Session, organization_id: uuid.UUID, key: str) -> MediaRoot | None:
    return session.scalar(
        select(MediaRoot).where(MediaRoot.organization_id == organization_id, MediaRoot.key == key)
    )


def _kill(proc: subprocess.Popen) -> None:
    try:
        proc.kill()
        proc.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


def export_is_current(export: MediaFileVariant, source: MediaFileVariant) -> bool:
    """For consumers (E2): a ready export is usable only while its recorded source identity
    matches the source variant as currently indexed."""
    return (
        export.kind == MediaVariantKind.EXPORT
        and export.status == MediaVariantStatus.PRESENT
        and export.derived_from_variant_id == source.id
        and export.source_file_size == source.file_size
        and (source.fingerprint is None or export.source_fingerprint == source.fingerprint)
        and export.source_modified_at is not None
        and abs((export.source_modified_at - source.modified_at).total_seconds())
        <= MTIME_TOLERANCE_SECONDS
    )


# ------------------------------------------------------------------------------ readiness (E2)

ExportState = (
    str  # not_needed | not_generated | queued | processing | ready | failed | blocked | stale
)


def _variant_profile_problems(variant: MediaFileVariant, settings: Settings) -> list[str]:
    """Profile problems from the stored probe columns (no filesystem access)."""
    try:
        probe = parse_ffprobe_output(variant.probe) if variant.probe else None
    except ProbeError:
        probe = None
    if probe is None:
        probe = ProbeResult(
            duration_seconds=variant.duration_seconds,
            width=variant.width,
            height=variant.height,
            rotation=variant.rotation,
            frame_rate=variant.frame_rate,
            video_codec=variant.video_codec,
            audio_codec=variant.audio_codec,
            pixel_format=variant.pixel_format,
            bit_depth=variant.bit_depth,
            bit_rate=variant.bit_rate,
            container_format=variant.container_format,
            raw={
                "streams": [{"codec_type": "video"}]
                + ([{"codec_type": "audio"}] if variant.audio_codec else [])
            },
        )
    return export_profile.profile_problems(probe, file_size=variant.file_size, settings=settings)


def readiness_from_db(asset: MediaAsset, settings: Settings) -> tuple[str, str | None]:
    """Publish-readiness of a final from indexed facts only (queue lists; no stat calls).

    Returns one of `export_ready`, `final_ready`, `export_stale`, `needs_export`,
    `not_final` with a short detail. The authoritative check with filesystem access is
    `publishing.media_checks.check_reel_media` at publish time.
    """
    if asset.asset_kind != MediaAssetKind.FINAL:
        return "not_final", None
    final = asset.variant(MediaVariantKind.FINAL)
    if final is None or final.status != MediaVariantStatus.PRESENT:
        return "needs_export", "final file missing"
    export = _export_variant(asset, RECIPE_KEY)
    if export is not None and export.status == MediaVariantStatus.PRESENT:
        if export_is_current(export, final) and not _variant_profile_problems(export, settings):
            return "export_ready", None
        return "export_stale", "the final changed after the export was rendered"
    problems = _variant_profile_problems(final, settings)
    if not problems:
        return "final_ready", None
    return "needs_export", "; ".join(problems)


def latest_export_job(session: Session, asset_id: uuid.UUID) -> MediaExportJob | None:
    return session.scalar(
        select(MediaExportJob)
        .where(MediaExportJob.media_asset_id == asset_id)
        .order_by(MediaExportJob.created_at.desc())
        .limit(1)
    )


@dataclass(slots=True)
class AssetExportState:
    state: ExportState
    detail: str | None
    job: MediaExportJob | None
    export: MediaFileVariant | None
    can_export: bool


def asset_export_state(
    session: Session, asset: MediaAsset, registry: MediaRootRegistry, settings: Settings
) -> AssetExportState | None:
    """State shown on a final's page (E2). One stat call for the export file at most."""
    if asset.asset_kind != MediaAssetKind.FINAL:
        return None
    final = asset.variant(MediaVariantKind.FINAL)
    job = latest_export_job(session, asset.id)
    export = _export_variant(asset, RECIPE_KEY)
    eligible = (
        asset.status == MediaAssetStatus.ACTIVE
        and final is not None
        and final.status == MediaVariantStatus.PRESENT
    )
    active = job is not None and job.is_active
    if active:
        state = "processing" if job.status == ExportJobStatus.RUNNING else "queued"
        return AssetExportState(state, job.error, job, export, can_export=False)
    if export is not None and export.status == MediaVariantStatus.PRESENT and final is not None:
        current = export_is_current(export, final)
        exists = False
        if current and registry.exports is not None:
            try:
                info = registry.exports.resolve(export.relative_path).stat()
                exists = info.st_size == export.file_size and info.st_size > 0
            except (OSError, ValueError, InvalidMediaPathError):
                exists = False
        if current and exists:
            return AssetExportState("ready", None, job, export, can_export=eligible)
        detail = (
            "the final changed after the export was rendered"
            if not current
            else "the export file is missing from the exports root"
        )
        return AssetExportState("stale", detail, job, export, can_export=eligible)
    if job is not None and job.status == ExportJobStatus.BLOCKED:
        return AssetExportState("blocked", job.error, job, None, can_export=False)
    if job is not None and job.status == ExportJobStatus.FAILED:
        return AssetExportState("failed", job.error, job, None, can_export=eligible)
    if final is not None and not _variant_profile_problems(final, settings):
        return AssetExportState("not_needed", None, job, None, can_export=False)
    detail = (
        "; ".join(_variant_profile_problems(final, settings)) if final else "final file missing"
    )
    return AssetExportState("not_generated", detail, job, None, can_export=eligible)


# ------------------------------------------------------------------------------ worker helpers (E2)

REQUIRED_ENCODERS = ("libx264", "aac")


def check_encoders(ffmpeg: str) -> dict[str, bool]:
    """Which required encoders this ffmpeg build offers (`ffmpeg -encoders`, no media touched)."""
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            timeout=20,
            check=False,
            stdin=subprocess.DEVNULL,
        )
        listing = completed.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired):
        listing = ""
    return {name: f" {name} " in listing for name in REQUIRED_ENCODERS}


def cleanup_orphan_temp_files(exports: RootBinding | None, *, older_than: timedelta) -> int:
    """Remove `.<name>.<pid>.tmp.mp4` leftovers of crashed runs that are older than the stale
    window. Live runs rewrite their temp file continuously, so an old one is never in use.
    Completed targets are never touched here: a target without a row is replaced by the next
    successful run (atomic rename), and a row without a target is reported stale."""
    if exports is None or not exports.availability().writable:
        return 0
    cutoff = time.time() - older_than.total_seconds()
    removed = 0
    for candidate in exports.path.glob(f"*/{TEMP_PATTERN}"):
        try:
            if candidate.is_file() and candidate.stat().st_mtime < cutoff:
                candidate.unlink()
                removed += 1
        except OSError:
            continue
    if removed:
        log.warning("media.export.orphans_removed", count=removed)
    return removed
