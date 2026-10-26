"""Final-video handoff: independent index, settle window, approval. Needs TEST_DATABASE_URL."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from decimal import Decimal  # noqa: F401 - keep import surface stable

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from affilio.core.enums import (
    HandoffStatus,
    MediaAssetKind,
    MediaVariantKind,
    ScanJobStatus,
    ScanTrigger,
)
from affilio.modules.media.models import MediaAsset
from affilio.modules.media.scanner import MediaScanner
from affilio.modules.media.schemas import HandoffDecision
from affilio.modules.media.service import (
    HandoffNotApplicableError,
    HandoffNoteRequiredError,
    enqueue_scan,
    media_stats,
    review_handoff,
)
from tests.media_helpers import FakeProber, build_settings, write_bytes

pytestmark = pytest.mark.database


def _scan(session: Session, org_id, dirs, root_key: str, **settings_overrides) -> MediaScanner:
    settings = build_settings(dirs, **settings_overrides)
    job = enqueue_scan(session, org_id, dirs.registry, root_key=root_key, trigger=ScanTrigger.CLI)
    job.status = ScanJobStatus.RUNNING
    stats = MediaScanner(session, dirs.registry, settings, prober=FakeProber()).run(job)
    job.status = ScanJobStatus.SUCCEEDED
    session.commit()
    return stats


def test_finals_indexed_independently_with_review_gate(
    db_session: Session, organization, media_dirs
) -> None:
    write_bytes(media_dirs.originals / "raw.mp4", 5_000, seed=1)
    write_bytes(
        media_dirs.finals / "blender_final_v3.mp4", 5_000, seed=1
    )  # same bytes as raw: still separate

    _scan(db_session, organization.id, media_dirs, "originals")
    stats = _scan(db_session, organization.id, media_dirs, "finals")
    assert stats.added == 1 and stats.duplicates == 0  # duplicates are per root

    assets = {
        a.asset_kind: a
        for a in db_session.scalars(
            select(MediaAsset).where(MediaAsset.organization_id == organization.id)
        )
    }
    footage, final = assets[MediaAssetKind.FOOTAGE], assets[MediaAssetKind.FINAL]
    assert footage.handoff_status is None
    assert final.handoff_status == HandoffStatus.PENDING_REVIEW
    assert final.main_variant() is not None and final.main_variant().kind == MediaVariantKind.FINAL
    assert final.variant(MediaVariantKind.ORIGINAL) is None
    counters = media_stats(db_session, organization.id)
    assert (counters.footage, counters.finals, counters.finals_pending_review) == (1, 1, 1)

    with pytest.raises(HandoffNotApplicableError):  # footage has no handoff
        review_handoff(
            db_session,
            organization.id,
            footage.id,
            HandoffDecision(status=HandoffStatus.APPROVED, note="x"),
        )
    with pytest.raises(HandoffNoteRequiredError):  # approval needs a human note
        review_handoff(
            db_session, organization.id, final.id, HandoffDecision(status=HandoffStatus.APPROVED)
        )
    final = review_handoff(
        db_session,
        organization.id,
        final.id,
        HandoffDecision(status=HandoffStatus.APPROVED, note="checked audio + captions"),
        actor="editor-lead",
    )
    assert (
        final.handoff_status == HandoffStatus.APPROVED
        and final.handoff_reviewed_by == "editor-lead"
    )
    assert final.handoff_reviewed_at is not None and final.handoff_reviewed_at <= datetime.now(UTC)
    final = review_handoff(
        db_session, organization.id, final.id, HandoffDecision(status=HandoffStatus.PENDING_REVIEW)
    )
    assert final.handoff_status == HandoffStatus.PENDING_REVIEW


def test_settle_window_skips_fresh_and_temp_files(
    db_session: Session, organization, media_dirs
) -> None:
    settled = write_bytes(media_dirs.finals / "done.mp4", 4_000, seed=2)
    old = time.time() - 3600
    os.utime(settled, (old, old))
    write_bytes(media_dirs.finals / "uploading.mp4", 4_000, seed=3)  # mtime = now → still settling
    write_bytes(media_dirs.finals / "export.mp4.part", 4_000, seed=4)  # temp marker, also wrong ext
    write_bytes(media_dirs.finals / "draft.tmp.mp4", 4_000, seed=5)  # temp marker inside the name

    stats = _scan(db_session, organization.id, media_dirs, "finals", media_final_settle_seconds=600)
    assert stats.added == 1 and stats.settling == 2
    titles = [
        a.title
        for a in db_session.scalars(
            select(MediaAsset).where(MediaAsset.organization_id == organization.id)
        )
    ]
    assert titles == ["done"]

    # Once the upload has settled it is picked up by the next scan.
    fresh = media_dirs.finals / "uploading.mp4"
    os.utime(fresh, (old, old))
    stats = _scan(db_session, organization.id, media_dirs, "finals", media_final_settle_seconds=600)
    assert stats.added == 1 and stats.settling == 1
