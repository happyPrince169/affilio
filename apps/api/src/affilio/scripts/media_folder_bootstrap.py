#!/usr/bin/env python3
"""Create the standardized finals folder tree: {Owner}/{ProductCategory}/{ContentType}/.

Dry run by default: prints every planned directory with its state and creates nothing.
`--apply` creates only the directories that are missing. Nothing is ever moved, renamed,
overwritten, chmod-ed or deleted, existing directories (legacy `Hang/T9`, …) are left alone,
and the root itself must already exist (it is never created).

    python -m affilio.scripts.media_folder_bootstrap --root /path/to/FINAL
    python -m affilio.scripts.media_folder_bootstrap --root /path/to/FINAL --apply
    python -m affilio.scripts.media_folder_bootstrap --root /path/to/FINAL --json
    python -m affilio.scripts.media_folder_bootstrap --root /path/to/FINAL \
        --owner Hang --owner Hao --product-category Mens-Fashion --content-type Review

Standard library only (no affilio imports): the file can be copied to the NAS and run with
the system `python3` there, outside the containers, by an operator who can create folders on
the share. Exit codes: 0 ok, 1 nothing applied because of conflicts, 2 invalid arguments.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata

DEFAULT_OWNERS = ("Hang", "Hao", "Minh Anh")
DEFAULT_PRODUCT_CATEGORIES = ("Mens-Fashion", "Womens-Fashion", "Accessories", "Sports")
DEFAULT_CONTENT_TYPES = ("Review", "Try-On", "Couple", "Lifestyle", "Comparison")

STATE_EXISTS = "exists"
STATE_CREATE = "create"  # missing; would be / was created
STATE_CONFLICT = "conflict"  # a non-directory (file, symlink) occupies the path


def normalize(value: str) -> str:
    """Same matching key AFFILIO uses for folder names (see taxonomy.service.normalize_name)."""
    text = unicodedata.normalize("NFKC", value).casefold()
    for mark in "'\u2019\u2018`\u02bc":  # straight and curly apostrophes
        text = text.replace(mark, "")
    out = []
    space = False
    for ch in text:
        if ch.isalnum():
            out.append(ch)
            space = False
        elif not space:
            out.append(" ")
            space = True
    return "".join(out).strip()


def validate_names(label: str, names: list[str]) -> list[str]:
    cleaned = []
    seen = {}
    for raw in names:
        name = raw.strip()
        if not name:
            raise ValueError(f"{label}: empty name")
        if name in (".", "..") or "/" in name or "\\" in name or "\0" in name:
            raise ValueError(f"{label}: {name!r} is not a valid folder name")
        if name.startswith("."):
            raise ValueError(f"{label}: {name!r} must not start with a dot")
        if len(name) > 120:
            raise ValueError(f"{label}: {name!r} is longer than 120 characters")
        key = normalize(name)
        if not key:
            raise ValueError(f"{label}: {name!r} has no letters or digits")
        if key in seen:
            raise ValueError(
                f"{label}: {name!r} and {seen[key]!r} would be the same category "
                f"(both normalize to {key!r})"
            )
        seen[key] = name
        cleaned.append(name)
    return cleaned


def plan(root: str, owners: list[str], categories: list[str], content_types: list[str]) -> list:
    """Every directory of the tree (owners, owner/category, owner/category/type) with its
    current state. Parents come before children so creation order is right."""
    rows = []
    for owner in owners:
        rows.append(os.path.join(owner))
        for category in categories:
            rows.append(os.path.join(owner, category))
            for content_type in content_types:
                rows.append(os.path.join(owner, category, content_type))
    out = []
    for relative in rows:
        absolute = os.path.join(root, relative)
        if os.path.islink(absolute) or (os.path.lexists(absolute) and not os.path.isdir(absolute)):
            state = STATE_CONFLICT
        elif os.path.isdir(absolute):
            state = STATE_EXISTS
        else:
            state = STATE_CREATE
        out.append({"path": relative, "state": state})
    return out


def apply(root: str, entries: list) -> int:
    """Create the missing directories, parents first. Returns how many were created."""
    created = 0
    for entry in entries:
        if entry["state"] != STATE_CREATE:
            continue
        absolute = os.path.join(root, entry["path"])
        os.mkdir(absolute)  # never exist_ok: a race with another writer must surface
        created += 1
    return created


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create the {Owner}/{ProductCategory}/{ContentType} folder tree (dry run by "
        "default)."
    )
    parser.add_argument("--root", required=True, help="existing finals root directory")
    parser.add_argument("--owner", action="append", help="production owner (repeatable)")
    parser.add_argument(
        "--product-category", action="append", help="product category folder (repeatable)"
    )
    parser.add_argument("--content-type", action="append", help="content type folder (repeatable)")
    parser.add_argument("--apply", action="store_true", help="create the missing directories")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    root = os.path.abspath(args.root)
    if not os.path.isdir(root) or os.path.islink(args.root):
        print(f"error: root {root} is not an existing directory (it is never created)")
        return 2
    try:
        owners = validate_names("owner", args.owner or list(DEFAULT_OWNERS))
        categories = validate_names(
            "product category", args.product_category or list(DEFAULT_PRODUCT_CATEGORIES)
        )
        content_types = validate_names(
            "content type", args.content_type or list(DEFAULT_CONTENT_TYPES)
        )
    except ValueError as exc:
        print(f"error: {exc}")
        return 2

    entries = plan(root, owners, categories, content_types)
    conflicts = [e for e in entries if e["state"] == STATE_CONFLICT]
    missing = [e for e in entries if e["state"] == STATE_CREATE]
    created = 0
    applied = False
    if args.apply and not conflicts:
        created = apply(root, entries)
        applied = True

    summary = {
        "root": root,
        "mode": "apply" if applied else "dry-run",
        "planned": len(entries),
        "existing": sum(1 for e in entries if e["state"] == STATE_EXISTS),
        "missing": len(missing),
        "created": created,
        "conflicts": len(conflicts),
        "entries": entries,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"root: {root}")
        print(f"mode: {summary['mode']}")
        for entry in entries:
            marker = {STATE_EXISTS: "=", STATE_CREATE: "+", STATE_CONFLICT: "!"}[entry["state"]]
            print(f"  {marker} {entry['path']}  ({entry['state']})")
        print(
            f"{summary['planned']} directories planned: {summary['existing']} exist, "
            f"{summary['missing']} missing, {summary['conflicts']} conflicts"
            + (f", {created} created" if applied else "")
        )
        if conflicts:
            print("conflicts block --apply: a file or link occupies a planned folder path")
        elif not args.apply and missing:
            print("dry run: re-run with --apply to create the missing directories")
    if conflicts and args.apply:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
