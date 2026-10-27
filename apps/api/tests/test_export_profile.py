"""Publishing profile + fixed recipe (E1). Pure unit tests, no database, no ffmpeg."""

from __future__ import annotations

import uuid

from affilio.core.config import Settings
from affilio.modules.media import export_profile
from affilio.modules.media.exports import RECIPE_KEY, export_relative_path, plan_recipe
from affilio.modules.media.probe import ProbeResult

SETTINGS = Settings(app_env="test", export_preset="medium", export_threads=2, export_crf=18)


def _probe(**overrides) -> ProbeResult:
    base = dict(
        duration_seconds=12.5,
        width=1080,
        height=1920,
        frame_rate=30.0,
        video_codec="hevc",
        audio_codec="aac",
        pixel_format="yuv420p10le",
        bit_depth=10,
        bit_rate=7_500_000,
        container_format="mov,mp4,m4a,3gp,3g2,mj2",
        raw={
            "streams": [
                {"codec_type": "video", "color_primaries": "bt709", "color_transfer": "bt709"},
                {"codec_type": "audio", "sample_rate": "44100"},
            ]
        },
    )
    base.update(overrides)
    return ProbeResult(**base)


def test_profile_rejects_10bit_and_accepts_8bit_h264_or_hevc() -> None:
    problems = export_profile.profile_problems(_probe(), file_size=10_000, settings=SETTINGS)
    assert problems == ["pixel format yuv420p10le is not 8-bit yuv420p"]
    ok = _probe(pixel_format="yuv420p", bit_depth=8)
    assert export_profile.profile_problems(ok, file_size=10_000, settings=SETTINGS) == []
    ok264 = _probe(pixel_format="yuv420p", bit_depth=8, video_codec="h264")
    assert export_profile.profile_problems(ok264, file_size=10_000, settings=SETTINGS) == []


def test_profile_lists_every_problem() -> None:
    bad = _probe(
        pixel_format="yuv422p",
        bit_depth=8,
        video_codec="prores",
        audio_codec="pcm_s16le",
        width=1920,
        height=1080,
        frame_rate=10.0,
        duration_seconds=120.0,
        container_format="mov",
    )
    problems = export_profile.profile_problems(bad, file_size=0, settings=SETTINGS)
    assert [p.split(" ")[0] for p in problems] == [
        "container",
        "video",
        "pixel",
        "audio",
        "duration",
        "not",
        "frame",
        "file",
    ]


def test_hdr_detection_from_stream_metadata() -> None:
    sdr = export_profile.color_info(_probe())
    assert not sdr.is_hdr_or_wide_gamut and sdr.primaries == "bt709"
    pq = _probe(
        raw={
            "streams": [
                {
                    "codec_type": "video",
                    "color_primaries": "bt2020",
                    "color_transfer": "smpte2084",
                    "color_space": "bt2020nc",
                }
            ]
        }
    )
    assert export_profile.color_info(pq).is_hdr_or_wide_gamut
    hlg = _probe(raw={"streams": [{"codec_type": "video", "color_transfer": "arib-std-b67"}]})
    assert export_profile.color_info(hlg).is_hdr_or_wide_gamut
    assert not export_profile.has_audio(hlg)
    assert export_profile.audio_sample_rate(_probe()) == 44100


def test_recipe_is_fixed_bounded_and_path_free() -> None:
    plan = plan_recipe(_probe(width=2160, height=3840), SETTINGS)
    args = plan.ffmpeg_args("ffmpeg", "/media/finals/x.mp4", "/data/exports/aa/x.tmp.mp4")
    assert plan.key == RECIPE_KEY and plan.fps == 30 and plan.audio is True
    assert args[0] == "ffmpeg" and args[-1] == "/data/exports/aa/x.tmp.mp4"
    assert "-vf" in args and "scale=w='min(iw,1080)':h=-2:flags=lanczos" in args
    assert args[args.index("-c:v") + 1] == "libx264"
    assert args[args.index("-pix_fmt") + 1] == "yuv420p"
    assert args[args.index("-g") + 1] == "60" and args[args.index("-r") + 1] == "30"
    assert args[args.index("-ar") + 1] == "48000" and args[args.index("-c:a") + 1] == "aac"
    assert args[args.index("-preset") + 1] == "medium" and args[args.index("-crf") + 1] == "18"
    assert "-filter_complex" not in args and not any(a.startswith("-x264opts") for a in args)

    described = plan.describe("ffmpeg version 5.1.6")
    assert described["key"] == RECIPE_KEY and described["ffmpeg_version"] == "ffmpeg version 5.1.6"
    assert "<source>" in described["args"] and "<target>" in described["args"]
    assert not any("/media" in a or "/data" in a for a in described["args"])

    # Frame rate is clamped to the documented 24-60 window and fixed (cfr).
    assert plan_recipe(_probe(frame_rate=10.0), SETTINGS).fps == 24
    assert plan_recipe(_probe(frame_rate=120.0), SETTINGS).fps == 60
    assert plan_recipe(_probe(frame_rate=None), SETTINGS).fps == 30
    # No audio stream → no audio mapping, nothing invented.
    silent = plan_recipe(_probe(raw={"streams": [{"codec_type": "video"}]}), SETTINGS)
    silent_args = silent.ffmpeg_args("ffmpeg", "s", "t")
    assert "-an" in silent_args and "-c:a" not in silent_args


def test_export_path_layout_matches_thumbnails() -> None:
    asset_id = uuid.UUID("12345678-1234-4123-8123-123456789abc")
    assert export_relative_path(asset_id) == (
        "12/123456781234412381231234567 89abc-reels-h264-v1.mp4".replace(" ", "")
    )


def test_settings_bounds_are_enforced() -> None:
    import pytest

    with pytest.raises(ValueError, match="EXPORT_CRF"):
        Settings(app_env="test", export_crf=5)
    with pytest.raises(ValueError, match="EXPORT_THREADS"):
        Settings(app_env="test", export_threads=0)
    with pytest.raises(ValueError, match="EXPORT_STALE_AFTER_SECONDS"):
        Settings(app_env="test", export_heartbeat_seconds=15, export_stale_after_seconds=20)
    assert Settings(app_env="test", export_heartbeat_seconds=0, export_stale_after_seconds=1)
