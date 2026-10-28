"""media_folder_bootstrap: dry run creates nothing, --apply creates only missing folders and
never touches files or existing folders. No database."""

from __future__ import annotations

import json
from pathlib import Path

from affilio.scripts import media_folder_bootstrap as boot


def _snapshot(root: Path) -> dict[str, tuple[float, int] | str]:
    """Files with mtime and size; directories by presence only (adding a child folder
    legitimately bumps the parent directory's mtime)."""
    out: dict[str, tuple[float, int] | str] = {}
    for path in root.rglob("*"):
        stat = path.lstat()
        key = str(path.relative_to(root))
        out[key] = "dir" if path.is_dir() else (stat.st_mtime, stat.st_size)
    return out


def test_dry_run_plans_the_tree_and_creates_nothing(tmp_path: Path, capsys) -> None:
    root = tmp_path / "FINAL"
    (root / "Hang" / "T9").mkdir(parents=True)
    (root / "Hang" / "T9" / "legacy.mp4").write_bytes(b"x" * 10)
    before = _snapshot(root)

    assert boot.main(["--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "mode: dry-run" in out and "+ Hang/Mens-Fashion/Review  (create)" in out
    assert "= Hang  (exists)" in out
    # 3 owners + 3*4 categories + 3*4*5 types
    assert "75 directories planned: 1 exist, 74 missing, 0 conflicts" in out
    assert _snapshot(root) == before  # nothing created, nothing touched


def test_apply_creates_only_missing_directories_and_is_idempotent(tmp_path: Path, capsys) -> None:
    root = tmp_path / "FINAL"
    legacy = root / "Hang" / "T9"
    legacy.mkdir(parents=True)
    legacy_file = legacy / "legacy.mp4"
    legacy_file.write_bytes(b"x" * 10)
    existing = root / "Hao" / "Sports"
    existing.mkdir(parents=True)
    before = _snapshot(root)

    assert boot.main(["--root", str(root), "--apply", "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["mode"] == "apply" and summary["created"] == 72 and summary["conflicts"] == 0
    assert summary["existing"] == 3  # Hang, Hao, Hao/Sports
    for owner in ("Hang", "Hao", "Minh Anh"):
        for category in ("Mens-Fashion", "Womens-Fashion", "Accessories", "Sports"):
            for content_type in ("Review", "Try-On", "Couple", "Lifestyle", "Comparison"):
                assert (root / owner / category / content_type).is_dir()
    # Pre-existing entries are byte-for-byte and mtime untouched; legacy folder kept.
    after = _snapshot(root)
    assert {k: after[k] for k in before} == before
    assert legacy_file.read_bytes() == b"x" * 10 and (root / "Hang" / "T9").is_dir()

    assert boot.main(["--root", str(root), "--apply", "--json"]) == 0
    again = json.loads(capsys.readouterr().out)
    assert again["created"] == 0 and again["missing"] == 0 and again["existing"] == 75


def test_conflicts_block_apply_and_are_reported(tmp_path: Path, capsys) -> None:
    root = tmp_path / "FINAL"
    (root / "Hang" / "Sports").mkdir(parents=True)
    (root / "Hang" / "Sports" / "Review").write_bytes(b"not a folder")
    assert boot.main(["--root", str(root), "--apply"]) == 1
    out = capsys.readouterr().out
    assert "! Hang/Sports/Review  (conflict)" in out and "conflicts block --apply" in out
    assert (root / "Hang" / "Sports" / "Review").read_bytes() == b"not a folder"
    assert not (root / "Hao").exists()  # nothing at all was created


def test_root_must_exist_and_names_are_validated(tmp_path: Path, capsys) -> None:
    assert boot.main(["--root", str(tmp_path / "missing")]) == 2
    assert not (tmp_path / "missing").exists()
    root = tmp_path / "FINAL"
    root.mkdir()
    assert boot.main(["--root", str(root), "--owner", "../x"]) == 2
    assert boot.main(["--root", str(root), "--owner", ".hidden"]) == 2
    assert (
        boot.main(["--root", str(root), "--content-type", "Try-On", "--content-type", "try on"])
        == 2
    )
    assert "same category" in capsys.readouterr().out
    assert _snapshot(root) == {}


def test_custom_lists_and_normalization(tmp_path: Path, capsys) -> None:
    root = tmp_path / "FINAL"
    root.mkdir()
    code = boot.main(
        [
            "--root",
            str(root),
            "--owner",
            "Minh Anh",
            "--product-category",
            "Mens-Fashion",
            "--content-type",
            "Review",
            "--apply",
            "--json",
        ]
    )
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert [e["path"] for e in summary["entries"]] == [
        "Minh Anh",
        "Minh Anh/Mens-Fashion",
        "Minh Anh/Mens-Fashion/Review",
    ]
    assert boot.normalize("Men's Fashion") == boot.normalize("Mens-Fashion") == "mens fashion"
