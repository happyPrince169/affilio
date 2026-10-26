"""File identity without reading whole videos.

`quick_fingerprint` reads at most three 1 MiB samples (head, middle, tail) plus the size, which
is enough to detect moves and candidate duplicates cheaply over SMB/NFS. `full_hash` streams
the entire file and is only invoked when two files share a fingerprint.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

SAMPLE_BYTES = 1024 * 1024
CHUNK_BYTES = 1024 * 1024


def quick_fingerprint(path: Path, size: int | None = None) -> str:
    if size is None:
        size = path.stat().st_size
    digest = hashlib.sha256()
    digest.update(size.to_bytes(8, "big"))
    with path.open("rb") as handle:
        if size <= SAMPLE_BYTES * 3:
            for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
                digest.update(chunk)
        else:
            for offset in (0, (size // 2) - (SAMPLE_BYTES // 2), size - SAMPLE_BYTES):
                handle.seek(offset)
                digest.update(handle.read(SAMPLE_BYTES))
    return digest.hexdigest()


def full_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()
