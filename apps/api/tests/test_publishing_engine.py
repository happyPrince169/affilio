"""Reels publishing engine against the mock provider (M3B.2A). Needs TEST_DATABASE_URL."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from affilio.app import create_app
from affilio.core.config import Settings, get_settings
from affilio.core.enums import MembershipRole, UploadPhase
from affilio.db.session import dispose_engine, get_session_factory
from affilio.modules.accounts.models import SocialAccount
from affilio.modules.accounts.providers.mock import MockMetaProvider
from affilio.modules.publishing import engine
from affilio.modules.publishing.eligibility import account_api_eligibility
from affilio.modules.publishing.models import PublicationJob, PublicationUpload
from affilio.worker.publisher import Publisher
from tests.auth_helpers import create_member, login, sign_in
from tests.connection_helpers import assert_no_secret
from tests.engine_helpers import (
    ALPHA,
    GAMMA,
    connect_pages,
    drive,
    engine_env,
    live_upload,
    make_job,
    past,
    phases,
    refresh,
    set_scheduled,
    start_job,
    upload_rows,
)
from tests.media_helpers import create_organization
from tests.publishing_helpers import act, create_campaign, make_final, register_page

pytestmark = pytest.mark.database


@pytest.fixture
def api(
    monkeypatch: pytest.MonkeyPatch, media_dirs, organization, schema, db_session: Session
) -> Iterator[TestClient]:
    engine_env(monkeypatch, media_dirs, organization)
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
    asset = make_final(db_session, organization, media_dirs, "reel_v1.mp4")
    campaign = create_campaign(api, "Engine campaign")
    return {"pages": pages, "asset": asset, "campaign": campaign}


def _job(api: TestClient, world: dict, page: str = ALPHA, **kw) -> dict:
    return make_job(
        api, world["campaign"]["id"], str(world["asset"].id), world["pages"][page]["id"], **kw
    )


def _mock_fault(api: TestClient, account_id: str, fault: str) -> None:
    response = api.post(
        "/api/v1/accounts/connect/facebook/mock/actions",
        json={"action": "script_fault", "account_id": account_id, "fault": fault},
    )
    assert response.status_code == 200, response.text


# ------------------------------------------------------------------ happy path


def test_one_video_publishes_to_two_pages_independently(
    api: TestClient, world: dict, db_session: Session
) -> None:
    alpha = start_job(api, _job(api, world, ALPHA, caption="Alpha caption"))
    gamma = start_job(api, _job(api, world, GAMMA, caption="Gamma caption"))
    for job in (alpha, gamma):
        assert job["status"] == "publishing" and job["publish_mode"] == "api"
        assert job["upload"]["phase"] == "pending" and job["upload"]["is_mock"]
        assert job["target_is_mock"]
    results = drive()
    assert results, "nothing was driven"
    for job_id, page_id in ((alpha["id"], ALPHA), (gamma["id"], GAMMA)):
        detail = refresh(api, job_id)
        assert_no_secret(detail)
        assert detail["status"] == "published", detail["last_error"]
        assert detail["evidence_kind"] == "api_verified"
        assert detail["external_post_id"].startswith(page_id + "_mockvid")
        assert detail["external_post_url"].startswith("mock://facebook/reel/")
        assert detail["upload"]["phase"] == "published"
        assert detail["upload"]["publish_request_status"] == "confirmed"
        assert detail["upload"]["progress_percent"] == 100
        assert detail["attempts"][-1]["status"] == "succeeded"
        assert detail["attempts"][-1]["mode"] == "api"
        assert len(detail["uploads"]) == 1
        video_id = detail["upload"]["provider_video_id"]
        assert MockMetaProvider.finish_count(video_id) == 1
        assert MockMetaProvider.video(video_id)["description"].startswith(
            "Alpha caption" if page_id == ALPHA else "Gamma caption"
        )
    assert len(MockMetaProvider.published_videos(ALPHA)) == 1
    assert len(MockMetaProvider.published_videos(GAMMA)) == 1
    audit = api.get(
        "/api/v1/auth/audit", params={"entity_type": "publication_job", "limit": 200}
    ).json()
    actions = {e["action"] for e in audit}
    assert {"publication.upload_phase", "publication.start", "publication.confirm"} <= actions
    assert_no_secret(audit)
    # Phase sequence recorded on at least one upload.
    seq = phases(results)
    assert any(p.startswith("pending->") for p in seq) and any(
        p.endswith("->published") for p in seq
    )
    status = api.get("/api/v1/publishing/engine/status").json()
    assert status["mode"] == "mock" and status["live_publishing_enabled"] is False
    assert status["phase_counts"]["published"] == 2
    by_page = {p["display_name"]: p for p in status["pages"]}
    assert by_page["Mock Page Alpha"]["published_last_24h"] == 1
    assert by_page["Mock Page Alpha"]["is_mock"] and by_page["Mock Page Alpha"]["api_eligible"]


def test_publisher_worker_run_once_drives_the_lifecycle(api: TestClient, world: dict) -> None:
    job = start_job(api, _job(api, world))
    publisher = Publisher(get_settings(), worker_id="pytest-publisher")
    publisher.heartbeat()
    for _ in range(30):
        publisher.run_once()
        if refresh(api, job["id"])["status"] != "publishing":
            break
    assert refresh(api, job["id"])["status"] == "published"
    status = api.get("/api/v1/publishing/engine/status").json()
    assert any(w["worker_id"] == "pytest-publisher" and w["healthy"] for w in status["workers"])
    publisher.mark_stopped()
    publisher.pool.shutdown(wait=True)
    status = api.get("/api/v1/publishing/engine/status").json()
    assert not any(w["worker_id"] == "pytest-publisher" for w in status["workers"])


# ------------------------------------------------------------------ scheduling


def test_scheduled_jobs_publish_only_after_their_time_and_only_when_eligible(
    api: TestClient, world: dict, db_session: Session
) -> None:
    due = _job(api, world, ALPHA)
    future = _job(api, world, GAMMA)
    manual = register_page(api, "Manual page", external_account_id="555")
    assisted = make_job(api, world["campaign"]["id"], str(world["asset"].id), manual["id"])
    set_scheduled(db_session, due["id"], past())
    set_scheduled(db_session, future["id"], datetime.now(UTC) + timedelta(hours=2))
    set_scheduled(db_session, assisted["id"], past())
    drive()
    assert refresh(api, due["id"])["status"] == "published"
    assert refresh(api, future["id"])["status"] == "scheduled"
    assisted_after = refresh(api, assisted["id"])
    # Released, but the manual (unverified) Page is not API-eligible: waits for an operator.
    assert assisted_after["status"] == "ready_to_publish"
    assert assisted_after["publish_mode"] == "assisted"
    assert assisted_after["api_eligibility_reason"] == "account_not_connected"


def test_nothing_publishes_before_approval(
    api: TestClient, world: dict, db_session: Session
) -> None:
    from tests.publishing_helpers import create_publication

    draft = create_publication(
        api, world["campaign"]["id"], str(world["asset"].id), world["pages"][ALPHA]["id"]
    )
    job = db_session.get(PublicationJob, uuid.UUID(draft["id"]))
    job.scheduled_at = past()  # a draft with a past time must never be picked up
    db_session.commit()
    drive()
    assert refresh(api, draft["id"])["status"] == "draft"
    assert upload_rows(db_session, draft["id"]) == []


def test_restart_safety_scheduler_continues_with_new_worker_identity(
    api: TestClient, world: dict, db_session: Session
) -> None:
    job = _job(api, world)
    set_scheduled(db_session, job["id"], past())
    first = Publisher(get_settings(), worker_id="publisher-a")
    first.run_once()  # releases + dispatches + runs the first step(s)
    first.pool.shutdown(wait=True)
    assert refresh(api, job["id"])["status"] == "publishing"
    second = Publisher(get_settings(), worker_id="publisher-b")  # "restarted" process
    for _ in range(30):
        second.run_once()
        if refresh(api, job["id"])["status"] != "publishing":
            break
    second.pool.shutdown(wait=True)
    assert refresh(api, job["id"])["status"] == "published"


# ------------------------------------------------------------------ eligibility guards


def test_blocked_page_and_classification_change_hold_dispatch(
    api: TestClient, world: dict, db_session: Session
) -> None:
    job = _job(api, world, ALPHA)
    set_scheduled(db_session, job["id"], past())
    # Block the Page for every category: routing verdict becomes ineligible.
    cat = api.post("/api/v1/content-categories", json={"name": "Blocked", "slug": "blocked"})
    assert cat.status_code == 201, cat.text
    assign = api.request(
        "PUT",
        f"/api/v1/media/assets/{world['asset'].id}/categories",
        json={"primary_category_id": cat.json()["id"], "secondary_category_ids": []},
    )
    if assign.status_code == 405:
        assign = api.post(
            f"/api/v1/media/assets/{world['asset'].id}/categories",
            json={"primary_category_id": cat.json()["id"], "secondary_category_ids": []},
        )
    assert assign.status_code in (200, 201), assign.text
    rules = api.put(
        f"/api/v1/accounts/{world['pages'][ALPHA]['id']}/channel-profile",
        json={
            "routing_enabled": True,
            "rules": [{"category_id": cat.json()["id"], "match_policy": "block"}],
        },
    )
    assert rules.status_code == 200, rules.text
    drive()
    after = refresh(api, job["id"])
    # release_due holds blocked jobs in SCHEDULED; dispatch holds READY ones: never PUBLISHING.
    assert after["status"] in {"scheduled", "ready_to_publish"} and after["target_blocked_reason"]
    assert upload_rows(db_session, job["id"]) == []
    audit = api.get(
        "/api/v1/auth/audit", params={"entity_type": "publication_job", "entity_id": job["id"]}
    ).json()
    assert "publication.release_held" in {e["action"] for e in audit}


def test_approval_revocation_and_cancellation_stop_the_engine(
    api: TestClient, world: dict, db_session: Session
) -> None:
    job = _job(api, world, ALPHA)
    revoked = act(api, job, "revoke_approval")
    assert revoked["status"] == "draft"
    set_scheduled(db_session, job["id"], past())  # pretend someone forced a schedule
    drive()
    assert upload_rows(db_session, job["id"]) == []  # no approved snapshot → never dispatched
    # A job that leaves PUBLISHING while an upload is live is abandoned, never finished.
    other = start_job(api, _job(api, world, GAMMA))
    drive(stop_when=lambda r: r.phase_after == UploadPhase.UPLOADING)
    row = db_session.get(PublicationJob, uuid.UUID(other["id"]))
    row.status = "cancelled"
    db_session.commit()
    drive()
    rows = upload_rows(db_session, other["id"])
    assert rows[-1].phase == UploadPhase.ABANDONED
    assert MockMetaProvider.finish_count(rows[-1].provider_video_id or "") == 0


def test_disconnected_page_is_not_dispatched(
    api: TestClient, world: dict, db_session: Session
) -> None:
    job = _job(api, world, ALPHA)
    set_scheduled(db_session, job["id"], past())
    api.post(f"/api/v1/accounts/{world['pages'][ALPHA]['id']}/disconnect", json={})
    drive()
    after = refresh(api, job["id"])
    assert (
        after["status"] == "ready_to_publish"
        and after["api_eligibility_reason"] == "account_not_connected"
    )
    assert upload_rows(db_session, job["id"]) == []


# ------------------------------------------------------------------ faults and recovery


def test_upload_interruption_resumes_from_the_provider_offset(
    api: TestClient, world: dict, db_session: Session
) -> None:
    _mock_fault(api, world["pages"][ALPHA]["id"], "upload_timeout")
    job = start_job(api, _job(api, world))
    results = drive()
    assert refresh(api, job["id"])["status"] == "published"
    rows = upload_rows(db_session, job["id"])
    assert len(rows) == 1 and rows[0].retry_count == 1
    assert rows[0].bytes_sent == rows[0].bytes_total == 6_000
    assert "uploading->retry_wait" in phases(results)
    assert any(r.phase_before == UploadPhase.RETRY_WAIT for r in results)
    assert MockMetaProvider.finish_count(rows[0].provider_video_id) == 1


def test_transient_start_failure_retries_and_rate_limit_holds_the_page(
    api: TestClient, world: dict, db_session: Session
) -> None:
    _mock_fault(api, world["pages"][ALPHA]["id"], "start_timeout")
    _mock_fault(api, world["pages"][ALPHA]["id"], "start_rate_limit")
    job = start_job(api, _job(api, world))
    drive()
    upload = live_upload(db_session, job["id"])
    assert upload.phase == UploadPhase.RETRY_WAIT and upload.retry_count == 2
    assert upload.last_error_code == "rate_limited"
    account = db_session.get(SocialAccount, uuid.UUID(world["pages"][ALPHA]["id"]))
    assert (
        account.publish_hold_reason == "rate_limited"
        and account.publish_hold_until > datetime.now(UTC)
    )
    summary = refresh(api, job["id"])
    assert summary["target_hold_reason"] == "rate_limited" and summary["upload"]["retry_count"] == 2
    status = api.get("/api/v1/publishing/engine/status").json()
    assert (
        next(p for p in status["pages"] if p["display_name"] == "Mock Page Alpha")["hold_reason"]
        == "rate_limited"
    )
    # Nothing moves while the hold is active; clearing it resumes.
    assert drive() == []
    account.publish_hold_until = datetime.now(UTC) - timedelta(seconds=1)
    upload.next_action_at = datetime.now(UTC)
    db_session.commit()
    drive()
    assert refresh(api, job["id"])["status"] == "published"


def test_permanent_failures_fail_the_job_and_explicit_retry_starts_fresh(
    api: TestClient, world: dict, db_session: Session
) -> None:
    _mock_fault(api, world["pages"][ALPHA]["id"], "start_permission_denied")
    job = start_job(api, _job(api, world))
    drive()
    failed = refresh(api, job["id"])
    assert failed["status"] == "failed" and failed["upload"]["phase"] == "failed"
    assert failed["upload"]["last_error_code"] == "permission_denied"
    assert failed["attempts"][-1]["status"] == "failed"
    retried = act(api, failed, "retry")
    assert retried["status"] == "ready_to_publish"
    started = start_job(api, retried)
    assert started["attempts"][-1]["attempt_no"] == 2
    drive()
    done = refresh(api, job["id"])
    assert done["status"] == "published" and len(done["uploads"]) == 2
    assert done["uploads"][0]["phase"] == "failed" and done["uploads"][1]["phase"] == "published"
    assert len(MockMetaProvider.published_videos(ALPHA)) == 1


@pytest.mark.parametrize(
    ("fault", "code"),
    [
        ("upload_reject", "provider_6000"),
        ("processing_error", "processing_failed"),
        ("finish_reject", "finish_rejected_368"),
    ],
)
def test_other_permanent_provider_errors(
    api: TestClient, world: dict, db_session: Session, fault: str, code: str
) -> None:
    _mock_fault(api, world["pages"][ALPHA]["id"], fault)
    job = start_job(api, _job(api, world))
    drive()
    after = refresh(api, job["id"])
    assert after["status"] == "failed", after
    assert after["upload"]["last_error_code"] == code
    assert len(MockMetaProvider.published_videos(ALPHA)) == 0


def test_processing_delay_and_status_outage_are_waited_out(
    api: TestClient, world: dict, db_session: Session
) -> None:
    _mock_fault(api, world["pages"][ALPHA]["id"], "processing_delay")
    _mock_fault(api, world["pages"][ALPHA]["id"], "status_outage")
    job = start_job(api, _job(api, world))
    results = drive()
    assert refresh(api, job["id"])["status"] == "published"
    assert "processing->retry_wait" in phases(results)  # the status outage was retried
    rows = upload_rows(db_session, job["id"])
    assert rows[0].poll_count >= 4 and rows[0].retry_count == 1


def test_retries_are_bounded(api: TestClient, world: dict, db_session: Session) -> None:
    for _ in range(4):  # max retries = 3 in the fixture
        _mock_fault(api, world["pages"][ALPHA]["id"], "start_timeout")
    job = start_job(api, _job(api, world))
    drive()
    after = refresh(api, job["id"])
    assert after["status"] == "failed" and after["upload"]["last_error_code"] == "retries_exhausted"
    assert after["upload"]["retry_count"] == 4


def test_token_revocation_during_publishing_fails_and_disconnects(
    api: TestClient, world: dict, db_session: Session
) -> None:
    job = start_job(api, _job(api, world))
    drive(stop_when=lambda r: r.phase_after == UploadPhase.UPLOADING)
    revoke = api.post(
        "/api/v1/accounts/connect/facebook/mock/actions",
        json={"action": "revoke_page_token", "account_id": world["pages"][ALPHA]["id"]},
    )
    assert revoke.status_code == 200
    drive()
    after = refresh(api, job["id"])
    assert after["status"] == "failed" and after["upload"]["last_error_code"] == "token_invalid"
    account = api.get(f"/api/v1/accounts/{world['pages'][ALPHA]['id']}").json()
    assert account["status"] == "disconnected" and account["disconnect_reason"] == "token_invalid"
    assert len(MockMetaProvider.published_videos(ALPHA)) == 0


# ------------------------------------------------------------------ ambiguous finish


def test_ambiguous_finish_parks_then_provider_reconciliation_confirms_without_resending(
    api: TestClient, world: dict, db_session: Session
) -> None:
    _mock_fault(api, world["pages"][ALPHA]["id"], "finish_timeout_ambiguous")
    job = start_job(api, _job(api, world))
    drive()
    parked = refresh(api, job["id"])
    assert parked["status"] == "needs_reconciliation"
    assert parked["upload"]["phase"] == "needs_reconciliation"
    assert parked["upload"]["publish_request_status"] == "unknown"
    video_id = parked["upload"]["provider_video_id"]
    assert MockMetaProvider.finish_count(video_id) == 1  # applied by the provider
    assert drive() == []  # parked uploads are never claimed by workers
    assert MockMetaProvider.finish_count(video_id) == 1
    reconciled = act(api, parked, "reconcile_provider")
    assert reconciled["status"] == "published" and reconciled["evidence_kind"] == "api_verified"
    assert reconciled["upload"]["publish_request_status"] == "confirmed"
    assert MockMetaProvider.finish_count(video_id) == 1  # still exactly one publish request
    assert len(MockMetaProvider.published_videos(ALPHA)) == 1


def test_lost_finish_is_detected_as_not_applied_and_retried_safely(
    api: TestClient, world: dict, db_session: Session
) -> None:
    _mock_fault(api, world["pages"][ALPHA]["id"], "finish_timeout_lost")
    job = start_job(api, _job(api, world))
    drive()
    parked = refresh(api, job["id"])
    assert parked["status"] == "needs_reconciliation"
    video_id = parked["upload"]["provider_video_id"]
    assert MockMetaProvider.finish_count(video_id) == 0
    reconciled = act(api, parked, "reconcile_provider")
    assert reconciled["status"] == "failed"
    assert reconciled["upload"]["last_error_code"] == "finish_not_applied"
    assert MockMetaProvider.finish_count(video_id) == 0  # never re-sent
    retried = start_job(api, act(api, reconciled, "retry"))
    drive()
    done = refresh(api, retried["id"])
    assert done["status"] == "published" and done["upload"]["provider_video_id"] != video_id
    assert len(MockMetaProvider.published_videos(ALPHA)) == 1


def test_crash_after_sending_finish_never_resends(
    api: TestClient, world: dict, db_session: Session
) -> None:
    job = start_job(api, _job(api, world))
    drive(stop_when=lambda r: r.phase_after == UploadPhase.FINALIZING)
    upload = live_upload(db_session, job["id"])
    assert upload.phase == UploadPhase.FINALIZING
    # Simulate: the worker persisted SENT, called the provider, then died before recording.
    upload.publish_request_status = "sent"
    upload.finish_requested_at = datetime.now(UTC)
    upload.lease_owner = "dead-worker"
    upload.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    drive(worker_id="survivor")
    parked = refresh(api, job["id"])
    assert parked["status"] == "needs_reconciliation"
    assert parked["upload"]["last_error_code"] == "finish_outcome_unknown"
    video_id = parked["upload"]["provider_video_id"]
    assert MockMetaProvider.finish_count(video_id) == 0  # the survivor did not resend
    reconciled = act(api, parked, "reconcile_provider")
    assert (
        reconciled["status"] == "failed"
        and reconciled["upload"]["last_error_code"] == "finish_not_applied"
    )


def test_crash_mid_upload_is_recovered_by_another_worker(
    api: TestClient, world: dict, db_session: Session
) -> None:
    job = start_job(api, _job(api, world))
    drive(worker_id="worker-1", stop_when=lambda r: r.phase_after == UploadPhase.UPLOADING)
    upload = live_upload(db_session, job["id"])
    # Lease held by a dead worker: not claimable until it expires.
    upload.lease_owner = "worker-1"
    upload.lease_expires_at = datetime.now(UTC) + timedelta(seconds=30)
    db_session.commit()
    assert drive(worker_id="worker-2") == []
    upload.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    results = drive(worker_id="worker-2")
    assert results and refresh(api, job["id"])["status"] == "published"
    rows = upload_rows(db_session, job["id"])
    assert rows[0].last_status.get("recovered_from") == "worker-1"
    assert MockMetaProvider.finish_count(rows[0].provider_video_id) == 1


# ------------------------------------------------------------------ claiming and isolation


def test_concurrent_claims_never_hand_the_same_upload_to_two_workers(
    api: TestClient, world: dict, db_session: Session
) -> None:
    alpha = start_job(api, _job(api, world, ALPHA))
    gamma = start_job(api, _job(api, world, GAMMA))
    factory = get_session_factory()
    claimed: dict[str, list[uuid.UUID]] = {}
    barrier = threading.Barrier(2)

    def worker(name: str) -> None:
        barrier.wait()
        with factory() as session:
            ids = engine.claim_uploads(session, worker_id=name, limit=4, lease_seconds=60)
            session.commit()
        claimed[name] = ids

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    all_ids = claimed["w0"] + claimed["w1"]
    assert len(all_ids) == len(set(all_ids)) == 2
    with factory() as session:
        for job_id in (alpha["id"], gamma["id"]):
            upload = live_upload(session, job_id)
            assert upload.lease_owner in {"w0", "w1"}


def test_per_page_serialization_one_upload_at_a_time(
    api: TestClient, world: dict, db_session: Session
) -> None:
    second = create_campaign(api, "Second campaign")
    first_job = start_job(api, _job(api, world, ALPHA))
    second_job = start_job(
        api, make_job(api, second["id"], str(world["asset"].id), world["pages"][ALPHA]["id"])
    )
    with get_session_factory()() as session:
        ids = engine.claim_uploads(session, worker_id="w", limit=10, lease_seconds=60)
        session.commit()
    assert len(ids) == 1  # same Page → only one leased at a time
    with get_session_factory()() as session:
        leased = session.get(PublicationUpload, ids[0])
        leased.lease_owner, leased.lease_expires_at = None, None  # hand it back
        session.commit()
    drive()
    for job_id in (first_job["id"], second_job["id"]):
        detail = refresh(api, job_id)
        assert detail["status"] == "published", (detail["status"], detail["upload"])
    assert len(MockMetaProvider.published_videos(ALPHA)) == 2  # two publications, two videos


def test_daily_quota_holds_the_page(
    api: TestClient, world: dict, db_session: Session, monkeypatch
) -> None:
    monkeypatch.setenv("META_REELS_DAILY_QUOTA", "1")
    get_settings.cache_clear()
    first = start_job(api, _job(api, world, ALPHA))
    drive()
    assert refresh(api, first["id"])["status"] == "published"
    second_campaign = create_campaign(api, "Quota campaign")
    second = make_job(
        api, second_campaign["id"], str(world["asset"].id), world["pages"][ALPHA]["id"]
    )
    set_scheduled(db_session, second["id"], past())
    drive()
    after = refresh(api, second["id"])
    assert after["status"] == "ready_to_publish" and after["target_hold_reason"] == "reels_quota"
    assert upload_rows(db_session, second["id"]) == []


def test_rbac_and_cross_organization_isolation(
    api: TestClient, world: dict, db_session: Session, organization
) -> None:
    _mock_fault(api, world["pages"][ALPHA]["id"], "finish_timeout_ambiguous")
    job = start_job(api, _job(api, world))
    drive()
    parked = refresh(api, job["id"])
    editor = create_member(db_session, organization, role=MembershipRole.EDITOR)
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        login(client, editor)
        assert client.get("/api/v1/publishing/engine/status").status_code == 200
        denied = client.post(
            f"/api/v1/publishing/publications/{job['id']}/reconcile-provider",
            json={"version": parked["version"]},
        )
        assert denied.status_code == 403
    other_org = create_organization(db_session)
    db_session.commit()
    other_admin = create_member(db_session, other_org, role=MembershipRole.ADMIN)
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        login(client, other_admin)
        assert client.get(f"/api/v1/publishing/publications/{job['id']}").status_code == 404
        foreign = client.post(
            f"/api/v1/publishing/publications/{job['id']}/reconcile-provider",
            json={"version": parked["version"]},
        )
        assert foreign.status_code == 404
        status = client.get("/api/v1/publishing/engine/status").json()
        assert status["phase_counts"]["needs_reconciliation"] == 0 and status["pages"] == []
    db_session.delete(other_org)
    db_session.commit()


# ------------------------------------------------------------------ mock / live separation


def test_live_accounts_are_never_api_eligible_in_this_milestone() -> None:
    class Acc:  # minimal stand-in for SocialAccount
        platform = "facebook"
        is_connected = True
        can_publish = True
        permission_status = "ok"

    real = Acc()
    real.is_mock = False
    mock = Acc()
    mock.is_mock = True
    from affilio.core.enums import Platform

    real.platform = mock.platform = Platform.FACEBOOK
    live_off = Settings(
        app_env="development",
        meta_provider_mode="live",
        meta_app_id="1",
        meta_app_secret="s",
        meta_oauth_redirect_uri="http://x/cb",
    )
    assert account_api_eligibility(real, live_off).reason == "live_publishing_disabled"
    live_on = Settings(
        app_env="development",
        meta_provider_mode="live",
        meta_live_publishing_enabled=True,
        meta_app_id="1",
        meta_app_secret="s",
        meta_oauth_redirect_uri="http://x/cb",
    )
    assert account_api_eligibility(real, live_on).eligible
    assert account_api_eligibility(mock, live_on).reason == "mock_account_in_live_mode"
    mock_mode = Settings(app_env="development", meta_provider_mode="mock")
    assert account_api_eligibility(mock, mock_mode).eligible
    assert account_api_eligibility(real, mock_mode).reason == "real_account_in_mock_mode"


def test_live_provider_reels_calls_refuse_mock_tokens(api: TestClient) -> None:
    import httpx

    from affilio.modules.accounts.providers.base import (
        MockCredentialRejectedError,
        ReelUploadSession,
    )
    from affilio.modules.accounts.providers.live import LiveMetaProvider

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={})

    provider = LiveMetaProvider(
        Settings(meta_app_id="1", meta_app_secret="s", meta_oauth_redirect_uri="https://x/cb"),
        transport=httpx.MockTransport(handler),
    )
    session = ReelUploadSession(video_id="v", upload_url="u")
    for call in (
        lambda: provider.start_reel_upload(page_token="mock:page:1:1", page_id="1"),
        lambda: provider.upload_reel_bytes(
            page_token="mock:page:1:1", session=session, chunk=b"x", offset=0, total=1
        ),
        lambda: provider.reel_status(page_token="mock:page:1:1", video_id="v"),
        lambda: provider.finish_reel(
            page_token="mock:page:1:1", page_id="1", video_id="v", description="d", title=None
        ),
        lambda: provider.reel_identity(page_token="mock:page:1:1", video_id="v"),
    ):
        with pytest.raises(MockCredentialRejectedError):
            call()
    assert calls == []
