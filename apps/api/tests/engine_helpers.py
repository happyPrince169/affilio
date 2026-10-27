"""Helpers for publishing-engine tests: mock Pages, approved finals, and step driving."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from affilio.core.config import Settings, get_settings
from affilio.core.enums import UploadPhase
from affilio.db.session import get_session_factory
from affilio.modules.media.roots import MediaRootRegistry
from affilio.modules.publishing import engine
from affilio.modules.publishing.models import PublicationJob, PublicationUpload
from tests.connection_helpers import connect, link, mock_env
from tests.publishing_helpers import act, approve_flow, create_publication

ALPHA, GAMMA = "100000000000001", "100000000000003"


def engine_env(monkeypatch: pytest.MonkeyPatch, media_dirs, organization) -> None:
    """Mock provider + vault + zero delays so steps can be driven synchronously."""
    for key, value in media_dirs.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DEFAULT_ORGANIZATION_SLUG", organization.slug)
    monkeypatch.delenv("MEDIA_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("MEDIA_FINAL_SETTLE_SECONDS", "0")
    monkeypatch.setenv("PUBLISHER_STATUS_POLL_SECONDS", "0")
    monkeypatch.setenv("PUBLISHER_BACKOFF_BASE_SECONDS", "0")
    monkeypatch.setenv("PUBLISHER_BACKOFF_MAX_SECONDS", "0")
    monkeypatch.setenv("PUBLISHER_UPLOAD_CHUNK_BYTES", "2048")
    monkeypatch.setenv("PUBLISHER_MAX_RETRIES", "3")
    monkeypatch.setenv("PUBLISHER_LEASE_SECONDS", "60")
    monkeypatch.setenv("META_LIVE_PUBLISHING_ENABLED", "false")
    mock_env(monkeypatch)


def connect_pages(client: TestClient, page_ids: list[str] | None = None) -> dict[str, dict]:
    connection_id = connect(client, "success")
    linked = link(client, connection_id, page_ids or [ALPHA, GAMMA])
    return {a["external_account_id"]: a for a in linked}


def make_job(
    client: TestClient, campaign_id: str, asset_id: str, account_id: str, *, caption: str = "Deal"
) -> dict:
    job = create_publication(client, campaign_id, asset_id, account_id, caption=caption)
    return approve_flow(client, job)


def start_job(client: TestClient, job: dict) -> dict:
    return act(client, job, "start")


def refresh(client: TestClient, job_id: str) -> dict:
    response = client.get(f"/api/v1/publishing/publications/{job_id}")
    assert response.status_code == 200, response.text
    return response.json()


def registry() -> MediaRootRegistry:
    return MediaRootRegistry.from_settings(get_settings())


def drive(
    *,
    worker_id: str = "test-worker",
    max_steps: int = 60,
    settings: Settings | None = None,
    stop_when=None,
) -> list[engine.StepResult]:
    """Claim + run steps until nothing is claimable (or `stop_when(result)` is true)."""
    settings = settings or get_settings()
    factory = get_session_factory()
    reg = registry()
    results: list[engine.StepResult] = []
    for _ in range(max_steps):
        with factory() as session:
            engine.release_due_everywhere(session)
            engine.abandon_orphans(session)
            engine.dispatch_ready(session, settings)
            session.commit()
            claimed = engine.claim_uploads(
                session,
                worker_id=worker_id,
                limit=4,
                lease_seconds=settings.publisher_lease_seconds,
            )
            session.commit()
        if not claimed:
            break
        for upload_id in claimed:
            with factory() as session:
                result = engine.run_step(session, settings, reg, upload_id, worker_id=worker_id)
            results.append(result)
            if stop_when is not None and stop_when(result):
                return results
    return results


def upload_rows(session: Session, job_id: str) -> list[PublicationUpload]:
    session.expire_all()
    return list(
        session.scalars(
            select(PublicationUpload)
            .where(PublicationUpload.job_id == uuid.UUID(job_id))
            .order_by(PublicationUpload.created_at)
        )
    )


def live_upload(session: Session, job_id: str) -> PublicationUpload:
    rows = [u for u in upload_rows(session, job_id) if u.is_live]
    assert len(rows) == 1, [(u.phase, u.last_error_code) for u in upload_rows(session, job_id)]
    return rows[0]


def set_scheduled(session: Session, job_id: str, when: datetime) -> None:
    job = session.get(PublicationJob, uuid.UUID(job_id))
    assert job is not None
    job.scheduled_at = when
    job.status = "scheduled"
    session.commit()


def past() -> datetime:
    return datetime.now(UTC) - timedelta(minutes=5)


def phases(results: list[engine.StepResult]) -> list[str]:
    return [f"{r.phase_before.value}->{r.phase_after.value}" for r in results]


TERMINAL = {UploadPhase.PUBLISHED, UploadPhase.FAILED, UploadPhase.NEEDS_RECONCILIATION}
