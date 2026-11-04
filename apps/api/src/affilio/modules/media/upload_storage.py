"""Filesystem side of editor uploads (ADR 0012, amendment 2: dedicated upload root).

Everything an upload writes lives under the AFFILIO_UPLOADS root (`MEDIA_UPLOADS_PATH`), an
application-managed directory tree that no other system writes:

    <uploads>/staging/<organization_id>/<32 hex>.part                 chunks, written by the API
    <uploads>/ready/<organization_id>/<YYYY>/<MM>/<upload uuid>.<ext>  finalized, written once

Two write surfaces, both confined to this module:

- `StagingStore` (`staging/`): files are named by a server-generated token, created
  exclusively, opened `O_NOFOLLOW`, must be regular files owned by this uid with exactly one
  name (`st_nlink == 1`), and are only ever written at the committed offset. Deletes happen
  only here.
- `ReadyStore` (`ready/`): the destination directory is walked one component at a time with
  `O_DIRECTORY | O_NOFOLLOW` using exact, validated names (never anything a client sent);
  missing directories are created; the staged file is moved under a UUID name with
  `rename_noreplace`, an atomic rename on the same filesystem that refuses to replace an
  existing name (`renameat2(RENAME_NOREPLACE)`, with an exclusive-reservation fallback where
  the kernel or filesystem lacks the flag). Nothing here ever opens a finalized file for
  writing, renames or deletes one, or follows a symlink.

The legacy FINAL tree is not named anywhere in this module: it stays read-only in every
process. Classification (owner, product category, content type) is database metadata, so a
reclassification never moves a file (the physical name carries no meaning).
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import errno
import os
import re
import secrets
import stat as stat_module
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

STAGING_DIRNAME = "staging"
READY_DIRNAME = "ready"
STAGING_SUFFIX = ".part"
DIR_MODE = 0o750
FILE_MODE = 0o640
MAX_FILENAME = 120

_UNSAFE = re.compile(r"[\x00-\x1f\x7f/\\]")
_SPACES = re.compile(r"\s+")
_STAGING_NAME = re.compile(r"[0-9a-f]{32}")
_EXTENSION = re.compile(r"\.[a-z0-9]{1,8}")
# Directory components the ready tree is allowed to contain: organization uuid, year, month.
_READY_COMPONENT = re.compile(r"[0-9a-f-]{36}|\d{4}|\d{2}")

RENAME_NOREPLACE = 1  # <linux/fs.h>
_AT_FDCWD = -100


class StorageError(Exception):
    """A filesystem precondition failed (symlink, non-regular file, missing path)."""


# ---------------------------------------------------------------- names


def sanitize_filename(original: str, allowed_extensions: set[str]) -> str:
    """Client filename → safe display name: NFKC, control and separator characters removed,
    leading dots and spaces stripped, whitespace collapsed, length bounded while keeping the
    extension, which must be allowed. Raises ValueError otherwise. Used for the asset title
    and the physical file's extension only; it never becomes a path by itself."""
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


def physical_name(upload_id: uuid.UUID, safe_filename: str) -> str:
    """UUID-based physical filename: `<upload id>.<ext>`. The extension is the only part of
    the client's name that reaches the filesystem, and only after `sanitize_filename`."""
    _, dot, ext = safe_filename.rpartition(".")
    ext = f".{ext.lower()}" if dot else ""
    if not _EXTENSION.fullmatch(ext):
        raise StorageError("filename extension is not usable")
    return f"{upload_id}{ext}"


def new_staging_name() -> str:
    return secrets.token_hex(16)


# ---------------------------------------------------------------- no-overwrite rename


def _libc_renameat2():
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        fn = libc.renameat2
    except (OSError, AttributeError):
        return None
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    fn.restype = ctypes.c_int
    return fn


_RENAMEAT2 = _libc_renameat2()


def rename_noreplace(
    src: Path, dst_dir_fd: int, dst_name: str, *, use_renameat2: bool = True
) -> str:
    """Atomically move `src` to `dst_name` inside the directory `dst_dir_fd`, refusing to
    replace an existing entry (FileExistsError). Same filesystem only (rename semantics).

    Primary: `renameat2(2)` with RENAME_NOREPLACE, atomic by the kernel. Fallback (flag not
    supported by the kernel or filesystem, or no libc symbol): the destination name is first
    reserved with an exclusive create, then the staged file is renamed over that reservation,
    so the only entry ever replaced is the empty file this call created a moment earlier.
    Returns the strategy used ("renameat2" or "reserve") for diagnostics and tests."""
    if use_renameat2 and _RENAMEAT2 is not None:
        rc = _RENAMEAT2(
            _AT_FDCWD, os.fsencode(src), dst_dir_fd, os.fsencode(dst_name), RENAME_NOREPLACE
        )
        if rc == 0:
            return "renameat2"
        err = ctypes.get_errno()
        if err == errno.EEXIST:
            raise FileExistsError(err, os.strerror(err), dst_name)
        if err not in (errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP):
            raise OSError(err, os.strerror(err), str(src))
    # Fallback: exclusive reservation, then rename over our own reservation only.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open(dst_name, flags, FILE_MODE, dir_fd=dst_dir_fd)
    os.close(fd)
    try:
        os.rename(src, dst_name, dst_dir_fd=dst_dir_fd)
    except OSError:
        try:
            info = os.lstat(dst_name, dir_fd=dst_dir_fd)
            if stat_module.S_ISREG(info.st_mode) and info.st_size == 0:
                os.unlink(dst_name, dir_fd=dst_dir_fd)  # only the empty reservation
        except OSError:
            pass
        raise
    return "reserve"


# ---------------------------------------------------------------- layout


def ensure_layout(uploads_root: Path) -> None:
    """Create `staging/` and `ready/` under the uploads root (worker, writable process)."""
    root = Path(uploads_root)
    if root.is_symlink() or not root.is_dir():
        raise StorageError("uploads root is missing or a symlink")
    for name in (STAGING_DIRNAME, READY_DIRNAME):
        child = root / name
        if child.is_symlink():
            raise StorageError(f"{name} under the uploads root is a symlink")
        child.mkdir(mode=DIR_MODE, exist_ok=True)


# ---------------------------------------------------------------- staging


@dataclass(slots=True)
class StagingStore:
    root: Path  # the `staging` directory itself

    @classmethod
    def for_root(cls, uploads_root: Path | None) -> StagingStore | None:
        return cls(Path(uploads_root) / STAGING_DIRNAME) if uploads_root else None

    def available(self) -> bool:
        return (
            not self.root.is_symlink()
            and self.root.is_dir()
            and os.access(self.root, os.W_OK | os.X_OK)
        )

    def ensure(self) -> None:
        """Create the staging directory when the uploads root exists (API side: this is the
        one directory the API may create)."""
        parent = self.root.parent
        if parent.is_symlink() or not parent.is_dir() or self.root.is_symlink():
            return
        self.root.mkdir(mode=DIR_MODE, exist_ok=True)

    def org_dir(self, organization_id: uuid.UUID) -> Path:
        return self.root / str(organization_id)

    def path_for(self, organization_id: uuid.UUID, staging_name: str) -> Path:
        if not _STAGING_NAME.fullmatch(staging_name):
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
        """fd for `pwrite`; the target must be a regular file owned by this process with a
        single name (a staged file that has been finalized is never reachable this way)."""
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
        if (
            not stat_module.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
        ):
            os.close(fd)
            raise StorageError("staging file is not a regular, singly-linked file of this service")
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
                if entry.suffix == STAGING_SUFFIX and _STAGING_NAME.fullmatch(entry.stem):
                    out.append(entry)
        return out

    def free_bytes(self) -> int:
        vfs = os.statvfs(self.root)
        return vfs.f_bavail * vfs.f_frsize


# ---------------------------------------------------------------- ready (finalized files)


@dataclass(slots=True)
class ReadyStore:
    root: Path  # the `ready` directory itself

    @classmethod
    def for_root(cls, uploads_root: Path) -> ReadyStore:
        return cls(Path(uploads_root) / READY_DIRNAME)

    def available(self) -> bool:
        return (
            not self.root.is_symlink()
            and self.root.is_dir()
            and os.access(self.root, os.W_OK | os.X_OK)
        )

    @staticmethod
    def directory_parts(organization_id: uuid.UUID, when: datetime) -> list[str]:
        """`<organization>/<YYYY>/<MM>`: a date shard for operators browsing the share. It is
        not a classification; nothing moves when metadata changes."""
        return [str(organization_id), when.strftime("%Y"), when.strftime("%m")]

    @staticmethod
    def relative_path(parts: list[str], filename: str) -> str:
        return "/".join([READY_DIRNAME, *parts, filename])

    def open_directory(self, parts: list[str], *, create: bool) -> int:
        """Open `ready/<parts…>` one component at a time with `O_DIRECTORY | O_NOFOLLOW`,
        creating missing components when `create` is set. Only the fixed component shapes
        (organization uuid, year, month) are accepted. Returns the open directory fd."""
        if self.root.is_symlink():
            raise StorageError("ready directory is a symlink")
        try:
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as exc:
            raise StorageError(f"ready directory unavailable: {exc.strerror}") from exc
        try:
            for name in parts:
                if not _READY_COMPONENT.fullmatch(name):
                    raise StorageError(f"unexpected ready path component {name!r}")
                try:
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                except FileNotFoundError:
                    if not create:
                        raise StorageError(f"ready directory {name!r} is missing") from None
                    with contextlib.suppress(FileExistsError):  # created concurrently
                        os.mkdir(name, DIR_MODE, dir_fd=fd)
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                except OSError as exc:
                    if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                        raise StorageError(f"{name!r} is a symlink or not a directory") from exc
                    raise
                os.close(fd)
                fd = child
            return fd
        except Exception:
            os.close(fd)
            raise

    @staticmethod
    def stat_entry(dir_fd: int, filename: str) -> os.stat_result | None:
        try:
            info = os.lstat(filename, dir_fd=dir_fd)
        except FileNotFoundError:
            return None
        if not stat_module.S_ISREG(info.st_mode):
            raise StorageError("destination entry is not a regular file")
        return info

    @staticmethod
    def place(staging_path: Path, dir_fd: int, filename: str) -> str:
        """Move the staged file under its UUID name. Raises FileExistsError when the name is
        taken (never overwrites). The staging name disappears in the same operation."""
        return rename_noreplace(staging_path, dir_fd, filename)

    @staticmethod
    def replace_reservation(staging_path: Path, dir_fd: int, filename: str) -> None:
        """Recovery only: an empty reservation left by an interrupted fallback rename is the
        one entry a rename may replace. The caller has proven it is empty, regular and ours."""
        os.rename(staging_path, filename, dst_dir_fd=dir_fd)


__all__ = [
    "DIR_MODE",
    "FILE_MODE",
    "READY_DIRNAME",
    "RENAME_NOREPLACE",
    "STAGING_DIRNAME",
    "ReadyStore",
    "StagingStore",
    "StorageError",
    "ensure_layout",
    "new_staging_name",
    "physical_name",
    "rename_noreplace",
    "sanitize_filename",
]
