"""The publishing profile a Reels upload must satisfy, checked from ffprobe output (E1).

Facts from the documented Reels spec (docs/integrations/facebook-reels-feasibility.md, B3):
MP4, H.264 or H.265, 4:2:0 chroma, 8-bit (10-bit is unverified and therefore refused),
portrait, at least 540x960, 24 to 60 fps, 3 to 90 s, AAC audio. `publishing/media_checks.py`
enforces the same facts on the asset at publish time; this module is the file-level form the
export worker uses both to decide "already compliant, skip" and to validate its own output.
Keep the two in agreement (E2 makes `media_checks` import these constants).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from affilio.core.config import Settings
from affilio.modules.media.probe import ProbeResult

ACCEPTED_CONTAINERS = frozenset({"mp4", "mov,mp4,m4a,3gp,3g2,mj2"})
ACCEPTED_VIDEO_CODECS = frozenset({"h264", "hevc"})
ACCEPTED_AUDIO_CODECS = frozenset({"aac"})
ACCEPTED_PIXEL_FORMATS = frozenset({"yuv420p"})

HDR_TRANSFERS = frozenset({"smpte2084", "arib-std-b67", "smpte428", "bt2020-10", "bt2020-12"})
HDR_PRIMARIES = frozenset({"bt2020"})
HDR_MATRICES = frozenset({"bt2020nc", "bt2020c"})


@dataclass(frozen=True, slots=True)
class ColorInfo:
    primaries: str | None
    transfer: str | None
    matrix: str | None

    @property
    def is_hdr_or_wide_gamut(self) -> bool:
        return bool(
            (self.transfer and self.transfer.lower() in HDR_TRANSFERS)
            or (self.primaries and self.primaries.lower() in HDR_PRIMARIES)
            or (self.matrix and self.matrix.lower() in HDR_MATRICES)
        )

    def as_dict(self) -> dict[str, str | None]:
        return {"primaries": self.primaries, "transfer": self.transfer, "matrix": self.matrix}


def color_info(probe: ProbeResult) -> ColorInfo:
    """Colour metadata of the first video stream from the raw ffprobe output."""
    streams: list[dict[str, Any]] = (probe.raw or {}).get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    return ColorInfo(
        primaries=video.get("color_primaries"),
        transfer=video.get("color_transfer"),
        matrix=video.get("color_space"),
    )


def has_audio(probe: ProbeResult) -> bool:
    streams: list[dict[str, Any]] = (probe.raw or {}).get("streams") or []
    return any(s.get("codec_type") == "audio" for s in streams)


def audio_sample_rate(probe: ProbeResult) -> int | None:
    streams: list[dict[str, Any]] = (probe.raw or {}).get("streams") or []
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if audio is None:
        return None
    try:
        return int(audio.get("sample_rate") or 0) or None
    except (TypeError, ValueError):
        return None


def profile_problems(probe: ProbeResult, *, file_size: int, settings: Settings) -> list[str]:
    """Why a file does NOT satisfy the publishing profile. Empty list = publish-ready."""
    problems: list[str] = []
    container = (probe.container_format or "").lower()
    if container not in ACCEPTED_CONTAINERS:
        problems.append(f"container {container or 'unknown'} is not MP4")
    codec = (probe.video_codec or "").lower()
    if codec not in ACCEPTED_VIDEO_CODECS:
        problems.append(f"video codec {codec or 'unknown'} is not H.264/H.265")
    pixel_format = (probe.pixel_format or "").lower()
    if pixel_format not in ACCEPTED_PIXEL_FORMATS or (probe.bit_depth and probe.bit_depth > 8):
        problems.append(f"pixel format {pixel_format or 'unknown'} is not 8-bit yuv420p")
    if has_audio(probe):
        audio = (probe.audio_codec or "").lower()
        if audio not in ACCEPTED_AUDIO_CODECS:
            problems.append(f"audio codec {audio or 'unknown'} is not AAC")
    duration = probe.duration_seconds
    if duration is None:
        problems.append("duration unknown")
    elif not (
        settings.reels_min_duration_seconds <= duration <= settings.reels_max_duration_seconds
    ):
        problems.append(f"duration {duration:.1f}s outside the accepted range")
    width, height = probe.width, probe.height
    if not width or not height:
        problems.append("resolution unknown")
    else:
        if height <= width:
            problems.append("not portrait")
        if width < settings.reels_min_width or height < settings.reels_min_height:
            problems.append(f"resolution {width}x{height} below the minimum")
    fps = probe.frame_rate
    if fps is None:
        problems.append("frame rate unknown")
    elif not (settings.reels_min_frame_rate <= fps <= settings.reels_max_frame_rate):
        problems.append(f"frame rate {fps:.1f} fps outside 24-60")
    if file_size <= 0:
        problems.append("file is empty")
    elif file_size > settings.reels_max_file_bytes:
        problems.append("file larger than the accepted maximum")
    return problems
