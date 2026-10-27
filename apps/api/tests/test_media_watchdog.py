"""Scan watchdog, job fence and cancellation (M3C.1 step 1). Requires TEST_DATABASE_URL."""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from affilio.app import create_app
from affilio.core.config import get_settings
from affilio.core.enums import (
    MediaVariantKind,
    MediaVariantStatus,
    MembershipRole,
    ScanJobStatus,
    ScanTrigger,
    ThumbnailStatus,
)
from affilio.db.session import dispose_engine
from affilio.modules.identity.models import AuditEvent
from affilio.modules.media.errors import ScanNotCancellableError
from affilio.modules.media.models import MediaAsset, MediaFileVariant, MediaScanJob
from affilio.modules.media.service import cancel_scan, enqueue_scan, recover_stale_scan_jobs
from affilio.worker.runner import claim_next_scan_job, execute_scan_job
from tests.auth_helpers import sign_in
from tests.media_helpers import FakeProber, build_settings, write_bytes

pytestmark = pytest.mark.database

STALE = timedelta(minutes=10)


def _asset_count(session: Session, organization_id: uuid.UUID) -> int:
    return session.scalar(
        select(func.count(MediaAsset.id)).where(MediaAsset.organization_id == organization_id)
    )


def _claimed_job(db_session: Session, organization, media_dirs, **enqueue_kwargs) -> MediaScanJob:
    enqueue_scan(
        db_session,
        organization.id,
        media_dirs.registry,
        trigger=ScanTrigger.MANUAL,
        **enqueue_kwargs,
    )
    db_session.commit()
    job = claim_next_scan_job(db_session)
    assert job is not None and job.status == ScanJobStatus.RUNNING
    db_session.commit()
    return job


# ------------------------------------------------------------------------- watchdog recovery


def test_stale_running_job_is_requeued_and_fresh_jobs_are_left_alone(
    db_session: Session, organization, media_dirs
) -> None:
    write_bytes(media_dirs.originals / "clip.mp4", 5_000, seed=1)
    settings = build_settings(media_dirs)
    job = _claimed_job(db_session, organization, media_dirs, max_attempts=3)

    # Fresh heartbeat: nothing to recover, even on repeated passes.
    assert recover_stale_scan_jobs(db_session, stale_after=STALE) == []
    assert recover_stale_scan_jobs(db_session, stale_after=STALE) == []

    # The worker died: its last heartbeat is older than the stale window.
    job.heartbeat_at = datetime.now(UTC) - STALE - timedelta(seconds=1)
    db_session.commit()
    recovered = recover_stale_scan_jobs(db_session, stale_after=STALE)
    db_session.commit()

    assert [r.id for r in recovered] == [job.id]
    assert job.status == ScanJobStatus.QUEUED
    assert job.attempt == 1  # the retry increments it on claim, not on recovery
    assert job.finished_at is None
    assert job.next_attempt_at is not None and job.next_attempt_at > datetime.now(UTC)
    assert "Recovered by watchdog" in (job.error or "")
    # Idempotent: a second pass (another worker's watchdog) finds nothing.
    assert recover_stale_scan_jobs(db_session, stale_after=STALE) == []

    # Once the backoff elapses the job is claimed again and completes normally.
    job.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    again = claim_next_scan_job(db_session)
    assert again is not None and again.id == job.id and again.attempt == 2
    db_session.commit()
    done = execute_scan_job(
        db_session, again, settings=settings, registry=media_dirs.registry, prober=FakeProber()
    )
    assert done.status == ScanJobStatus.SUCCEEDED and done.stats["added"] == 1
    assert done.error is None and done.heartbeat_at is not None


def test_stale_job_with_no_attempts_left_becomes_failed(
    db_session: Session, organization, media_dirs
) -> None:
    job = _claimed_job(db_session, organization, media_dirs, max_attempts=1)
    job.heartbeat_at = None  # falls back to started_at
    job.started_at = datetime.now(UTC) - STALE - timedelta(seconds=1)
    db_session.commit()

    recovered = recover_stale_scan_jobs(db_session, stale_after=STALE)
    db_session.commit()

    assert [r.id for r in recovered] == [job.id]
    assert job.status == ScanJobStatus.FAILED
    assert job.finished_at is not None and job.next_attempt_at is None
    assert "no attempts left" in (job.error or "")
    assert claim_next_scan_job(db_session) is None


def test_recovery_skips_rows_locked_by_another_watchdog(
    db_session: Session, organization, media_dirs, schema: Engine
) -> None:
    job = _claimed_job(db_session, organization, media_dirs, max_attempts=3)
    job.heartbeat_at = datetime.now(UTC) - STALE - timedelta(seconds=1)
    db_session.commit()

    other_factory = sessionmaker(bind=schema, autoflush=False, expire_on_commit=False)
    with other_factory() as other:
        # Another watchdog holds the row (mid-recovery): ours must neither block nor double up.
        locked = other.scalar(
            select(MediaScanJob).where(MediaScanJob.id == job.id).with_for_update()
        )
        assert locked is not None
        assert recover_stale_scan_jobs(db_session, stale_after=STALE) == []
        db_session.rollback()
        other.rollback()

    recovered = recover_stale_scan_jobs(db_session, stale_after=STALE)
    db_session.commit()
    assert [r.id for r in recovered] == [job.id] and job.status == ScanJobStatus.QUEUED


# ------------------------------------------------------------------------------- job fence


def test_zombie_worker_is_fenced_out_after_recovery_and_job_runs_once(
    db_session: Session, organization, media_dirs, schema: Engine
) -> None:
    for index in range(3):
        write_bytes(media_dirs.originals / f"clip_{index}.mp4", 4_000 + index, seed=index)
    # heartbeat_seconds=0 → the zombie hits a fenced checkpoint after its first file.
    settings = build_settings(media_dirs, media_scan_heartbeat_seconds=0)
    zombie_job = _claimed_job(db_session, organization, media_dirs, max_attempts=3)

    other_factory = sessionmaker(bind=schema, autoflush=False, expire_on_commit=False)
    with other_factory() as watchdog:
        stale = watchdog.get(MediaScanJob, zombie_job.id)
        assert stale is not None
        stale.heartbeat_at = datetime.now(UTC) - STALE - timedelta(seconds=1)
        watchdog.commit()
        assert len(recover_stale_scan_jobs(watchdog, stale_after=STALE)) == 1
        watchdog.commit()

    # The "dead" worker was only stalled and resumes: it must not write anything.
    result = execute_scan_job(
        db_session,
        zombie_job,
        settings=settings,
        registry=media_dirs.registry,
        prober=FakeProber(),
    )
    assert result.status == ScanJobStatus.QUEUED and result.attempt == 1
    assert "Recovered by watchdog" in (result.error or "")
    assert _asset_count(db_session, organization.id) == 0

    # A live worker picks the recovered job up exactly once and indexes everything.
    result.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    live_job = claim_next_scan_job(db_session)
    assert live_job is not None and live_job.id == zombie_job.id and live_job.attempt == 2
    db_session.commit()
    done = execute_scan_job(
        db_session, live_job, settings=settings, registry=media_dirs.registry, prober=FakeProber()
    )
    assert done.status == ScanJobStatus.SUCCEEDED and done.stats["added"] == 3
    assert _asset_count(db_session, organization.id) == 3
    assert claim_next_scan_job(db_session) is None


# ------------------------------------------------------------------------------ cancellation


def test_cancel_queued_job_frees_the_root(db_session: Session, organization, media_dirs) -> None:
    job = enqueue_scan(db_session, organization.id, media_dirs.registry, trigger=ScanTrigger.MANUAL)
    db_session.commit()

    cancelled = cancel_scan(db_session, organization.id, job.id, actor="tester")
    db_session.commit()

    assert cancelled.id == job.id and cancelled.status == ScanJobStatus.CANCELLED
    assert cancelled.finished_at is not None and cancelled.next_attempt_at is None
    assert (cancelled.error or "").startswith("Cancelled by tester")
    with pytest.raises(ScanNotCancellableError):
        cancel_scan(db_session, organization.id, job.id, actor="tester")
    assert claim_next_scan_job(db_session) is None  # never handed to a worker
    # The root is free again: a new scan can be queued immediately.
    enqueue_scan(db_session, organization.id, media_dirs.registry, trigger=ScanTrigger.MANUAL)


def test_cancel_running_job_stops_worker_at_next_checkpoint(
    db_session: Session, organization, media_dirs, schema: Engine
) -> None:
    for index in range(3):
        write_bytes(media_dirs.originals / f"clip_{index}.mp4", 4_000 + index, seed=index)
    settings = build_settings(media_dirs, media_scan_heartbeat_seconds=0)
    job = _claimed_job(db_session, organization, media_dirs)

    other_factory = sessionmaker(bind=schema, autoflush=False, expire_on_commit=False)
    with other_factory() as admin:
        cancel_scan(admin, organization.id, job.id, actor="admin")
        admin.commit()

    result = execute_scan_job(
        db_session, job, settings=settings, registry=media_dirs.registry, prober=FakeProber()
    )

    assert result.status == ScanJobStatus.CANCELLED
    assert "discards uncommitted work" in (result.error or "")
    assert _asset_count(db_session, organization.id) == 0
    assert claim_next_scan_job(db_session) is None


def test_cancel_after_last_checkpoint_blocks_the_final_write(
    db_session: Session, organization, media_dirs, schema: Engine
) -> None:
    write_bytes(media_dirs.originals / "clip.mp4", 5_000, seed=1)
    # No intermediate checkpoints: only the final fenced write can notice the cancellation.
    settings = build_settings(
        media_dirs, media_scan_heartbeat_seconds=3_600, media_scan_stale_after_seconds=36_000
    )
    job = _claimed_job(db_session, organization, media_dirs)

    other_factory = sessionmaker(bind=schema, autoflush=False, expire_on_commit=False)
    with other_factory() as admin:
        cancel_scan(admin, organization.id, job.id, actor="admin")
        admin.commit()

    result = execute_scan_job(
        db_session, job, settings=settings, registry=media_dirs.registry, prober=FakeProber()
    )

    assert result.status == ScanJobStatus.CANCELLED and result.stats == {}
    assert _asset_count(db_session, organization.id) == 0


def test_failure_outcome_is_fenced_too(
    db_session: Session, organization, media_dirs, schema: Engine
) -> None:
    settings = build_settings(media_dirs)
    job = _claimed_job(db_session, organization, media_dirs, max_attempts=3)
    shutil.rmtree(media_dirs.originals)  # the scan will fail with a configuration error

    other_factory = sessionmaker(bind=schema, autoflush=False, expire_on_commit=False)
    with other_factory() as admin:
        cancel_scan(admin, organization.id, job.id, actor="admin")
        admin.commit()

    result = execute_scan_job(db_session, job, settings=settings, registry=media_dirs.registry)

    # Without the fence this would have re-queued the cancelled job for a retry.
    assert result.status == ScanJobStatus.CANCELLED and result.next_attempt_at is None
    assert claim_next_scan_job(db_session) is None


# ----------------------------------------------------------------------------------- API


@contextmanager
def _signed_in_client(
    monkeypatch: pytest.MonkeyPatch,
    media_dirs,
    organization,
    db_session: Session,
    *,
    role: MembershipRole = MembershipRole.OWNER,
    admin_token: str | None = None,
) -> Iterator[TestClient]:
    for key, value in media_dirs.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DEFAULT_ORGANIZATION_SLUG", organization.slug)
    if admin_token is None:
        monkeypatch.delenv("MEDIA_ADMIN_TOKEN", raising=False)
    else:
        monkeypatch.setenv("MEDIA_ADMIN_TOKEN", admin_token)
    get_settings.cache_clear()
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        if admin_token is None:
            sign_in(client, db_session, organization, role=role)
        yield client
    dispose_engine()
    get_settings.cache_clear()


def test_cancel_scan_api_lifecycle(
    monkeypatch: pytest.MonkeyPatch, media_dirs, organization, schema, db_session: Session
) -> None:
    with _signed_in_client(monkeypatch, media_dirs, organization, db_session) as client:
        created = client.post("/api/v1/media/scans", json={})
        assert created.status_code == 202, created.text
        job_id = created.json()["id"]
        assert client.get("/api/v1/media/scans/status").json()["current"]["id"] == job_id

        cancelled = client.post(f"/api/v1/media/scans/{job_id}/cancel")
        assert cancelled.status_code == 200, cancelled.text
        body = cancelled.json()
        assert body["status"] == "cancelled" and body["finished_at"] is not None
        assert body["error"].startswith("Cancelled by ")

        again = client.post(f"/api/v1/media/scans/{job_id}/cancel")
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "scan_not_cancellable"
        assert client.post(f"/api/v1/media/scans/{uuid.uuid4()}/cancel").status_code == 404

        status = client.get("/api/v1/media/scans/status").json()
        assert status["current"] is None
        history = client.get("/api/v1/media/scans").json()
        assert [h["status"] for h in history if h["id"] == job_id] == ["cancelled"]
        # The root is free: queueing works again straight away.
        assert client.post("/api/v1/media/scans", json={}).status_code == 202

    audit = db_session.scalars(
        select(AuditEvent).where(
            AuditEvent.organization_id == organization.id, AuditEvent.action == "scan.cancelled"
        )
    ).all()
    assert len(audit) == 1 and str(audit[0].entity_id) == job_id


def test_cancel_scan_requires_admin_role(
    monkeypatch: pytest.MonkeyPatch, media_dirs, organization, schema, db_session: Session
) -> None:
    job = enqueue_scan(db_session, organization.id, media_dirs.registry, trigger=ScanTrigger.CLI)
    db_session.commit()
    with _signed_in_client(
        monkeypatch, media_dirs, organization, db_session, role=MembershipRole.EDITOR
    ) as client:
        assert client.get(f"/api/v1/media/scans/{job.id}").status_code == 200  # reads are fine
        denied = client.post(f"/api/v1/media/scans/{job.id}/cancel")
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "forbidden"
    db_session.refresh(job)
    assert job.status == ScanJobStatus.QUEUED


def test_service_token_may_cancel_scans(
    monkeypatch: pytest.MonkeyPatch, media_dirs, organization, schema, db_session: Session
) -> None:
    headers = {"X-Affilio-Admin-Token": "s3cret-token"}
    with _signed_in_client(
        monkeypatch, media_dirs, organization, db_session, admin_token="s3cret-token"
    ) as client:
        created = client.post("/api/v1/media/scans", json={}, headers=headers)
        assert created.status_code == 202, created.text
        job_id = created.json()["id"]
        assert client.post(f"/api/v1/media/scans/{job_id}/cancel").status_code == 401
        cancelled = client.post(f"/api/v1/media/scans/{job_id}/cancel", headers=headers)
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"


# ------------------------------------------------------------------ partial-commit resume


class _CancellingProber:
    """Probes like FakeProber but cancels the job (from another session) on the N-th call,
    i.e. while the worker is in the middle of a file operation."""

    def __init__(self, inner: FakeProber, *, on_call: int, cancel) -> None:
        self.inner = inner
        self.on_call = on_call
        self.cancel = cancel

    def probe(self, path):
        result = self.inner.probe(path)
        if len(self.inner.calls) == self.on_call:
            self.cancel()
        return result


def test_cancelled_scan_resumes_without_duplicates_or_dangling_state(
    db_session: Session, organization, media_dirs, schema: Engine
) -> None:
    for index in range(3):
        write_bytes(media_dirs.originals / "shoot" / f"C{index}.MOV", 6_000 + index, seed=index)
        write_bytes(media_dirs.proxies / "shoot" / f"C{index}.mp4", 1_000 + index, seed=50 + index)
    # Checkpoint after every unit of work, never on time: file 1 commits, file 2 is in flight
    # when the cancel lands, file 3 is never reached.
    settings = build_settings(
        media_dirs,
        media_scan_commit_every=1,
        media_scan_heartbeat_seconds=3_600,
        media_scan_stale_after_seconds=36_000,
    )
    job = _claimed_job(db_session, organization, media_dirs)
    other_factory = sessionmaker(bind=schema, autoflush=False, expire_on_commit=False)

    def cancel_from_admin() -> None:
        with other_factory() as admin:
            cancel_scan(admin, organization.id, job.id, actor="admin")
            admin.commit()

    # Walk: root dir, shoot dir, C0 (probe 1 → checkpoint commits C0), C1 (probe 2 → cancel,
    # then the checkpoint is fenced out and the uncommitted C1 is rolled back).
    prober = _CancellingProber(FakeProber(), on_call=2, cancel=cancel_from_admin)
    result = execute_scan_job(
        db_session, job, settings=settings, registry=media_dirs.registry, prober=prober
    )
    assert result.status == ScanJobStatus.CANCELLED
    assert _asset_count(db_session, organization.id) == 1  # C0 only, no proxy linked yet
    assert result.stats["added"] == 1  # progress counters reflect the last checkpoint

    # Resume with a fresh job: nothing duplicated, everything linked, counters consistent.
    resume = _claimed_job(db_session, organization, media_dirs)
    done = execute_scan_job(
        db_session,
        resume,
        settings=build_settings(media_dirs),
        registry=media_dirs.registry,
        prober=FakeProber(),
    )
    assert done.status == ScanJobStatus.SUCCEEDED
    assert done.stats["added"] == 2 and done.stats["unchanged"] == 1
    assert done.stats["proxies_linked"] == 3 and done.stats["duplicates"] == 0
    assets = db_session.scalars(
        select(MediaAsset).where(MediaAsset.organization_id == organization.id)
    ).all()
    assert len(assets) == 3
    for asset in assets:
        db_session.refresh(asset)
        original = asset.variant(MediaVariantKind.ORIGINAL)
        proxy = asset.variant(MediaVariantKind.PROXY)
        assert original is not None and original.fingerprint and original.probed_at
        assert proxy is not None and proxy.status == MediaVariantStatus.PRESENT
        assert asset.thumbnail_status == ThumbnailStatus.PENDING
        assert asset.duplicate_of_id is None
    paths = db_session.scalars(select(MediaFileVariant.relative_path)).all()
    assert len(paths) == len(set(paths)) == 6  # 3 originals + 3 proxies, each path once
