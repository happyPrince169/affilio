"""ffprobe wrapper. Produces a compact, typed `ProbeResult`; never transcodes."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

ProbeFailureKind = Literal["timeout", "executable_missing", "unsupported", "failed"]

_BIT_DEPTH_RE = re.compile(r"p(\d{1,2})(?:le|be)?$")
_RAW_KEEP_FORMAT = ("format_name", "format_long_name", "duration", "size", "bit_rate")
_RAW_KEEP_STREAM = (
    "index",
    "codec_type",
    "codec_name",
    "codec_long_name",
    "profile",
    "width",
    "height",
    "pix_fmt",
    "r_frame_rate",
    "avg_frame_rate",
    "bit_rate",
    "duration",
    "channels",
    "sample_rate",
    "color_space",
    "color_transfer",
    "color_primaries",
    "level",
)


class ProbeError(Exception):
    def __init__(self, kind: ProbeFailureKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass(slots=True)
class ProbeResult:
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    rotation: int | None = None
    frame_rate: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    pixel_format: str | None = None
    bit_depth: int | None = None
    bit_rate: int | None = None
    container_format: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def columns(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("raw", None)
        return data


class Prober(Protocol):
    def probe(self, path: Path) -> ProbeResult: ...


def parse_frame_rate(value: str | None) -> float | None:
    if not value or value in {"0/0", "0"}:
        return None
    if "/" in value:
        num, _, den = value.partition("/")
        try:
            numerator, denominator = float(num), float(den)
        except ValueError:
            return None
        return round(numerator / denominator, 3) if denominator else None
    try:
        return round(float(value), 3)
    except ValueError:
        return None


def bit_depth_from_pix_fmt(pix_fmt: str | None) -> int | None:
    if not pix_fmt:
        return None
    match = _BIT_DEPTH_RE.search(pix_fmt)
    if match:
        return int(match.group(1))
    return 8


def _rotation_from_stream(stream: dict[str, Any]) -> int | None:
    for side_data in stream.get("side_data_list") or []:
        if "rotation" in side_data:
            try:
                return round(float(side_data["rotation"])) % 360
            except (TypeError, ValueError):
                continue
    tags = stream.get("tags") or {}
    if "rotate" in tags:
        try:
            return round(float(tags["rotate"])) % 360
        except (TypeError, ValueError):
            return None
    return None


def _to_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_ffprobe_output(data: dict[str, Any]) -> ProbeResult:
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise ProbeError("unsupported", "No video stream found")

    width = _to_int(video.get("width"))
    height = _to_int(video.get("height"))
    rotation = _rotation_from_stream(video)
    if rotation in {90, 270} and width and height:
        width, height = height, width

    duration = _to_float(fmt.get("duration")) or _to_float(video.get("duration"))
    bit_rate = _to_int(fmt.get("bit_rate")) or _to_int(video.get("bit_rate"))
    pix_fmt = video.get("pix_fmt")

    raw = {
        "format": {k: fmt[k] for k in _RAW_KEEP_FORMAT if k in fmt},
        "streams": [{k: s[k] for k in _RAW_KEEP_STREAM if k in s} for s in streams],
    }
    return ProbeResult(
        duration_seconds=round(duration, 3) if duration is not None else None,
        width=width,
        height=height,
        rotation=rotation,
        frame_rate=parse_frame_rate(video.get("avg_frame_rate") or video.get("r_frame_rate")),
        video_codec=video.get("codec_name"),
        audio_codec=audio.get("codec_name") if audio else None,
        pixel_format=pix_fmt,
        bit_depth=bit_depth_from_pix_fmt(pix_fmt),
        bit_rate=bit_rate,
        container_format=fmt.get("format_name"),
        raw=raw,
    )


class FFprobe:
    """Runs the ffprobe executable with a timeout. Output is JSON, parsed by the function above."""

    def __init__(self, executable: str = "ffprobe", timeout_seconds: int = 60) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds

    def available(self) -> bool:
        return shutil.which(self.executable) is not None

    def probe(self, path: Path) -> ProbeResult:
        command = [
            self.executable,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
        try:
            completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
                command,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise ProbeError("executable_missing", f"{self.executable} not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise ProbeError("timeout", f"ffprobe timed out after {self.timeout_seconds}s") from exc

        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", "replace").strip()
            raise ProbeError("unsupported", (stderr or "ffprobe failed")[:500])
        try:
            data = json.loads(completed.stdout.decode("utf-8", "replace") or "{}")
        except json.JSONDecodeError as exc:
            raise ProbeError("failed", "ffprobe returned invalid JSON") from exc
        return parse_ffprobe_output(data)
