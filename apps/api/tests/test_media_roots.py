from __future__ import annotations

import os
from pathlib import Path

import pytest

from affilio.core.enums import MediaRootAccess, MediaRootKind
from affilio.modules.media.errors import InvalidMediaPathError
from affilio.modules.media.roots import MediaRootRegistry, RootBinding, normalize_relative_path


@pytest.mark.parametrize(
    "value",
    ["../etc/passwd", "/abs/path", "a/../../b", "", "a\\b", "a/..", "..", "a\x00b"],
)
def test_normalize_rejects_unsafe_paths(value: str) -> None:
    with pytest.raises(InvalidMediaPathError):
        normalize_relative_path(value)


def test_normalize_keeps_plain_nested_paths() -> None:
    assert normalize_relative_path("2026/clips/a.mp4") == "2026/clips/a.mp4"
    assert normalize_relative_path("clip.mp4") == "clip.mp4"
    assert normalize_relative_path("a/./b.mp4") == "a/b.mp4"


def test_resolve_refuses_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.mp4").write_bytes(b"x")
    os.symlink(outside, root / "link")
    binding = RootBinding("originals", MediaRootKind.ORIGINAL, MediaRootAccess.READ_ONLY, root)

    with pytest.raises(InvalidMediaPathError):
        binding.resolve("link/secret.mp4")
    assert binding.resolve("a/b.mp4") == (root / "a" / "b.mp4").resolve()


def test_availability_reports_missing_and_readonly(tmp_path: Path) -> None:
    missing = RootBinding(
        "originals", MediaRootKind.ORIGINAL, MediaRootAccess.READ_ONLY, tmp_path / "nope"
    )
    assert missing.availability().exists is False
    present = RootBinding(
        "thumbnails", MediaRootKind.THUMBNAIL, MediaRootAccess.READ_WRITE, tmp_path
    )
    availability = present.availability()
    assert availability.exists and availability.readable and availability.writable
    read_only_kind = RootBinding(
        "originals", MediaRootKind.ORIGINAL, MediaRootAccess.READ_ONLY, tmp_path
    )
    assert read_only_kind.availability().writable is False  # never writable by policy


def test_registry_only_binds_configured_roots(tmp_path: Path) -> None:
    registry = MediaRootRegistry.from_paths(originals=tmp_path)
    assert registry.originals is not None
    assert registry.proxies is None
    assert registry.get("thumbnails") is None
    assert set(registry.keys()) == {"originals", "proxies", "finals", "thumbnails", "exports"}
