"""Thumbnail generation with ffmpeg.

- Source: the proxy variant when present (smaller, faster to decode), else the original.
- Output: `<thumbnails root>/<id[:2]>/<asset id>.jpg`, written to a temp file and renamed
  atomically. Only the thumbnails root is ever written to.
- Resource limits: single-threaded ffmpeg, bounded concurrency, hard timeout.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from affilio.core.config import Settings
from affilio.core.enums import (
    MediaAssetStatus,
    MediaVariantKind,
    MediaVariantStatus,
    ThumbnailStatus,
)
from affilio.modules.media.models import MediaAsset, MediaFileVariant, MediaRoot
from affilio.modules.media.roots import MediaRootRegistry

log = structlog.get_logger(__name__)


class ThumbnailError(Exception):
    pass


def thumbnail_relative_path(asset_id: uuid.UUID) -> str:
    hex_id = asset_id.hex
    return f"{hex_id[:2]}/{hex_id}.jpg"


def seek_position(duration_seconds: float | None) -> float:
    if not duration_seconds or duration_seconds <= 0:
        return 0.0
    return round(min(3.0, max(0.0, duration_seconds * 0.1)), 3)


def run_ffmpeg_thumbnail(
    *,
    ffmpeg: str,
    source: Path,
    target: Path,
    seek: float,
    width: int,
    timeout_seconds: int,
) -> None:
    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-threads",
        "1",
        "-ss",
        f"{seek:.3f}",
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-vf",
        f"scale='min({width},iw)':-2",
        "-q:v",
        "3",
        "-f",
        "image2",
        str(target),
    ]
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            command,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise ThumbnailError(f"{ffmpeg} not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise ThumbnailError(f"ffmpeg timed out after {timeout_seconds}s") from exc
    if completed.returncode != 0 or not target.exists() or target.stat().st_size == 0:
        stderr = completed.stderr.decode("utf-8", "replace").strip()
        raise ThumbnailError((stderr or "ffmpeg produced no output")[:500])


class ThumbnailGenerator:
    def __init__(self, settings: Settings, registry: MediaRootRegistry) -> None:
        self.settings = settings
        self.registry = registry

    def pick_source(self, asset: MediaAsset) -> tuple[str, MediaFileVariant] | None:
        proxy = asset.variant(MediaVariantKind.PROXY)
        if (
            proxy is not None
            and proxy.status == MediaVariantStatus.PRESENT
            and not proxy.probe_error
        ):
            return "proxies", proxy
        original = asset.variant(MediaVariantKind.ORIGINAL)
        if original is not None and original.status == MediaVariantStatus.PRESENT:
            return "originals", original
        final = asset.variant(MediaVariantKind.FINAL)
        if final is not None and final.status == MediaVariantStatus.PRESENT:
            return "finals", final
        return None

    def generate(self, session: Session, asset: MediaAsset) -> bool:
        """Generate one thumbnail. Returns True on success. Records failure state on the asset."""
        now = datetime.now(UTC)
        thumbs = self.registry.thumbnails
        if thumbs is None or not thumbs.availability().writable:
            asset.thumbnail_status = ThumbnailStatus.SKIPPED
            asset.thumbnail_error = "thumbnails root not configured or not writable"
            return False
        source = self.pick_source(asset)
        if source is None or asset.status != MediaAssetStatus.ACTIVE:
            asset.thumbnail_status = ThumbnailStatus.SKIPPED
            asset.thumbnail_error = "no readable source file"
            return False
        source_key, source_variant = source
        source_binding = self.registry.get(source_key)
        if source_binding is None:
            asset.thumbnail_status = ThumbnailStatus.SKIPPED
            asset.thumbnail_error = f"{source_key} root not configured"
            return False

        asset.thumbnail_attempts += 1
        rel = thumbnail_relative_path(asset.id)
        target = thumbs.resolve(rel)
        tmp = target.with_name(f".{target.stem}.{os.getpid()}.tmp.jpg")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            run_ffmpeg_thumbnail(
                ffmpeg=self.settings.media_ffmpeg_path,
                source=source_binding.resolve(source_variant.relative_path),
                target=tmp,
                seek=seek_position(source_variant.duration_seconds or asset.duration_seconds),
                width=self.settings.media_thumbnail_width,
                timeout_seconds=self.settings.media_thumbnail_timeout_seconds,
            )
            os.replace(tmp, target)
        except (ThumbnailError, OSError) as exc:
            tmp.unlink(missing_ok=True)
            asset.thumbnail_error = str(exc)[:500]
            if asset.thumbnail_attempts >= self.settings.media_thumbnail_max_attempts:
                asset.thumbnail_status = ThumbnailStatus.FAILED
            else:
                asset.thumbnail_status = ThumbnailStatus.PENDING
            log.warning("media.thumbnail.failed", asset_id=str(asset.id), error=str(exc)[:200])
            return False

        info = target.stat()
        root_row = session.scalar(
            select(MediaRoot).where(
                MediaRoot.organization_id == asset.organization_id, MediaRoot.key == "thumbnails"
            )
        )
        if root_row is None:
            asset.thumbnail_status = ThumbnailStatus.FAILED
            asset.thumbnail_error = "thumbnails root row missing"
            return False
        variant = asset.variant(MediaVariantKind.THUMBNAIL)
        if variant is None:
            variant = MediaFileVariant(
                asset=asset,
                media_root_id=root_row.id,
                kind=MediaVariantKind.THUMBNAIL,
                relative_path=rel,
                file_name=PurePosixPath(rel).name,
                extension=".jpg",
                file_size=info.st_size,
                modified_at=datetime.fromtimestamp(info.st_mtime, tz=UTC),
            )
            session.add(variant)
        variant.status = MediaVariantStatus.PRESENT
        variant.file_size = info.st_size
        variant.modified_at = datetime.fromtimestamp(info.st_mtime, tz=UTC)
        variant.last_seen_at = now
        variant.missing_since = None
        asset.thumbnail_status = ThumbnailStatus.READY
        asset.thumbnail_error = None
        return True

    def claim_pending(self, session: Session, limit: int) -> list[MediaAsset]:
        stmt = (
            select(MediaAsset)
            .options(selectinload(MediaAsset.variants))
            .where(
                MediaAsset.thumbnail_status == ThumbnailStatus.PENDING,
                MediaAsset.status == MediaAssetStatus.ACTIVE,
                MediaAsset.thumbnail_attempts < self.settings.media_thumbnail_max_attempts,
            )
            .order_by(MediaAsset.updated_at)
            .limit(limit)
            .with_for_update(skip_locked=True, of=MediaAsset)
        )
        return list(session.scalars(stmt))

    def process_pending(self, session_factory, limit: int | None = None) -> int:
        """Claim up to `limit` pending assets and render them with bounded concurrency."""
        concurrency = max(1, self.settings.media_thumbnail_concurrency)
        limit = limit or concurrency * 2
        with session_factory() as session, session.begin():
            claimed = self.claim_pending(session, limit)
            ids = [asset.id for asset in claimed]
            # Hold the row locks only long enough to mark the attempt; rendering happens after.
            for asset in claimed:
                asset.thumbnail_status = ThumbnailStatus.PENDING
        if not ids:
            return 0

        def work(asset_id: uuid.UUID) -> bool:
            with session_factory() as session, session.begin():
                asset = session.get(
                    MediaAsset, asset_id, options=[selectinload(MediaAsset.variants)]
                )
                if asset is None:
                    return False
                return self.generate(session, asset)

        done = 0
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            for ok in pool.map(work, ids):
                done += 1 if ok else 0
        return len(ids)
