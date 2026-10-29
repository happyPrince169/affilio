"""Filesystem side of editor uploads (upload track U4, ADR 0012).

Two write surfaces, both confined to this module:

- `StagingStore`: the hidden staging directory `FINAL/.affilio-incoming`. The API sees it as
  its own writable bind (`MEDIA_INCOMING_PATH`); the worker reaches the same directory through
  the finals root (`<finals>/.affilio-incoming`). Files are named by a server-generated token,
  created exclusively, opened `O_NOFOLLOW`, and only ever written at the committed offset.
  Deletes happen only here.
- `FinalsPlacer`: resolves the destination directory from *category names* (never from
  client strings) by walking the finals root one component at a time with `O_DIRECTORY |
  O_NOFOLLOW`, matching existing folders by `normalize_name`, creating missing ones, and
  places a staged file with `link` (fails with `EEXIST` instead of overwriting). The
  staging copy is removed afterwards by the caller; link + unlink is a recoverable sequence,
  not a transaction.

Nothing here opens an existing final for writing, renames or deletes anything outside the
staging directory, or follows a symlink.
"""

from __future__ import annotations

import contextlib
import errno
import os
import re
import secrets
import stat as stat_module
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path

from affilio.modules.taxonomy.service import normalize_name

STAGING_DIRNAME = ".affilio-incoming"
STAGING_SUFFIX = ".part"
DIR_MODE = 0o750
FILE_MODE = 0o640
MAX_FILENAME = 120
MAX_FOLDER_NAME = 120

_UNSAFE = re.compile(r"[\x00-\x1f\x7f/\\]")
_SPACES = re.compile(r"\s+")


class StorageError(Exception):
    """A filesystem precondition failed (symlink, non-regular file, missing path)."""


# ---------------------------------------------------------------- names


def sanitize_filename(original: str, allowed_extensions: set[str]) -> str:
    """Client filename → safe basename: NFKC, control and separator characters removed,
    leading dots and spaces stripped, whitespace collapsed, length bounded while keeping
    the extension, which must be allowed. Raises ValueError otherwise."""
    name = unicodedata.normalize("NFKC", original or "")
    name = _UNSAFE.sub("", name)
    name = _SPACES.sub(" ", name).strip().lstrip(".").strip()
    if not name or name in {".", ".."}:
        raise ValueError("filename is empty after sanitising")
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        raise ValueError("filename has no extension")
    ext = "." + ext.lower()
    if ext not in allowed_extensions:
        raise ValueError(f"extension {ext!r} is not an allowed video extension")
    stem = stem.strip()
    budget = MAX_FILENAME - len(ext)
    if len(stem) > budget:
        stem = stem[:budget].rstrip()
    return f"{stem}{ext}"


def folder_name_for(category_name: str) -> str:
    """Category display name → folder name (first-time creation only). NFKC, separators and
    control characters removed, no leading dot, bounded."""
    name = unicodedata.normalize("NFKC", category_name or "")
    name = _UNSAFE.sub("", name)
    name = _SPACES.sub(" ", name).strip().lstrip(".").strip()
    if not name or not normalize_name(name):
        raise ValueError("category name yields no usable folder name")
    return name[:MAX_FOLDER_NAME].rstrip()


def with_suffix(filename: str, token: str) -> str:
    stem, dot, ext = filename.rpartition(".")
    return f"{stem}-{token}.{ext}" if dot else f"{filename}-{token}"


def new_staging_name() -> str:
    return secrets.token_hex(16)


# ---------------------------------------------------------------- staging


@dataclass(slots=True)
class StagingStore:
    root: Path  # the `.affilio-incoming` directory itself

    @classmethod
    def for_api(cls, incoming_path: Path | None) -> StagingStore | None:
        return cls(Path(incoming_path)) if incoming_path else None

    @classmethod
    def for_worker(cls, finals_path: Path) -> StagingStore:
        return cls(Path(finals_path) / STAGING_DIRNAME)

    def available(self) -> bool:
        return self.root.is_dir() and os.access(self.root, os.W_OK | os.X_OK)

    def org_dir(self, organization_id: uuid.UUID) -> Path:
        return self.root / str(organization_id)

    def path_for(self, organization_id: uuid.UUID, staging_name: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", staging_name):
            raise StorageError("invalid staging name")
        return self.org_dir(organization_id) / f"{staging_name}{STAGING_SUFFIX}"

    def create(self, organization_id: uuid.UUID, staging_name: str) -> Path:
        """Create the empty staging file exclusively (never reuses an existing one)."""
        if not self.available():
            raise StorageError("staging directory is not available")
        org_dir = self.org_dir(organization_id)
        if os.path.islink(org_dir):
            raise StorageError("staging organization directory is a symlink")
        org_dir.mkdir(mode=DIR_MODE, exist_ok=True)
        path = self.path_for(organization_id, staging_name)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, FILE_MODE)
        os.close(fd)
        return path

    def open_for_write(self, organization_id: uuid.UUID, staging_name: str) -> int:
        """fd for `pwrite`; the target must be a regular file owned by this process."""
        path = self.path_for(organization_id, staging_name)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_NOFOLLOW)
        except FileNotFoundError as exc:
            raise StorageError("staging file is missing") from exc
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise StorageError("staging file is a symlink") from exc
            raise
        info = os.fstat(fd)
        if not stat_module.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            os.close(fd)
            raise StorageError("staging file is not a regular file of this service")
        return fd

    def stat_regular(self, organization_id: uuid.UUID, staging_name: str) -> os.stat_result | None:
        """lstat of the staging file; None when missing; StorageError for a symlink or a
        non-regular file."""
        path = self.path_for(organization_id, staging_name)
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return None
        if stat_module.S_ISLNK(info.st_mode):
            raise StorageError("staging file is a symlink")
        if not stat_module.S_ISREG(info.st_mode):
            raise StorageError("staging file is not a regular file")
        return info

    def truncate(self, organization_id: uuid.UUID, staging_name: str, size: int) -> None:
        fd = self.open_for_write(organization_id, staging_name)
        try:
            os.ftruncate(fd, size)
        finally:
            os.close(fd)

    def remove(self, organization_id: uuid.UUID, staging_name: str) -> bool:
        """Unlink the staging file (only ever inside the staging directory, only a regular
        file). Returns False when it was already gone."""
        path = self.path_for(organization_id, staging_name)
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return False
        if not stat_module.S_ISREG(info.st_mode):
            raise StorageError("refusing to remove a non-regular staging entry")
        os.unlink(path)
        return True

    def orphan_parts(self) -> list[Path]:
        """Every `*.part` file under the staging directory (one level of organization dirs)."""
        out: list[Path] = []
        if not self.root.is_dir():
            return out
        for org_dir in self.root.iterdir():
            if org_dir.is_symlink() or not org_dir.is_dir():
                continue
            for entry in org_dir.iterdir():
                if entry.is_symlink() or not entry.is_file():
                    continue
                if entry.suffix == STAGING_SUFFIX and re.fullmatch(r"[0-9a-f]{32}", entry.stem):
                    out.append(entry)
        return out

    def free_bytes(self) -> int:
        vfs = os.statvfs(self.root)
        return vfs.f_bavail * vfs.f_frsize


# ---------------------------------------------------------------- placement into finals


@dataclass(slots=True)
class FinalsPlacer:
    finals_root: Path

    def resolve_directory(self, category_names: list[str]) -> tuple[int, list[str]]:
        """Walk `finals_root/<owner>/<product>/<type>` by category names. Each segment is
        matched to an existing folder by normalized name (so `Mens-Fashion` and "Men's
        Fashion" are the same folder) or created from the sanitized category name. Returns
        the open directory fd of the deepest folder and the folder names actually used.
        Symlinks and non-directories anywhere on the path abort with StorageError."""
        fd = os.open(self.finals_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        used: list[str] = []
        try:
            for name in category_names:
                wanted = normalize_name(name)
                chosen: str | None = None
                for entry in sorted(os.listdir(fd)):
                    if entry.startswith("."):
                        continue
                    if normalize_name(entry) == wanted:
                        chosen = entry
                        break
                if chosen is None:
                    chosen = folder_name_for(name)
                    with contextlib.suppress(FileExistsError):  # created concurrently
                        os.mkdir(chosen, DIR_MODE, dir_fd=fd)
                try:
                    child = os.open(chosen, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                except OSError as exc:
                    if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                        raise StorageError(f"{chosen!r} is a symlink or not a directory") from exc
                    raise
                os.close(fd)
                fd = child
                used.append(chosen)
            return fd, used
        except Exception:
            os.close(fd)
            raise

    @staticmethod
    def entry_exists(dir_fd: int, filename: str) -> bool:
        try:
            os.lstat(filename, dir_fd=dir_fd)
            return True
        except FileNotFoundError:
            return False

    @staticmethod
    def link_into(staging_path: Path, dir_fd: int, filename: str) -> None:
        """Hard-link the staged file under its final name. Raises FileExistsError when the
        name is taken (never overwrites); the staging copy stays until the caller unlinks it."""
        os.link(staging_path, filename, dst_dir_fd=dir_fd, follow_symlinks=False)

    @staticmethod
    def stat_entry(dir_fd: int, filename: str) -> os.stat_result | None:
        try:
            info = os.lstat(filename, dir_fd=dir_fd)
        except FileNotFoundError:
            return None
        if not stat_module.S_ISREG(info.st_mode):
            raise StorageError("destination entry is not a regular file")
        return info


__all__ = [
    "DIR_MODE",
    "FILE_MODE",
    "STAGING_DIRNAME",
    "FinalsPlacer",
    "StagingStore",
    "StorageError",
    "folder_name_for",
    "new_staging_name",
    "sanitize_filename",
    "with_suffix",
]
