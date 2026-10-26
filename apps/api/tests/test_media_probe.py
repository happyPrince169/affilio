from __future__ import annotations

from pathlib import Path

import pytest

from affilio.modules.media.probe import (
    FFprobe,
    ProbeError,
    bit_depth_from_pix_fmt,
    parse_ffprobe_output,
    parse_frame_rate,
)
from tests.media_helpers import FFPROBE, make_video, requires_ffmpeg

SAMPLE = {
    "format": {
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "duration": "12.512000",
        "bit_rate": "8000000",
    },
    "streams": [
        {
            "codec_type": "video",
            "codec_name": "hevc",
            "width": 1920,
            "height": 1080,
            "pix_fmt": "yuv420p10le",
            "avg_frame_rate": "30000/1001",
            "side_data_list": [{"rotation": -90}],
        },
        {"codec_type": "audio", "codec_name": "aac", "channels": 2},
    ],
}


def test_parse_applies_rotation_and_bit_depth() -> None:
    result = parse_ffprobe_output(SAMPLE)

    assert (result.width, result.height) == (1080, 1920)  # rotated portrait
    assert result.rotation == 270
    assert result.bit_depth == 10
    assert result.video_codec == "hevc"
    assert result.audio_codec == "aac"
    assert result.frame_rate == 29.97
    assert result.duration_seconds == 12.512
    assert result.bit_rate == 8_000_000
    assert "streams" in result.raw and "side_data_list" not in result.raw["streams"][0]


def test_parse_requires_video_stream() -> None:
    with pytest.raises(ProbeError) as excinfo:
        parse_ffprobe_output({"streams": [{"codec_type": "audio"}], "format": {}})
    assert excinfo.value.kind == "unsupported"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("30000/1001", 29.97),
        ("25/1", 25.0),
        ("0/0", None),
        ("abc", None),
        (None, None),
        ("24", 24.0),
    ],
)
def test_parse_frame_rate(value: str | None, expected: float | None) -> None:
    assert parse_frame_rate(value) == expected


@pytest.mark.parametrize(
    ("pix_fmt", "expected"),
    [("yuv420p", 8), ("yuv420p10le", 10), ("yuv422p12be", 12), ("gbrp", 8), (None, None)],
)
def test_bit_depth(pix_fmt: str | None, expected: int | None) -> None:
    assert bit_depth_from_pix_fmt(pix_fmt) == expected


@requires_ffmpeg
def test_ffprobe_reads_real_video(tmp_path: Path) -> None:
    clip = make_video(tmp_path / "clip.mp4", size="36x64")

    result = FFprobe(FFPROBE or "ffprobe", timeout_seconds=30).probe(clip)

    assert (result.width, result.height) == (36, 64)
    assert result.video_codec == "h264"
    assert result.audio_codec == "aac"
    assert result.duration_seconds and 0.5 <= result.duration_seconds <= 2.0


@requires_ffmpeg
def test_ffprobe_rejects_garbage(tmp_path: Path) -> None:
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not a video at all" * 100)

    with pytest.raises(ProbeError) as excinfo:
        FFprobe(FFPROBE or "ffprobe", timeout_seconds=30).probe(junk)
    assert excinfo.value.kind == "unsupported"


def test_ffprobe_missing_executable(tmp_path: Path) -> None:
    with pytest.raises(ProbeError) as excinfo:
        FFprobe("/definitely/not/ffprobe").probe(tmp_path / "x.mp4")
    assert excinfo.value.kind == "executable_missing"
