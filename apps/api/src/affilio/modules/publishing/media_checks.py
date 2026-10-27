"""Pre-upload media validation for Reels (M3B.2A).

Facts come from the probe stored at scan time plus a fresh `stat` of the file on the read-only
finals mount. Nothing is transcoded, copied or modified. Bounds mirror the documented Reels
specification (docs/integrations/facebook-reels-feasibility.md, B3); anything the
documentation leaves unconfirmed (10-bit, odd containers) is refused rather than guessed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from affilio.core.config import Settings
from affilio.core.enums import HandoffStatus, MediaAssetKind, MediaAssetStatus, MediaVariantStatus
from affilio.modules.media.errors import InvalidMediaPathError
from affilio.modules.media.models import MediaAsset, MediaFileVariant
from affilio.modules.media.roots import MediaRootRegistry

ACCEPTED_EXTENSIONS = frozenset({".mp4", ".mov"})
ACCEPTED_VIDEO_CODECS = frozenset({"h264", "hevc", "h265", "avc1"})
ACCEPTED_AUDIO_CODECS = frozenset({"aac"})
MTIME_TOLERANCE_SECONDS = 2.0


@dataclass(slots=True)
class MediaCheck:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    path: Path | None = None
    relative_path: str | None = None
    file_size: int | None = None
    file_mtime: datetime | None = None

    def error(self, message: str) -> None:
        self.ok = False
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def summary(self) -> str:
        return "; ".join(self.errors) if self.errors else "ok"


def _variant(asset: MediaAsset) -> MediaFileVariant | None:
    variant = asset.main_variant()
    if variant is None or variant.status != MediaVariantStatus.PRESENT:
        return None
    return variant


def check_reel_media(
    asset: MediaAsset, registry: MediaRootRegistry, settings: Settings
) -> MediaCheck:
    """Validate an approved final video for a Reels upload without touching its bytes."""
    check = MediaCheck()
    if asset.asset_kind != MediaAssetKind.FINAL:
        check.error("Only final videos can be published")
    if asset.handoff_status != HandoffStatus.APPROVED:
        check.error("The final video has not been approved in the handoff review")
    if asset.status != MediaAssetStatus.ACTIVE:
        check.error("The media file is missing or unsupported")
    variant = _variant(asset)
    if variant is None:
        check.error("No present final file is recorded for this asset")
        return check
    root = registry.finals
    if root is None:
        check.error("The finals root is not configured on this worker")
        return check
    try:
        path = root.resolve(variant.relative_path)
    except InvalidMediaPathError as exc:
        check.error(f"Invalid media path: {exc}")
        return check
    check.path, check.relative_path = path, variant.relative_path
    try:
        stat = path.stat()
    except OSError:
        check.error("The final file is missing on the finals mount")
        return check
    if not path.is_file():
        check.error("The final path is not a regular file")
        return check
    check.file_size = stat.st_size
    check.file_mtime = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
    if stat.st_size != variant.file_size:
        check.error("The file size changed since it was indexed; rescan before publishing")
    if variant.modified_at is not None and (
        abs((check.file_mtime - variant.modified_at).total_seconds()) > MTIME_TOLERANCE_SECONDS
    ):
        check.error("The file was modified since it was indexed; rescan before publishing")
    age = (datetime.now(UTC) - check.file_mtime).total_seconds()
    if age < settings.media_final_settle_seconds:
        check.error("The file is still settling (modified too recently)")
    if not os.access(path, os.R_OK):
        check.error("The final file is not readable by the publisher")
    if stat.st_size <= 0:
        check.error("The final file is empty")
    if stat.st_size > settings.reels_max_file_bytes:
        check.error(f"File larger than {settings.reels_max_file_bytes // (1024 * 1024)} MiB")

    ext = (variant.extension or "").lower()
    if ext not in ACCEPTED_EXTENSIONS:
        check.error(f"Container {ext or 'unknown'} is not accepted for Reels (use .mp4)")
    elif ext == ".mov":
        check.warn("MOV container: MP4 is the documented recommendation")
    codec = (asset.video_codec or variant.video_codec or "").lower()
    if codec not in ACCEPTED_VIDEO_CODECS:
        check.error(f"Video codec {codec or 'unknown'} is not accepted (H.264 or H.265)")
    audio = (asset.audio_codec or variant.audio_codec or "").lower()
    if audio and audio not in ACCEPTED_AUDIO_CODECS:
        check.error(f"Audio codec {audio} is not accepted (AAC)")
    elif not audio:
        check.warn("No audio stream detected")
    pixel_format = (asset.pixel_format or variant.pixel_format or "").lower()
    bit_depth = asset.bit_depth or variant.bit_depth
    if (bit_depth and bit_depth > 8) or (pixel_format and not pixel_format.startswith("yuv420p")):
        check.error("Only 8-bit 4:2:0 video is accepted; 10-bit/HEVC proxies are not publish-ready")
    duration = asset.duration_seconds or variant.duration_seconds
    if duration is None:
        check.error("Duration unknown (probe missing)")
    elif not (
        settings.reels_min_duration_seconds <= duration <= settings.reels_max_duration_seconds
    ):
        check.error(
            f"Duration {duration:.1f}s outside {settings.reels_min_duration_seconds:.0f}-"
            f"{settings.reels_max_duration_seconds:.0f}s"
        )
    width, height = asset.width or variant.width, asset.height or variant.height
    if not width or not height:
        check.error("Resolution unknown (probe missing)")
    else:
        rotation = asset.rotation or variant.rotation or 0
        if rotation in (90, 270, -90):
            width, height = height, width
        if height <= width:
            check.error("Video is not vertical; Reels require portrait 9:16")
        if width < settings.reels_min_width or height < settings.reels_min_height:
            check.error(
                f"Resolution {width}x{height} below the minimum "
                f"{settings.reels_min_width}x{settings.reels_min_height}"
            )
        elif abs((width / height) - (9 / 16)) > 0.02:
            check.warn("Aspect ratio is not 9:16; Meta may crop or letterbox")
    frame_rate = asset.frame_rate or variant.frame_rate
    if frame_rate and not (
        settings.reels_min_frame_rate <= frame_rate <= settings.reels_max_frame_rate
    ):
        check.error(f"Frame rate {frame_rate:.1f} fps outside 24-60 fps")
    return check
