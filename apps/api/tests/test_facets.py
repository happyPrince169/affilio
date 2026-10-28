"""Folder-driven multi-facet classification: path parsing, facet roots, scanner integration,
suggestions, approval workflow, rescans, moves, manual overrides, organization isolation.
Database tests need TEST_DATABASE_URL."""

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
from affilio.core.enums import CategoryFacet, CategorySuggestionStatus, MembershipRole
from affilio.db.session import dispose_engine
from affilio.modules.identity.models import AuditEvent
from affilio.modules.media import facets
from affilio.modules.media.models import CategorySuggestion, MediaAsset
from affilio.modules.taxonomy import service as taxonomy_service
from affilio.modules.taxonomy.schemas import CategoryCreate
from affilio.modules.taxonomy.service import normalize_name
from tests.auth_helpers import create_member, login, sign_in
from tests.media_helpers import create_organization, write_bytes
from tests.publishing_helpers import scan_root
from tests.test_content_routing import asset_by_path, cat, finals_root_id, rule

# ---------------------------------------------------------------- unit: parsing and matching


@pytest.mark.parametrize(
    ("relative", "structured", "legacy", "values"),
    [
        (
            "Hang/Mens-Fashion/Review/a.mp4",
            True,
            False,
            {
                "production_owner": "Hang",
                "product_category": "Mens-Fashion",
                "content_type": "Review",
            },
        ),
        (
            "Minh Anh/Womens-Fashion/Try-On/2026-11/b.mp4",  # deeper folders are ignored
            True,
            False,
            {
                "production_owner": "Minh Anh",
                "product_category": "Womens-Fashion",
                "content_type": "Try-On",
            },
        ),
        ("Hang/T9/c.mp4", False, True, {"production_owner": "Hang"}),
        ("Hang/2026-09/c.mp4", False, True, {"production_owner": "Hang"}),
        ("Hang/T10/Review/c.mp4", False, True, {"production_owner": "Hang"}),
        ("Hang/d.mp4", False, False, {"production_owner": "Hang"}),
        ("Hang/Mens-Fashion/e.mp4", False, False, {"production_owner": "Hang"}),
        ("root.mp4", False, False, {}),
        (".hidden/Mens-Fashion/Review/f.mp4", False, False, {}),
        ("Hang/---/Review/g.mp4", False, False, {}),
    ],
)
def test_parse_final_path(relative: str, structured: bool, legacy: bool, values: dict) -> None:
    parsed = facets.parse_final_path(relative)
    assert parsed.structured is structured and parsed.legacy is legacy
    assert {f.value: v for f, v in parsed.values.items()} == values
    assert (parsed.reason is None) is structured


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Mens-Fashion", "Men's Fashion"),
        ("mens_fashion", "MENS FASHION"),
        ("Try-On", "try on"),
        ("Minh Anh", "minh-anh"),
        ("Women\u2019s Fashion", "womens-fashion"),
    ],
)
def test_normalized_names_match(a: str, b: str) -> None:
    assert normalize_name(a) == normalize_name(b)


def test_normalized_names_keep_distinct_values_apart() -> None:
    assert normalize_name("Hao") != normalize_name("Hào")
    assert normalize_name("Mens-Fashion") != normalize_name("Womens-Fashion")
    assert normalize_name("...") == ""


def test_production_period_is_the_utc_month_of_first_ingestion() -> None:
    first = datetime(2026, 10, 31, 20, 30, tzinfo=UTC)
    assert facets.production_period_for(first) == "2026-10"
    # A local-time offset never changes the period: the UTC month is what is stored.
    assert facets.production_period_for(first.astimezone()) == "2026-10"


# ---------------------------------------------------------------- database fixtures


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


def enable_facets(client: TestClient) -> dict[str, dict]:
    response = client.post("/api/v1/content-categories/ensure-facets")
    assert response.status_code == 200, response.text
    return {r["facet"]: r for r in response.json()["roots"]}


@pytest.fixture
def facet_taxonomy(api: TestClient) -> dict[str, dict]:
    """Facet roots plus the initial values: owners Hang / Minh Anh, product categories Men's
    Fashion / Sports, content types Review / Couple. Hao, Womens-Fashion and Try-On are
    deliberately missing so the scanner has something to suggest."""
    roots = enable_facets(api)
    t: dict[str, dict] = {"roots": roots}
    t["hang"] = cat(api, "Hang", roots["production_owner"]["id"])
    t["minh_anh"] = cat(api, "Minh Anh", roots["production_owner"]["id"])
    t["mens_fashion"] = cat(
        api, "Men's Fashion", roots["product_category"]["id"], slug="mens-fashion"
    )
    t["sports"] = cat(api, "Sports", roots["product_category"]["id"])
    t["review"] = cat(api, "Review", roots["content_type"]["id"])
    t["couple"] = cat(api, "Couple", roots["content_type"]["id"])
    return t


def drop_final(media_dirs, relative: str, seed: int = 1) -> None:
    path = media_dirs.finals / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    write_bytes(path, 6_000, seed=seed)
    old = (datetime.now(UTC) - timedelta(hours=1)).timestamp()
    os.utime(path, (old, old))


def categories_of(asset: MediaAsset) -> dict[str, tuple[str, bool]]:
    """{category slug: (assignment_source, is_primary)}"""
    return {
        a.category.slug: (a.assignment_source.value, a.is_primary)
        for a in asset.category_assignments
    }


def suggestions(session: Session, organization) -> dict[tuple[str, str], CategorySuggestion]:
    session.expire_all()
    rows = session.scalars(
        select(CategorySuggestion).where(CategorySuggestion.organization_id == organization.id)
    )
    return {(s.facet.value, s.normalized_name): s for s in rows}


# ---------------------------------------------------------------- taxonomy: facet roots


@pytest.mark.database
def test_facet_roots_are_idempotent_and_children_inherit_the_facet(
    api: TestClient, db_session: Session, organization
) -> None:
    first = enable_facets(api)
    again = enable_facets(api)
    assert {k: v["id"] for k, v in first.items()} == {k: v["id"] for k, v in again.items()}
    assert set(first) == {"production_owner", "product_category", "content_type"}
    listing = api.get("/api/v1/content-categories").json()
    roots = [c for c in listing if c["facet"] and c["depth"] == 0]
    assert len(roots) == 3

    child = cat(api, "Hang", first["production_owner"]["id"])
    assert child["facet"] == "production_owner" and child["path"] == "Production owner / Hang"
    grandchild = cat(api, "Hang Studio", child["id"])
    assert grandchild["facet"] == "production_owner"

    # A child may not claim a different facet; a second root per facet is refused.
    mismatch = api.post(
        "/api/v1/content-categories",
        json={"name": "Oops", "parent_id": child["id"], "facet": "content_type"},
    )
    assert mismatch.status_code == 422 and mismatch.json()["error"]["code"] == (
        "category_facet_mismatch"
    )
    duplicate = api.post(
        "/api/v1/content-categories", json={"name": "Owners again", "facet": "production_owner"}
    )
    assert duplicate.status_code == 409

    # Moving a value under another facet's root re-labels its subtree.
    moved = api.patch(
        f"/api/v1/content-categories/{child['id']}",
        json={"parent_id": first["content_type"]["id"]},
    ).json()
    assert moved["facet"] == "content_type"
    assert api.get(f"/api/v1/content-categories/{grandchild['id']}").json()["facet"] == (
        "content_type"
    )

    # Facet roots cannot be archived; generic top-level categories still can.
    archived = api.patch(
        f"/api/v1/content-categories/{first['product_category']['id']}",
        json={"status": "archived"},
    )
    assert archived.status_code == 409
    generic = cat(api, "Generic")
    assert generic["facet"] is None
    assert (
        api.patch(
            f"/api/v1/content-categories/{generic['id']}", json={"status": "archived"}
        ).status_code
        == 200
    )

    # Another organization has no roots: facets stay disabled there.
    other = create_organization(db_session)
    db_session.commit()
    assert taxonomy_service.facets_enabled(db_session, other.id) is False
    assert taxonomy_service.facets_enabled(db_session, organization.id) is True


# ---------------------------------------------------------------- scanner integration


@pytest.mark.database
def test_structured_paths_classify_by_facet_and_suggest_unknown_values(
    api: TestClient, facet_taxonomy: dict, db_session: Session, organization, media_dirs
) -> None:
    t = facet_taxonomy
    drop_final(media_dirs, "Hang/Mens-Fashion/Review/known.mp4", seed=1)
    drop_final(media_dirs, "Hang/Womens-Fashion/Try-On/new_values.mp4", seed=2)
    drop_final(media_dirs, "Hang/Womens-Fashion/Try-On/new_values_2.mp4", seed=3)
    drop_final(media_dirs, "Hao/Sports/Couple/new_owner.mp4", seed=4)
    drop_final(media_dirs, "Hang/T9/legacy.mp4", seed=5)
    drop_final(media_dirs, "Hang/incomplete.mp4", seed=6)
    drop_final(media_dirs, "Hang/Mens-Fashion/no_type.mp4", seed=7)
    drop_final(media_dirs, "Hang/Streetwear/Review/broken_file.mp4", seed=8)  # probe fails
    (media_dirs.finals / "Hang/Accessories/Review").mkdir(parents=True)  # empty folder

    scan_root(db_session, organization.id, media_dirs, "finals")

    # All three facets known: product category is the derived primary, no fallback row.
    known = asset_by_path(db_session, organization, "Hang/Mens-Fashion/Review/known.mp4")
    assert categories_of(known) == {
        "mens-fashion": ("path", True),
        "hang": ("path", False),
        "review": ("path", False),
    }
    assert known.handoff_status == "pending_review" and known.classification_changed_at is None
    assert known.production_period == datetime.now(UTC).strftime("%Y-%m")

    # Unknown product category and content type: owner row + Uncategorized primary.
    unknown = asset_by_path(db_session, organization, "Hang/Womens-Fashion/Try-On/new_values.mp4")
    assert categories_of(unknown) == {"uncategorized": ("folder", True), "hang": ("path", False)}

    # Legacy and incomplete paths: owner only, Uncategorized primary (M3A.1 behaviour kept).
    for relative in ("Hang/T9/legacy.mp4", "Hang/incomplete.mp4", "Hang/Mens-Fashion/no_type.mp4"):
        asset = asset_by_path(db_session, organization, relative)
        assert categories_of(asset) == {
            "uncategorized": ("folder", True),
            "hang": ("path", False),
        }, relative

    # Suggestions: one per facet value, counted per file, never from unsupported files, legacy
    # or incomplete paths, or empty folders.
    rows = suggestions(db_session, organization)
    assert set(rows) == {
        ("product_category", "womens fashion"),
        ("content_type", "try on"),
        ("production_owner", "hao"),
    }
    womens = rows[("product_category", "womens fashion")]
    assert womens.status == CategorySuggestionStatus.PENDING
    assert womens.name == "Womens-Fashion" and womens.relative_folder_path == "Hang/Womens-Fashion"
    assert womens.seen_count == 2 and womens.example_relative_path.startswith(
        "Hang/Womens-Fashion/"
    )
    assert rows[("production_owner", "hao")].relative_folder_path == "Hao"
    broken = asset_by_path(db_session, organization, "Hang/Streetwear/Review/broken_file.mp4")
    assert broken.status == "unsupported"

    # Rescan: nothing changes, nothing is duplicated.
    before = {k: (v.id, v.seen_count, v.first_seen_at) for k, v in rows.items()}
    scan_root(db_session, organization.id, media_dirs, "finals")
    after = suggestions(db_session, organization)
    assert {k: (v.id, v.seen_count, v.first_seen_at) for k, v in after.items()} == before
    known = asset_by_path(db_session, organization, "Hang/Mens-Fashion/Review/known.mp4")
    assert len(known.category_assignments) == 3 and known.classification_changed_at is None

    # API surface: listing filters by status and carries the root key; facet on assignments.
    pending = api.get("/api/v1/media/category-suggestions", params={"status": "pending"}).json()
    assert {p["name"] for p in pending} == {"Womens-Fashion", "Try-On", "Hao"}
    assert all(p["root_key"] == "finals" for p in pending)
    detail = api.get(f"/api/v1/media/assets/{known.id}").json()
    assert {c["name"]: c["facet"] for c in detail["categories"]} == {
        "Men's Fashion": "product_category",
        "Hang": "production_owner",
        "Review": "content_type",
    }
    assert detail["production_period"] == known.production_period
    listing = api.get(
        "/api/v1/media/assets", params={"category_id": t["roots"]["product_category"]["id"]}
    ).json()
    assert {i["title"] for i in listing["items"]} == {"known", "new owner"}  # Sports is known
    by_period = api.get(
        "/api/v1/media/assets", params={"production_period": known.production_period}
    ).json()
    assert by_period["total"] == 8
    assert (
        api.get("/api/v1/media/assets", params={"production_period": "1999-01"}).json()["total"]
        == 0
    )
    assert api.get("/api/v1/media/assets", params={"production_period": "bad"}).status_code == 422


@pytest.mark.database
def test_folder_rules_keep_precedence_over_path_facets(
    api: TestClient, facet_taxonomy: dict, db_session: Session, organization, media_dirs
) -> None:
    generic = cat(api, "Campaign X")
    rule(api, finals_root_id(api), "Hang/Mens-Fashion", generic["id"])
    drop_final(media_dirs, "Hang/Mens-Fashion/Review/ruled.mp4", seed=11)
    scan_root(db_session, organization.id, media_dirs, "finals")
    asset = asset_by_path(db_session, organization, "Hang/Mens-Fashion/Review/ruled.mp4")
    assert categories_of(asset) == {
        "campaign-x": ("folder", True),
        "mens-fashion": ("path", False),
        "hang": ("path", False),
        "review": ("path", False),
    }


@pytest.mark.database
def test_legacy_behaviour_is_unchanged_while_facets_are_disabled(
    api: TestClient, db_session: Session, organization, media_dirs
) -> None:
    drop_final(media_dirs, "Hang/Mens-Fashion/Review/structured.mp4", seed=21)
    drop_final(media_dirs, "Hang/T9/legacy.mp4", seed=22)
    scan_root(db_session, organization.id, media_dirs, "finals")
    for relative in ("Hang/Mens-Fashion/Review/structured.mp4", "Hang/T9/legacy.mp4"):
        asset = asset_by_path(db_session, organization, relative)
        assert categories_of(asset) == {"uncategorized": ("folder", True)}
        assert asset.production_period is not None
    assert suggestions(db_session, organization) == {}


# ---------------------------------------------------------------- approval workflow


@pytest.mark.database
def test_approval_creates_the_category_and_reclassifies_existing_finals(
    api: TestClient, facet_taxonomy: dict, db_session: Session, organization, media_dirs
) -> None:
    drop_final(media_dirs, "Hang/Womens-Fashion/Try-On/one.mp4", seed=31)
    drop_final(media_dirs, "Minh Anh/Womens-Fashion/Review/two.mp4", seed=32)
    drop_final(media_dirs, "Hao/Sports/Couple/three.mp4", seed=33)
    scan_root(db_session, organization.id, media_dirs, "finals")
    one = asset_by_path(db_session, organization, "Hang/Womens-Fashion/Try-On/one.mp4")
    one_id, one_handoff = one.id, one.handoff_status
    reviewed = api.post(
        f"/api/v1/media/assets/{one_id}/handoff", json={"status": "approved", "note": "ok"}
    )
    assert reviewed.status_code == 200, reviewed.text
    pending = {
        s["name"]: s
        for s in api.get("/api/v1/media/category-suggestions", params={"status": "pending"}).json()
    }
    assert set(pending) == {"Womens-Fashion", "Try-On", "Hao"}

    # A name that would stop matching the folder is refused.
    bad = api.post(
        f"/api/v1/media/category-suggestions/{pending['Womens-Fashion']['id']}/approve",
        json={"name": "Ladies"},
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "suggestion_name_mismatch"

    approved = api.post(
        f"/api/v1/media/category-suggestions/{pending['Womens-Fashion']['id']}/approve",
        json={"name": "Women's Fashion"},
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["reclassified_assets"] == 2 and body["suggestion"]["status"] == "approved"
    category = api.get(f"/api/v1/content-categories/{body['category_id']}").json()
    assert category["name"] == "Women's Fashion" and category["slug"] == "women-s-fashion"
    assert category["facet"] == "product_category" and category["path"] == (
        "Product category / Women's Fashion"
    )

    # Existing finals under that folder gained the product primary; fallback removed; the
    # asset id and the handoff decision are untouched; the change is recorded.
    one = asset_by_path(db_session, organization, "Hang/Womens-Fashion/Try-On/one.mp4")
    assert one.id == one_id and one.handoff_status == "approved" and one_handoff == "pending_review"
    assert categories_of(one) == {"women-s-fashion": ("path", True), "hang": ("path", False)}
    assert one.classification_changed_at is not None
    two = asset_by_path(db_session, organization, "Minh Anh/Womens-Fashion/Review/two.mp4")
    assert categories_of(two) == {
        "women-s-fashion": ("path", True),
        "minh-anh": ("path", False),
        "review": ("path", False),
    }

    # Approving twice is a conflict; approving with the default name uses the folder name.
    assert (
        api.post(
            f"/api/v1/media/category-suggestions/{pending['Womens-Fashion']['id']}/approve",
            json={},
        ).status_code
        == 409
    )
    try_on = api.post(
        f"/api/v1/media/category-suggestions/{pending['Try-On']['id']}/approve", json={}
    ).json()
    assert api.get(f"/api/v1/content-categories/{try_on['category_id']}").json()["name"] == (
        "Try On"
    )
    one = asset_by_path(db_session, organization, "Hang/Womens-Fashion/Try-On/one.mp4")
    assert categories_of(one)["try-on"] == ("path", False)

    # Rejected values stay rejected across rescans and never resurface as a new row.
    rejected = api.post(
        f"/api/v1/media/category-suggestions/{pending['Hao']['id']}/reject",
        json={"note": "Hao is not a production owner"},
    )
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    drop_final(media_dirs, "Hao/Sports/Couple/four.mp4", seed=34)
    scan_root(db_session, organization.id, media_dirs, "finals")
    rows = suggestions(db_session, organization)
    hao = rows[("production_owner", "hao")]
    assert hao.status == CategorySuggestionStatus.REJECTED and hao.seen_count == 2
    assert len([s for s in rows if s[0] == "production_owner"]) == 1
    three = asset_by_path(db_session, organization, "Hao/Sports/Couple/three.mp4")
    assert "hao" not in categories_of(three) and categories_of(three)["sports"] == ("path", True)
    assert (
        api.post(f"/api/v1/media/category-suggestions/{hao.id}/reject", json={}).json()["status"]
        == "rejected"
    )

    # A category created by hand with a matching name is adopted instead of duplicated.
    drop_final(media_dirs, "Hang/Accessories/Review/five.mp4", seed=35)
    scan_root(db_session, organization.id, media_dirs, "finals")
    accessories_suggestion = suggestions(db_session, organization)[
        ("product_category", "accessories")
    ]
    manual = cat(api, "Accessories", facet_taxonomy["roots"]["product_category"]["id"])
    adopted = api.post(
        f"/api/v1/media/category-suggestions/{accessories_suggestion.id}/approve", json={}
    ).json()
    assert adopted["category_id"] == manual["id"] and adopted["reclassified_assets"] == 1
    assert (
        len([c for c in api.get("/api/v1/content-categories").json() if c["name"] == "Accessories"])
        == 1
    )

    # Audit trail
    events = db_session.execute(
        select(AuditEvent.action).where(AuditEvent.organization_id == organization.id)
    ).scalars()
    assert {"category_suggestion.approved", "category_suggestion.rejected"} <= set(events)


@pytest.mark.database
def test_manual_overrides_coexist_with_path_facets(
    api: TestClient, facet_taxonomy: dict, db_session: Session, organization, media_dirs
) -> None:
    t = facet_taxonomy
    drop_final(media_dirs, "Hang/Mens-Fashion/Review/combo.mp4", seed=41)
    scan_root(db_session, organization.id, media_dirs, "finals")
    asset = asset_by_path(db_session, organization, "Hang/Mens-Fashion/Review/combo.mp4")

    # Manual primary wins; manual secondary added; derived rows preserved.
    updated = api.put(
        f"/api/v1/media/assets/{asset.id}/categories",
        json={
            "primary_category_id": t["sports"]["id"],
            "secondary_category_ids": [t["couple"]["id"]],
        },
    ).json()
    cats = {c["slug"]: (c["assignment_source"], c["is_primary"]) for c in updated["categories"]}
    assert cats == {
        "sports": ("manual", True),
        "couple": ("manual", False),
        "mens-fashion": ("path", False),
        "hang": ("path", False),
        "review": ("path", False),
    }
    scan_root(db_session, organization.id, media_dirs, "finals")
    asset = asset_by_path(db_session, organization, "Hang/Mens-Fashion/Review/combo.mp4")
    assert categories_of(asset)["sports"] == ("manual", True)
    assert asset.classification_changed_at is not None  # the override itself is recorded

    # Handing primacy back returns it to the path's product category.
    reset = api.put(
        f"/api/v1/media/assets/{asset.id}/categories",
        json={"primary_category_id": None, "secondary_category_ids": []},
    ).json()
    cats = {c["slug"]: (c["assignment_source"], c["is_primary"]) for c in reset["categories"]}
    assert cats == {
        "mens-fashion": ("path", True),
        "hang": ("path", False),
        "review": ("path", False),
    }

    # A manual primary equal to a derived category keeps that derived row primary.
    same = api.put(
        f"/api/v1/media/assets/{asset.id}/categories",
        json={"primary_category_id": t["review"]["id"], "secondary_category_ids": []},
    ).json()
    cats = {c["slug"]: (c["assignment_source"], c["is_primary"]) for c in same["categories"]}
    assert cats["review"] == ("path", True) and cats["mens-fashion"] == ("path", False)
    scan_root(db_session, organization.id, media_dirs, "finals")
    asset = asset_by_path(db_session, organization, "Hang/Mens-Fashion/Review/combo.mp4")
    assert categories_of(asset)["review"] == ("path", True)


@pytest.mark.database
def test_moves_reclassify_but_keep_identity_and_production_period(
    api: TestClient, facet_taxonomy: dict, db_session: Session, organization, media_dirs
) -> None:
    drop_final(media_dirs, "Hang/Mens-Fashion/Review/moving.mp4", seed=51)
    scan_root(db_session, organization.id, media_dirs, "finals")
    asset = asset_by_path(db_session, organization, "Hang/Mens-Fashion/Review/moving.mp4")
    asset_id, period, first_seen = asset.id, asset.production_period, asset.first_seen_at
    # Pretend the asset was ingested months ago: the period must stay what was stamped then.
    asset.production_period = "2026-01"
    db_session.commit()

    src = media_dirs.finals / "Hang/Mens-Fashion/Review/moving.mp4"
    dst = media_dirs.finals / "Minh Anh/Sports/Couple/moving.mp4"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(src, dst)
    scan_root(db_session, organization.id, media_dirs, "finals")
    moved = asset_by_path(db_session, organization, "Minh Anh/Sports/Couple/moving.mp4")
    assert moved.id == asset_id and moved.first_seen_at == first_seen
    assert moved.production_period == "2026-01" and period is not None
    assert categories_of(moved) == {
        "sports": ("path", True),
        "minh-anh": ("path", False),
        "couple": ("path", False),
    }
    assert moved.classification_changed_at is not None


# ---------------------------------------------------------------- RBAC and isolation


@pytest.mark.database
def test_suggestions_are_organization_scoped_and_admin_gated(
    api: TestClient, facet_taxonomy: dict, db_session: Session, organization, media_dirs
) -> None:
    drop_final(media_dirs, "Hang/Womens-Fashion/Review/scoped.mp4", seed=61)
    scan_root(db_session, organization.id, media_dirs, "finals")
    suggestion = suggestions(db_session, organization)[("product_category", "womens fashion")]

    other = create_organization(db_session)
    db_session.commit()
    assert facets.list_suggestions(db_session, other.id) == []
    # Another organization's category with the same name never classifies our paths...
    taxonomy_service.ensure_facet_roots(db_session, other.id)
    other_root = taxonomy_service.facet_roots(db_session, other.id)[CategoryFacet.PRODUCT_CATEGORY]
    taxonomy_service.create_category(
        db_session, other.id, CategoryCreate(name="Womens-Fashion", parent_id=other_root.id)
    )
    db_session.commit()
    context = facets.FacetContext.load(db_session, organization.id)
    assert context.resolve(CategoryFacet.PRODUCT_CATEGORY, "Womens-Fashion") is None
    # ...and its suggestions cannot be decided through our organization.
    with pytest.raises(Exception, match="not found"):
        facets.approve_suggestion(db_session, other.id, suggestion.id)
    db_session.rollback()

    editor = create_member(db_session, organization, role=MembershipRole.EDITOR)
    login(api, editor)
    assert api.get("/api/v1/media/category-suggestions").status_code == 200  # any member may read
    assert (
        api.post(f"/api/v1/media/category-suggestions/{suggestion.id}/approve", json={}).status_code
        == 403
    )
    assert (
        api.post(f"/api/v1/media/category-suggestions/{suggestion.id}/reject", json={}).status_code
        == 403
    )
    assert api.post("/api/v1/content-categories/ensure-facets").status_code == 403
    assert (
        api.post(f"/api/v1/media/category-suggestions/{uuid.uuid4()}/approve", json={}).status_code
        == 403
    )
