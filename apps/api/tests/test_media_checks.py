"""Pre-upload media validation (no provider, needs TEST_DATABASE_URL for the asset rows)."""

from __future__ import annotations

import os
import time

import pytest
from sqlalchemy.orm import Session

from affilio.core.enums import HandoffStatus
from affilio.modules.media.models import MediaAsset
from affilio.modules.publishing.media_checks import check_reel_media
from tests.media_helpers import build_settings
from tests.publishing_helpers import make_final

pytestmark = pytest.mark.database


def _settings(media_dirs, **overrides):
    return build_settings(media_dirs, **overrides)


def test_accepts_a_documented_vertical_h264_final(
    db_session: Session, organization, media_dirs
) -> None:
    asset = make_final(db_session, organization, media_dirs, "good.mp4")
    check = check_reel_media(asset, media_dirs.registry, _settings(media_dirs))
    assert check.ok, check.errors
    assert check.file_size == 6_000 and check.relative_path == "good.mp4"


def test_rejects_unapproved_and_missing_or_changed_files(
    db_session: Session, organization, media_dirs
) -> None:
    pending = make_final(db_session, organization, media_dirs, "pending.mp4", approve=False)
    check = check_reel_media(pending, media_dirs.registry, _settings(media_dirs))
    assert not check.ok and any("not been approved" in e for e in check.errors)
    good = make_final(db_session, organization, media_dirs, "changed.mp4")
    path = media_dirs.finals / "changed.mp4"
    path.write_bytes(b"x" * 10)  # size changed after indexing
    check = check_reel_media(good, media_dirs.registry, _settings(media_dirs))
    assert any("size changed" in e for e in check.errors)
    os.remove(path)
    check = check_reel_media(good, media_dirs.registry, _settings(media_dirs))
    assert any("missing on the finals mount" in e for e in check.errors)


def test_rejects_recently_modified_files_until_settled(
    db_session: Session, organization, media_dirs
) -> None:
    asset = make_final(db_session, organization, media_dirs, "fresh.mp4")
    settling = _settings(media_dirs, media_final_settle_seconds=3600)
    check = check_reel_media(asset, media_dirs.registry, settling)
    assert any("still settling" in e for e in check.errors)
    # An old mtime passes (the scanner recorded the same mtime).
    path = media_dirs.finals / "fresh.mp4"
    old = time.time() - 7200
    os.utime(path, (old, old))
    variant = asset.main_variant()
    from datetime import UTC, datetime

    variant.modified_at = datetime.fromtimestamp(old, tz=UTC)
    db_session.commit()
    assert check_reel_media(asset, media_dirs.registry, settling).ok


@pytest.mark.parametrize(
    ("field", "value", "fragment"),
    [
        ("duration_seconds", 120.0, "Duration"),
        ("duration_seconds", 1.0, "Duration"),
        ("bit_depth", 10, "8-bit"),
        ("pixel_format", "yuv422p", "8-bit"),
        ("video_codec", "prores", "codec"),
        ("audio_codec", "mp3", "Audio codec"),
        ("frame_rate", 120.0, "Frame rate"),
        ("width", 400, "below the minimum"),
    ],
)
def test_rejects_incompatible_probe_facts(
    db_session: Session, organization, media_dirs, field: str, value, fragment: str
) -> None:
    asset = make_final(db_session, organization, media_dirs, f"probe_{field}.mp4")
    setattr(asset, field, value)
    if field == "width":
        asset.height = 700  # still vertical, but too small
    db_session.commit()
    check = check_reel_media(asset, media_dirs.registry, _settings(media_dirs))
    assert not check.ok and any(fragment in e for e in check.errors), check.errors


def test_rejects_landscape_and_unapproved_kinds(
    db_session: Session, organization, media_dirs
) -> None:
    asset = make_final(db_session, organization, media_dirs, "landscape.mp4")
    asset.width, asset.height = 1920, 1080
    db_session.commit()
    check = check_reel_media(asset, media_dirs.registry, _settings(media_dirs))
    assert any("not vertical" in e for e in check.errors)
    asset.handoff_status = HandoffStatus.REJECTED
    db_session.commit()
    assert "approved" in " ".join(
        check_reel_media(asset, media_dirs.registry, _settings(media_dirs)).errors
    )
    assert isinstance(asset, MediaAsset)
