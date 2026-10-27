from __future__ import annotations

import os
import signal
import socket
import threading
import time
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from affilio.core.config import Settings, get_settings
from affilio.core.enums import ScanJobStatus
from affilio.core.logging import configure_logging
from affilio.db.session import get_session_factory
from affilio.modules.media.errors import ScanConfigurationError
from affilio.modules.media.models import MediaScanJob
from affilio.modules.media.probe import Prober
from affilio.modules.media.roots import ROOT_KEYS, MediaRootRegistry
from affilio.modules.media.scanner import MediaScanner
from affilio.modules.media.thumbnails import ThumbnailGenerator
from affilio.worker.maintenance import run_maintenance

log = structlog.get_logger("affilio.worker")

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


def _backoff(attempt: int) -> timedelta:
    return timedelta(seconds=min(30 * (2 ** max(attempt - 1, 0)), 900))


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
    """Run one claimed job to completion, handling retries. Commits."""
    scanner = MediaScanner(session, registry, settings, prober=prober)
    try:
        scanner.run(job)
    except (ScanConfigurationError, OSError, Exception) as exc:
        session.rollback()
        job = session.get(MediaScanJob, job.id, options=[selectinload(MediaScanJob.root)])
        assert job is not None  # noqa: S101 - just re-fetched
        message = f"{exc.__class__.__name__}: {exc}"[:1000]
        log.warning("media.scan.failed", job_id=str(job.id), attempt=job.attempt, error=message)
        job.error = message
        job.finished_at = datetime.now(UTC)
        if job.attempt >= job.max_attempts:
            job.status = ScanJobStatus.FAILED
        else:
            job.status = ScanJobStatus.QUEUED
            job.next_attempt_at = datetime.now(UTC) + _backoff(job.attempt)
        session.commit()
        return job
    job.status = ScanJobStatus.SUCCEEDED
    job.finished_at = datetime.now(UTC)
    job.error = None
    session.commit()
    return job


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
        job = claim_next_scan_job(session)
        if job is not None:
            session.commit()
            job = session.get(MediaScanJob, job.id, options=[selectinload(MediaScanJob.root)])
            assert job is not None  # noqa: S101
            execute_scan_job(session, job, settings=settings, registry=registry, prober=prober)
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
