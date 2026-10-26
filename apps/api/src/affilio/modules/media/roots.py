"""Logical media roots.

A root is a (key, kind, access, path) binding. Keys and kinds are stored in `media_roots`;
the PATH comes from settings at runtime so the database never contains host-specific paths
and the same database works on the Ubuntu workstation and inside the Synology container.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from affilio.core.config import Settings
from affilio.core.enums import MediaRootAccess, MediaRootKind
from affilio.modules.media.errors import InvalidMediaPathError
from affilio.modules.media.models import MediaRoot

ROOT_KEYS: dict[str, tuple[MediaRootKind, MediaRootAccess, str]] = {
    "originals": (MediaRootKind.ORIGINAL, MediaRootAccess.READ_ONLY, "Original videos"),
    "proxies": (MediaRootKind.PROXY, MediaRootAccess.READ_ONLY, "Proxy videos"),
    "finals": (MediaRootKind.FINAL, MediaRootAccess.READ_ONLY, "Final videos (handoff)"),
    "thumbnails": (MediaRootKind.THUMBNAIL, MediaRootAccess.READ_WRITE, "Generated thumbnails"),
    "exports": (MediaRootKind.EXPORT, MediaRootAccess.READ_WRITE, "Publish-ready exports"),
}

_MAX_RELATIVE_PATH = 1024
SCANNABLE_KINDS: frozenset[MediaRootKind] = frozenset({MediaRootKind.ORIGINAL, MediaRootKind.FINAL})


@dataclass(frozen=True, slots=True)
class RootAvailability:
    exists: bool
    readable: bool
    writable: bool


@dataclass(frozen=True, slots=True)
class RootBinding:
    key: str
    kind: MediaRootKind
    access: MediaRootAccess
    path: Path  # absolute, as configured (not necessarily existing)

    @property
    def is_writable_kind(self) -> bool:
        return self.access == MediaRootAccess.READ_WRITE

    def availability(self) -> RootAvailability:
        exists = self.path.is_dir()
        readable = exists and os.access(self.path, os.R_OK | os.X_OK)
        writable = exists and self.is_writable_kind and os.access(self.path, os.W_OK)
        return RootAvailability(exists=exists, readable=readable, writable=writable)

    def resolve(self, relative_path: str) -> Path:
        """Join a stored relative path onto the root, refusing anything that escapes it."""
        normalized = normalize_relative_path(relative_path)
        real_root = Path(os.path.realpath(self.path))
        candidate = Path(os.path.realpath(real_root / normalized))
        if candidate != real_root and real_root not in candidate.parents:
            msg = "Path escapes its media root"
            raise InvalidMediaPathError(msg)
        return candidate

    def relative_to_root(self, absolute: Path) -> str:
        real_root = Path(os.path.realpath(self.path))
        try:
            rel = Path(os.path.realpath(absolute)).relative_to(real_root)
        except ValueError as exc:
            msg = "Path is outside its media root"
            raise InvalidMediaPathError(msg) from exc
        return rel.as_posix()


def normalize_relative_path(value: str) -> str:
    """Validate a POSIX relative path: no absolute, no `..`, no empty/dot segments, bounded."""
    if not isinstance(value, str) or not value or len(value) > _MAX_RELATIVE_PATH:
        msg = "Relative path is empty or too long"
        raise InvalidMediaPathError(msg)
    if "\x00" in value or value.startswith(("/", "\\")) or "\\" in value:
        msg = "Relative path must be a POSIX relative path"
        raise InvalidMediaPathError(msg)
    parts = PurePosixPath(value).parts
    if not parts or any(part in {"..", ".", ""} for part in parts):
        msg = "Relative path contains forbidden segments"
        raise InvalidMediaPathError(msg)
    return PurePosixPath(*parts).as_posix()


class MediaRootRegistry:
    """Resolves root keys to bindings. Built from settings or explicitly (tests)."""

    def __init__(self, bindings: dict[str, RootBinding]) -> None:
        self._bindings = dict(bindings)

    @classmethod
    def from_paths(
        cls,
        *,
        originals: Path | None = None,
        proxies: Path | None = None,
        finals: Path | None = None,
        thumbnails: Path | None = None,
        exports: Path | None = None,
    ) -> MediaRootRegistry:
        provided = {
            "originals": originals,
            "proxies": proxies,
            "finals": finals,
            "thumbnails": thumbnails,
            "exports": exports,
        }
        bindings: dict[str, RootBinding] = {}
        for key, path in provided.items():
            if path is None:
                continue
            kind, access, _ = ROOT_KEYS[key]
            bindings[key] = RootBinding(key=key, kind=kind, access=access, path=Path(path))
        return cls(bindings)

    @classmethod
    def from_settings(cls, settings: Settings) -> MediaRootRegistry:
        return cls.from_paths(
            originals=settings.media_originals_path,
            proxies=settings.media_proxies_path,
            finals=settings.media_finals_path,
            thumbnails=settings.media_thumbnails_path,
            exports=settings.media_exports_path,
        )

    def get(self, key: str) -> RootBinding | None:
        return self._bindings.get(key)

    def keys(self) -> list[str]:
        return list(ROOT_KEYS)

    @property
    def originals(self) -> RootBinding | None:
        return self._bindings.get("originals")

    @property
    def proxies(self) -> RootBinding | None:
        return self._bindings.get("proxies")

    @property
    def finals(self) -> RootBinding | None:
        return self._bindings.get("finals")

    @property
    def thumbnails(self) -> RootBinding | None:
        return self._bindings.get("thumbnails")

    @property
    def exports(self) -> RootBinding | None:
        return self._bindings.get("exports")

    def ensure_db_roots(self, session: Session, organization_id: uuid.UUID) -> dict[str, MediaRoot]:
        """Make sure a `media_roots` row exists for every known key (configured or not)."""
        existing = {
            root.key: root
            for root in session.scalars(
                select(MediaRoot).where(MediaRoot.organization_id == organization_id)
            )
        }
        for key, (kind, access, name) in ROOT_KEYS.items():
            if key in existing:
                continue
            root = MediaRoot(
                organization_id=organization_id,
                key=key,
                kind=kind,
                access=access,
                name=name,
                scan_enabled=kind in SCANNABLE_KINDS,
            )
            session.add(root)
            existing[key] = root
        session.flush()
        return existing
