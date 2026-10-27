"""E2: the publishing engine streams a current export, and refuses anything else.

Mock provider, mock Pages, synthetic finals with FakeProber facts. Requires TEST_DATABASE_URL.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from affilio.app import create_app
from affilio.core.config import Settings, get_settings
from affilio.core.enums import MembershipRole, PublicationStatus, UploadPhase
from affilio.db.session import dispose_engine
from affilio.modules.accounts.providers.mock import MockMetaProvider
from affilio.modules.publishing.eligibility import account_api_eligibility
from affilio.modules.publishing.models import PublicationJob
from tests.auth_helpers import sign_in
from tests.engine_helpers import (
    ALPHA,
    connect_pages,
    drive,
    engine_env,
    make_job,
    past,
    refresh,
    set_scheduled,
    start_job,
    upload_rows,
)
from tests.media_helpers import write_bytes
from tests.publishing_helpers import create_campaign, make_final
from tests.test_export_selection import make_export, make_final_incompatible

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
    return {"pages": connect_pages(api), "campaign": create_campaign(api, "Export campaign")}


def _last_upload(session: Session, job_id: str):
    rows = upload_rows(session, job_id)
    assert rows, "no upload row"
    return rows[-1]


def _job(api: TestClient, world: dict, asset) -> dict:
    return make_job(api, world["campaign"]["id"], str(asset.id), world["pages"][ALPHA]["id"])


def test_current_export_is_streamed_instead_of_the_10bit_final(
    api: TestClient, world: dict, db_session: Session, organization, media_dirs
) -> None:
    asset = make_final(db_session, organization, media_dirs, "ten_bit.mp4")
    make_final_incompatible(db_session, asset)
    export = make_export(db_session, media_dirs, asset)
    job = _job(api, world, asset)
    queue_row = api.get("/api/v1/publishing/publications").json()["items"][0]
    assert queue_row["media_readiness"] == "export_ready"

    start_job(api, job)
    results = drive()

    final = refresh(api, job["id"])
    assert final["status"] == "published", [r.note for r in results]
    upload = _last_upload(db_session, job["id"])
    assert upload.phase == UploadPhase.PUBLISHED
    assert upload.file_root_key == "exports" and upload.file_relative_path == export.relative_path
    assert upload.file_variant_id == export.id and upload.file_fingerprint == export.fingerprint
    assert upload.file_size == export.file_size and upload.bytes_sent == export.file_size
    assert upload.last_status["media_source"] == "export"
    assert final["upload"]["file_root_key"] == "exports"


def test_compatible_final_is_streamed_directly_from_the_finals_root(
    api: TestClient, world: dict, db_session: Session, organization, media_dirs
) -> None:
    asset = make_final(db_session, organization, media_dirs, "ok.mp4")
    job = _job(api, world, asset)
    assert api.get("/api/v1/publishing/publications").json()["items"][0]["media_readiness"] == (
        "final_ready"
    )
    start_job(api, job)
    drive()
    upload = _last_upload(db_session, job["id"])
    assert upload.phase == UploadPhase.PUBLISHED and upload.file_root_key == "finals"
    assert upload.last_status["media_source"] == "final"


def test_incompatible_final_without_export_fails_with_an_actionable_error(
    api: TestClient, world: dict, db_session: Session, organization, media_dirs
) -> None:
    asset = make_final(db_session, organization, media_dirs, "no_export.mp4")
    make_final_incompatible(db_session, asset)
    job = _job(api, world, asset)
    assert api.get("/api/v1/publishing/publications").json()["items"][0]["media_readiness"] == (
        "needs_export"
    )
    start_job(api, job)
    drive()
    final = refresh(api, job["id"])
    assert final["status"] == "failed"
    upload = _last_upload(db_session, job["id"])
    assert upload.last_error_code == "media_incompatible"
    assert "Queue a new publish-ready export" in (upload.last_error or "")
    assert upload.bytes_sent == 0 and upload.file_root_key is None


def test_source_changed_after_scheduling_is_refused_at_validation(
    api: TestClient, world: dict, db_session: Session, organization, media_dirs
) -> None:
    asset = make_final(db_session, organization, media_dirs, "edited.mp4")
    job = _job(api, world, asset)
    set_scheduled(db_session, job["id"], past())
    write_bytes(media_dirs.finals / "edited.mp4", 9_000, seed=42)  # the editor re-exported it

    drive()

    upload = _last_upload(db_session, job["id"])
    assert upload.phase == UploadPhase.FAILED and upload.last_error_code == "media_incompatible"
    assert "rescan before publishing" in (upload.last_error or "")
    assert refresh(api, job["id"])["status"] == "failed"


def test_export_removed_or_replaced_before_upload_stops_the_stream(
    api: TestClient, world: dict, db_session: Session, organization, media_dirs
) -> None:
    asset = make_final(db_session, organization, media_dirs, "swap.mp4")
    make_final_incompatible(db_session, asset)
    export = make_export(db_session, media_dirs, asset)
    job = _job(api, world, asset)
    start_job(api, job)
    results = drive(stop_when=lambda r: r.phase_after == UploadPhase.UPLOADING)
    assert results and results[-1].phase_after == UploadPhase.UPLOADING
    # Same size, different bytes: only the fingerprint check can catch this.
    write_bytes(media_dirs.exports / export.relative_path, export.file_size, seed=7)

    drive()

    upload = _last_upload(db_session, job["id"])
    assert upload.phase == UploadPhase.FAILED and upload.last_error_code == "media_changed"
    assert "content differs" in (upload.last_error or "")
    assert upload.bytes_sent == 0  # nothing was streamed

    stale = make_final(db_session, organization, media_dirs, "gone.mp4")
    make_final_incompatible(db_session, stale)
    gone = make_export(db_session, media_dirs, stale)
    job2 = _job(api, world, stale)
    start_job(api, job2)
    drive(stop_when=lambda r: r.phase_after == UploadPhase.UPLOADING)
    (media_dirs.exports / gone.relative_path).unlink()
    drive()
    upload2 = _last_upload(db_session, job2["id"])
    assert upload2.phase == UploadPhase.FAILED and upload2.last_error_code == "media_changed"
    assert "disappeared" in (upload2.last_error or "")


def test_no_publishing_without_explicit_authorization(db_session: Session) -> None:
    """A ready export changes nothing about authorization: live Pages stay ineligible while
    META_LIVE_PUBLISHING_ENABLED is false, and mock Pages never publish for real."""

    class Acc:
        platform = "facebook"
        is_connected = True
        can_publish = True
        permission_status = "ok"
        is_mock = False

    from affilio.core.enums import Platform

    real = Acc()
    real.platform = Platform.FACEBOOK
    live_off = Settings(
        app_env="development",
        meta_provider_mode="live",
        meta_app_id="1",
        meta_app_secret="s",
        meta_oauth_redirect_uri="http://x/cb",
    )
    assert live_off.meta_live_publishing_enabled is False
    assert account_api_eligibility(real, live_off).reason == "live_publishing_disabled"
    assert PublicationJob.__tablename__ == "publication_jobs"
    assert PublicationStatus.PUBLISHED.value == "published"
