"""Production tracker. Requires TEST_DATABASE_URL."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from affilio.app import create_app
from affilio.core.config import get_settings
from affilio.core.enums import EditingStatus, ProductionMediaRole, ProductionStatus, ScanTrigger
from affilio.core.errors import NotFoundError
from affilio.db.session import dispose_engine
from affilio.modules.media.models import MediaAsset
from affilio.modules.media.scanner import MediaScanner
from affilio.modules.media.service import enqueue_scan
from affilio.modules.production import service
from affilio.modules.production.errors import (
    DuplicateJobAssociationError,
    InvalidProductionTransitionError,
)
from affilio.modules.production.schemas import (
    JobMediaInput,
    JobProductInput,
    ProductionJobCreate,
    ProductionStatusChange,
)
from affilio.modules.products import service as products
from affilio.modules.products.schemas import ProductCreate
from tests.media_helpers import FakeProber, build_settings, create_organization, write_bytes

pytestmark = pytest.mark.database
THUMB = "/api/v1/media/assets"


def _asset(session: Session, organization, media_dirs, name: str = "clip.mp4") -> MediaAsset:
    write_bytes(media_dirs.originals / name, 3_000, seed=len(name))
    job = enqueue_scan(session, organization.id, media_dirs.registry, trigger=ScanTrigger.CLI)
    MediaScanner(session, media_dirs.registry, build_settings(media_dirs), prober=FakeProber()).run(
        job
    )
    job.status = "succeeded"
    session.flush()
    asset = session.scalar(
        select(MediaAsset)
        .where(MediaAsset.organization_id == organization.id)
        .order_by(MediaAsset.created_at.desc())
    )
    assert asset is not None
    return asset


def test_job_without_any_associations(db_session: Session, organization) -> None:
    job = service.create_job(
        db_session,
        organization.id,
        ProductionJobCreate(title="Studio day", staff_labels=[" Linh ", "Minh", "Linh"]),
    )
    assert job.job_number.startswith("PJ-") and job.status == ProductionStatus.PLANNED
    assert job.staff_labels == ["Linh", "Minh"]
    assert job.products == [] and job.media == []
    d = service.detail(job, thumbnail_url_prefix=THUMB)
    assert d.product_count == 0 and d.media_count == 0
    assert set(d.allowed_transitions) == {ProductionStatus.SCHEDULED, ProductionStatus.CANCELLED}


def test_status_machines(db_session: Session, organization) -> None:
    job = service.create_job(db_session, organization.id, ProductionJobCreate(title="Shoot"))
    with pytest.raises(InvalidProductionTransitionError):
        service.change_status(
            db_session,
            organization.id,
            job.id,
            ProductionStatusChange(status=ProductionStatus.FILMED),
        )
    with pytest.raises(InvalidProductionTransitionError):  # editing before filming
        service.change_status(
            db_session,
            organization.id,
            job.id,
            ProductionStatusChange(editing_status=EditingStatus.IN_PROGRESS),
        )
    for s in (ProductionStatus.SCHEDULED, ProductionStatus.FILMING, ProductionStatus.FILMED):
        job = service.change_status(
            db_session, organization.id, job.id, ProductionStatusChange(status=s)
        )
    job = service.change_status(
        db_session,
        organization.id,
        job.id,
        ProductionStatusChange(editing_status=EditingStatus.IN_PROGRESS, note="editor assigned"),
    )
    assert job.editing_status == EditingStatus.IN_PROGRESS and "editor assigned" in (
        job.notes or ""
    )
    with pytest.raises(InvalidProductionTransitionError):  # in_progress → done skips review
        service.change_status(
            db_session,
            organization.id,
            job.id,
            ProductionStatusChange(editing_status=EditingStatus.DONE),
        )
    job = service.change_status(
        db_session,
        organization.id,
        job.id,
        ProductionStatusChange(editing_status=EditingStatus.REVIEW),
    )
    job = service.change_status(
        db_session,
        organization.id,
        job.id,
        ProductionStatusChange(editing_status=EditingStatus.DONE),
    )
    assert job.editing_status == EditingStatus.DONE


def test_optional_product_and_media_associations(
    db_session: Session, organization, media_dirs
) -> None:
    product = products.create_product(db_session, organization.id, ProductCreate(name="Blender"))
    second = products.create_product(db_session, organization.id, ProductCreate(name="Mat"))
    asset = _asset(db_session, organization, media_dirs)
    job = service.create_job(
        db_session,
        organization.id,
        ProductionJobCreate(title="Multi-product shoot", product_ids=[product.id, product.id]),
    )
    assert [p.product.name for p in job.products] == ["Blender"]  # de-duplicated

    job = service.add_product(
        db_session, organization.id, job.id, JobProductInput(product_id=second.id, quantity_used=2)
    )
    assert {p.product.name for p in job.products} == {"Blender", "Mat"}
    with pytest.raises(DuplicateJobAssociationError):
        service.add_product(
            db_session, organization.id, job.id, JobProductInput(product_id=second.id)
        )

    job = service.add_media(
        db_session,
        organization.id,
        job.id,
        JobMediaInput(media_asset_id=asset.id, role=ProductionMediaRole.FOOTAGE),
    )
    with pytest.raises(DuplicateJobAssociationError):
        service.add_media(
            db_session, organization.id, job.id, JobMediaInput(media_asset_id=asset.id)
        )
    detail = service.detail(job, thumbnail_url_prefix=THUMB)
    assert (
        detail.media[0].asset.id == asset.id and detail.media_count == 1 and detail.final_count == 0
    )

    # The same product/asset can be on another job too (many-to-many), and detaching is clean.
    other_job = service.create_job(
        db_session, organization.id, ProductionJobCreate(title="Re-shoot", product_ids=[product.id])
    )
    service.add_media(
        db_session,
        organization.id,
        other_job.id,
        JobMediaInput(media_asset_id=asset.id, role=ProductionMediaRole.REFERENCE),
    )
    job = service.remove_media(db_session, organization.id, job.id, asset.id)
    assert job.media == []
    with pytest.raises(NotFoundError):
        service.remove_media(db_session, organization.id, job.id, asset.id)
    job = service.remove_product(db_session, organization.id, job.id, second.id)
    assert [p.product.name for p in job.products] == ["Blender"]
    # The asset row itself never gained a job reference.
    assert not hasattr(asset, "production_job_id")
    assert (
        service.list_jobs(
            db_session, organization.id, service.JobFilters(media_asset_id=asset.id)
        ).total
        == 1
    )
    assert (
        service.list_jobs(
            db_session, organization.id, service.JobFilters(product_id=product.id)
        ).total
        == 2
    )


def test_cross_organization(db_session: Session, organization, media_dirs) -> None:
    asset = _asset(db_session, organization, media_dirs)
    product = products.create_product(db_session, organization.id, ProductCreate(name="Blender"))
    job = service.create_job(db_session, organization.id, ProductionJobCreate(title="Shoot"))
    other = create_organization(db_session)
    foreign_job = service.create_job(db_session, other.id, ProductionJobCreate(title="Other org"))
    with pytest.raises(NotFoundError):
        service.get_job(db_session, other.id, job.id)
    with pytest.raises(NotFoundError):  # foreign asset
        service.add_media(
            db_session, other.id, foreign_job.id, JobMediaInput(media_asset_id=asset.id)
        )
    with pytest.raises(NotFoundError):  # foreign product
        service.add_product(
            db_session, other.id, foreign_job.id, JobProductInput(product_id=product.id)
        )
    with pytest.raises(NotFoundError):
        service.create_job(
            db_session, other.id, ProductionJobCreate(title="x", product_ids=[product.id])
        )


def test_stats_upcoming_and_overdue(db_session: Session, organization) -> None:
    today = date.today()  # noqa: DTZ011
    service.create_job(
        db_session,
        organization.id,
        ProductionJobCreate(title="Soon", planned_filming_date=today + timedelta(days=2)),
    )
    service.create_job(
        db_session,
        organization.id,
        ProductionJobCreate(title="Late", deadline=today - timedelta(days=1)),
    )
    stats = service.stats(db_session, organization.id)
    assert stats.upcoming == 1 and stats.overdue == 1 and stats.by_status["planned"] == 2


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, media_dirs, organization, schema) -> Iterator[TestClient]:
    for key, value in media_dirs.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DEFAULT_ORGANIZATION_SLUG", organization.slug)
    monkeypatch.delenv("MEDIA_ADMIN_TOKEN", raising=False)
    get_settings.cache_clear()
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        yield client
    dispose_engine()
    get_settings.cache_clear()


def test_production_api(api: TestClient) -> None:
    product_id = api.post("/api/v1/products", json={"name": "Lamp"}).json()["id"]
    created = api.post(
        "/api/v1/production/jobs",
        json={
            "title": "Lamp unboxing",
            "planned_filming_date": "2026-11-02",
            "staff_labels": ["Linh", "Cam op"],
            "product_ids": [product_id],
        },
    )
    assert created.status_code == 201, created.text
    job = created.json()
    assert (
        job["job_number"].startswith("PJ-2026-")
        and job["product_count"] == 1
        and job["media_count"] == 0
    )
    job_id = job["id"]

    assert (
        api.post(f"/api/v1/production/jobs/{job_id}/status", json={"status": "filmed"}).status_code
        == 409
    )
    assert api.post(f"/api/v1/production/jobs/{job_id}/status", json={}).status_code == 409
    assert (
        api.post(f"/api/v1/production/jobs/{job_id}/status", json={"status": "scheduled"}).json()[
            "status"
        ]
        == "scheduled"
    )
    assert (
        api.patch(
            f"/api/v1/production/jobs/{job_id}",
            json={"deadline": "2026-11-10", "notes": "bring tripod"},
        ).json()["deadline"]
        == "2026-11-10"
    )

    dup = api.post(f"/api/v1/production/jobs/{job_id}/products", json={"product_id": product_id})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "duplicate_job_association"
    missing_asset = api.post(
        f"/api/v1/production/jobs/{job_id}/media", json={"media_asset_id": str(uuid.uuid4())}
    )
    assert missing_asset.status_code == 404

    board = api.get("/api/v1/production/jobs", params={"status": "scheduled"}).json()
    assert board["total"] == 1 and board["items"][0]["staff_labels"] == ["Linh", "Cam op"]
    assert api.get("/api/v1/production/stats").json()["by_status"]["scheduled"] == 1
    assert api.get(f"/api/v1/production/jobs/{uuid.uuid4()}").status_code == 404
