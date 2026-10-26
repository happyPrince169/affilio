"""Shared helpers for media tests: synthetic videos, fake prober, DB organization setup."""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from affilio.core.config import Settings
from affilio.modules.identity.models import Organization
from affilio.modules.media.probe import ProbeError, ProbeResult
from affilio.modules.media.roots import MediaRootRegistry

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

requires_ffmpeg = pytest.mark.skipif(
    FFMPEG is None or FFPROBE is None, reason="ffmpeg/ffprobe not installed"
)


def make_video(
    path: Path, *, size: str = "64x64", seconds: float = 1.0, audio: bool = True
) -> Path:
    """Render a tiny real H.264/AAC clip with ffmpeg's test sources (no NAS needed)."""
    assert FFMPEG, "ffmpeg required"
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        FFMPEG,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={seconds}:size={size}:rate=10",
    ]
    if audio:
        command += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    command += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if audio:
        command += ["-c:a", "aac", "-shortest"]
    command += [str(path)]
    subprocess.run(command, check=True, timeout=60)  # noqa: S603
    return path


def write_bytes(path: Path, size: int, seed: int = 0) -> Path:
    """Deterministic pseudo-random file content without loading huge buffers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = (seed * 2654435761 + 12345) & 0xFFFFFFFF
    with path.open("wb") as handle:
        remaining = size
        while remaining > 0:
            block = bytearray(min(65536, remaining))
            for i in range(len(block)):
                rng = (1103515245 * rng + 12345) & 0x7FFFFFFF
                block[i] = rng & 0xFF
            handle.write(block)
            remaining -= len(block)
    return path


@dataclass
class FakeProber:
    """Deterministic prober: returns canned results; raises for names containing 'broken'."""

    width: int = 1080
    height: int = 1920
    calls: list[Path] = field(default_factory=list)

    def probe(self, path: Path) -> ProbeResult:
        self.calls.append(path)
        if "broken" in path.name:
            raise ProbeError("unsupported", "Invalid data found when processing input")
        if "timeout" in path.name:
            raise ProbeError("timeout", "ffprobe timed out")
        return ProbeResult(
            duration_seconds=12.5,
            width=self.width,
            height=self.height,
            frame_rate=30.0,
            video_codec="hevc" if "proxy" in str(path) else "h264",
            audio_codec="aac",
            pixel_format="yuv420p10le" if "proxy" in str(path) else "yuv420p",
            bit_depth=10 if "proxy" in str(path) else 8,
            bit_rate=8_000_000,
            container_format="mov,mp4,m4a,3gp,3g2,mj2",
            raw={"format": {"format_name": "mov"}, "streams": []},
        )


@dataclass
class MediaDirs:
    originals: Path
    proxies: Path
    thumbnails: Path
    exports: Path
    finals: Path

    @property
    def registry(self) -> MediaRootRegistry:
        return MediaRootRegistry.from_paths(
            originals=self.originals,
            proxies=self.proxies,
            finals=self.finals,
            thumbnails=self.thumbnails,
            exports=self.exports,
        )

    def env(self) -> dict[str, str]:
        return {
            "MEDIA_ORIGINALS_PATH": str(self.originals),
            "MEDIA_PROXIES_PATH": str(self.proxies),
            "MEDIA_FINALS_PATH": str(self.finals),
            "MEDIA_THUMBNAILS_PATH": str(self.thumbnails),
            "MEDIA_EXPORTS_PATH": str(self.exports),
        }


def make_dirs(base: Path) -> MediaDirs:
    dirs = MediaDirs(
        base / "originals", base / "proxies", base / "thumbnails", base / "exports", base / "finals"
    )
    for path in (dirs.originals, dirs.proxies, dirs.thumbnails, dirs.exports, dirs.finals):
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def build_settings(dirs: MediaDirs, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "media_originals_path": dirs.originals,
        "media_proxies_path": dirs.proxies,
        "media_finals_path": dirs.finals,
        "media_thumbnails_path": dirs.thumbnails,
        "media_exports_path": dirs.exports,
        "media_scan_commit_every": 1000,
        "media_final_settle_seconds": 0,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def create_organization(session: Session) -> Organization:
    organization = Organization(name="Media Test Org", slug=f"media-test-{uuid.uuid4().hex[:10]}")
    session.add(organization)
    session.flush()
    return organization


def ensure_schema(engine: Engine, api_dir: Path) -> None:
    from alembic import command
    from alembic.config import Config

    config = Config(str(api_dir / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", str(engine.url.render_as_string(hide_password=False)))
    command.upgrade(config, "head")


def can_test_permissions() -> bool:
    return os.name == "posix" and os.geteuid() != 0
