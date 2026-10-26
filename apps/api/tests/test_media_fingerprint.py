from __future__ import annotations

from pathlib import Path

from affilio.modules.media.fingerprint import SAMPLE_BYTES, full_hash, quick_fingerprint
from tests.media_helpers import write_bytes


def test_fingerprint_is_stable_and_size_sensitive(tmp_path: Path) -> None:
    a = write_bytes(tmp_path / "a.bin", 10_000, seed=1)
    b = write_bytes(tmp_path / "b.bin", 10_000, seed=1)
    c = write_bytes(tmp_path / "c.bin", 10_001, seed=1)

    assert quick_fingerprint(a) == quick_fingerprint(b)
    assert quick_fingerprint(a) != quick_fingerprint(c)
    assert full_hash(a) == full_hash(b)


def test_fingerprint_samples_large_files(tmp_path: Path) -> None:
    size = SAMPLE_BYTES * 4
    big = write_bytes(tmp_path / "big.bin", size, seed=7)
    before = quick_fingerprint(big)
    # Modify bytes outside the sampled windows: fingerprint is unchanged, full hash differs.
    with big.open("r+b") as handle:
        handle.seek(SAMPLE_BYTES + 1000)
        handle.write(b"\xff" * 16)
    assert quick_fingerprint(big) == before
    # Modify the head: fingerprint changes.
    with big.open("r+b") as handle:
        handle.seek(10)
        handle.write(b"\x00" * 16)
    assert quick_fingerprint(big) != before
