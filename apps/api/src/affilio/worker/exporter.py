"""`python -m affilio.worker.exporter` — dedicated publish-ready export worker (E1).

One ffmpeg at a time, bounded by EXPORT_THREADS / EXPORT_NICE and the container memory limit.
Each loop: touch the heartbeat file, re-queue stale running jobs (watchdog), claim one due job
(SKIP LOCKED) and run it through `ExportGenerator` with fenced heartbeats.

    python -m affilio.worker.exporter               # loop until SIGTERM/SIGINT
    python -m affilio.worker.exporter --once        # one claim/run cycle (tests, CLI)
    python -m affilio.worker.exporter --healthcheck # exit 0 when the heartbeat file is fresh
"""

from __future__ import annotations

import os
import signal
import socket
import sys
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import structlog

from affilio.core.config import Settings, get_settings
from affilio.core.logging import configure_logging
from affilio.db.session import get_session_factory
from affilio.modules.media.exports import (
    REQUIRED_ENCODERS,
    ExportGenerator,
    check_encoders,
    claim_next_export_job,
    cleanup_orphan_temp_files,
    recover_stale_export_jobs,
)
from affilio.modules.media.roots import MediaRootRegistry

log = structlog.get_logger("affilio.exporter")

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


class Exporter:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        registry: MediaRootRegistry | None = None,
        worker_id: str = WORKER_ID,
    ) -> None:
        self.settings = settings or get_settings()
        self.registry = registry or MediaRootRegistry.from_settings(self.settings)
        self.worker_id = worker_id
        self.session_factory = get_session_factory()
        self.stop_event = threading.Event()
        self.generator = ExportGenerator(self.settings, self.registry, worker_id=worker_id)

    def touch_heartbeat(self) -> None:
        try:
            path = self.settings.exporter_heartbeat_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(datetime.now(UTC).isoformat())
        except OSError as exc:  # pragma: no cover - diagnostics only
            log.debug("exporter.heartbeat_file_failed", error=str(exc))

    def run_once(self) -> bool:
        """Recover stale jobs, then claim and run at most one job. Returns True if one ran."""
        cleanup_orphan_temp_files(
            self.registry.exports,
            older_than=timedelta(seconds=self.settings.export_stale_after_seconds),
        )
        with self.session_factory() as session:
            stale = recover_stale_export_jobs(
                session, stale_after=timedelta(seconds=self.settings.export_stale_after_seconds)
            )
            if stale:
                session.commit()
                for job in stale:
                    log.warning(
                        "media.export.recovered",
                        job_id=str(job.id),
                        status=job.status.value,
                        attempt=job.attempt,
                    )
            job = claim_next_export_job(session, self.worker_id)
            if job is None:
                return False
            session.commit()
            log.info("media.export.start", job_id=str(job.id), attempt=job.attempt)
            result = self.generator.run(session, job)
            log.info(
                "media.export.done",
                job_id=str(result.id),
                status=result.status.value,
                error_code=result.error_code,
            )
            return True

    def run_forever(self) -> None:
        def _stop(signum, _frame) -> None:
            log.info("exporter.stopping", signal=signum)
            self.stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, _stop)
        encoders = check_encoders(self.settings.media_ffmpeg_path)
        if not all(encoders.values()):
            log.error("exporter.encoders_missing", **encoders)
        log.info(
            "exporter.started",
            worker_id=self.worker_id,
            threads=self.settings.export_threads,
            nice=self.settings.export_nice,
            preset=self.settings.export_preset,
            finals_root=bool(self.registry.finals),
            exports_root=bool(self.registry.exports),
        )
        while not self.stop_event.is_set():
            self.touch_heartbeat()
            try:
                did_work = self.run_once()
            except Exception as exc:
                log.exception("exporter.iteration_failed", error=str(exc))
                did_work = False
            if not did_work:
                self.stop_event.wait(self.settings.export_poll_interval_seconds)
        log.info("exporter.stopped", worker_id=self.worker_id)


def heartbeat_is_fresh(settings: Settings, max_age_seconds: int = 120) -> bool:
    try:
        age = time.time() - settings.exporter_heartbeat_path.stat().st_mtime
    except OSError:
        return False
    return age <= max_age_seconds


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    settings = get_settings()
    if "--healthcheck" in argv:
        return 0 if heartbeat_is_fresh(settings) else 1
    if "--check" in argv:  # encoder availability of this ffmpeg build; touches no media
        found = check_encoders(settings.media_ffmpeg_path)
        for name in REQUIRED_ENCODERS:
            sys.stdout.write(f"{name}: {'ok' if found.get(name) else 'MISSING'}\n")
        return 0 if all(found.values()) else 1
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    exporter = Exporter(settings)
    if "--once" in argv:
        exporter.touch_heartbeat()
        ran = exporter.run_once()
        sys.stdout.write("export job processed\n" if ran else "no export job due\n")
        return 0
    exporter.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
