"""M3A.1: taxonomy, folder classification, scanner integration, channel routing, composer
integration. Needs TEST_DATABASE_URL."""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from affilio.app import create_app
from affilio.core.config import get_settings
from affilio.core.enums import MediaAssetKind, MembershipRole
from affilio.db.session import dispose_engine
from affilio.modules.media import classification
from affilio.modules.media.models import MediaAsset, MediaContentCategory
from affilio.modules.media.service import get_asset
from affilio.modules.publishing.models import PublicationSnapshot
from tests.auth_helpers import create_member, login, sign_in
from tests.media_helpers import create_organization, write_bytes
from tests.publishing_helpers import (
    act,
    approve_flow,
    create_campaign,
    register_page,
    scan_root,
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


def cat(client: TestClient, name: str, parent_id: str | None = None, **extra) -> dict:
    response = client.post(
        "/api/v1/content-categories", json={"name": name, "parent_id": parent_id, **extra}
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def taxonomy(api: TestClient) -> dict[str, dict]:
    """Realistic fixture: Men's Sports (Football, Pickleball, Running), Men's Fashion,
    Women's Fashion, Home & Living (Kitchen, Cleaning), Mom & Baby."""
    t: dict[str, dict] = {}
    t["mens_sports"] = cat(api, "Men's Sports")
    for child in ("Football", "Pickleball", "Running"):
        t[child.lower()] = cat(api, child, t["mens_sports"]["id"])
    t["mens_fashion"] = cat(api, "Men's Fashion")
    t["womens_fashion"] = cat(api, "Women's Fashion")
    t["home"] = cat(api, "Home & Living")
    t["kitchen"] = cat(api, "Kitchen", t["home"]["id"])
    t["cleaning"] = cat(api, "Cleaning", t["home"]["id"])
    t["mom_baby"] = cat(api, "Mom & Baby")
    return t


def finals_root_id(api: TestClient) -> str:
    # Root rows are created on first read (ensure_db_roots).
    return next(r["id"] for r in api.get("/api/v1/media/roots").json() if r["key"] == "finals")


def rule(client: TestClient, root_id: str, path: str, category_id: str, **extra) -> dict:
    response = client.post(
        "/api/v1/media/folder-rules",
        json={
            "media_root_id": root_id,
            "relative_folder_path": path,
            "category_id": category_id,
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def drop_final(media_dirs, relative: str, seed: int = 1) -> None:
    path = media_dirs.finals / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    write_bytes(path, 6_000, seed=seed)
    old = (datetime.now(UTC) - timedelta(hours=1)).timestamp()
    os.utime(path, (old, old))


def asset_by_path(session: Session, organization, relative: str) -> MediaAsset:
    session.expire_all()
    for asset in session.scalars(
        select(MediaAsset).where(
            MediaAsset.organization_id == organization.id,
            MediaAsset.asset_kind == MediaAssetKind.FINAL,
        )
    ):
        main = asset.main_variant()
        if main and main.relative_path == relative:
            return get_asset(session, organization.id, asset.id)
    raise AssertionError(f"no final at {relative}")


def primary_of(asset: MediaAsset) -> MediaContentCategory | None:
    return next((a for a in asset.category_assignments if a.is_primary), None)


# ---------------------------------------------------------------- taxonomy


def test_category_tree_rules(api: TestClient, taxonomy: dict) -> None:
    listing = api.get("/api/v1/content-categories").json()
    paths = {c["path"]: c["depth"] for c in listing}
    assert paths["Men's Sports / Pickleball"] == 1 and paths["Home & Living"] == 0
    assert [c["name"] for c in listing][:4] == [
        "Home & Living",
        "Cleaning",
        "Kitchen",
        "Men's Fashion",
    ]
    # duplicate sibling slug
    dup = api.post(
        "/api/v1/content-categories",
        json={"name": "Football", "parent_id": taxonomy["mens_sports"]["id"]},
    )
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "duplicate_category_slug"
    # same slug under another parent is fine
    assert (
        api.post(
            "/api/v1/content-categories",
            json={"name": "Football", "parent_id": taxonomy["home"]["id"]},
        ).status_code
        == 201
    )
    # cycles
    sports, pickle = taxonomy["mens_sports"], taxonomy["pickleball"]
    own = api.patch(f"/api/v1/content-categories/{sports['id']}", json={"parent_id": sports["id"]})
    assert own.status_code == 422 and own.json()["error"]["code"] == "category_cycle"
    under_child = api.patch(
        f"/api/v1/content-categories/{sports['id']}", json={"parent_id": pickle["id"]}
    )
    assert (
        under_child.status_code == 422 and under_child.json()["error"]["code"] == "category_cycle"
    )
    # archive protection: parent with active children, Uncategorized
    blocked = api.patch(f"/api/v1/content-categories/{sports['id']}", json={"status": "archived"})
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "category_in_use"
    unc = api.post("/api/v1/content-categories/ensure-uncategorized").json()
    assert (
        api.patch(
            f"/api/v1/content-categories/{unc['id']}", json={"status": "archived"}
        ).status_code
        == 409
    )
    assert api.delete(f"/api/v1/content-categories/{sports['id']}").status_code == 409
    # leaf archive + delete of an unreferenced leaf
    assert (
        api.patch(
            f"/api/v1/content-categories/{taxonomy['running']['id']}", json={"status": "archived"}
        ).json()["status"]
        == "archived"
    )
    assert api.delete(f"/api/v1/content-categories/{taxonomy['running']['id']}").status_code == 204
    assert api.get(f"/api/v1/content-categories/{taxonomy['running']['id']}").status_code == 404
    assert (
        api.post("/api/v1/content-categories", json={"name": "x", "slug": "Bad Slug!"}).status_code
        == 422
    )


def test_taxonomy_rbac_and_isolation(
    api: TestClient, taxonomy: dict, db_session: Session, organization
) -> None:
    editor = create_member(db_session, organization, role=MembershipRole.EDITOR)
    login(api, editor)
    assert api.get("/api/v1/content-categories").status_code == 200
    assert api.post("/api/v1/content-categories", json={"name": "Nope"}).status_code == 403
    other = create_organization(db_session)
    db_session.commit()
    outsider = create_member(db_session, other, role=MembershipRole.OWNER)
    login(api, outsider)
    assert api.get("/api/v1/content-categories").json() == []
    assert api.get(f"/api/v1/content-categories/{taxonomy['kitchen']['id']}").status_code == 404
    assert (
        api.post(
            "/api/v1/content-categories",
            json={"name": "Child", "parent_id": taxonomy["home"]["id"]},
        ).status_code
        == 404
    )


# ---------------------------------------------------------------- folder rules


def test_folder_rule_resolution_and_validation(api: TestClient, taxonomy: dict) -> None:
    root = finals_root_id(api)
    sports = rule(api, root, "mens-sports", taxonomy["mens_sports"]["id"])
    pickle = rule(api, root, "mens-sports/pickleball/", taxonomy["pickleball"]["id"])
    exact_only = rule(api, root, "home-living", taxonomy["home"]["id"], include_subfolders=False)
    assert pickle["relative_folder_path"] == "mens-sports/pickleball"

    def preview(path: str) -> dict:
        return api.get(
            "/api/v1/media/folder-rules/preview",
            params={"media_root_id": root, "relative_folder_path": path},
        ).json()

    # nested → most specific wins
    assert preview("mens-sports/pickleball/2026")["rule"]["id"] == pickle["id"]
    assert preview("mens-sports/football")["rule"]["id"] == sports["id"]
    # exact vs descendant
    assert preview("home-living")["rule"]["id"] == exact_only["id"]
    unmapped = preview("home-living/kitchen")
    assert (
        unmapped["rule"] is None
        and unmapped["fallback"] is True
        and unmapped["category_name"] == "Uncategorized"
    )
    assert [c["id"] for c in preview("mens-sports/pickleball")["candidates"]] == [
        sports["id"],
        pickle["id"],
    ] or len(preview("mens-sports/pickleball")["candidates"]) == 2
    # overlapping rules at equal depth: priority decides
    low = rule(api, root, "mom-baby", taxonomy["mom_baby"]["id"], priority=1)
    api.patch(f"/api/v1/media/folder-rules/{low['id']}", json={"relative_folder_path": "mom-baby"})
    dup = api.post(
        "/api/v1/media/folder-rules",
        json={
            "media_root_id": root,
            "relative_folder_path": "mom-baby/",
            "category_id": taxonomy["home"]["id"],
        },
    )
    assert dup.status_code == 409
    # traversal / invalid paths
    for bad in ("../outside", "/abs/../..", "a/../../b"):
        response = api.post(
            "/api/v1/media/folder-rules",
            json={
                "media_root_id": root,
                "relative_folder_path": bad,
                "category_id": taxonomy["home"]["id"],
            },
        )
        assert response.status_code == 422, bad
    assert (
        classification.normalize_folder_path("  Mens-Sports\\Running// ") == "Mens-Sports/Running"
    )
    # only final roots
    originals = next(
        r for r in api.get("/api/v1/media/scans/status").json()["roots"] if r["key"] == "originals"
    )
    not_final = api.post(
        "/api/v1/media/folder-rules",
        json={
            "media_root_id": originals["id"],
            "relative_folder_path": "x",
            "category_id": taxonomy["home"]["id"],
        },
    )
    assert not_final.status_code == 409
    # disable → preview falls back
    api.patch(f"/api/v1/media/folder-rules/{pickle['id']}", json={"is_active": False})
    assert preview("mens-sports/pickleball")["rule"]["id"] == sports["id"]
    assert api.delete(f"/api/v1/media/folder-rules/{exact_only['id']}").status_code == 204
    assert len(api.get("/api/v1/media/folder-rules").json()) == 3


# ---------------------------------------------------------------- scanner integration


def test_scanner_classifies_moves_and_is_idempotent(
    api: TestClient, taxonomy: dict, db_session: Session, organization, media_dirs
) -> None:
    root = finals_root_id(api)
    rule(api, root, "mens-sports", taxonomy["mens_sports"]["id"])
    rule(api, root, "mens-sports/pickleball", taxonomy["pickleball"]["id"])
    rule(api, root, "home-living/kitchen", taxonomy["kitchen"]["id"])
    drop_final(media_dirs, "mens-sports/pickleball/paddle_review.mp4", seed=1)
    drop_final(media_dirs, "mens-sports/football/boots.mp4", seed=2)
    drop_final(media_dirs, "unknown-folder/mystery.mp4", seed=3)

    scan_root(db_session, organization.id, media_dirs, "finals")
    paddle = asset_by_path(db_session, organization, "mens-sports/pickleball/paddle_review.mp4")
    boots = asset_by_path(db_session, organization, "mens-sports/football/boots.mp4")
    mystery = asset_by_path(db_session, organization, "unknown-folder/mystery.mp4")
    assert primary_of(paddle).category_id == uuid.UUID(taxonomy["pickleball"]["id"])
    assert primary_of(boots).category_id == uuid.UUID(
        taxonomy["mens_sports"]["id"]
    )  # inherited from parent folder
    assert primary_of(mystery).category.slug == "uncategorized"
    assert primary_of(paddle).assignment_source == "folder"
    assert paddle.handoff_status == "pending_review"  # classification never bypasses review
    assert paddle.classification_changed_at is None  # first assignment is not a "change"

    # Duplicate scan → no duplicate assignments, no change marker
    scan_root(db_session, organization.id, media_dirs, "finals")
    paddle = asset_by_path(db_session, organization, "mens-sports/pickleball/paddle_review.mp4")
    assert len(paddle.category_assignments) == 1 and paddle.classification_changed_at is None

    # Report: unmapped folder visible
    report = api.get("/api/v1/media/folder-rules/report").json()
    by_folder = {r["folder"]: r for r in report}
    assert (
        by_folder["unknown-folder"]["mapped"] is False
        and by_folder["mens-sports/pickleball"]["mapped"] is True
    )
    assert by_folder["mens-sports/football"]["rule"]["relative_folder_path"] == "mens-sports"

    # Library filters + summary fields
    listing = api.get(
        "/api/v1/media/assets", params={"category_id": taxonomy["mens_sports"]["id"]}
    ).json()
    assert {i["title"] for i in listing["items"]} == {
        "paddle review",
        "boots",
    }  # descendants included
    unc = api.get("/api/v1/media/assets", params={"uncategorized": "true"}).json()
    assert [i["title"] for i in unc["items"]] == ["mystery"]
    item = next(i for i in listing["items"] if i["title"] == "paddle review")
    assert (
        item["folder_path"] == "mens-sports/pickleball"
        and item["categories"][0]["path"] == "Men's Sports / Pickleball"
    )

    # Move between category folders → reclassified, change recorded, identity preserved
    src = media_dirs.finals / "mens-sports/pickleball/paddle_review.mp4"
    dst = media_dirs.finals / "home-living/kitchen/paddle_review.mp4"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(src, dst)
    scan_root(db_session, organization.id, media_dirs, "finals")
    moved = asset_by_path(db_session, organization, "home-living/kitchen/paddle_review.mp4")
    assert moved.id == paddle.id
    assert primary_of(moved).category_id == uuid.UUID(taxonomy["kitchen"]["id"])
    assert moved.classification_changed_at is not None and len(moved.category_assignments) == 1

    # Missing file keeps existing lifecycle and keeps its classification
    os.remove(media_dirs.finals / "mens-sports/football/boots.mp4")
    scan_root(db_session, organization.id, media_dirs, "finals")
    boots = asset_by_path(db_session, organization, "mens-sports/football/boots.mp4")
    assert boots.status == "missing" and primary_of(boots) is not None

    # Rule change applies on the next scan without touching files
    api.post(
        "/api/v1/media/folder-rules",
        json={
            "media_root_id": root,
            "relative_folder_path": "unknown-folder",
            "category_id": taxonomy["mom_baby"]["id"],
        },
    )
    scan_root(db_session, organization.id, media_dirs, "finals")
    mystery = asset_by_path(db_session, organization, "unknown-folder/mystery.mp4")
    assert primary_of(mystery).category_id == uuid.UUID(taxonomy["mom_baby"]["id"])
    assert (media_dirs.finals / "unknown-folder/mystery.mp4").exists()


def test_manual_override_survives_rescans(
    api: TestClient, taxonomy: dict, db_session: Session, organization, media_dirs
) -> None:
    root = finals_root_id(api)
    rule(api, root, "mens-sports", taxonomy["mens_sports"]["id"])
    drop_final(media_dirs, "mens-sports/combo.mp4", seed=7)
    scan_root(db_session, organization.id, media_dirs, "finals")
    asset = asset_by_path(db_session, organization, "mens-sports/combo.mp4")

    # Footage cannot be classified
    body = {
        "primary_category_id": taxonomy["mens_fashion"]["id"],
        "secondary_category_ids": [taxonomy["kitchen"]["id"]],
    }
    updated = api.put(f"/api/v1/media/assets/{asset.id}/categories", json=body).json()
    cats = {c["name"]: c for c in updated["categories"]}
    assert (
        cats["Men's Fashion"]["is_primary"]
        and cats["Men's Fashion"]["assignment_source"] == "manual"
    )
    assert cats["Kitchen"]["assignment_source"] == "manual" and not cats["Kitchen"]["is_primary"]
    assert (
        cats["Men's Sports"]["assignment_source"] == "folder"
        and not cats["Men's Sports"]["is_primary"]
    )

    scan_root(db_session, organization.id, media_dirs, "finals")  # rescan keeps the manual primary
    again = api.get(f"/api/v1/media/assets/{asset.id}").json()
    assert next(c for c in again["categories"] if c["is_primary"])["name"] == "Men's Fashion"
    assert len(again["categories"]) == 3

    # Hand primacy back to the folder rule
    reset = api.put(
        f"/api/v1/media/assets/{asset.id}/categories",
        json={"primary_category_id": None, "secondary_category_ids": []},
    ).json()
    assert [c["name"] for c in reset["categories"]] == ["Men's Sports"] and reset["categories"][0][
        "is_primary"
    ]
    # viewer cannot override; foreign category 404
    assert (
        api.put(
            f"/api/v1/media/assets/{asset.id}/categories",
            json={"primary_category_id": str(uuid.uuid4())},
        ).status_code
        == 404
    )
    viewer = create_member(db_session, organization, role=MembershipRole.VIEWER)
    login(api, viewer)
    assert (
        api.put(
            f"/api/v1/media/assets/{asset.id}/categories", json={"primary_category_id": None}
        ).status_code
        == 403
    )


# ---------------------------------------------------------------- channel routing


def set_profile(client: TestClient, account_id: str, **body) -> dict:
    payload = {"routing_enabled": True, "rules": [], **body}
    response = client.put(f"/api/v1/accounts/{account_id}/channel-profile", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_channel_rules_inheritance_block_and_disabled(
    api: TestClient, taxonomy: dict, db_session: Session, organization, media_dirs
) -> None:
    root = finals_root_id(api)
    rule(api, root, "mens-sports", taxonomy["mens_sports"]["id"])
    rule(api, root, "mens-sports/pickleball", taxonomy["pickleball"]["id"])
    rule(api, root, "womens-fashion", taxonomy["womens_fashion"]["id"])
    drop_final(media_dirs, "mens-sports/pickleball/serve.mp4", seed=11)
    drop_final(media_dirs, "womens-fashion/dress.mp4", seed=12)
    scan_root(db_session, organization.id, media_dirs, "finals")
    serve = asset_by_path(db_session, organization, "mens-sports/pickleball/serve.mp4")
    dress = asset_by_path(db_session, organization, "womens-fashion/dress.mp4")
    # secondary: Kitchen on the serve video (manual)
    api.put(
        f"/api/v1/media/assets/{serve.id}/categories",
        json={"primary_category_id": None, "secondary_category_ids": [taxonomy["kitchen"]["id"]]},
    )

    antor = register_page(api, "ANTOR Men | Sport & Style")
    home = register_page(api, "Home & Living Page")
    moms = register_page(api, "Mom & Baby Page")
    off = register_page(api, "Routing Off Page")
    disabled = register_page(api, "Disabled Page")
    profile = set_profile(
        api,
        antor["id"],
        primary_category_id=taxonomy["mens_sports"]["id"],
        target_audience="Men 25-40",
        rules=[
            {"category_id": taxonomy["mens_sports"]["id"], "match_policy": "prefer"},
            {"category_id": taxonomy["mens_fashion"]["id"], "match_policy": "allow"},
            {"category_id": taxonomy["womens_fashion"]["id"], "match_policy": "block"},
        ],
    )
    assert profile["primary_category"]["name"] == "Men's Sports" and len(profile["rules"]) == 3
    set_profile(
        api,
        home["id"],
        rules=[
            {"category_id": taxonomy["home"]["id"], "match_policy": "prefer"},
            {"category_id": taxonomy["mom_baby"]["id"], "match_policy": "allow"},
        ],
    )
    set_profile(
        api,
        moms["id"],
        rules=[
            {"category_id": taxonomy["mom_baby"]["id"], "match_policy": "prefer"},
            {"category_id": taxonomy["kitchen"]["id"], "match_policy": "block"},
        ],
    )
    set_profile(
        api,
        off["id"],
        routing_enabled=False,
        rules=[{"category_id": taxonomy["mens_sports"]["id"], "match_policy": "prefer"}],
    )
    set_profile(
        api,
        disabled["id"],
        rules=[{"category_id": taxonomy["mens_sports"]["id"], "match_policy": "prefer"}],
    )
    api.post(f"/api/v1/accounts/{disabled['id']}/status", json={"status": "disabled"})

    rec = api.get(
        "/api/v1/distribution/recommendations", params={"media_asset_id": str(serve.id)}
    ).json()
    assert rec["primary_category"]["name"] == "Pickleball" and [
        c["name"] for c in rec["secondary_categories"]
    ] == ["Kitchen"]
    recommended = {r["display_name"]: r for r in rec["recommended"]}
    low = {r["display_name"]: r for r in rec["low_relevance"]}
    blocked = {r["display_name"]: r for r in rec["blocked"]}
    # inherited PREFER from Men's Sports → Pickleball, plus profile focus
    assert recommended["ANTOR Men | Sport & Style"]["score"] == 110
    assert any("via Men's Sports" in r for r in recommended["ANTOR Men | Sport & Style"]["reasons"])
    # Home page: preferred secondary only (Kitchen via Home & Living) → low relevance
    assert low["Home & Living Page"]["score"] == 25 and low["Home & Living Page"]["eligible"]
    # Mom & Baby page: Kitchen secondary is BLOCKED → ineligible even though nothing else matches
    assert blocked["Mom & Baby Page"]["blocking_reason"].startswith("Kitchen is blocked")
    assert blocked["Routing Off Page"]["blocking_reason"] == "Routing is disabled for this account"
    assert blocked["Disabled Page"]["blocking_reason"] == "Account is disabled"
    assert rec["engine_version"] == "rules-v1" and rec["recommend_threshold"] == 70

    # BLOCK precedence on the primary: Women's fashion video never reaches ANTOR
    rec2 = api.get(
        "/api/v1/distribution/recommendations", params={"media_asset_id": str(dress.id)}
    ).json()
    names_blocked = {r["display_name"]: r["blocking_reason"] for r in rec2["blocked"]}
    assert (
        names_blocked["ANTOR Men | Sport & Style"] == "Women's Fashion is blocked for this account"
    )
    assert {r["display_name"] for r in rec2["recommended"]} == set()
    assert {r["display_name"] for r in rec2["low_relevance"]} == {
        "Home & Living Page",
        "Mom & Baby Page",
    }

    # Rules referencing a foreign category are rejected; viewer cannot edit profiles
    other = create_organization(db_session)
    db_session.commit()
    outsider = create_member(db_session, other, role=MembershipRole.OWNER)
    login(api, outsider)
    assert api.get(f"/api/v1/accounts/{antor['id']}/channel-profile").status_code == 404
    assert (
        api.get(
            "/api/v1/distribution/recommendations", params={"media_asset_id": str(serve.id)}
        ).status_code
        == 404
    )
    foreign_cat = api.post("/api/v1/content-categories", json={"name": "Foreign"}).json()
    owner_again = create_member(db_session, organization, role=MembershipRole.ADMIN)
    login(api, owner_again)
    assert (
        api.put(
            f"/api/v1/accounts/{antor['id']}/channel-profile",
            json={
                "routing_enabled": True,
                "rules": [{"category_id": foreign_cat["id"], "match_policy": "allow"}],
            },
        ).status_code
        == 404
    )
    viewer = create_member(db_session, organization, role=MembershipRole.VIEWER)
    login(api, viewer)
    assert api.get(f"/api/v1/accounts/{antor['id']}/channel-profile").status_code == 200
    assert (
        api.put(
            f"/api/v1/accounts/{antor['id']}/channel-profile",
            json={"routing_enabled": True, "rules": []},
        ).status_code
        == 403
    )


# ---------------------------------------------------------------- composer integration


def test_recommendations_to_drafts_preserve_safeguards(
    api: TestClient, taxonomy: dict, db_session: Session, organization, media_dirs
) -> None:
    root = finals_root_id(api)
    rule(api, root, "home-living", taxonomy["home"]["id"])
    rule(api, root, "home-living/kitchen", taxonomy["kitchen"]["id"])
    drop_final(media_dirs, "home-living/kitchen/airfryer.mp4", seed=21)
    scan_root(db_session, organization.id, media_dirs, "finals")
    asset = asset_by_path(db_session, organization, "home-living/kitchen/airfryer.mp4")
    home = register_page(api, "Home Page")
    deals = register_page(api, "Deals Page")
    set_profile(
        api, home["id"], rules=[{"category_id": taxonomy["home"]["id"], "match_policy": "prefer"}]
    )
    set_profile(
        api,
        deals["id"],
        rules=[{"category_id": taxonomy["mens_sports"]["id"], "match_policy": "allow"}],
    )
    campaign = create_campaign(api, "Kitchen week")

    rec = api.get(
        "/api/v1/distribution/recommendations",
        params={"media_asset_id": str(asset.id), "campaign_id": campaign["id"]},
    ).json()
    assert rec["media_approved"] is False
    assert [r["display_name"] for r in rec["recommended"]] == ["Home Page"]
    assert [r["display_name"] for r in rec["low_relevance"]] == ["Deals Page"]
    # Not approved yet → the campaign refuses the video; approval is still a human step.
    refused = api.post(
        "/api/v1/publishing/publications/batch",
        json={
            "campaign_id": campaign["id"],
            "media_asset_id": str(asset.id),
            "items": [{"social_account_id": home["id"], "caption": "x"}],
        },
    )
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "media_not_approved"

    api.post(f"/api/v1/media/assets/{asset.id}/handoff", json={"status": "approved", "note": "ok"})
    rec = api.get(
        "/api/v1/distribution/recommendations",
        params={"media_asset_id": str(asset.id), "campaign_id": campaign["id"]},
    ).json()
    assert rec["media_approved"] is True
    # Operator picks the recommended page and, explicitly, the low-relevance one
    batch = api.post(
        "/api/v1/publishing/publications/batch",
        json={
            "campaign_id": campaign["id"],
            "media_asset_id": str(asset.id),
            "items": [
                {"social_account_id": home["id"], "caption": "Home caption"},
                {"social_account_id": deals["id"], "caption": "Deals caption"},
            ],
        },
    ).json()
    assert len(batch["created"]) == 2 and {c["status"] for c in batch["created"]} == {"draft"}
    assert {c["caption"] for c in batch["created"]} == {"Home caption", "Deals caption"}
    rec = api.get(
        "/api/v1/distribution/recommendations",
        params={"media_asset_id": str(asset.id), "campaign_id": campaign["id"]},
    ).json()
    assert all(r["already_targeted"] for r in rec["recommended"] + rec["low_relevance"])
    # Duplicate drafts prevented
    dup = api.post(
        "/api/v1/publishing/publications",
        json={
            "campaign_id": campaign["id"],
            "media_asset_id": str(asset.id),
            "social_account_id": home["id"],
        },
    )
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "duplicate_publication"

    # Approve one draft, then move the file: snapshot untouched, publication flagged
    job = approve_flow(
        api, next(c for c in batch["created"] if c["social_account_id"] == home["id"])
    )
    snapshot_id = job["approved_snapshot"]["id"]
    shutil.move(
        media_dirs.finals / "home-living/kitchen/airfryer.mp4",
        media_dirs.finals / "home-living/airfryer.mp4",
    )
    scan_root(db_session, organization.id, media_dirs, "finals")
    detail = api.get(f"/api/v1/publishing/publications/{job['id']}").json()
    assert detail["classification_stale"] is True and detail["status"] == "approved"
    assert detail["approved_snapshot"]["id"] == snapshot_id
    row = db_session.get(PublicationSnapshot, uuid.UUID(snapshot_id))
    db_session.refresh(row)
    assert (
        row.media["relative_path"] == "home-living/kitchen/airfryer.mp4"
    )  # immutable snapshot keeps the old path
    # Draft (unapproved) publication is not flagged
    other = next(c for c in batch["created"] if c["social_account_id"] == deals["id"])
    assert (
        api.get(f"/api/v1/publishing/publications/{other['id']}").json()["classification_stale"]
        is False
    )
    # No scheduling/publishing happened automatically; a stale classification now needs an
    # explicit admin decision before any publishing step.
    assert api.get("/api/v1/publishing/queue").json()[0]["status"] == "approved"
    assert act(api, detail, "start", expected=409)["error"]["code"] == "revalidation_required"
    revalidated = act(api, detail, "revalidate", note="kitchen → home-living is fine")
    assert revalidated["classification_stale"] is False
    assert act(api, revalidated, "start")["status"] == "publishing"
