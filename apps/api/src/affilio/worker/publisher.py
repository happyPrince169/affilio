"""`python -m affilio.worker.publisher` — the Reels publisher worker (M3B.2A).

Separate from the media scan/thumbnail worker (`affilio.worker.runner`), which is untouched.

Loop (no busy polling): heartbeat → release due schedules → dispatch API-eligible READY jobs →
claim due uploads (bounded by free pool slots) → run one step per claimed upload in a thread
pool → wait on the stop event for the poll interval (shorter while work is in flight).
Everything durable lives in PostgreSQL; the only in-memory state is the set of in-flight
futures. SIGTERM/SIGINT stop claiming, let in-flight steps finish (each is one network call
kind, bounded by the step time budget), mark the worker row stopped and exit.
"""

from __future__ import annotations

import os
import signal
import socket
import sys
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import delete, select

from affilio import __version__
from affilio.core.config import Settings, get_settings
from affilio.core.logging import configure_logging
from affilio.db.session import get_session_factory
from affilio.modules.media.roots import MediaRootRegistry
from affilio.modules.publishing import engine
from affilio.modules.publishing.models import PublisherWorker

log = structlog.get_logger("affilio.publisher")

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
STALE_WORKER_AFTER = timedelta(days=1)


class Publisher:
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
        self.concurrency = max(1, self.settings.publisher_max_concurrent_uploads)
        self.pool = ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="publish")
        self.in_flight: dict[uuid.UUID, Future] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ heartbeat
    def heartbeat(self) -> None:
        now = datetime.now(UTC)
        with self.session_factory() as session:
            row = session.scalar(
                select(PublisherWorker).where(PublisherWorker.worker_id == self.worker_id)
            )
            if row is None:
                row = PublisherWorker(
                    id=uuid.uuid4(),
                    worker_id=self.worker_id,
                    hostname=socket.gethostname()[:255],
                    pid=os.getpid(),
                    version=__version__[:32],
                    max_concurrency=self.concurrency,
                    started_at=now,
                )
                session.add(row)
            if row.stopped_at is not None:  # same worker id restarted
                row.stopped_at = None
                row.started_at = now
            row.last_seen_at = now
            row.in_flight = len(self.in_flight)
            row.max_concurrency = self.concurrency
            session.execute(
                delete(PublisherWorker).where(
                    PublisherWorker.last_seen_at < now - STALE_WORKER_AFTER,
                    PublisherWorker.worker_id != self.worker_id,
                )
            )
            session.commit()
        try:
            path = self.settings.publisher_heartbeat_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(now.isoformat())
        except OSError as exc:  # pragma: no cover - diagnostics only
            log.debug("publisher.heartbeat_file_failed", error=str(exc))

    def mark_stopped(self) -> None:
        with self.session_factory() as session:
            row = session.scalar(
                select(PublisherWorker).where(PublisherWorker.worker_id == self.worker_id)
            )
            if row is not None:
                row.stopped_at = datetime.now(UTC)
                row.in_flight = 0
                session.commit()

    # ------------------------------------------------------------------ one iteration
    def tick(self) -> int:
        """Release, dispatch, claim and start steps. Returns the number of steps started."""
        self._reap()
        with self.session_factory() as session:
            released = engine.release_due_everywhere(session)
            abandoned = engine.abandon_orphans(session)
            dispatched = engine.dispatch_ready(session, self.settings)
            session.commit()
        if released or dispatched or abandoned:
            log.info(
                "publisher.dispatch",
                released=released,
                dispatched=len(dispatched),
                abandoned=abandoned,
            )
        free = self.concurrency - len(self.in_flight)
        if free <= 0:
            return 0
        with self.session_factory() as session:
            claimed = engine.claim_uploads(
                session,
                worker_id=self.worker_id,
                limit=free,
                lease_seconds=self.settings.publisher_lease_seconds,
            )
            session.commit()
        for upload_id in claimed:
            with self._lock:
                self.in_flight[upload_id] = self.pool.submit(self._run, upload_id)
        return len(claimed)

    def _run(self, upload_id: uuid.UUID) -> engine.StepResult:
        with self.session_factory() as session:
            try:
                result = engine.run_step(
                    session,
                    self.settings,
                    self.registry,
                    upload_id,
                    worker_id=self.worker_id,
                )
            except Exception:
                session.rollback()
                raise
        log.info(
            "publisher.step",
            upload_id=str(upload_id),
            phase=f"{result.phase_before.value}->{result.phase_after.value}",
            note=result.note,
        )
        return result

    def _reap(self) -> None:
        with self._lock:
            done = [uid for uid, fut in self.in_flight.items() if fut.done()]
            for uid in done:
                fut = self.in_flight.pop(uid)
                exc = fut.exception()
                if exc is not None:
                    log.error("publisher.step_failed", upload_id=str(uid), error=str(exc)[:300])

    def run_once(self) -> int:
        """Single iteration for tests and the CLI `--once` flag; waits for started steps."""
        started = self.tick()
        self.wait_idle(timeout=self.settings.publisher_lease_seconds)
        return started

    def wait_idle(self, *, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while self.in_flight and time.monotonic() < deadline:
            self._reap()
            time.sleep(0.05)
        self._reap()

    # ------------------------------------------------------------------ main loop
    def run_forever(self) -> None:
        def _stop(signum, _frame) -> None:
            log.info("publisher.stopping", signal=signum)
            self.stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, _stop)
        log.info(
            "publisher.started",
            worker_id=self.worker_id,
            concurrency=self.concurrency,
            mode=self.settings.meta_provider_mode,
            live_publishing=self.settings.meta_live_publishing_enabled,
            finals_root=bool(self.registry.finals),
        )
        try:
            while not self.stop_event.is_set():
                try:
                    self.heartbeat()
                    started = self.tick()
                except Exception as exc:
                    log.exception("publisher.iteration_failed", error=str(exc))
                    started = 0
                busy = started > 0 or bool(self.in_flight)
                self.stop_event.wait(1.0 if busy else self.settings.publisher_poll_interval_seconds)
        finally:
            log.info("publisher.draining", in_flight=len(self.in_flight))
            self.pool.shutdown(wait=True, cancel_futures=True)
            self._reap()
            try:
                self.mark_stopped()
            except Exception as exc:
                log.warning("publisher.mark_stopped_failed", error=str(exc))
            log.info("publisher.stopped", worker_id=self.worker_id)


def heartbeat_is_fresh(settings: Settings, max_age_seconds: int = 120) -> bool:
    try:
        age = time.time() - settings.publisher_heartbeat_path.stat().st_mtime
    except OSError:
        return False
    return age <= max_age_seconds


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    settings = get_settings()
    if "--healthcheck" in argv:
        return 0 if heartbeat_is_fresh(settings) else 1
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    publisher = Publisher(settings)
    if "--once" in argv:
        publisher.heartbeat()
        started = publisher.run_once()
        publisher.mark_stopped()
        sys.stdout.write(f"steps started: {started}\n")
        return 0
    publisher.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
