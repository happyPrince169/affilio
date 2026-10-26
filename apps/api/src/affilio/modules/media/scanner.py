"""Incremental media scanner.

Per original-root scan:
1. Walk the root (iterative `os.scandir`, no symlink following, Synology service dirs skipped).
2. Known path + same size + same mtime → unchanged, zero reads.
3. Otherwise compute a sampled fingerprint (≤ 3 MiB read) to classify: touched, changed, moved
   or new. New files are probed with ffprobe and become assets.
4. Known files not seen → missing. Missing files whose fingerprint reappears elsewhere → moved.
5. Files sharing (size, fingerprint) → confirm with a streaming full hash → duplicate link.
6. For touched assets, look for an existing proxy under the proxies root by filename pattern.
7. Touched assets get `thumbnail_status = pending` for the worker.

The scanner never writes under the originals or proxies roots and never opens more than the
sampled bytes of a file unless a duplicate must be confirmed.
"""

from __future__ import annotations

import os
import stat as stat_module
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from affilio.core.config import Settings
from affilio.core.enums import (
    HandoffStatus,
    MediaAssetKind,
    MediaAssetStatus,
    MediaRootKind,
    MediaVariantKind,
    MediaVariantStatus,
    ThumbnailStatus,
)
from affilio.modules.media.errors import InvalidMediaPathError, ScanConfigurationError
from affilio.modules.media.fingerprint import full_hash, quick_fingerprint
from affilio.modules.media.models import MediaAsset, MediaFileVariant, MediaRoot, MediaScanJob
from affilio.modules.media.probe import FFprobe, ProbeError, Prober, ProbeResult
from affilio.modules.media.roots import SCANNABLE_KINDS, MediaRootRegistry, RootBinding

log = structlog.get_logger(__name__)

Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class ScanStats:
    files_seen: int = 0
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    moved: int = 0
    missing: int = 0
    rediscovered: int = 0
    unsupported: int = 0
    duplicates: int = 0
    proxies_linked: int = 0
    skipped: int = 0
    errors: int = 0
    directories: int = 0
    settling: int = 0  # finals root: files still being uploaded, not indexed yet

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(slots=True)
class _Entry:
    relative_path: str
    path: Path
    size: int
    mtime_ns: int


@dataclass(slots=True)
class _ScanContext:
    binding: RootBinding
    root: MediaRoot
    organization_id: uuid.UUID
    now: datetime
    stats: ScanStats = field(default_factory=ScanStats)
    errors: list[dict[str, str]] = field(default_factory=list)
    touched_assets: dict[uuid.UUID, MediaAsset] = field(default_factory=dict)
    pending_commit: int = 0

    @property
    def is_final_root(self) -> bool:
        return self.root.kind == MediaRootKind.FINAL

    @property
    def main_kind(self) -> MediaVariantKind:
        return MediaVariantKind.FINAL if self.is_final_root else MediaVariantKind.ORIGINAL

    @property
    def asset_kind(self) -> MediaAssetKind:
        return MediaAssetKind.FINAL if self.is_final_root else MediaAssetKind.FOOTAGE


def _mtime_from_ns(mtime_ns: int) -> datetime:
    return datetime.fromtimestamp(mtime_ns / 1_000_000_000, tz=UTC)


def _same_mtime(stored: datetime, mtime_ns: int) -> bool:
    # Compare at microsecond resolution: PostgreSQL stores microseconds.
    return abs(stored.timestamp() * 1_000_000 - mtime_ns / 1_000) < 1.0


class MediaScanner:
    def __init__(
        self,
        session: Session,
        registry: MediaRootRegistry,
        settings: Settings,
        prober: Prober | None = None,
        clock: Clock = _utcnow,
    ) -> None:
        self.session = session
        self.registry = registry
        self.settings = settings
        self.prober = prober or FFprobe(
            settings.media_ffprobe_path, settings.media_probe_timeout_seconds
        )
        self.clock = clock
        self._extensions = set(settings.media_video_extensions)
        self._ignored = set(settings.media_ignore_names)
        self._temp_markers = tuple(m.lower() for m in settings.media_temp_name_markers)

    # ------------------------------------------------------------------ public API
    def run(self, job: MediaScanJob) -> ScanStats:
        root = self.session.get(MediaRoot, job.media_root_id)
        if root is None:
            raise ScanConfigurationError("Scan job references an unknown media root")
        if root.kind not in SCANNABLE_KINDS:
            raise ScanConfigurationError(f"Root '{root.key}' is not scannable (kind={root.kind})")
        binding = self.registry.get(root.key)
        if binding is None:
            raise ScanConfigurationError(f"Root '{root.key}' has no path configured")
        availability = binding.availability()
        if not availability.exists:
            raise ScanConfigurationError(f"Root '{root.key}' directory does not exist")
        if not availability.readable:
            raise ScanConfigurationError(f"Root '{root.key}' directory is not readable")

        ctx = _ScanContext(
            binding=binding, root=root, organization_id=job.organization_id, now=self.clock()
        )
        log.info("media.scan.start", root=root.key, job_id=str(job.id))

        existing = self._load_existing(ctx)
        seen: set[str] = set()
        new_entries: list[_Entry] = []

        for entry in self._walk(ctx):
            ctx.stats.files_seen += 1
            seen.add(entry.relative_path)
            variant = existing.get(entry.relative_path)
            if variant is None:
                new_entries.append(entry)
            else:
                self._reconcile_known(ctx, variant, entry, full_rescan=job.full_rescan)
            self._maybe_commit(ctx, job)

        missing_variants = [
            variant
            for rel, variant in existing.items()
            if rel not in seen and variant.status == MediaVariantStatus.PRESENT
        ]
        self._handle_new_and_moved(ctx, new_entries, missing_variants, job)
        self._mark_missing(ctx, missing_variants)
        self._detect_duplicates(ctx)
        self._link_proxies(ctx)
        self._queue_thumbnails(ctx)

        root.last_scan_at = ctx.now
        job.stats = ctx.stats.as_dict()
        job.errors = ctx.errors[: self.settings.media_scan_max_errors]
        self.session.flush()
        log.info("media.scan.done", root=root.key, **ctx.stats.as_dict())
        return ctx.stats

    # ------------------------------------------------------------------ discovery
    def _load_existing(self, ctx: _ScanContext) -> dict[str, MediaFileVariant]:
        stmt = (
            select(MediaFileVariant)
            .options(selectinload(MediaFileVariant.asset).selectinload(MediaAsset.variants))
            .where(
                MediaFileVariant.media_root_id == ctx.root.id,
                MediaFileVariant.kind == ctx.main_kind,
            )
        )
        return {variant.relative_path: variant for variant in self.session.scalars(stmt)}

    def _walk(self, ctx: _ScanContext) -> Iterator[_Entry]:
        root_path = ctx.binding.path
        stack: list[Path] = [root_path]
        while stack:
            directory = stack.pop()
            ctx.stats.directories += 1
            try:
                with os.scandir(directory) as iterator:
                    entries = list(iterator)
            except PermissionError as exc:
                self._record_error(ctx, "permission_denied", directory, str(exc))
                continue
            except OSError as exc:
                self._record_error(ctx, "directory_error", directory, str(exc))
                continue

            for dir_entry in sorted(entries, key=lambda e: e.name):
                name = dir_entry.name
                if name in self._ignored or name.startswith(("._", ".")):
                    ctx.stats.skipped += 1
                    continue
                try:
                    if dir_entry.is_symlink():
                        ctx.stats.skipped += 1
                        continue
                    if dir_entry.is_dir(follow_symlinks=False):
                        stack.append(Path(dir_entry.path))
                        continue
                    if not dir_entry.is_file(follow_symlinks=False):
                        ctx.stats.skipped += 1
                        continue
                    if PurePosixPath(name).suffix.lower() not in self._extensions:
                        ctx.stats.skipped += 1
                        continue
                    info = dir_entry.stat(follow_symlinks=False)
                except OSError as exc:
                    self._record_error(ctx, "stat_error", Path(dir_entry.path), str(exc))
                    continue
                if not stat_module.S_ISREG(info.st_mode):
                    ctx.stats.skipped += 1
                    continue
                if ctx.is_final_root and self._is_settling(ctx, name, info):
                    ctx.stats.settling += 1
                    continue
                path = Path(dir_entry.path)
                yield _Entry(
                    relative_path=path.relative_to(root_path).as_posix(),
                    path=path,
                    size=info.st_size,
                    mtime_ns=info.st_mtime_ns,
                )

    def _is_settling(self, ctx: _ScanContext, name: str, info: os.stat_result) -> bool:
        """Final handoff safety: skip temp-named files and files modified too recently."""
        lowered = name.lower()
        if any(marker in lowered for marker in self._temp_markers):
            return True
        age = ctx.now.timestamp() - info.st_mtime
        return age < self.settings.media_final_settle_seconds

    # ------------------------------------------------------------------ reconciliation
    def _reconcile_known(
        self, ctx: _ScanContext, variant: MediaFileVariant, entry: _Entry, *, full_rescan: bool
    ) -> None:
        asset = variant.asset
        was_missing = variant.status == MediaVariantStatus.MISSING
        variant.last_seen_at = ctx.now
        asset.last_seen_at = ctx.now

        if was_missing:
            variant.status = MediaVariantStatus.PRESENT
            variant.missing_since = None
            asset.missing_since = None
            asset.status = (
                MediaAssetStatus.UNSUPPORTED if variant.probe_error else MediaAssetStatus.ACTIVE
            )
            ctx.stats.rediscovered += 1
            ctx.touched_assets[asset.id] = asset

        unchanged = variant.file_size == entry.size and _same_mtime(
            variant.modified_at, entry.mtime_ns
        )
        if unchanged and not full_rescan:
            if not was_missing:
                ctx.stats.unchanged += 1
            return

        fingerprint = self._fingerprint(ctx, entry)
        if fingerprint is None:
            return
        if fingerprint == variant.fingerprint and not full_rescan:
            # Same bytes, new mtime (copied/touched). Keep metadata, record new stat.
            variant.file_size = entry.size
            variant.modified_at = _mtime_from_ns(entry.mtime_ns)
            ctx.stats.unchanged += 1
            return

        variant.file_size = entry.size
        variant.modified_at = _mtime_from_ns(entry.mtime_ns)
        variant.fingerprint = fingerprint
        variant.content_hash = None
        self._probe_into(ctx, variant, entry.path, asset)
        asset.duplicate_of_id = None
        ctx.stats.updated += 1
        ctx.touched_assets[asset.id] = asset

    def _handle_new_and_moved(
        self,
        ctx: _ScanContext,
        new_entries: list[_Entry],
        missing_variants: list[MediaFileVariant],
        job: MediaScanJob,
    ) -> None:
        missing_by_identity: dict[tuple[int, str | None], list[MediaFileVariant]] = defaultdict(
            list
        )
        for variant in missing_variants:
            missing_by_identity[(variant.file_size, variant.fingerprint)].append(variant)

        for entry in new_entries:
            fingerprint = self._fingerprint(ctx, entry)
            if fingerprint is None:
                continue
            candidates = missing_by_identity.get((entry.size, fingerprint))
            if candidates:
                variant = candidates.pop(0)
                missing_variants.remove(variant)
                self._apply_location(variant, entry)
                variant.last_seen_at = ctx.now
                variant.asset.last_seen_at = ctx.now
                variant.asset.title = _title_from_name(entry.path.name)
                ctx.stats.moved += 1
                ctx.touched_assets[variant.asset.id] = variant.asset
            else:
                self._create_asset(ctx, entry, fingerprint)
            self._maybe_commit(ctx, job)

    def _create_asset(self, ctx: _ScanContext, entry: _Entry, fingerprint: str) -> None:
        asset = MediaAsset(
            id=uuid.uuid4(),  # assigned eagerly: touched_assets is keyed by id before flush
            organization_id=ctx.organization_id,
            title=_title_from_name(entry.path.name),
            status=MediaAssetStatus.ACTIVE,
            asset_kind=ctx.asset_kind,
            # Finals enter the human review gate; footage has no handoff state.
            handoff_status=HandoffStatus.PENDING_REVIEW if ctx.is_final_root else None,
            first_seen_at=ctx.now,
            last_seen_at=ctx.now,
        )
        variant = MediaFileVariant(
            asset=asset,
            media_root_id=ctx.root.id,
            kind=ctx.main_kind,
            status=MediaVariantStatus.PRESENT,
            file_size=entry.size,
            modified_at=_mtime_from_ns(entry.mtime_ns),
            fingerprint=fingerprint,
            last_seen_at=ctx.now,
            relative_path=entry.relative_path,
            file_name=entry.path.name,
            extension=entry.path.suffix.lower(),
        )
        self.session.add(asset)
        self._probe_into(ctx, variant, entry.path, asset)
        ctx.stats.added += 1
        ctx.touched_assets[asset.id] = asset

    def _mark_missing(self, ctx: _ScanContext, missing_variants: list[MediaFileVariant]) -> None:
        for variant in missing_variants:
            variant.status = MediaVariantStatus.MISSING
            variant.missing_since = ctx.now
            asset = variant.asset
            asset.status = MediaAssetStatus.MISSING
            asset.missing_since = ctx.now
            ctx.stats.missing += 1

    # ------------------------------------------------------------------ duplicates
    def _detect_duplicates(self, ctx: _ScanContext) -> None:
        # The worker session runs with autoflush=False: make this scan's new rows visible.
        self.session.flush()
        stmt = (
            select(MediaFileVariant)
            .options(selectinload(MediaFileVariant.asset))
            .where(
                MediaFileVariant.media_root_id == ctx.root.id,
                MediaFileVariant.kind == ctx.main_kind,
                MediaFileVariant.status == MediaVariantStatus.PRESENT,
                MediaFileVariant.fingerprint.is_not(None),
            )
        )
        groups: dict[tuple[int, str], list[MediaFileVariant]] = defaultdict(list)
        for variant in self.session.scalars(stmt):
            groups[(variant.file_size, variant.fingerprint or "")].append(variant)

        duplicates = 0
        for members in groups.values():
            if len(members) < 2:
                continue
            by_hash: dict[str, list[MediaFileVariant]] = defaultdict(list)
            for variant in members:
                if variant.content_hash is None:
                    try:
                        variant.content_hash = full_hash(ctx.binding.resolve(variant.relative_path))
                    except OSError as exc:
                        self._record_error(ctx, "hash_error", variant.relative_path, str(exc))
                        continue
                by_hash[variant.content_hash].append(variant)
            for same in by_hash.values():
                if len(same) < 2:
                    continue
                same.sort(key=lambda v: (v.asset.created_at or ctx.now, v.relative_path))
                canonical = same[0].asset
                canonical.duplicate_of_id = None
                for variant in same[1:]:
                    if variant.asset.id != canonical.id:
                        variant.asset.duplicate_of_id = canonical.id
                        duplicates += 1
        ctx.stats.duplicates = duplicates

    # ------------------------------------------------------------------ proxies
    def _link_proxies(self, ctx: _ScanContext) -> None:
        if ctx.is_final_root:
            return  # proxies belong to footage; finals are complete files already
        proxies = self.registry.proxies
        if proxies is None or not proxies.availability().readable:
            return
        proxy_root = self._db_root(ctx, "proxies")
        if proxy_root is None:
            return
        for asset in ctx.touched_assets.values():
            original = asset.variant(MediaVariantKind.ORIGINAL)
            if original is None or original.status != MediaVariantStatus.PRESENT:
                continue
            existing = asset.variant(MediaVariantKind.PROXY)
            match = self._find_proxy(proxies, original.relative_path)
            if match is None:
                if existing is not None and existing.status == MediaVariantStatus.PRESENT:
                    existing.status = MediaVariantStatus.MISSING
                    existing.missing_since = ctx.now
                continue
            rel, info = match
            if existing is None:
                existing = MediaFileVariant(
                    asset=asset,
                    media_root_id=proxy_root.id,
                    kind=MediaVariantKind.PROXY,
                    relative_path=rel,
                    file_name=PurePosixPath(rel).name,
                    extension=PurePosixPath(rel).suffix.lower(),
                    file_size=info.st_size,
                    modified_at=_mtime_from_ns(info.st_mtime_ns),
                )
                self.session.add(existing)
                changed = True
            else:
                changed = (
                    existing.relative_path != rel
                    or existing.file_size != info.st_size
                    or not _same_mtime(existing.modified_at, info.st_mtime_ns)
                    or existing.status != MediaVariantStatus.PRESENT
                )
                existing.relative_path = rel
                existing.file_name = PurePosixPath(rel).name
                existing.extension = PurePosixPath(rel).suffix.lower()
                existing.file_size = info.st_size
                existing.modified_at = _mtime_from_ns(info.st_mtime_ns)
            existing.status = MediaVariantStatus.PRESENT
            existing.missing_since = None
            existing.last_seen_at = ctx.now
            if changed or existing.probed_at is None:
                try:
                    result = self.prober.probe(proxies.resolve(rel))
                    _apply_probe(existing, result, ctx.now)
                except ProbeError as exc:
                    existing.probe_error = f"{exc.kind}: {exc.message}"
                    existing.probed_at = ctx.now
            ctx.stats.proxies_linked += 1

    def _find_proxy(
        self, proxies: RootBinding, original_rel: str
    ) -> tuple[str, os.stat_result] | None:
        rel = PurePosixPath(original_rel)
        placeholders = {"stem": rel.stem, "name": rel.name, "ext": rel.suffix.lstrip(".")}
        for pattern in self.settings.media_proxy_patterns:
            try:
                candidate_name = pattern.format(**placeholders)
            except (KeyError, IndexError, ValueError):
                continue
            candidate_rel = (rel.parent / candidate_name).as_posix()
            try:
                candidate = proxies.resolve(candidate_rel)
            except InvalidMediaPathError:
                continue
            try:
                info = candidate.stat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                log.debug("media.proxy.stat_failed", path=candidate_rel, error=str(exc))
                continue
            if stat_module.S_ISREG(info.st_mode):
                return candidate_rel, info
        return None

    # ------------------------------------------------------------------ helpers
    def _queue_thumbnails(self, ctx: _ScanContext) -> None:
        for asset in ctx.touched_assets.values():
            if asset.status == MediaAssetStatus.UNSUPPORTED:
                asset.thumbnail_status = ThumbnailStatus.SKIPPED
            elif asset.status == MediaAssetStatus.ACTIVE and (
                asset.thumbnail_status != ThumbnailStatus.READY or asset.id in ctx.touched_assets
            ):
                asset.thumbnail_status = ThumbnailStatus.PENDING
                asset.thumbnail_attempts = 0
                asset.thumbnail_error = None

    def _probe_into(
        self, ctx: _ScanContext, variant: MediaFileVariant, path: Path, asset: MediaAsset
    ) -> None:
        try:
            result = self.prober.probe(path)
        except ProbeError as exc:
            variant.probe_error = f"{exc.kind}: {exc.message}"
            variant.probed_at = ctx.now
            asset.probe_error = variant.probe_error
            asset.status = MediaAssetStatus.UNSUPPORTED
            ctx.stats.unsupported += 1
            self._record_error(ctx, f"probe_{exc.kind}", variant.relative_path, exc.message)
            return
        _apply_probe(variant, result, ctx.now)
        for key, value in result.columns().items():
            setattr(asset, key, value)
        asset.probe_error = None
        asset.status = MediaAssetStatus.ACTIVE

    def _fingerprint(self, ctx: _ScanContext, entry: _Entry) -> str | None:
        try:
            return quick_fingerprint(entry.path, entry.size)
        except PermissionError as exc:
            self._record_error(ctx, "permission_denied", entry.relative_path, str(exc))
        except OSError as exc:
            self._record_error(ctx, "read_error", entry.relative_path, str(exc))
        return None

    def _apply_location(self, variant: MediaFileVariant, entry: _Entry) -> None:
        variant.relative_path = entry.relative_path
        variant.file_name = entry.path.name
        variant.extension = entry.path.suffix.lower()
        variant.file_size = entry.size
        variant.modified_at = _mtime_from_ns(entry.mtime_ns)
        variant.status = MediaVariantStatus.PRESENT
        variant.missing_since = None
        variant.asset.status = (
            MediaAssetStatus.UNSUPPORTED if variant.probe_error else MediaAssetStatus.ACTIVE
        )
        variant.asset.missing_since = None

    def _db_root(self, ctx: _ScanContext, key: str) -> MediaRoot | None:
        return self.session.scalar(
            select(MediaRoot).where(
                MediaRoot.organization_id == ctx.organization_id, MediaRoot.key == key
            )
        )

    def _record_error(self, ctx: _ScanContext, kind: str, path: Path | str, message: str) -> None:
        ctx.stats.errors += 1
        if len(ctx.errors) >= self.settings.media_scan_max_errors:
            return
        display = path
        if isinstance(path, Path):
            try:
                display = path.relative_to(ctx.binding.path).as_posix() or "."
            except ValueError:
                display = path.name
        ctx.errors.append({"kind": kind, "path": str(display), "message": message[:500]})

    def _maybe_commit(self, ctx: _ScanContext, job: MediaScanJob) -> None:
        ctx.pending_commit += 1
        if ctx.pending_commit >= self.settings.media_scan_commit_every:
            job.stats = ctx.stats.as_dict()
            job.heartbeat_at = self.clock()
            self.session.commit()
            ctx.pending_commit = 0


def _apply_probe(variant: MediaFileVariant, result: ProbeResult, now: datetime) -> None:
    for key, value in result.columns().items():
        setattr(variant, key, value)
    variant.probe = result.raw
    variant.probe_error = None
    variant.probed_at = now


def _title_from_name(name: str) -> str:
    stem = PurePosixPath(name).stem.replace("_", " ").replace("-", " ").strip()
    return (stem or name)[:255]
