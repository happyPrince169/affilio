"""Restart / overdue schedule policy (M3B.2A follow-up). Needs TEST_DATABASE_URL."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from affilio.app import create_app
from affilio.core.config import get_settings
from affilio.core.enums import MembershipRole
from affilio.db.session import dispose_engine
from affilio.modules.accounts.models import SocialAccount
from affilio.modules.accounts.providers.mock import MockMetaProvider
from affilio.worker.publisher import Publisher
from tests.auth_helpers import create_member, login, sign_in
from tests.engine_helpers import (
    ALPHA,
    GAMMA,
    connect_pages,
    drive,
    engine_env,
    make_job,
    refresh,
    set_scheduled,
    start_job,
    upload_rows,
)
from tests.publishing_helpers import act, create_campaign, make_final

pytestmark = pytest.mark.database


@pytest.fixture
def api(
    monkeypatch: pytest.MonkeyPatch, media_dirs, organization, schema, db_session: Session
) -> Iterator[TestClient]:
    engine_env(monkeypatch, media_dirs, organization)
    monkeypatch.setenv("PUBLISHER_OVERDUE_GRACE_MINUTES", "120")
    monkeypatch.setenv("PUBLISHER_OVERDUE_POLICY", "review")
    MockMetaProvider.reset()
    get_settings.cache_clear()
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        sign_in(client, db_session, organization, role=MembershipRole.ADMIN)
        yield client
    dispose_engine()
    MockMetaProvider.reset()
    get_settings.cache_clear()


@pytest.fixture
def world(api: TestClient, db_session: Session, organization, media_dirs) -> dict:
    pages = connect_pages(api)
    asset = make_final(db_session, organization, media_dirs, "overdue_v1.mp4")
    campaign = create_campaign(api, "Overdue campaign")
    return {"pages": pages, "asset": asset, "campaign": campaign}


def _job(api: TestClient, world: dict, page: str, *, campaign: dict | None = None) -> dict:
    return make_job(
        api,
        (campaign or world["campaign"])["id"],
        str(world["asset"].id),
        world["pages"][page]["id"],
    )


def _ago(minutes: int) -> datetime:
    return datetime.now(UTC) - timedelta(minutes=minutes)


def _audit(api: TestClient, job_id: str) -> list[dict]:
    return api.get(
        "/api/v1/auth/audit",
        params={"entity_type": "publication_job", "entity_id": job_id, "limit": 100},
    ).json()


def test_jobs_within_grace_publish_after_revalidation(
    api: TestClient, world: dict, db_session: Session
) -> None:
    job = _job(api, world, ALPHA)
    set_scheduled(db_session, job["id"], _ago(30))
    drive()
    done = refresh(api, job["id"])
    assert done["status"] == "published"
    release = next(e for e in _audit(api, job["id"]) if e["action"] == "publication.release")
    assert 29 <= release["data"]["lateness_minutes"] <= 31 and "reason" not in release["data"]


def test_jobs_beyond_grace_wait_for_operator_review(
    api: TestClient, world: dict, db_session: Session
) -> None:
    job = _job(api, world, ALPHA)
    set_scheduled(db_session, job["id"], _ago(5 * 60))
    drive()
    drive()  # a second pass must not flag or audit twice
    held = refresh(api, job["id"])
    assert held["status"] == "scheduled"
    assert held["overdue_review_required"] and held["overdue_review_at"]
    assert held["overdue_minutes"] >= 299
    assert "release" in held["allowed_actions"]
    assert upload_rows(db_session, job["id"]) == []
    holds = [e for e in _audit(api, job["id"]) if e["action"] == "publication.release_held"]
    assert len(holds) == 1 and holds[0]["data"]["reason"] == "overdue_beyond_grace"
    assert holds[0]["data"]["lateness_minutes"] >= 299 and holds[0]["data"]["grace_minutes"] == 120
    # Operator decision: release now.
    released = act(api, held, "release")
    assert released["status"] == "ready_to_publish" and not released["overdue_review_required"]
    release = next(e for e in _audit(api, job["id"]) if e["action"] == "publication.release")
    assert (
        release["data"]["overdue_override"] is True and release["data"]["lateness_minutes"] >= 299
    )
    assert release["actor_label"] != "system"
    drive()
    assert refresh(api, job["id"])["status"] == "published"


def test_release_requires_a_due_job_and_an_editor(
    api: TestClient, world: dict, db_session: Session, organization
) -> None:
    future = _job(api, world, ALPHA)
    set_scheduled(db_session, future["id"], datetime.now(UTC) + timedelta(hours=3))
    not_due = act(api, refresh(api, future["id"]), "release", expected=409)
    assert not_due["error"]["code"] == "invalid_publication_transition"
    overdue = _job(api, world, GAMMA)
    set_scheduled(db_session, overdue["id"], _ago(10 * 60))
    drive()
    viewer = create_member(db_session, organization, role=MembershipRole.VIEWER)
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        login(client, viewer)
        job = refresh(api, overdue["id"])
        denied = client.post(
            f"/api/v1/publishing/publications/{job['id']}/release", json={"version": job["version"]}
        )
        assert denied.status_code == 403
    # Rescheduling clears the review flag.
    job = refresh(api, overdue["id"])
    rescheduled = act(
        api, job, "schedule", scheduled_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat()
    )
    assert rescheduled["status"] == "scheduled" and not rescheduled["overdue_review_required"]


def test_publish_policy_releases_overdue_jobs_with_an_audited_reason(
    api: TestClient, world: dict, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PUBLISHER_OVERDUE_POLICY", "publish")
    get_settings.cache_clear()
    job = _job(api, world, ALPHA)
    set_scheduled(db_session, job["id"], _ago(6 * 60))
    drive()
    assert refresh(api, job["id"])["status"] == "published"
    release = next(e for e in _audit(api, job["id"]) if e["action"] == "publication.release")
    assert release["data"]["reason"] == "overdue_published_by_policy"


def test_nas_downtime_scenario_after_worker_restart(
    api: TestClient, world: dict, db_session: Session
) -> None:
    """The publisher was down for hours: one job is slightly late, one far beyond the grace
    window, one still in the future; a held Page keeps its hold across the restart."""
    other = create_campaign(api, "Downtime campaign")
    slightly_late = _job(api, world, ALPHA)
    far_overdue = _job(api, world, GAMMA)
    future = _job(api, world, ALPHA, campaign=other)
    set_scheduled(db_session, slightly_late["id"], _ago(20))
    set_scheduled(db_session, far_overdue["id"], _ago(8 * 60))
    set_scheduled(db_session, future["id"], datetime.now(UTC) + timedelta(hours=1))
    # A rate-limit hold recorded before the downtime must survive the restart (durable).
    gamma = db_session.get(SocialAccount, uuid.UUID(world["pages"][GAMMA]["id"]))
    gamma.publish_hold_until = datetime.now(UTC) + timedelta(minutes=30)
    gamma.publish_hold_reason = "rate_limited"
    db_session.commit()

    restarted = Publisher(get_settings(), worker_id="publisher-after-restart")
    for _ in range(30):
        restarted.run_once()
        if refresh(api, slightly_late["id"])["status"] == "published":
            break
    restarted.pool.shutdown(wait=True)

    assert refresh(api, slightly_late["id"])["status"] == "published"
    held = refresh(api, far_overdue["id"])
    assert held["status"] == "scheduled" and held["overdue_review_required"]
    assert refresh(api, future["id"])["status"] == "scheduled"
    assert refresh(api, future["id"])["overdue_minutes"] is None
    # Gamma's hold is still in force: even an explicit release cannot start an upload yet.
    released = act(api, held, "release")
    assert (
        released["status"] == "ready_to_publish"
        and released["target_hold_reason"] == "rate_limited"
    )
    drive()
    assert refresh(api, far_overdue["id"])["status"] == "ready_to_publish"
    assert upload_rows(db_session, far_overdue["id"]) == []


def test_api_start_respects_overdue_flag_only_through_release(
    api: TestClient, world: dict, db_session: Session
) -> None:
    job = _job(api, world, ALPHA)
    set_scheduled(db_session, job["id"], _ago(5 * 60))
    drive()
    held = refresh(api, job["id"])
    # "start" from SCHEDULED is still an explicit operator action and therefore allowed.
    started = start_job(api, held)
    assert started["status"] == "publishing"
    drive()
    assert refresh(api, job["id"])["status"] == "published"
