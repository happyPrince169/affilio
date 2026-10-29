"""Production reporting (upload track U6): metric definitions, revision chains and unique
approved work units, historical finals without attribution, period filtering, admin-only
access, organization isolation, pagination and CSV escaping. Needs TEST_DATABASE_URL."""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from affilio.app import create_app
from affilio.core.config import get_settings
from affilio.core.enums import (
    CategoryFacet,
    HandoffStatus,
    IngestSource,
    MembershipRole,
    UploadStatus,
)
from affilio.db.session import dispose_engine
from affilio.modules.media import reporting
from affilio.modules.media.models import MediaAsset, MediaUpload
from affilio.modules.taxonomy import service as taxonomy_service
from affilio.modules.taxonomy.schemas import CategoryCreate
from tests.auth_helpers import create_member, login, sign_in
from tests.media_helpers import create_organization
from tests.publishing_helpers import scan_root
from tests.test_media_ingest import drop as drop_final

pytestmark = pytest.mark.database

SUMMARY = "/api/v1/analytics/production/summary"
ROWS = "/api/v1/analytics/production/rows"
CSV = "/api/v1/analytics/production/rows.csv"
PERIODS = "/api/v1/analytics/production/periods"


@pytest.fixture
def api(
    monkeypatch: pytest.MonkeyPatch, media_dirs, organization, schema, db_session: Session
) -> Iterator[TestClient]:
    for key, value in media_dirs.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DEFAULT_ORGANIZATION_SLUG", organization.slug)
    monkeypatch.delenv("MEDIA_ADMIN_TOKEN", raising=False)
    get_settings.cache_clear()
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        yield client
    dispose_engine()
    get_settings.cache_clear()


@pytest.fixture
def world(api: TestClient, db_session: Session, organization, media_dirs) -> dict:
    """Admin signed in; facets with owners Hang/Hao, product Men's Fashion, type Review; two
    editors; a historical final `Hang/T9/legacy.mp4` indexed by a scan (owner from path,
    no uploader)."""
    admin = sign_in(api, db_session, organization, role=MembershipRole.ADMIN)
    roots = taxonomy_service.ensure_facet_roots(db_session, organization.id)
    w: dict = {"admin": admin}
    for key, facet, name, slug in (
        ("hang", CategoryFacet.PRODUCTION_OWNER, "Hang", "hang"),
        ("hao", CategoryFacet.PRODUCTION_OWNER, "Hao", "hao"),
        ("mens", CategoryFacet.PRODUCT_CATEGORY, "Men's Fashion", "mens-fashion"),
        ("review", CategoryFacet.CONTENT_TYPE, "Review", "review"),
    ):
        w[key] = taxonomy_service.create_category(
            db_session,
            organization.id,
            CategoryCreate(name=name, slug=slug, parent_id=roots[facet].id),
        )
    db_session.commit()
    w["editor"] = create_member(db_session, organization, role=MembershipRole.EDITOR)
    w["editor2"] = create_member(db_session, organization, role=MembershipRole.EDITOR)
    drop_final(media_dirs, "Hang/T9/legacy.mp4", seed=1)
    old = (datetime.now(UTC) - timedelta(hours=1)).timestamp()
    import os

    os.utime(media_dirs.finals / "Hang/T9/legacy.mp4", (old, old))
    scan_root(db_session, organization.id, media_dirs, "finals")
    w["legacy"] = db_session.scalar(
        __import__("sqlalchemy").select(MediaAsset).where(MediaAsset.asset_kind == "final")
    )
    return w


def uploaded_final(
    db_session: Session,
    organization,
    w: dict,
    *,
    name: str,
    uploader,
    submitted_for=None,
    owner="hang",
    period: str | None = None,
    status: HandoffStatus = HandoffStatus.PENDING_REVIEW,
    reviewed_at: datetime | None = None,
    note: str | None = None,
    revision_of: MediaAsset | None = None,
    seen_at: datetime | None = None,
) -> MediaAsset:
    """A completed upload and its registered asset, created directly (the U4 suite covers the
    pipeline; here only the resulting rows matter)."""
    now = seen_at or datetime.now(UTC)
    asset = MediaAsset(
        id=uuid.uuid4(),
        organization_id=organization.id,
        title=name.rsplit(".", 1)[0],
        status="active",
        asset_kind="final",
        handoff_status=status,
        handoff_reviewed_at=reviewed_at,
        handoff_reviewed_by="reviewer@test" if reviewed_at else None,
        handoff_note=note,
        first_seen_at=now,
        last_seen_at=now,
        production_period=period or now.strftime("%Y-%m"),
        ingest_source=IngestSource.UPLOAD,
        uploaded_by_user_id=uploader.id,
        revision_of_asset_id=revision_of.id if revision_of else None,
    )
    db_session.add(asset)
    db_session.flush()
    upload = MediaUpload(
        id=uuid.uuid4(),
        organization_id=organization.id,
        uploaded_by_user_id=uploader.id,
        submitted_for_user_id=submitted_for.id if submitted_for else None,
        production_owner_category_id=w[owner].id,
        production_owner_name=w[owner].name,
        product_category_id=w["mens"].id,
        content_type_id=w["review"].id,
        replaces_asset_id=revision_of.id if revision_of else None,
        original_filename=name,
        safe_filename=name,
        expected_size=10,
        chunk_bytes=8,
        committed_offset=10,
        staging_name=uuid.uuid4().hex,
        status=UploadStatus.COMPLETED,
        verified_sha256=uuid.uuid4().hex * 2,
        final_relative_path=f"Hang/Men's Fashion/Review/{name}",
        media_asset_id=asset.id,
        created_at=now,
        completed_at=now + timedelta(minutes=1),
        expires_at=now + timedelta(days=2),
    )
    db_session.add(upload)
    db_session.commit()
    return asset


# ---------------------------------------------------------------- metrics


def test_historical_finals_report_unknown_attribution_and_ingestion_period(
    api: TestClient, world: dict, db_session: Session, organization
) -> None:
    rows = reporting.load_rows(db_session, organization.id)
    assert len(rows) == 1
    legacy = rows[0]
    assert legacy.ingest_source == IngestSource.SCAN
    assert (
        legacy.owner is not None and legacy.owner.name == "Hang" and legacy.owner_source == "path"
    )
    assert legacy.uploader is None and legacy.attributed_editor is None
    assert legacy.production_period == datetime.now(UTC).strftime("%Y-%m")
    assert legacy.period_source == "ingestion"
    assert legacy.file_name == "legacy.mp4"
    summary = api.get(SUMMARY).json()
    assert summary["totals"] == {
        "key": "all",
        "label": "All",
        "files": 1,
        "submissions": 0,
        "historical": 1,
        "pending": 1,
        "approved": 0,
        "rejected": 0,
        "corrected": 0,
        "approved_units": 0,
    }
    assert summary["unknown_editor"] == 1 and summary["unknown_owner"] == 0
    assert summary["by_editor"][0]["label"] == "Unknown editor (historical)"
    assert summary["period_source"] == "ingestion"


def test_revision_chains_count_one_approved_unit(
    api: TestClient, world: dict, db_session: Session, organization
) -> None:
    editor = world["editor"]
    t0 = datetime(2026, 11, 3, 10, tzinfo=UTC)
    original = uploaded_final(
        db_session,
        organization,
        world,
        name="take1.mp4",
        uploader=editor,
        status=HandoffStatus.REJECTED,
        reviewed_at=t0 + timedelta(hours=1),
        note="colour",
        seen_at=t0,
    )
    second = uploaded_final(
        db_session,
        organization,
        world,
        name="take2.mp4",
        uploader=editor,
        status=HandoffStatus.REJECTED,
        reviewed_at=t0 + timedelta(hours=3),
        revision_of=original,
        seen_at=t0 + timedelta(hours=2),
    )
    uploaded_final(
        db_session,
        organization,
        world,
        name="take3.mp4",
        uploader=editor,
        status=HandoffStatus.APPROVED,
        reviewed_at=t0 + timedelta(hours=5),
        revision_of=second,
        seen_at=t0 + timedelta(hours=4),
    )
    # An unrelated approved video of the same owner and categories is its own unit.
    solo = uploaded_final(
        db_session,
        organization,
        world,
        name="solo.mp4",
        uploader=editor,
        status=HandoffStatus.APPROVED,
        reviewed_at=t0 + timedelta(hours=6),
        seen_at=t0,
    )
    # A chain still pending after a rejection counts no unit.
    pending_root = uploaded_final(
        db_session,
        organization,
        world,
        name="p1.mp4",
        uploader=editor,
        status=HandoffStatus.REJECTED,
        reviewed_at=t0,
        seen_at=t0,
    )
    uploaded_final(
        db_session,
        organization,
        world,
        name="p2.mp4",
        uploader=editor,
        revision_of=pending_root,
        seen_at=t0,
    )

    rows = {r.title: r for r in reporting.load_rows(db_session, organization.id)}
    assert rows["take3"].work_root_id == original.id and rows["take3"].revision_index == 2
    assert rows["take3"].chain_length == 3 and rows["take3"].is_latest_in_chain
    assert (
        rows["take3"].approved_unit
        and not rows["take1"].approved_unit
        and not rows["take2"].approved_unit
    )
    assert rows["solo"].approved_unit and rows["solo"].work_root_id == solo.id
    assert rows["take1"].review_note == "colour" and rows["take1"].reviewed_by == "reviewer@test"
    assert not rows["p2"].approved_unit and rows["p2"].work_root_id == pending_root.id

    summary = api.get(SUMMARY, params={"period": "2026-11"}).json()
    assert summary["totals"] == {
        "key": "all",
        "label": "All",
        "files": 6,
        "submissions": 6,
        "historical": 0,
        "pending": 1,
        "approved": 2,
        "rejected": 3,
        "corrected": 3,
        "approved_units": 2,
    }
    assert summary["by_status"] == {"pending_review": 1, "approved": 2, "rejected": 3}
    assert [b["label"] for b in summary["by_owner"]] == ["Hang"]
    assert summary["by_editor"][0]["approved_units"] == 2
    assert summary["unknown_editor"] == 0
    # Lineage is visible per row.
    page = api.get(ROWS, params={"period": "2026-11", "page_size": 10}).json()
    assert page["total"] == 6
    by_title = {r["title"]: r for r in page["items"]}
    assert by_title["take2"]["revision_of_asset_id"] == str(original.id)
    assert by_title["take3"]["approved_unit"] is True and by_title["take3"]["chain_length"] == 3
    assert by_title["take1"]["is_latest_in_chain"] is False
    # The historical legacy final sits in the current month only.
    this_month = api.get(SUMMARY, params={"period": datetime.now(UTC).strftime("%Y-%m")}).json()
    assert this_month["totals"]["historical"] == 1 and this_month["totals"]["files"] == 1
    assert [p["period"] for p in api.get(PERIODS).json()][:2] == sorted(
        {datetime.now(UTC).strftime("%Y-%m"), "2026-11"}, reverse=True
    )


def test_multiple_approvals_in_a_chain_count_once_on_the_latest_review(
    api: TestClient, world: dict, db_session: Session, organization
) -> None:
    editor = world["editor"]
    t0 = datetime(2026, 11, 5, tzinfo=UTC)
    first = uploaded_final(
        db_session,
        organization,
        world,
        name="a1.mp4",
        uploader=editor,
        status=HandoffStatus.APPROVED,
        reviewed_at=t0 + timedelta(hours=1),
        seen_at=t0,
    )
    uploaded_final(
        db_session,
        organization,
        world,
        name="a2.mp4",
        uploader=editor,
        status=HandoffStatus.APPROVED,
        reviewed_at=t0 + timedelta(hours=9),
        revision_of=first,
        seen_at=t0 + timedelta(hours=8),
    )
    rows = {r.title: r for r in reporting.load_rows(db_session, organization.id)}
    assert rows["a2"].approved_unit and not rows["a1"].approved_unit
    summary = api.get(SUMMARY, params={"period": "2026-11"}).json()
    assert summary["totals"]["approved"] == 2 and summary["totals"]["approved_units"] == 1


def test_attribution_filters_and_on_behalf_uploads(
    api: TestClient, world: dict, db_session: Session, organization
) -> None:
    admin, editor, editor2 = world["admin"], world["editor"], world["editor2"]
    uploaded_final(
        db_session,
        organization,
        world,
        name="e1.mp4",
        uploader=editor,
        owner="hang",
        period="2026-11",
    )
    uploaded_final(
        db_session,
        organization,
        world,
        name="e2.mp4",
        uploader=editor2,
        owner="hao",
        period="2026-11",
    )
    on_behalf = uploaded_final(
        db_session,
        organization,
        world,
        name="admin-for-editor.mp4",
        uploader=admin,
        submitted_for=editor,
        owner="hao",
        period="2026-11",
    )
    rows = {r.title: r for r in reporting.load_rows(db_session, organization.id)}
    assert rows["admin-for-editor"].uploader.id == admin.id
    assert rows["admin-for-editor"].attributed_editor.id == editor.id
    assert (
        rows["admin-for-editor"].owner.name == "Hao"
        and rows["admin-for-editor"].owner_source == "upload"
    )

    by_editor = {
        b["key"]: b for b in api.get(SUMMARY, params={"period": "2026-11"}).json()["by_editor"]
    }
    assert by_editor[str(editor.id)]["files"] == 2 and by_editor[str(editor2.id)]["files"] == 1
    assert str(admin.id) not in by_editor  # the admin delivered, the editor is attributed
    by_owner = {
        b["label"]: b for b in api.get(SUMMARY, params={"period": "2026-11"}).json()["by_owner"]
    }
    assert by_owner["Hang"]["files"] == 1 and by_owner["Hao"]["files"] == 2

    only_editor = api.get(ROWS, params={"editor_id": str(editor.id)}).json()
    assert {r["title"] for r in only_editor["items"]} == {"e1", "admin-for-editor"}
    assert api.get(ROWS, params={"owner_id": str(world["hao"].id)}).json()["total"] == 2
    assert (
        api.get(ROWS, params={"attribution": "historical"}).json()["items"][0]["title"] == "legacy"
    )
    assert api.get(ROWS, params={"unknown_editor": "true"}).json()["total"] == 1
    assert (
        api.get(ROWS, params={"status": "pending_review", "period": "2026-11"}).json()["total"] == 3
    )
    assert api.get(ROWS, params={"period": "2026-1"}).status_code == 422
    assert on_behalf.ingest_source == IngestSource.UPLOAD


def test_pagination_and_csv_escaping(
    api: TestClient, world: dict, db_session: Session, organization
) -> None:
    editor = world["editor"]
    for n in range(5):
        uploaded_final(
            db_session,
            organization,
            world,
            name=f"v{n}.mp4",
            uploader=editor,
            period="2026-12",
            status=HandoffStatus.REJECTED,
            reviewed_at=datetime(2026, 12, 1, n, tzinfo=UTC),
            note='needs "quotes", commas\nand a newline' if n == 0 else "=SUM(1)",
            seen_at=datetime(2026, 12, 1, n, tzinfo=UTC),
        )
    first = api.get(ROWS, params={"period": "2026-12", "page_size": 2, "page": 1}).json()
    second = api.get(ROWS, params={"period": "2026-12", "page_size": 2, "page": 3}).json()
    assert first["total"] == 5 and len(first["items"]) == 2 and len(second["items"]) == 1
    assert first["items"][0]["title"] == "v4"  # newest first
    assert api.get(ROWS, params={"page_size": 500}).status_code == 422

    response = api.get(CSV, params={"period": "2026-12"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert 'filename="production-2026-12.csv"' in response.headers["content-disposition"]
    text = response.text
    assert text.startswith("﻿")
    parsed = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    assert parsed[0] == reporting.CSV_COLUMNS
    assert len(parsed) == 6
    notes = {row[1]: row[reporting.CSV_COLUMNS.index("review_note")] for row in parsed[1:]}
    assert notes["v0"] == 'needs "quotes", commas\nand a newline'
    assert notes["v1"] == "'=SUM(1)"  # formula injection neutralised
    assert all(row[reporting.CSV_COLUMNS.index("attribution")] == "upload" for row in parsed[1:])


# ---------------------------------------------------------------- access


def test_reporting_is_admin_only_and_organization_scoped(
    api: TestClient, world: dict, db_session: Session, organization
) -> None:
    uploaded_final(db_session, organization, world, name="x.mp4", uploader=world["editor"])
    login(api, world["editor"])
    for url in (SUMMARY, ROWS, CSV, PERIODS):
        assert api.get(url).status_code == 403, url
    other = create_organization(db_session)
    db_session.commit()
    stranger = create_member(db_session, other, role=MembershipRole.ADMIN)
    login(api, stranger, organization_slug=other.slug)
    assert api.get(SUMMARY).json()["totals"]["files"] == 0
    assert api.get(ROWS).json()["total"] == 0
    assert api.get(PERIODS).json() == []
    assert api.get(CSV).text.count("\n") == 1  # header only
    # Reporting never writes: the reviewed asset is untouched.
    db_session.expire_all()
    asset = db_session.scalar(
        __import__("sqlalchemy").select(MediaAsset).where(MediaAsset.title == "x")
    )
    assert asset.handoff_status == HandoffStatus.PENDING_REVIEW
