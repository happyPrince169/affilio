"""media_scan CLI root selection (M3C.1 step 2). Requires TEST_DATABASE_URL."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from affilio.core.config import get_settings
from affilio.core.enums import ScanJobStatus, ScanTrigger
from affilio.db.session import dispose_engine
from affilio.modules.media.models import MediaRoot, MediaScanJob
from affilio.modules.media.roots import SCANNABLE_ROOT_KEYS
from affilio.scripts import media_scan

pytestmark = pytest.mark.database


@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch, media_dirs, organization, schema) -> Iterator[None]:
    for key, value in media_dirs.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DEFAULT_ORGANIZATION_SLUG", organization.slug)
    get_settings.cache_clear()
    yield
    dispose_engine()
    get_settings.cache_clear()


def _stub_execute(monkeypatch: pytest.MonkeyPatch, seen: list[str]) -> None:
    """Replace the scanner run: the CLI's job is root selection and queue handling, the
    scanner itself is covered by test_media_scanner / test_media_finals."""

    def fake_execute(session, job, *, settings, registry, prober=None):
        seen.append(job.root.key)
        job.status = ScanJobStatus.SUCCEEDED
        job.stats = {"files_seen": 0}
        session.commit()
        return job

    monkeypatch.setattr(media_scan, "execute_scan_job", fake_execute)


def _jobs(session: Session, organization) -> list[tuple[str, ScanTrigger, ScanJobStatus]]:
    rows = session.execute(
        select(MediaRoot.key, MediaScanJob.trigger, MediaScanJob.status)
        .join(MediaScanJob.root)
        .where(MediaScanJob.organization_id == organization.id)
    ).all()
    return [(key, trigger, status) for key, trigger, status in rows]


def test_scannable_roots_are_originals_and_finals() -> None:
    assert SCANNABLE_ROOT_KEYS == ("originals", "finals")


def test_cli_defaults_to_originals(
    cli_env, monkeypatch: pytest.MonkeyPatch, db_session: Session, organization, capsys
) -> None:
    seen: list[str] = []
    _stub_execute(monkeypatch, seen)

    assert media_scan.main([]) == 0

    assert seen == ["originals"]
    assert "scan of originals succeeded" in capsys.readouterr().out
    assert _jobs(db_session, organization) == [
        ("originals", ScanTrigger.CLI, ScanJobStatus.SUCCEEDED)
    ]


def test_cli_scans_finals_on_request(
    cli_env, monkeypatch: pytest.MonkeyPatch, db_session: Session, organization, capsys
) -> None:
    seen: list[str] = []
    _stub_execute(monkeypatch, seen)

    assert media_scan.main(["--root", "finals", "--full"]) == 0

    assert seen == ["finals"]
    assert "scan of finals succeeded" in capsys.readouterr().out
    assert _jobs(db_session, organization) == [("finals", ScanTrigger.CLI, ScanJobStatus.SUCCEEDED)]
    job = db_session.scalar(
        select(MediaScanJob).where(MediaScanJob.organization_id == organization.id)
    )
    assert job is not None and job.full_rescan is True


def test_cli_rejects_roots_that_cannot_be_scanned(
    cli_env, db_session: Session, organization
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        media_scan.main(["--root", "proxies"])
    assert exit_info.value.code == 2  # argparse usage error, nothing queued
    assert _jobs(db_session, organization) == []
