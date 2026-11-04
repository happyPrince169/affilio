"""Pre-upload media validation and file selection for Reels (M3B.2A, Export pipeline E2).

Facts come from the probe stored at scan or export time plus a fresh `stat` of the file on its
read-only mount. Nothing is transcoded, copied or modified here. Bounds mirror the documented
Reels specification (docs/integrations/facebook-reels-feasibility.md, B3); anything the
documentation leaves unconfirmed (10-bit, odd containers) is refused rather than guessed.

Selection (E2): a publish-ready **export** of the final is preferred when it is current:
it belongs to this asset, was derived from the present final variant, its recorded source
identity (size, mtime, fingerprint) still matches that final, its recipe is supported, its
file exists with the recorded size and mtime, and its own stream facts satisfy the profile.
Otherwise the **final itself** is used only if it independently satisfies every requirement.
If neither qualifies the check fails with both reasons, so nothing incompatible is ever
uploaded silently.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from affilio.core.config import Settings
from affilio.core.enums import (
    HandoffStatus,
    MediaAssetKind,
    MediaAssetStatus,
    MediaVariantKind,
    MediaVariantStatus,
)
from affilio.modules.media.errors import InvalidMediaPathError
from affilio.modules.media.exports import SUPPORTED_RECIPES, export_is_current
from affilio.modules.media.models import MediaAsset, MediaFileVariant
from affilio.modules.media.roots import MediaRootRegistry, RootBinding

ACCEPTED_EXTENSIONS = frozenset({".mp4", ".mov"})
ACCEPTED_VIDEO_CODECS = frozenset({"h264", "hevc", "h265", "avc1"})
ACCEPTED_AUDIO_CODECS = frozenset({"aac"})
MTIME_TOLERANCE_SECONDS = 2.0

MediaSource = Literal["export", "final"]
ExportState = Literal["used", "absent", "stale", "missing", "invalid", "unsupported_recipe"]


@dataclass(slots=True)
class MediaCheck:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    path: Path | None = None
    relative_path: str | None = None
    root_key: str | None = None
    variant_id: uuid.UUID | None = None
    fingerprint: str | None = None
    file_size: int | None = None
    file_mtime: datetime | None = None
    source: MediaSource | None = None
    export_state: ExportState = "absent"
    export_reasons: list[str] = field(default_factory=list)

    def error(self, message: str) -> None:
        self.ok = False
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def summary(self) -> str:
        return "; ".join(self.errors) if self.errors else "ok"


def _present_final(asset: MediaAsset) -> MediaFileVariant | None:
    variant = asset.main_variant()
    if variant is None or variant.status != MediaVariantStatus.PRESENT:
        return None
    return variant


def _export_candidate(asset: MediaAsset) -> MediaFileVariant | None:
    for variant in asset.variants:
        if variant.kind == MediaVariantKind.EXPORT and variant.status == MediaVariantStatus.PRESENT:
            return variant
    return None


def _check_file(
    check: MediaCheck,
    variant: MediaFileVariant,
    root: RootBinding | None,
    root_key: str,
    settings: Settings,
    *,
    facts: MediaAsset | MediaFileVariant,
) -> None:
    """Stat the file and compare the stored facts with the publishing profile. Errors go to
    `check`; `facts` supplies the stream columns (the asset for finals, the variant itself for
    exports)."""
    if root is None:
        check.error(f"The {root_key} root is not configured on this worker")
        return
    try:
        path = root.resolve(variant.relative_path)
    except InvalidMediaPathError as exc:
        check.error(f"Invalid media path: {exc}")
        return
    check.path, check.relative_path, check.root_key = path, variant.relative_path, root_key
    check.variant_id, check.fingerprint = variant.id, variant.fingerprint
    try:
        stat = path.stat()
    except OSError:
        check.error(f"The file is missing on the {root_key} mount")
        return
    if not path.is_file():
        check.error("The path is not a regular file")
        return
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
        check.error("The file is not readable by the publisher")
    if stat.st_size <= 0:
        check.error("The file is empty")
    if stat.st_size > settings.reels_max_file_bytes:
        check.error(f"File larger than {settings.reels_max_file_bytes // (1024 * 1024)} MiB")

    ext = (variant.extension or "").lower()
    if ext not in ACCEPTED_EXTENSIONS:
        check.error(f"Container {ext or 'unknown'} is not accepted for Reels (use .mp4)")
    elif ext == ".mov":
        check.warn("MOV container: MP4 is the documented recommendation")
    codec = (facts.video_codec or variant.video_codec or "").lower()
    if codec not in ACCEPTED_VIDEO_CODECS:
        check.error(f"Video codec {codec or 'unknown'} is not accepted (H.264 or H.265)")
    audio = (facts.audio_codec or variant.audio_codec or "").lower()
    if audio and audio not in ACCEPTED_AUDIO_CODECS:
        check.error(f"Audio codec {audio} is not accepted (AAC)")
    elif not audio:
        check.warn("No audio stream detected")
    pixel_format = (facts.pixel_format or variant.pixel_format or "").lower()
    bit_depth = facts.bit_depth or variant.bit_depth
    if (bit_depth and bit_depth > 8) or (pixel_format and not pixel_format.startswith("yuv420p")):
        check.error("Only 8-bit 4:2:0 video is accepted; 10-bit/HEVC proxies are not publish-ready")
    duration = facts.duration_seconds or variant.duration_seconds
    if duration is None:
        check.error("Duration unknown (probe missing)")
    elif not (
        settings.reels_min_duration_seconds <= duration <= settings.reels_max_duration_seconds
    ):
        check.error(
            f"Duration {duration:.1f}s outside {settings.reels_min_duration_seconds:.0f}-"
            f"{settings.reels_max_duration_seconds:.0f}s"
        )
    width, height = facts.width or variant.width, facts.height or variant.height
    if not width or not height:
        check.error("Resolution unknown (probe missing)")
    else:
        rotation = facts.rotation or variant.rotation or 0
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
    frame_rate = facts.frame_rate or variant.frame_rate
    if frame_rate and not (
        settings.reels_min_frame_rate <= frame_rate <= settings.reels_max_frame_rate
    ):
        check.error(f"Frame rate {frame_rate:.1f} fps outside 24-60 fps")


def check_reel_media(
    asset: MediaAsset, registry: MediaRootRegistry, settings: Settings
) -> MediaCheck:
    check = MediaCheck()
    if asset.asset_kind != MediaAssetKind.FINAL:
        check.error("Only final videos can be published")
    if asset.handoff_status != HandoffStatus.APPROVED:
        check.error("The final video has not been approved in the handoff review")
    if asset.status != MediaAssetStatus.ACTIVE:
        check.error("The media file is missing or unsupported")
    final = _present_final(asset)
    if final is None:
        check.error("No present final file is recorded for this asset")
        return check

    # --- 1. a current, valid export is preferred ---------------------------------------------
    export = _export_candidate(asset)
    if export is not None:
        trial = MediaCheck()
        if export.recipe_key not in SUPPORTED_RECIPES:
            check.export_state = "unsupported_recipe"
            check.export_reasons = [f"recipe {export.recipe_key or 'unknown'} is not supported"]
        elif not export_is_current(export, final):
            check.export_state = "stale"
            check.export_reasons = ["the final changed after the export was rendered"]
        else:
            _check_file(trial, export, registry.exports, "exports", settings, facts=export)
            if trial.ok:
                check.path, check.relative_path, check.root_key = (
                    trial.path,
                    trial.relative_path,
                    "exports",
                )
                check.variant_id, check.fingerprint = trial.variant_id, trial.fingerprint
                check.file_size, check.file_mtime = trial.file_size, trial.file_mtime
                check.warnings.extend(trial.warnings)
                check.source = "export"
                check.export_state = "used"
                return check
            missing = any("missing on the" in e or "not a regular file" in e for e in trial.errors)
            check.export_state = "missing" if missing else "invalid"
            check.export_reasons = list(trial.errors)

    # --- 2. otherwise the final itself must pass on its own -----------------------------------
    before = len(check.errors)
    _check_file(check, final, registry.binding_for(final), final.root.key, settings, facts=asset)
    if check.ok:
        check.source = "final"
        return check
    if export is not None and check.export_state != "absent":
        reason = "; ".join(check.export_reasons) or check.export_state
        check.errors.insert(
            before, f"Export unusable ({check.export_state}: {reason}); final rejected"
        )
    if any("8-bit" in e or "codec" in e.lower() for e in check.errors):
        check.errors.append("Queue a new publish-ready export of this final before publishing")
    return check
