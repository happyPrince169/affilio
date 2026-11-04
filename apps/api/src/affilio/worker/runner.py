from __future__ import annotations

import os
import signal
import socket
import threading
import time
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session, selectinload

from affilio.core.config import Settings, get_settings
from affilio.core.enums import ScanJobStatus
from affilio.core.logging import configure_logging
from affilio.core.redaction import Redactor
from affilio.db.session import get_session_factory
from affilio.modules.media.errors import ScanAbortedError
from affilio.modules.media.models import MediaScanJob
from affilio.modules.media.probe import Prober
from affilio.modules.media.roots import ROOT_KEYS, MediaRootRegistry
from affilio.modules.media.scanner import JobFence, MediaScanner
from affilio.modules.media.service import recover_stale_scan_jobs, scan_retry_backoff
from affilio.modules.media.thumbnails import ThumbnailGenerator
from affilio.modules.media.upload_finalize import Finalizer
from affilio.worker.maintenance import run_maintenance

log = structlog.get_logger("affilio.worker")

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"

_backoff = scan_retry_backoff  # kept for callers/tests that import the old name


def claim_next_scan_job(session: Session) -> MediaScanJob | None:
    now = datetime.now(UTC)
    stmt = (
        select(MediaScanJob)
        .options(selectinload(MediaScanJob.root))
        .where(
            MediaScanJob.status == ScanJobStatus.QUEUED,
            or_(MediaScanJob.next_attempt_at.is_(None), MediaScanJob.next_attempt_at <= now),
        )
        .order_by(MediaScanJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True, of=MediaScanJob)
    )
    job = session.scalar(stmt)
    if job is None:
        return None
    job.status = ScanJobStatus.RUNNING
    job.attempt += 1
    job.started_at = now
    job.heartbeat_at = now
    job.worker_id = WORKER_ID
    job.error = None
    session.flush()
    return job


def execute_scan_job(
    session: Session,
    job: MediaScanJob,
    *,
    settings: Settings,
    registry: MediaRootRegistry,
    prober: Prober | None = None,
) -> MediaScanJob:
    """Run one claimed job to completion, handling retries. Commits.

    Every outcome is written through the job fence captured at the start (status, worker,
    attempt): if the job was cancelled, recovered by the watchdog or re-claimed meanwhile, the
    write affects zero rows, the uncommitted scan work is rolled back and the newer state
    stays untouched (M3C.1 step 1).
    """
    fence = JobFence.capture(job)
    scanner = MediaScanner(session, registry, settings, prober=prober)
    try:
        scanner.run(job)
    except ScanAbortedError as exc:
        session.rollback()
        log.warning("media.scan.aborted", job_id=str(fence.job_id), reason=str(exc))
        return _reload(session, fence.job_id)
    except Exception as exc:
        session.rollback()
        # Driver/OS messages can carry connection URLs or absolute paths: redact before storing.
        message = Redactor.from_settings(settings).message(
            f"{exc.__class__.__name__}: {exc}", limit=1000
        )
        log.warning(
            "media.scan.failed", job_id=str(fence.job_id), attempt=fence.attempt, error=message
        )
        current = _reload(session, fence.job_id)
        now = datetime.now(UTC)
        if current.attempt >= current.max_attempts:
            values: dict = {"status": ScanJobStatus.FAILED, "next_attempt_at": None}
        else:
            values = {
                "status": ScanJobStatus.QUEUED,
                "next_attempt_at": now + scan_retry_backoff(current.attempt),
            }
        values.update(error=message, finished_at=now)
        _finalize(session, fence, values)
        return _reload(session, fence.job_id)

    values = {
        "status": ScanJobStatus.SUCCEEDED,
        "finished_at": datetime.now(UTC),
        "error": None,
        "stats": job.stats,
        "errors": job.errors,
    }
    _finalize(session, fence, values)
    return _reload(session, fence.job_id)


def _reload(session: Session, job_id) -> MediaScanJob:
    job = session.get(MediaScanJob, job_id, options=[selectinload(MediaScanJob.root)])
    assert job is not None  # noqa: S101 - rows are never deleted while a worker holds them
    session.refresh(job)
    return job


def _finalize(session: Session, fence: JobFence, values: dict) -> bool:
    """Fenced final write. Returns False (after rolling back) when the run lost ownership."""
    result = session.execute(update(MediaScanJob).where(*fence.clauses()).values(**values))
    if result.rowcount != 1:
        session.rollback()
        log.warning("media.scan.aborted", job_id=str(fence.job_id), reason=fence.describe())
        return False
    session.commit()
    return True


def run_once(
    *,
    settings: Settings | None = None,
    registry: MediaRootRegistry | None = None,
    prober: Prober | None = None,
) -> bool:
    """Do one unit of work. Returns True if anything was processed."""
    settings = settings or get_settings()
    registry = registry or MediaRootRegistry.from_settings(settings)
    session_factory = get_session_factory()

    with session_factory() as session:
        stale = recover_stale_scan_jobs(
            session, stale_after=timedelta(seconds=settings.media_scan_stale_after_seconds)
        )
        if stale:
            session.commit()
            for recovered in stale:
                log.warning(
                    "media.scan.recovered",
                    job_id=str(recovered.id),
                    status=recovered.status.value,
                    attempt=recovered.attempt,
                    error=recovered.error,
                )
        job = claim_next_scan_job(session)
        if job is not None:
            session.commit()
            job = session.get(MediaScanJob, job.id, options=[selectinload(MediaScanJob.root)])
            assert job is not None  # noqa: S101
            execute_scan_job(session, job, settings=settings, registry=registry, prober=prober)
            return True

    # Editor uploads: one finalization per iteration, only where the uploads root is
    # writable (the worker container). Scans keep priority; thumbnails follow.
    if settings.media_uploads_writable:
        with session_factory() as session:
            finalizer = Finalizer.build(
                session, registry, settings, prober=prober, worker_id=WORKER_ID
            )
            if finalizer.run_next() is not None:
                return True

    generator = ThumbnailGenerator(settings, registry)
    return generator.process_pending(session_factory) > 0


def _touch_heartbeat(settings: Settings) -> None:
    try:
        settings.worker_heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        settings.worker_heartbeat_path.write_text(datetime.now(UTC).isoformat())
    except OSError as exc:  # pragma: no cover - diagnostics only
        log.debug("worker.heartbeat_failed", error=str(exc))


def run_forever(stop_event: threading.Event | None = None) -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    stop_event = stop_event or threading.Event()

    def _stop(signum, _frame) -> None:
        log.info("worker.stopping", signal=signum)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _stop)

    registry = MediaRootRegistry.from_settings(settings)
    log.info(
        "worker.started",
        worker_id=WORKER_ID,
        poll_interval=settings.worker_poll_interval_seconds,
        roots={k: bool(registry.get(k)) for k in ROOT_KEYS},
    )
    next_maintenance = time.monotonic()  # first tick runs maintenance once at startup
    while not stop_event.is_set():
        _touch_heartbeat(settings)
        if time.monotonic() >= next_maintenance:
            next_maintenance = time.monotonic() + settings.maintenance_interval_seconds
            try:
                run_maintenance(settings)
            except Exception as exc:
                log.exception("worker.maintenance_failed", error=str(exc))
        try:
            did_work = run_once(settings=settings, registry=registry)
        except Exception as exc:
            log.exception("worker.iteration_failed", error=str(exc))
            did_work = False
        if not did_work:
            stop_event.wait(settings.worker_poll_interval_seconds)
    log.info("worker.stopped", worker_id=WORKER_ID)


def heartbeat_is_fresh(settings: Settings, max_age_seconds: int = 120) -> bool:
    try:
        age = time.time() - settings.worker_heartbeat_path.stat().st_mtime
    except OSError:
        return False
    return age <= max_age_seconds
