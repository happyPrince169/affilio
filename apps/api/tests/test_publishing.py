"""Distribution core: campaigns, publication jobs, state machine, snapshots, evidence,
idempotency, concurrency, scheduling, reconciliation, adapters. Needs TEST_DATABASE_URL."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from affilio.app import create_app
from affilio.core.config import get_settings
from affilio.core.enums import MembershipRole, Platform, PublicationStatus
from affilio.db.session import dispose_engine
from affilio.modules.identity.models import AuditEvent
from affilio.modules.publishing import lifecycle
from affilio.modules.publishing.adapters import get_adapter, register_adapter
from affilio.modules.publishing.adapters.facebook import FacebookReelsAdapter
from affilio.modules.publishing.models import PublicationJob, PublicationSnapshot
from tests.auth_helpers import create_member, login, sign_in
from tests.publishing_helpers import (
    FakeNetworkAdapter,
    act,
    add_link,
    approve_flow,
    create_campaign,
    create_publication,
    make_final,
    make_footage,
    register_page,
)

pytestmark = pytest.mark.database


@pytest.fixture
def api(
    monkeypatch: pytest.MonkeyPatch, media_dirs, organization, schema, db_session: Session
) -> Iterator[TestClient]:
    for key, value in media_dirs.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DEFAULT_ORGANIZATION_SLUG", organization.slug)
    monkeypatch.setenv("PUBLICATION_MIN_GAP_MINUTES", "30")
    monkeypatch.delenv("MEDIA_ADMIN_TOKEN", raising=False)
    get_settings.cache_clear()
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        sign_in(client, db_session, organization, role=MembershipRole.OWNER)
        yield client
    dispose_engine()
    get_settings.cache_clear()


@pytest.fixture
def fake_adapter() -> Iterator[FakeNetworkAdapter]:
    adapter = FakeNetworkAdapter()
    register_adapter(Platform.FACEBOOK, lambda: adapter)
    yield adapter
    register_adapter(Platform.FACEBOOK, None)


@pytest.fixture
def world(api: TestClient, db_session: Session, organization, media_dirs) -> dict:
    final = make_final(db_session, organization, media_dirs)
    page_a = register_page(api, "Deals Page A")
    page_b = register_page(api, "Deals Page B")
    link = add_link(api)
    campaign = create_campaign(
        api, media_asset_ids=[str(final.id)], social_account_ids=[page_a["id"]]
    )
    return {"final": final, "page_a": page_a, "page_b": page_b, "link": link, "campaign": campaign}


def _future(minutes: int = 120) -> str:
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()


# ---------------------------------------------------------------- adapters


def test_adapters_declare_capabilities_and_never_publish() -> None:
    adapter = get_adapter(Platform.FACEBOOK)
    assert isinstance(adapter, FacebookReelsAdapter)
    caps = adapter.capabilities().as_dict()
    assert caps["api_publishing"] is False and caps["assisted_publishing"] is True
    assert caps["clickable_links_in_caption"] is False and caps["max_caption_length"] == 2200
    assert adapter.check_capabilities({"api_publishing"}).ok is False
    assert adapter.check_capabilities({"assisted_publishing"}).ok is True
    media_ok = adapter.validate_media(
        {
            "asset_kind": "final",
            "handoff_status": "approved",
            "status": "active",
            "extension": ".mp4",
            "width": 1080,
            "height": 1920,
        }
    )
    assert media_ok.ok and media_ok.errors == []
    media_bad = adapter.validate_media(
        {"asset_kind": "footage", "handoff_status": None, "status": "active"}
    )
    assert not media_bad.ok and len(media_bad.errors) == 2
    content_bad = adapter.validate_content(
        {"caption": "", "links": [{"label": "x", "url": "ftp://x"}], "hashtags": []}
    )
    assert not content_bad.ok
    prepared = adapter.prepare_publication(
        content={
            "caption": "Hi",
            "cta": "Shop",
            "hashtags": ["deal"],
            "links": [{"label": "Shop", "url": "https://shopee.vn/x"}],
        },
        media={"file_name": "final.mp4"},
        target={"display_name": "Page", "open_url": "https://www.facebook.com/Page"},
        intent_id="intent-1",
    )
    assert prepared.caption_text == "Hi\n\nShop\n\nShop: https://shopee.vn/x\n\n#deal"
    assert [c["key"] for c in prepared.checklist][:2] == ["open_target", "upload_media"]
    outcome = adapter.publish(prepared, intent_id="intent-1")
    assert outcome.status == "manual_required"  # no network, ever
    assert (
        adapter.get_publication_status(intent_id="intent-1", external_post_id=None).status
        == "unknown"
    )
    assert (
        adapter.reconcile_publication(intent_id="intent-1", operator_evidence=None).status
        == "still_unknown"
    )
    other = get_adapter(Platform.TIKTOK)
    assert other.name == "assisted" and other.capabilities().api_publishing is False


def test_lifecycle_table_is_consistent() -> None:
    for action, (sources, target, _) in lifecycle.ACTIONS.items():
        assert sources, action
        if action not in {"reconcile", "revalidate"}:
            assert target not in sources or action == "schedule", action
    assert (
        lifecycle.assert_transition("submit", PublicationStatus.DRAFT)
        == PublicationStatus.READY_FOR_REVIEW
    )
    assert "approve" in lifecycle.allowed_actions(
        PublicationStatus.READY_FOR_REVIEW, MembershipRole.ADMIN
    )
    assert "approve" not in lifecycle.allowed_actions(
        PublicationStatus.READY_FOR_REVIEW, MembershipRole.EDITOR
    )
    assert lifecycle.allowed_actions(PublicationStatus.PUBLISHED, MembershipRole.OWNER) == []


# ---------------------------------------------------------------- campaigns


def test_campaign_requires_approved_finals(
    api: TestClient, db_session: Session, organization, media_dirs
) -> None:
    footage = make_footage(db_session, organization, media_dirs)
    pending = make_final(db_session, organization, media_dirs, "pending_cut.mp4", approve=False)
    approved = make_final(db_session, organization, media_dirs, "approved_cut.mp4")

    bad = api.post(
        "/api/v1/publishing/campaigns", json={"name": "C", "media_asset_ids": [str(footage.id)]}
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "media_not_approved"
    assert bad.json()["error"]["details"]["asset_kind"] == "footage"
    bad2 = api.post(
        "/api/v1/publishing/campaigns", json={"name": "C", "media_asset_ids": [str(pending.id)]}
    )
    assert (
        bad2.status_code == 422
        and bad2.json()["error"]["details"]["handoff_status"] == "pending_review"
    )
    assert (
        api.post(
            "/api/v1/publishing/campaigns",
            json={"name": "C", "media_asset_ids": [str(uuid.uuid4())]},
        ).status_code
        == 404
    )

    created = api.post(
        "/api/v1/publishing/campaigns",
        json={"name": "  Launch  ", "media_asset_ids": [str(approved.id)]},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "Launch" and body["status"] == "draft" and body["media_count"] == 1
    assert body["media"][0]["handoff_status"] == "approved"
    assert api.get("/api/v1/publishing/campaigns").json()["total"] == 1
    assert (
        api.post(
            "/api/v1/publishing/campaigns",
            json={"name": "x", "starts_on": "2026-11-05", "ends_on": "2026-11-01"},
        ).status_code
        == 422
    )
    assert (
        api.post("/api/v1/publishing/campaigns", json={"name": "x", "bogus": 1}).status_code == 422
    )

    page = register_page(api, "P")
    with_target = api.post(
        f"/api/v1/publishing/campaigns/{body['id']}/targets",
        json={"social_account_ids": [page["id"]]},
    )
    assert with_target.status_code == 200 and with_target.json()["target_count"] == 1
    disabled = register_page(api, "Disabled")
    api.post(f"/api/v1/accounts/{disabled['id']}/status", json={"status": "disabled"})
    refused = api.post(
        f"/api/v1/publishing/campaigns/{body['id']}/targets",
        json={"social_account_ids": [disabled["id"]]},
    )
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "target_unavailable"
    assert (
        api.patch(f"/api/v1/publishing/campaigns/{body['id']}", json={"status": "active"}).json()[
            "status"
        ]
        == "active"
    )
    assert (
        api.delete(f"/api/v1/publishing/campaigns/{body['id']}/media/{approved.id}").json()[
            "media_count"
        ]
        == 0
    )


# ---------------------------------------------------------------- publication workflow


def test_full_assisted_workflow_with_snapshot_and_evidence(
    api: TestClient, db_session: Session, world: dict
) -> None:
    job = create_publication(
        api,
        world["campaign"]["id"],
        str(world["final"].id),
        world["page_a"]["id"],
        link_ids=[world["link"]["id"]],
        title="Reel 1",
    )
    assert job["status"] == "draft" and job["version"] == 1 and job["content_version"] == 1
    assert (
        job["platform"] == "facebook"
        and job["account_connection_label"] == "Manual (unverified, not connected)"
    )
    assert job["validation"]["ok"] is True and job["links"][0]["url"] == world["link"]["url"]
    assert job["hashtags"] == ["deal", "shopee"]
    assert job["allowed_actions"] == ["submit", "cancel"]
    assert job["capabilities"]["api_publishing"] is False
    # Not approved yet: no checklist.
    assert api.get(f"/api/v1/publishing/publications/{job['id']}/prepare").status_code == 409

    job = act(api, job, "submit")
    assert job["status"] == "ready_for_review" and job["version"] == 2
    job = act(api, job, "approve", note="looks good")
    assert (
        job["status"] == "approved"
        and job["has_approved_snapshot"]
        and job["approval_stale"] is False
    )
    snapshot = job["approved_snapshot"]
    assert (
        snapshot["caption"] == "Fresh deal for you"
        and snapshot["links"][0]["url"] == world["link"]["url"]
    )
    assert (
        snapshot["media"]["asset_id"] == str(world["final"].id)
        and snapshot["target"]["display_name"] == "Deals Page A"
    )
    assert snapshot["approved_by_label"].endswith("@test.affilio.local")

    prepared = api.get(f"/api/v1/publishing/publications/{job['id']}/prepare").json()
    assert prepared["mode"] == "assisted" and prepared["snapshot_id"] == snapshot["id"]
    assert (
        "Fresh deal for you" in prepared["caption_text"]
        and world["link"]["url"] in prepared["caption_text"]
    )
    assert prepared["open_url"] == "https://www.facebook.com/DealsPageA"
    assert len(prepared["checklist"]) == 6 and prepared["media_file_name"] == "final_v1.mp4"

    job = act(api, job, "schedule", scheduled_at=_future())
    assert (job["status"] == "scheduled" and job["scheduled_at"].endswith("Z")) or "+00:00" in job[
        "scheduled_at"
    ]
    job = act(api, job, "start")
    assert job["status"] == "publishing" and job["attempt_count"] == 1
    assert job["attempts"][0]["status"] == "started" and job["attempts"][0]["mode"] == "assisted"
    assert job["attempts"][0]["adapter"] == "facebook_reels"

    # Evidence validation: wrong host, bad scheme.
    bad_host = act(
        api, job, "confirm", expected=422, external_post_url="https://tiktok.com/@x/video/1"
    )
    assert bad_host["error"]["code"] == "evidence_invalid"
    assert act(api, job, "confirm", expected=422, external_post_url="javascript:alert(1)")["error"][
        "code"
    ] in {"invalid_product_url", "validation_error"}

    job = act(
        api,
        job,
        "confirm",
        external_post_url="https://www.facebook.com/reel/1234567890123",
        note="posted from phone",
    )
    assert job["status"] == "published" and job["evidence_kind"] == "operator_reported"
    assert job["external_post_id"] == "1234567890123" and job["published_at"]
    assert (
        job["confirmed_by_label"].endswith("@test.affilio.local")
        and job["confirmation_note"] == "posted from phone"
    )
    assert job["attempts"][0]["status"] == "succeeded" and job["attempts"][0]["finished_at"]
    assert job["allowed_actions"] == []
    # Terminal: nothing else is allowed, including edits.
    assert (
        act(api, job, "cancel", expected=409)["error"]["code"] == "invalid_publication_transition"
    )
    edit = api.patch(
        f"/api/v1/publishing/publications/{job['id']}",
        json={"version": job["version"], "caption": "x"},
    )
    assert edit.status_code == 409

    actions = [
        e["action"]
        for e in api.get(
            "/api/v1/auth/audit", params={"entity_type": "publication_job", "entity_id": job["id"]}
        ).json()
    ]
    assert actions[::-1] == [
        "publication.created",
        "publication.submit",
        "publication.approve",
        "publication.schedule",
        "publication.start",
        "publication.confirm",
    ]
    events = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == uuid.UUID(job["id"]))
    ).all()
    assert all(e.actor_user_id is not None for e in events)

    campaign = api.get(f"/api/v1/publishing/campaigns/{world['campaign']['id']}").json()
    assert campaign["publication_counts"] == {"published": 1}


def test_unapproved_media_and_disabled_target_rejected(
    api: TestClient, db_session: Session, organization, media_dirs, world: dict
) -> None:
    footage = make_footage(db_session, organization, media_dirs)
    resp = api.post(
        "/api/v1/publishing/publications",
        json={
            "campaign_id": world["campaign"]["id"],
            "media_asset_id": str(footage.id),
            "social_account_id": world["page_a"]["id"],
            "caption": "x",
        },
    )
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "media_not_approved"
    api.post(f"/api/v1/accounts/{world['page_b']['id']}/status", json={"status": "disabled"})
    resp = api.post(
        "/api/v1/publishing/publications",
        json={
            "campaign_id": world["campaign"]["id"],
            "media_asset_id": str(world["final"].id),
            "social_account_id": world["page_b"]["id"],
            "caption": "x",
        },
    )
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "target_unavailable"
    # Submitting without a caption fails validation.
    job = create_publication(
        api, world["campaign"]["id"], str(world["final"].id), world["page_a"]["id"], caption="   "
    )
    failed = act(api, job, "submit", expected=422)
    assert (
        failed["error"]["code"] == "content_invalid"
        and "caption" in failed["error"]["details"]["errors"][0].lower()
    )


def test_multi_page_unique_content_and_multi_link_order(api: TestClient, world: dict) -> None:
    link2 = add_link(api, "Second link")
    link3 = add_link(api, "Third link")
    resp = api.post(
        "/api/v1/publishing/publications/batch",
        json={
            "campaign_id": world["campaign"]["id"],
            "media_asset_id": str(world["final"].id),
            "items": [
                {
                    "social_account_id": world["page_a"]["id"],
                    "caption": "Caption for A",
                    "link_ids": [link3["id"], world["link"]["id"]],
                },
                {
                    "social_account_id": world["page_b"]["id"],
                    "caption": "Caption for B",
                    "link_ids": [world["link"]["id"], link2["id"], link3["id"]],
                    "hashtags": ["b"],
                },
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()["created"]
    assert len(created) == 2 and resp.json()["reused"] == []
    by_account = {c["social_account_id"]: c for c in created}
    a, b = by_account[world["page_a"]["id"]], by_account[world["page_b"]["id"]]
    assert a["caption"] == "Caption for A" and b["caption"] == "Caption for B"
    assert [link["label"] for link in a["links"]] == ["Third link", "Shopee deal"]
    assert [link["position"] for link in b["links"]] == [0, 1, 2]
    assert a["intent_id"] != b["intent_id"]
    # Target auto-joined the campaign; the campaign now lists both publications.
    campaign = api.get(f"/api/v1/publishing/campaigns/{world['campaign']['id']}").json()
    assert campaign["target_count"] == 2 and len(campaign["publications"]) == 2
    # Same target twice in one batch is rejected; duplicate link ids rejected.
    dup = api.post(
        "/api/v1/publishing/publications/batch",
        json={
            "campaign_id": world["campaign"]["id"],
            "media_asset_id": str(world["final"].id),
            "items": [
                {"social_account_id": world["page_a"]["id"]},
                {"social_account_id": world["page_a"]["id"]},
            ],
        },
    )
    assert dup.status_code == 422
    assert (
        api.post(
            "/api/v1/publishing/publications",
            json={
                "campaign_id": world["campaign"]["id"],
                "media_asset_id": str(world["final"].id),
                "social_account_id": world["page_b"]["id"],
                "link_ids": [link2["id"], link2["id"]],
            },
        ).status_code
        == 422
    )
    # Snapshot preserves the order and content per target after approval.
    approved_b = approve_flow(api, b)
    assert [link["label"] for link in approved_b["approved_snapshot"]["links"]] == [
        "Shopee deal",
        "Second link",
        "Third link",
    ]
    assert approved_b["approved_snapshot"]["hashtags"] == ["b"]
    # Unknown / foreign link ids 404.
    assert (
        api.patch(
            f"/api/v1/publishing/publications/{a['id']}",
            json={"version": a["version"], "link_ids": [str(uuid.uuid4())]},
        ).status_code
        == 404
    )


def test_edit_after_approval_invalidates_snapshot(
    api: TestClient, db_session: Session, world: dict
) -> None:
    job = create_publication(
        api, world["campaign"]["id"], str(world["final"].id), world["page_a"]["id"]
    )
    job = approve_flow(api, job)
    first_snapshot = job["approved_snapshot"]["id"]
    job = act(api, job, "schedule", scheduled_at=_future())
    edited = api.patch(
        f"/api/v1/publishing/publications/{job['id']}",
        json={"version": job["version"], "caption": "Edited caption"},
    ).json()
    assert edited["status"] == "draft" and edited["has_approved_snapshot"] is False
    assert (
        edited["content_version"] == 2 and edited["scheduled_at"] is not None
    )  # schedule kept for re-approval
    assert [s["id"] for s in edited["snapshots"]] == [first_snapshot]  # history retained, immutable
    snapshot_row = db_session.get(PublicationSnapshot, uuid.UUID(first_snapshot))
    assert snapshot_row is not None and snapshot_row.caption == "Fresh deal for you"
    # Re-approval creates a second snapshot with the new content.
    again = approve_flow(api, edited)
    assert (
        again["approved_snapshot"]["id"] != first_snapshot
        and again["approved_snapshot"]["caption"] == "Edited caption"
    )
    assert len(again["snapshots"]) == 2
    # Return to draft / revoke approval paths.
    submitted = act(
        api,
        create_publication(
            api, world["campaign"]["id"], str(world["final"].id), world["page_b"]["id"]
        ),
        "submit",
    )
    assert act(api, submitted, "return_to_draft")["status"] == "draft"
    revoked = act(api, again, "revoke_approval", note="rethinking")
    assert (
        revoked["status"] == "draft"
        and revoked["has_approved_snapshot"] is False
        and revoked["scheduled_at"] is None
    )


def test_invalid_transitions_and_rbac_on_actions(
    api: TestClient, db_session: Session, organization, world: dict
) -> None:
    job = create_publication(
        api, world["campaign"]["id"], str(world["final"].id), world["page_a"]["id"]
    )
    for action in (
        "approve",
        "schedule",
        "start",
        "confirm",
        "fail",
        "retry",
        "revise",
        "reconcile",
        "unschedule",
        "release",
    ):
        body = {"scheduled_at": _future()} if action == "schedule" else {}
        if action == "confirm":
            body = {"external_post_url": "https://facebook.com/reel/1"}
        if action == "fail":
            body = {"reason": "x"}
        if action == "reconcile":
            body = {"outcome": "still_unknown"}
        response = api.post(
            f"/api/v1/publishing/publications/{job['id']}/{action}",
            json={"version": job["version"], **body},
        )
        assert response.status_code in {409, 404}, (action, response.text)
        if response.status_code == 409:
            assert response.json()["error"]["code"] == "invalid_publication_transition"
    editor = create_member(db_session, organization, role=MembershipRole.EDITOR)
    viewer = create_member(db_session, organization, role=MembershipRole.VIEWER)
    login(api, editor)
    job = act(api, job, "submit")
    assert act(api, job, "approve", expected=403)["error"]["code"] == "forbidden"
    assert "approve" not in job["allowed_actions"]
    login(api, viewer)
    assert api.get(f"/api/v1/publishing/publications/{job['id']}").status_code == 200
    assert act(api, job, "submit", expected=403)
    assert (
        api.patch(
            f"/api/v1/publishing/publications/{job['id']}", json={"version": 1, "caption": "x"}
        ).status_code
        == 403
    )
    assert (
        api.post(
            "/api/v1/publishing/publications",
            json={
                "campaign_id": world["campaign"]["id"],
                "media_asset_id": str(world["final"].id),
                "social_account_id": world["page_b"]["id"],
            },
        ).status_code
        == 403
    )


def test_scheduling_rules_and_conflicts(api: TestClient, world: dict) -> None:
    job_a = approve_flow(
        api,
        create_publication(
            api, world["campaign"]["id"], str(world["final"].id), world["page_a"]["id"]
        ),
    )
    past = act(
        api,
        job_a,
        "schedule",
        expected=422,
        scheduled_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
    )
    assert past["error"]["code"] == "schedule_invalid"
    naive = api.post(
        f"/api/v1/publishing/publications/{job_a['id']}/schedule",
        json={"version": job_a["version"], "scheduled_at": "2030-01-01T10:00:00"},
    )
    assert naive.status_code == 422  # must be timezone-aware
    job_a = act(api, job_a, "schedule", scheduled_at="2030-01-02T00:00:00+07:00")
    assert job_a["scheduled_at"].startswith("2030-01-01T17:00:00")  # stored in UTC

    make_second = create_publication(
        api, world["campaign"]["id"], str(world["final"].id), world["page_b"]["id"]
    )
    # Same account, second intent via another campaign → conflict window applies.
    campaign2 = create_campaign(api, "Second campaign")
    job_a2 = approve_flow(
        api, create_publication(api, campaign2["id"], str(world["final"].id), world["page_a"]["id"])
    )
    conflict = act(api, job_a2, "schedule", expected=409, scheduled_at="2030-01-01T17:20:00+00:00")
    assert conflict["error"]["code"] == "schedule_conflict"
    assert conflict["error"]["details"]["conflicts"][0]["id"] == job_a["id"]
    ok_gap = act(api, job_a2, "schedule", scheduled_at="2030-01-01T17:30:00+00:00")
    assert ok_gap["status"] == "scheduled"
    forced = act(
        api, ok_gap, "schedule", scheduled_at="2030-01-01T17:05:00+00:00", allow_conflict=True
    )
    assert forced["status"] == "scheduled"
    # Other account at the same time is fine.
    job_b = act(
        api, approve_flow(api, make_second), "schedule", scheduled_at="2030-01-01T17:00:00+00:00"
    )
    assert job_b["status"] == "scheduled"
    # Calendar lists them in the UTC window with the organization timezone.
    cal = api.get(
        "/api/v1/publishing/calendar",
        params={"start": "2030-01-01T00:00:00Z", "end": "2030-01-02T00:00:00Z"},
    ).json()
    assert {i["id"] for i in cal["items"]} == {job_a["id"], forced["id"], job_b["id"]} and cal[
        "timezone"
    ]
    assert (
        api.get(
            "/api/v1/publishing/calendar",
            params={"start": "2030-01-01T00:00:00Z", "end": "2030-06-01T00:00:00Z"},
        ).status_code
        == 422
    )
    unscheduled = act(api, job_b, "unschedule")
    assert unscheduled["status"] == "approved" and unscheduled["scheduled_at"] is None


def test_release_due_moves_scheduled_jobs_without_publishing(
    api: TestClient, db_session: Session, world: dict, fake_adapter: FakeNetworkAdapter
) -> None:
    job = approve_flow(
        api,
        create_publication(
            api, world["campaign"]["id"], str(world["final"].id), world["page_a"]["id"]
        ),
    )
    job = act(api, job, "schedule", scheduled_at=_future())
    assert api.post("/api/v1/publishing/queue/release-due").json()["released"] == 0
    row = db_session.get(PublicationJob, uuid.UUID(job["id"]))
    row.scheduled_at = datetime.now(UTC) - timedelta(minutes=5)
    db_session.commit()
    released = api.post("/api/v1/publishing/queue/release-due").json()
    assert released == {"released": 1, "publication_ids": [job["id"]]}
    detail = api.get(f"/api/v1/publishing/publications/{job['id']}").json()
    assert detail["status"] == "ready_to_publish" and detail["version"] == job["version"] + 1
    assert fake_adapter.calls == []  # release never publishes
    stats = api.get("/api/v1/publishing/stats").json()
    assert stats["by_status"]["ready_to_publish"] == 1 and stats["due_now"] == 1
    queue = api.get("/api/v1/publishing/queue").json()
    assert queue[0]["id"] == job["id"]


def test_idempotency_and_duplicate_publications(api: TestClient, world: dict) -> None:
    body = {
        "campaign_id": world["campaign"]["id"],
        "media_asset_id": str(world["final"].id),
        "social_account_id": world["page_a"]["id"],
        "caption": "once",
        "idempotency_key": "composer-1",
    }
    first = api.post("/api/v1/publishing/publications", json=body)
    assert first.status_code == 201
    again = api.post("/api/v1/publishing/publications", json=body)
    assert again.status_code == 200 and again.json()["id"] == first.json()["id"]
    assert api.get("/api/v1/publishing/publications").json()["total"] == 1
    # Same triple without a key: refused (unique intent per campaign/media/account).
    no_key = {k: v for k, v in body.items() if k != "idempotency_key"}
    dup = api.post("/api/v1/publishing/publications", json=no_key)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "duplicate_publication"
    # Batch reuse reports the existing job instead of failing.
    batch = api.post(
        "/api/v1/publishing/publications/batch",
        json={
            "campaign_id": body["campaign_id"],
            "media_asset_id": body["media_asset_id"],
            "items": [
                {"social_account_id": world["page_a"]["id"], "idempotency_key": "composer-1"},
                {"social_account_id": world["page_b"]["id"], "caption": "new"},
            ],
        },
    ).json()
    assert len(batch["reused"]) == 1 and len(batch["created"]) == 1


def test_optimistic_locking_rejects_stale_versions(api: TestClient, world: dict) -> None:
    job = create_publication(
        api, world["campaign"]["id"], str(world["final"].id), world["page_a"]["id"]
    )
    first = api.patch(
        f"/api/v1/publishing/publications/{job['id']}",
        json={"version": 1, "caption": "from tab one"},
    )
    assert first.status_code == 200 and first.json()["version"] == 2
    second = api.patch(
        f"/api/v1/publishing/publications/{job['id']}",
        json={"version": 1, "caption": "from tab two"},
    )
    assert second.status_code == 409 and second.json()["error"]["code"] == "version_conflict"
    assert second.json()["error"]["details"] == {"expected_version": 1, "current_version": 2}
    stale_submit = api.post(
        f"/api/v1/publishing/publications/{job['id']}/submit", json={"version": 1}
    )
    assert stale_submit.status_code == 409
    assert (
        api.get(f"/api/v1/publishing/publications/{job['id']}").json()["caption"] == "from tab one"
    )
    assert (
        api.patch(
            f"/api/v1/publishing/publications/{job['id']}", json={"caption": "no version"}
        ).status_code
        == 422
    )


def test_failure_retry_and_reconciliation(
    api: TestClient,
    db_session: Session,
    organization,
    world: dict,
    fake_adapter: FakeNetworkAdapter,
) -> None:
    job = approve_flow(
        api,
        create_publication(
            api, world["campaign"]["id"], str(world["final"].id), world["page_a"]["id"]
        ),
    )
    job = act(api, job, "start")
    assert fake_adapter.calls == [job["intent_id"]] and job["attempts"][0]["adapter"] == "fake"
    job = act(api, job, "fail", reason="Upload rejected by Facebook UI")
    assert job["status"] == "failed" and job["last_error"] == "Upload rejected by Facebook UI"
    assert job["attempts"][0]["status"] == "failed"
    # No automatic retry happened.
    assert len(fake_adapter.calls) == 1
    job = act(api, job, "retry")
    assert job["status"] == "ready_to_publish"
    job = act(api, job, "start")
    assert job["attempt_count"] == 2 and len(fake_adapter.calls) == 2
    job = act(api, job, "fail", reason="Browser crashed mid-upload", outcome="ambiguous")
    assert job["status"] == "needs_reconciliation" and job["attempts"][1]["status"] == "ambiguous"
    # Nothing is retried while ambiguous; start is not allowed.
    assert act(api, job, "start", expected=409)
    assert act(api, job, "retry", expected=409)
    # Reconcile needs admin; evidence required to mark published.
    editor = create_member(db_session, organization, role=MembershipRole.EDITOR)
    owner_me = api.get("/api/v1/auth/me").json()
    login(api, editor)
    assert act(api, job, "reconcile", expected=403, outcome="still_unknown")
    from affilio.modules.identity.models import User  # local import keeps module list short

    owner = db_session.get(User, uuid.UUID(owner_me["id"]))
    login(api, owner)
    unknown = act(api, job, "reconcile", outcome="still_unknown", note="checked page, nothing yet")
    assert unknown["status"] == "needs_reconciliation" and unknown["version"] == job["version"] + 1
    no_evidence = api.post(
        f"/api/v1/publishing/publications/{job['id']}/reconcile",
        json={"version": unknown["version"], "outcome": "published"},
    )
    assert no_evidence.status_code == 422
    published = act(
        api,
        unknown,
        "reconcile",
        outcome="published",
        external_post_url="https://www.facebook.com/DealsPageA/videos/987654321098/",
    )
    assert published["status"] == "published" and published["evidence_kind"] == "operator_reported"
    assert (
        published["external_post_id"] == "987654321098"
        and published["attempts"][1]["status"] == "succeeded"
    )
    # A second ambiguous job reconciled as not published → FAILED, revise → DRAFT.
    other = approve_flow(
        api,
        create_publication(
            api, world["campaign"]["id"], str(world["final"].id), world["page_b"]["id"]
        ),
    )
    other = act(api, act(api, other, "start"), "fail", reason="unsure", outcome="ambiguous")
    not_published = act(api, other, "reconcile", outcome="not_published", note="Page shows nothing")
    assert (
        not_published.status_code
        if isinstance(not_published, int)
        else not_published["status"] == "failed"
    )
    assert act(api, not_published, "revise")["status"] == "draft"


def test_duplicate_evidence_is_rejected(api: TestClient, world: dict) -> None:
    job_a = act(
        api,
        approve_flow(
            api,
            create_publication(
                api, world["campaign"]["id"], str(world["final"].id), world["page_a"]["id"]
            ),
        ),
        "start",
    )
    campaign2 = create_campaign(api, "Second")
    job_a2 = act(
        api,
        approve_flow(
            api,
            create_publication(api, campaign2["id"], str(world["final"].id), world["page_a"]["id"]),
        ),
        "start",
    )
    job_b = act(
        api,
        approve_flow(
            api,
            create_publication(
                api, world["campaign"]["id"], str(world["final"].id), world["page_b"]["id"]
            ),
        ),
        "start",
    )
    url = "https://www.facebook.com/reel/555000111222333"
    assert act(api, job_a, "confirm", external_post_url=url)["status"] == "published"
    dup_id = act(
        api,
        job_a2,
        "confirm",
        expected=409,
        external_post_url="https://m.facebook.com/reel/555000111222333/?ref=share",
    )
    assert (
        dup_id["error"]["code"] == "duplicate_evidence"
        and dup_id["error"]["details"]["publication_id"] == job_a["id"]
    )
    dup_url = act(
        api,
        job_b,
        "confirm",
        expected=409,
        external_post_url="https://WWW.FACEBOOK.COM/reel/555000111222333/#share",
    )
    assert dup_url["error"]["code"] == "duplicate_evidence"
    # Confirming the same job twice is impossible (already PUBLISHED).
    published = api.get(f"/api/v1/publishing/publications/{job_a['id']}").json()
    assert (
        act(api, published, "confirm", expected=409, external_post_url=url)["error"]["code"]
        == "invalid_publication_transition"
    )
    assert (
        act(api, job_b, "confirm", external_post_url="https://www.facebook.com/reel/999")["status"]
        == "published"
    )


def test_cancel_and_listing_filters(api: TestClient, world: dict) -> None:
    job = create_publication(
        api, world["campaign"]["id"], str(world["final"].id), world["page_a"]["id"], title="Alpha"
    )
    other = create_publication(
        api, world["campaign"]["id"], str(world["final"].id), world["page_b"]["id"], title="Beta"
    )
    cancelled = act(api, job, "cancel", note="not needed")
    assert cancelled["status"] == "cancelled" and cancelled["allowed_actions"] == []
    listing = api.get("/api/v1/publishing/publications", params={"status": "draft"}).json()
    assert [i["id"] for i in listing["items"]] == [other["id"]]
    assert api.get("/api/v1/publishing/publications", params={"q": "alph"}).json()["total"] == 1
    assert (
        api.get(
            "/api/v1/publishing/publications", params={"social_account_id": world["page_b"]["id"]}
        ).json()["total"]
        == 1
    )
    assert (
        api.get("/api/v1/publishing/publications", params={"page_size": 1}).json()["total_pages"]
        == 2
    )
    assert api.get("/api/v1/publishing/publications", params={"sort": "bogus"}).status_code == 422
    assert api.get(f"/api/v1/publishing/publications/{uuid.uuid4()}").status_code == 404
    assert api.get("/api/v1/publishing/publications/not-a-uuid").status_code == 422
    assert api.get("/api/v1/publishing/queue").json() == []  # drafts/cancelled are not queue items
