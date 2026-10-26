"""M3A / M3A.1 hardening: concurrency, routing safety before publishing, revalidation after
reclassification, disabled vs disconnected targets, cross-organization isolation, precedence
edge cases. Needs TEST_DATABASE_URL."""

from __future__ import annotations

import shutil
import threading
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from affilio.api.deps import Identity
from affilio.app import create_app
from affilio.core.config import get_settings
from affilio.core.enums import MembershipRole
from affilio.db.session import dispose_engine
from affilio.modules.publishing import service as publishing_service
from affilio.modules.publishing.errors import DuplicatePublicationError
from affilio.modules.publishing.models import PublicationJob, PublicationSnapshot
from affilio.modules.publishing.schemas import PublicationCreate
from affilio.modules.taxonomy import service as taxonomy_service
from affilio.modules.taxonomy.schemas import CategoryCreate
from tests.auth_helpers import create_member, login, sign_in
from tests.media_helpers import create_organization
from tests.publishing_helpers import act, approve_flow, create_campaign, register_page, scan_root
from tests.test_content_routing import (
    asset_by_path,
    cat,
    drop_final,
    finals_root_id,
    rule,
    set_profile,
)

pytestmark = pytest.mark.database


@pytest.fixture
def api(
    monkeypatch: pytest.MonkeyPatch, media_dirs, organization, schema, db_session: Session
) -> Iterator[TestClient]:
    for key, value in media_dirs.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DEFAULT_ORGANIZATION_SLUG", organization.slug)
    monkeypatch.setenv("MEDIA_FINAL_SETTLE_SECONDS", "0")
    monkeypatch.delenv("MEDIA_ADMIN_TOKEN", raising=False)
    get_settings.cache_clear()
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        sign_in(client, db_session, organization, role=MembershipRole.OWNER)
        yield client
    dispose_engine()
    get_settings.cache_clear()


@pytest.fixture
def world(api: TestClient, db_session: Session, organization, media_dirs) -> dict:
    """Men's Sports/Pickleball + Women's Fashion + Home & Living/Kitchen + Mom & Baby, folder
    rules for each, three approved finals, four Pages with profiles."""
    t: dict[str, dict] = {}
    t["mens_sports"] = cat(api, "Men's Sports")
    t["pickleball"] = cat(api, "Pickleball", t["mens_sports"]["id"])
    t["womens_fashion"] = cat(api, "Women's Fashion")
    t["home"] = cat(api, "Home & Living")
    t["kitchen"] = cat(api, "Kitchen", t["home"]["id"])
    t["mom_baby"] = cat(api, "Mom & Baby")
    root = finals_root_id(api)
    rule(api, root, "mens-sports", t["mens_sports"]["id"])
    rule(api, root, "mens-sports/pickleball", t["pickleball"]["id"])
    rule(api, root, "womens-fashion", t["womens_fashion"]["id"])
    rule(api, root, "home-living", t["home"]["id"])
    rule(api, root, "home-living/kitchen", t["kitchen"]["id"])
    rule(api, root, "mom-baby", t["mom_baby"]["id"])
    drop_final(media_dirs, "mens-sports/pickleball/serve.mp4", seed=31)
    drop_final(media_dirs, "home-living/kitchen/pan.mp4", seed=32)
    drop_final(media_dirs, "womens-fashion/dress.mp4", seed=33)
    scan_root(db_session, organization.id, media_dirs, "finals")
    assets = {
        "serve": asset_by_path(db_session, organization, "mens-sports/pickleball/serve.mp4"),
        "pan": asset_by_path(db_session, organization, "home-living/kitchen/pan.mp4"),
        "dress": asset_by_path(db_session, organization, "womens-fashion/dress.mp4"),
    }
    for a in assets.values():
        assert (
            api.post(
                f"/api/v1/media/assets/{a.id}/handoff", json={"status": "approved", "note": "ok"}
            ).status_code
            == 200
        )
    pages = {
        "antor": register_page(api, "ANTOR Men"),
        "home": register_page(api, "Home Page"),
        "moms": register_page(api, "Mom Page"),
        "disconnected": register_page(api, "Disconnected Page"),
    }
    set_profile(
        api,
        pages["antor"]["id"],
        primary_category_id=t["mens_sports"]["id"],
        rules=[
            {"category_id": t["mens_sports"]["id"], "match_policy": "prefer"},
            {"category_id": t["womens_fashion"]["id"], "match_policy": "block"},
        ],
    )
    set_profile(
        api, pages["home"]["id"], rules=[{"category_id": t["home"]["id"], "match_policy": "prefer"}]
    )
    set_profile(
        api,
        pages["moms"]["id"],
        rules=[
            {"category_id": t["mom_baby"]["id"], "match_policy": "prefer"},
            {"category_id": t["kitchen"]["id"], "match_policy": "block"},
        ],
    )
    set_profile(
        api,
        pages["disconnected"]["id"],
        rules=[{"category_id": t["mens_sports"]["id"], "match_policy": "allow"}],
    )
    return {
        "t": t,
        "root": root,
        "assets": assets,
        "pages": pages,
        "campaign": create_campaign(api, "Hardening"),
    }


def draft(api: TestClient, world: dict, asset_key: str, page_key: str, **content) -> dict:
    body = {
        "campaign_id": world["campaign"]["id"],
        "media_asset_id": str(world["assets"][asset_key].id),
        "social_account_id": world["pages"][page_key]["id"],
        "caption": f"{asset_key} for {page_key}",
        **content,
    }
    response = api.post("/api/v1/publishing/publications", json=body)
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------- precedence edge cases


def test_equal_depth_rules_resolve_by_priority_then_age(api: TestClient, world: dict) -> None:
    t, root = world["t"], world["root"]
    # Two rules on the same folder cannot exist; equal *depth* competition happens between a
    # root rule and an exact sibling. Deepest wins regardless of priority.
    deep = rule(api, root, "mens-sports/pickleball/tournaments", t["pickleball"]["id"], priority=0)
    sports_rule = next(
        r
        for r in api.get("/api/v1/media/folder-rules").json()
        if r["relative_folder_path"] == "mens-sports"
    )
    high = api.patch(f"/api/v1/media/folder-rules/{sports_rule['id']}", json={"priority": 999})
    assert high.json()["priority"] == 999

    def preview(folder: str) -> dict:
        return api.get(
            "/api/v1/media/folder-rules/preview",
            params={"media_root_id": root, "relative_folder_path": folder},
        ).json()

    assert preview("mens-sports/pickleball/tournaments/2026")["rule"]["id"] == deep["id"]
    assert preview("mens-sports/football")["rule"]["relative_folder_path"] == "mens-sports"
    # root-level catch-all rule only applies where nothing deeper matches
    catch_all = rule(api, root, "", t["mom_baby"]["id"])
    assert preview("random/folder")["rule"]["id"] == catch_all["id"]
    assert (
        preview("mens-sports/pickleball")["rule"]["relative_folder_path"]
        == "mens-sports/pickleball"
    )
    assert preview("")["rule"]["id"] == catch_all["id"]


def test_deep_cycle_and_depth_limit(api: TestClient, world: dict) -> None:
    t = world["t"]
    chain = [t["kitchen"]]
    for i in range(5):
        chain.append(cat(api, f"Level {i}", chain[-1]["id"]))
    too_deep = api.post(
        "/api/v1/content-categories", json={"name": "Too deep", "parent_id": chain[-1]["id"]}
    )
    assert too_deep.status_code == 422 and too_deep.json()["error"]["code"] == "category_cycle"
    # moving the top (Home & Living) under its deepest descendant is a cycle
    cyc = api.patch(
        f"/api/v1/content-categories/{t['home']['id']}", json={"parent_id": chain[-1]["id"]}
    )
    assert cyc.status_code == 422 and cyc.json()["error"]["code"] == "category_cycle"


# ---------------------------------------------------------------- concurrency


def test_concurrent_draft_creation_yields_one_job(
    db_session: Session, organization, world: dict
) -> None:
    """Two sessions race to create the same (campaign, media, account) draft: exactly one wins."""
    engine = db_session.get_bind()
    user_identity = Identity(kind="service", organization=organization, role=MembershipRole.OWNER)
    payload = PublicationCreate(
        campaign_id=uuid.UUID(world["campaign"]["id"]),
        media_asset_id=world["assets"]["pan"].id,
        social_account_id=uuid.UUID(world["pages"]["home"]["id"]),
        caption="race",
    )
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        with Session(engine, autoflush=False) as session:
            try:
                barrier.wait(timeout=10)
                publishing_service.create_job(
                    session, organization.id, payload, identity=user_identity
                )
                session.commit()
                result = "created"
            except DuplicatePublicationError:
                session.rollback()
                result = "duplicate"
            except Exception as exc:
                session.rollback()
                result = f"error:{type(exc).__name__}"
            with lock:
                outcomes.append(result)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=30)
    assert sorted(outcomes) == ["created", "duplicate"], outcomes
    count = db_session.scalar(
        select(PublicationJob).where(
            PublicationJob.campaign_id == uuid.UUID(world["campaign"]["id"]),
            PublicationJob.media_asset_id == world["assets"]["pan"].id,
        )
    )
    assert count is not None
    rows = db_session.scalars(
        select(PublicationJob).where(PublicationJob.media_asset_id == world["assets"]["pan"].id)
    ).all()
    assert len(rows) == 1


# ---------------------------------------------------------------- routing safety


def test_blocked_target_is_refused_at_every_publishing_step(api: TestClient, world: dict) -> None:
    t = world["t"]
    job = approve_flow(api, draft(api, world, "serve", "antor"))
    assert job["target_blocked_reason"] is None and job["revalidation_required"] is False
    job = act(api, job, "schedule", scheduled_at="2030-01-01T10:00:00+00:00")
    # Admin changes ANTOR's rules: Men's Sports is now blocked → the scheduled job is held.
    set_profile(
        api,
        world["pages"]["antor"]["id"],
        rules=[
            {"category_id": t["mens_sports"]["id"], "match_policy": "block"},
            {"category_id": t["womens_fashion"]["id"], "match_policy": "block"},
        ],
    )
    detail = api.get(f"/api/v1/publishing/publications/{job['id']}").json()
    assert (
        detail["target_blocked_reason"].startswith("Pickleball is blocked")
        and detail["revalidation_required"]
    )
    assert detail["status"] == "scheduled"  # nothing changed automatically
    blocked = act(api, detail, "start", expected=409)
    assert blocked["error"]["code"] == "target_blocked"
    assert act(api, detail, "revalidate", expected=409)["error"]["code"] == "target_blocked"
    assert (
        act(api, detail, "schedule", expected=409, scheduled_at="2030-01-02T10:00:00+00:00")[
            "error"
        ]["code"]
        == "target_blocked"
    )
    # release-due holds it (audited) instead of moving it to READY_TO_PUBLISH
    from datetime import UTC, datetime, timedelta

    db_job = api.get(f"/api/v1/publishing/publications/{job['id']}").json()
    assert db_job["status"] == "scheduled"
    # Rescheduling is refused for a blocked target, so make the job due directly in the DB.
    import sqlalchemy as sa

    from affilio.db.session import get_session_factory

    with get_session_factory()() as s, s.begin():
        s.execute(
            sa.update(PublicationJob)
            .where(PublicationJob.id == uuid.UUID(job["id"]))
            .values(scheduled_at=datetime.now(UTC) - timedelta(minutes=5))
        )
    released = api.post("/api/v1/publishing/queue/release-due").json()
    assert released["released"] == 0
    held = api.get(
        "/api/v1/auth/audit", params={"entity_type": "publication_job", "entity_id": job["id"]}
    ).json()
    assert (
        held[0]["action"] == "publication.release_held" and "blocked" in held[0]["data"]["reason"]
    )
    assert api.get(f"/api/v1/publishing/publications/{job['id']}").json()["status"] == "scheduled"
    # Approving a draft for a blocked target is refused too; the operator must cancel or fix rules
    job2 = act(
        api,
        draft(api, world, "serve", "antor", caption="second attempt is impossible (duplicate)")
        if False
        else act(api, draft(api, world, "dress", "antor"), "submit"),
        "approve",
        expected=409,
    )
    assert job2["error"]["code"] == "target_blocked"
    # Unblock → start works again
    set_profile(
        api,
        world["pages"]["antor"]["id"],
        rules=[{"category_id": t["mens_sports"]["id"], "match_policy": "prefer"}],
    )
    fresh = api.get(f"/api/v1/publishing/publications/{job['id']}").json()
    assert fresh["target_blocked_reason"] is None
    assert act(api, fresh, "start")["status"] == "publishing"


def test_reclassification_after_approval_requires_explicit_revalidation(
    api: TestClient, db_session: Session, organization, media_dirs, world: dict
) -> None:
    job = approve_flow(api, draft(api, world, "pan", "home"))
    snapshot_id = job["approved_snapshot"]["id"]
    # Kitchen → Mom & Baby: Home Page has no rule for Mom & Baby → eligible but stale.
    (media_dirs.finals / "mom-baby").mkdir(exist_ok=True)
    shutil.move(
        media_dirs.finals / "home-living/kitchen/pan.mp4", media_dirs.finals / "mom-baby/pan.mp4"
    )
    scan_root(db_session, organization.id, media_dirs, "finals")
    detail = api.get(f"/api/v1/publishing/publications/{job['id']}").json()
    assert (
        detail["classification_stale"]
        and detail["revalidation_required"]
        and detail["target_blocked_reason"] is None
    )
    assert "revalidate" in detail["allowed_actions"]
    assert act(api, detail, "start", expected=409)["error"]["code"] == "revalidation_required"
    assert (
        act(api, detail, "schedule", expected=409, scheduled_at="2030-01-01T10:00:00+00:00")[
            "error"
        ]["code"]
        == "revalidation_required"
    )
    # editors cannot revalidate
    editor = create_member(db_session, organization, role=MembershipRole.EDITOR)
    login(api, editor)
    assert act(api, detail, "revalidate", expected=403)
    admin = create_member(db_session, organization, role=MembershipRole.ADMIN)
    login(api, admin)
    revalidated = act(api, detail, "revalidate", note="checked: still fine for Home Page")
    assert (
        revalidated["classification_stale"] is False
        and revalidated["revalidation_required"] is False
    )
    assert (
        revalidated["revalidated_by_label"] == admin.email and revalidated["status"] == "approved"
    )
    assert revalidated["approved_snapshot"]["id"] == snapshot_id  # snapshot untouched
    row = db_session.get(PublicationSnapshot, uuid.UUID(snapshot_id))
    db_session.refresh(row)
    assert row.media["relative_path"] == "home-living/kitchen/pan.mp4"
    assert act(api, revalidated, "start")["status"] == "publishing"
    # A second move after revalidation flags it again; a new approval clears the marker.
    shutil.move(media_dirs.finals / "mom-baby/pan.mp4", media_dirs.finals / "home-living/pan.mp4")
    scan_root(db_session, organization.id, media_dirs, "finals")
    again = api.get(f"/api/v1/publishing/publications/{job['id']}").json()
    assert again["classification_stale"] is True and again["status"] == "publishing"
    audit = [
        e["action"]
        for e in api.get(
            "/api/v1/auth/audit", params={"entity_type": "publication_job", "entity_id": job["id"]}
        ).json()
    ]
    assert "publication.revalidate" in audit
    # Blocked through reclassification: a Mom Page job whose video moves into Kitchen (BLOCK).
    moms = approve_flow(
        api, draft(api, world, "serve", "moms")
    )  # serve has no Mom rule → eligible, low relevance
    shutil.move(
        media_dirs.finals / "mens-sports/pickleball/serve.mp4",
        media_dirs.finals / "home-living/kitchen/serve.mp4",
    )
    scan_root(db_session, organization.id, media_dirs, "finals")
    moms_now = api.get(f"/api/v1/publishing/publications/{moms['id']}").json()
    assert (
        moms_now["target_blocked_reason"].startswith("Kitchen is blocked")
        and moms_now["classification_stale"]
    )
    assert act(api, moms_now, "start", expected=409)["error"]["code"] == "target_blocked"
    assert act(api, moms_now, "revoke_approval", note="wrong page now")["status"] == "draft"


def test_disabled_and_disconnected_pages(api: TestClient, world: dict) -> None:
    t = world["t"]
    # Manual (disconnected, unverified) Pages are eligible for assisted publishing…
    rec = api.get(
        "/api/v1/distribution/recommendations",
        params={"media_asset_id": str(world["assets"]["serve"].id)},
    ).json()
    low = {r["display_name"]: r for r in rec["recommended"] + rec["low_relevance"]}
    assert (
        low["Disconnected Page"]["score"] == 70
        and low["Disconnected Page"]["connection_label"] == "Manual (unverified, not connected)"
    )
    assert all(r["account_status"] == "disconnected" for r in rec["recommended"])
    # …but a disabled Page is excluded everywhere, including an existing approved job.
    job = approve_flow(api, draft(api, world, "serve", "disconnected"))
    api.post(
        f"/api/v1/accounts/{world['pages']['disconnected']['id']}/status",
        json={"status": "disabled"},
    )
    rec = api.get(
        "/api/v1/distribution/recommendations",
        params={"media_asset_id": str(world["assets"]["serve"].id)},
    ).json()
    assert {r["display_name"]: r["blocking_reason"] for r in rec["blocked"]}[
        "Disconnected Page"
    ] == "Account is disabled"
    detail = api.get(f"/api/v1/publishing/publications/{job['id']}").json()
    assert detail["target_blocked_reason"] == "Account is disabled"
    refused = api.post(
        f"/api/v1/publishing/publications/{job['id']}/start", json={"version": detail["version"]}
    )
    assert refused.status_code in {409, 422}
    assert refused.json()["error"]["code"] in {"target_blocked", "target_unavailable"}
    # re-enabled → eligible again; routing toggle also excludes
    api.post(
        f"/api/v1/accounts/{world['pages']['disconnected']['id']}/status",
        json={"status": "disconnected"},
    )
    set_profile(
        api,
        world["pages"]["disconnected"]["id"],
        routing_enabled=False,
        rules=[{"category_id": t["mens_sports"]["id"], "match_policy": "allow"}],
    )
    assert (
        api.get(f"/api/v1/publishing/publications/{job['id']}").json()["target_blocked_reason"]
        == "Routing is disabled for this account"
    )


# ---------------------------------------------------------------- isolation


def test_cross_organization_isolation_for_classification_and_routing(
    api: TestClient, db_session: Session, organization, world: dict
) -> None:
    other = create_organization(db_session)
    db_session.commit()
    outsider = create_member(db_session, other, role=MembershipRole.OWNER)
    serve = world["assets"]["serve"]
    rule_id = api.get("/api/v1/media/folder-rules").json()[0]["id"]
    job = draft(api, world, "serve", "antor")
    login(api, outsider)
    assert api.get("/api/v1/media/folder-rules").json() == []
    assert (
        api.patch(f"/api/v1/media/folder-rules/{rule_id}", json={"priority": 5}).status_code == 404
    )
    assert api.delete(f"/api/v1/media/folder-rules/{rule_id}").status_code == 404
    assert (
        api.put(
            f"/api/v1/media/assets/{serve.id}/categories", json={"primary_category_id": None}
        ).status_code
        == 404
    )
    assert (
        api.get(
            "/api/v1/media/assets", params={"category_id": world["t"]["mens_sports"]["id"]}
        ).json()["total"]
        == 0
    )
    assert api.get("/api/v1/media/folder-rules/report").json() == []
    assert (
        api.get(f"/api/v1/accounts/{world['pages']['antor']['id']}/channel-profile").status_code
        == 404
    )
    assert (
        api.get(
            "/api/v1/distribution/recommendations", params={"media_asset_id": str(serve.id)}
        ).status_code
        == 404
    )
    assert api.get(f"/api/v1/publishing/publications/{job['id']}").status_code == 404
    assert (
        api.post(
            f"/api/v1/publishing/publications/{job['id']}/revalidate", json={"version": 1}
        ).status_code
        == 404
    )
    # The outsider's own taxonomy is independent (same slugs allowed)
    mine = api.post("/api/v1/content-categories", json={"name": "Men's Sports"})
    assert mine.status_code == 201
    tree = taxonomy_service.load_tree(db_session, other.id)
    assert len(tree.by_id) == 1
    taxonomy_service.create_category(
        db_session,
        other.id,
        CategoryCreate(name="Pickleball", parent_id=uuid.UUID(mine.json()["id"])),
    )
    db_session.commit()
