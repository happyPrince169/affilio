"""Shared ingestion of media files (upload track U3, ADR 0012).

One place turns a file under a media root into library rows, for both callers:

- the scanner (`media/scanner.py`), which walks a whole root and feeds every file through
  `register_new`, `probe_into`, `classify`, `apply_location` and `queue_thumbnail`;
- the future upload finalizer (U4), which registers exactly one completed file with
  `register_file` and checks for duplicate content first with `find_duplicate`.

Both produce the same rows: asset identity (sampled fingerprint, probe columns), the final
variant's provenance (`relative_path`, size, mtime, fingerprint), facet classification with
the documented primacy, `handoff_status = pending_review` for finals, `production_period`
from the first ingestion and `thumbnail_status = pending` for new or changed content.

Transaction boundary
--------------------
Nothing here commits. Every function works inside the caller's `Session` and at most
flushes, so the finalizer can write its own upload-session state in the same transaction and
commit once (file placed → `register_file` → session row `completed` → `commit`). A failure
after the file is placed therefore leaves no half-registered asset: the next attempt (or a
scan) registers the file again from the filesystem, and `register_file` is idempotent for an
unchanged file.

Assumptions U4 must respect
---------------------------
- Call `register_file` only after the file is at its final path and fully written; the
  function reads the file (sampled fingerprint, ffprobe) and refuses symlinks and
  non-regular files but does not wait for settling.
- Use `find_duplicate` with a full SHA-256 of the new file. Size plus sampled fingerprint
  (`candidate_duplicates`) is a hint for preflight, never proof of identical content.
- `asset_fields` may only name existing `MediaAsset` columns (U4 adds `ingest_source`,
  `uploaded_by_user_id`, `revision_of_asset_id` by migration before using them).
- The `IngestContext` snapshot (folder rules, facet tree) is loaded once; reuse it for one
  unit of work and reload for the next, as the scanner does per job.
"""

from __future__ import annotations

import os
import stat as stat_module
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from affilio.core.enums import (
    HandoffStatus,
    MediaAssetKind,
    MediaAssetStatus,
    MediaRootKind,
    MediaVariantKind,
    MediaVariantStatus,
    ThumbnailStatus,
)
from affilio.core.errors import AppError
from affilio.core.redaction import Redactor
from affilio.modules.media import classification, facets
from affilio.modules.media.fingerprint import full_hash, quick_fingerprint
from affilio.modules.media.models import MediaAsset, MediaFileVariant, MediaRoot
from affilio.modules.media.probe import ProbeError, Prober, ProbeResult
from affilio.modules.media.roots import MediaRootRegistry, RootBinding, normalize_relative_path

ErrorSink = Callable[[str, "Path | str", str], None]  # (kind, path, message)


class IngestFileError(AppError):
    """The file to register is missing, a symlink or not a regular file."""

    status_code = 422
    code = "ingest_file_invalid"


# ---------------------------------------------------------------- context and small helpers


@dataclass(slots=True)
class FileEntry:
    relative_path: str
    path: Path
    size: int
    mtime_ns: int


@dataclass(slots=True)
class IngestContext:
    """Per-unit-of-work snapshot: root binding, clock, prober and, for FINAL roots, the folder
    rules and facet tree loaded once. `on_error` receives (kind, path, message) for problems
    that are recorded rather than raised (probe failures, unreadable files)."""

    session: Session
    binding: RootBinding
    root: MediaRoot
    organization_id: uuid.UUID
    now: datetime
    prober: Prober
    redactor: Redactor
    on_error: ErrorSink
    folder_rules: list | None = None
    facet_context: facets.FacetContext | None = None
    suggestions: facets.SuggestionCollector = field(default_factory=facets.SuggestionCollector)

    @classmethod
    def load(
        cls,
        session: Session,
        *,
        binding: RootBinding,
        root: MediaRoot,
        organization_id: uuid.UUID,
        now: datetime,
        prober: Prober,
        redactor: Redactor,
        on_error: ErrorSink | None = None,
    ) -> IngestContext:
        ctx = cls(
            session=session,
            binding=binding,
            root=root,
            organization_id=organization_id,
            now=now,
            prober=prober,
            redactor=redactor,
            on_error=on_error or (lambda _kind, _path, _message: None),
        )
        if ctx.is_final_root:
            ctx.folder_rules = classification.rules_for_root(session, organization_id, root.id)
            ctx.facet_context = facets.FacetContext.load(session, organization_id)
        return ctx

    @property
    def is_final_root(self) -> bool:
        return self.root.kind == MediaRootKind.FINAL

    @property
    def main_kind(self) -> MediaVariantKind:
        return MediaVariantKind.FINAL if self.is_final_root else MediaVariantKind.ORIGINAL

    @property
    def asset_kind(self) -> MediaAssetKind:
        return MediaAssetKind.FINAL if self.is_final_root else MediaAssetKind.FOOTAGE


def mtime_from_ns(mtime_ns: int) -> datetime:
    return datetime.fromtimestamp(mtime_ns / 1_000_000_000, tz=UTC)


def same_mtime(stored: datetime, mtime_ns: int) -> bool:
    # Compare at microsecond resolution: PostgreSQL stores microseconds.
    return abs(stored.timestamp() * 1_000_000 - mtime_ns / 1_000) < 1.0


def title_from_name(name: str) -> str:
    stem = PurePosixPath(name).stem.replace("_", " ").replace("-", " ").strip()
    return (stem or name)[:255]


def fingerprint_entry(ctx: IngestContext, entry: FileEntry) -> str | None:
    """Sampled fingerprint (≤ 3 MiB read); unreadable files are recorded, not raised."""
    try:
        return quick_fingerprint(entry.path, entry.size)
    except PermissionError as exc:
        ctx.on_error("permission_denied", entry.relative_path, str(exc))
    except OSError as exc:
        ctx.on_error("read_error", entry.relative_path, str(exc))
    return None


# ---------------------------------------------------------------- probe, classify, thumbnails


def apply_probe(variant: MediaFileVariant, result: ProbeResult, now: datetime) -> None:
    for key, value in result.columns().items():
        setattr(variant, key, value)
    variant.probe = result.raw
    variant.probe_error = None
    variant.probed_at = now


def probe_into(
    ctx: IngestContext, variant: MediaFileVariant, path: Path, asset: MediaAsset
) -> bool:
    """Probe the file into the variant and asset columns. Returns False (and marks the asset
    unsupported, recording the error) when ffprobe rejects the file."""
    try:
        result = ctx.prober.probe(path)
    except ProbeError as exc:
        variant.probe_error = ctx.redactor.message(f"{exc.kind}: {exc.message}")
        variant.probed_at = ctx.now
        asset.probe_error = variant.probe_error
        asset.status = MediaAssetStatus.UNSUPPORTED
        ctx.on_error(f"probe_{exc.kind}", variant.relative_path, exc.message)
        return False
    apply_probe(variant, result, ctx.now)
    for key, value in result.columns().items():
        setattr(asset, key, value)
    asset.probe_error = None
    asset.status = MediaAssetStatus.ACTIVE
    return True


def classify(ctx: IngestContext, asset: MediaAsset, relative_path: str) -> bool:
    """Folder-derived category and path facets for FINAL roots. Never touches the file or
    manual rows; unknown facet values of valid finals are collected as suggestions. Returns
    True when a previously derived classification changed (the first one is not a change)."""
    if not ctx.is_final_root:
        return False
    try:
        facet_assignments = None
        if ctx.facet_context is not None and ctx.facet_context.enabled:
            path_result = ctx.facet_context.classify(relative_path)
            facet_assignments = path_result.assignments
            if path_result.unknown and facets.suggestion_is_valid_source(asset):
                for unknown in path_result.unknown:
                    ctx.suggestions.add(unknown, relative_path)
        return classification.apply_folder_classification(
            ctx.session,
            asset,
            root=ctx.root,
            relative_path=relative_path,
            rules=ctx.folder_rules or [],
            now=ctx.now,
            facet_assignments=facet_assignments,
        )
    except Exception as exc:
        ctx.on_error("classification_error", relative_path, str(exc))
        return False


def queue_thumbnail(asset: MediaAsset) -> None:
    """Thumbnail rule for an asset whose file is new, changed, moved or rediscovered: a
    supported file renders (again), an unsupported one is skipped. Classification-only
    changes never reach this function (U0)."""
    if asset.status == MediaAssetStatus.UNSUPPORTED:
        asset.thumbnail_status = ThumbnailStatus.SKIPPED
    elif asset.status == MediaAssetStatus.ACTIVE:
        asset.thumbnail_status = ThumbnailStatus.PENDING
        asset.thumbnail_attempts = 0
        asset.thumbnail_error = None


def apply_location(variant: MediaFileVariant, entry: FileEntry) -> None:
    """Point a known variant at a new location/stat (moves, rediscoveries)."""
    variant.relative_path = entry.relative_path
    variant.file_name = entry.path.name
    variant.extension = entry.path.suffix.lower()
    variant.file_size = entry.size
    variant.modified_at = mtime_from_ns(entry.mtime_ns)
    variant.status = MediaVariantStatus.PRESENT
    variant.missing_since = None
    variant.asset.status = (
        MediaAssetStatus.UNSUPPORTED if variant.probe_error else MediaAssetStatus.ACTIVE
    )
    variant.asset.missing_since = None


# ---------------------------------------------------------------- registration


@dataclass(slots=True)
class Registration:
    asset: MediaAsset
    variant: MediaFileVariant
    created: bool  # a new asset row (False: an existing one was reconciled)
    changed: bool  # file content/stat changed or the asset was rediscovered → thumbnail queued
    probe_ok: bool
    classification_changed: bool


def register_new(ctx: IngestContext, entry: FileEntry, fingerprint: str) -> Registration:
    """Create the asset + main variant for a file never seen under this root: probe, classify
    (finals), handoff gate and production period. Thumbnail queueing is the caller's step
    (`queue_thumbnail`), as it is for every touched asset."""
    asset = MediaAsset(
        id=uuid.uuid4(),  # assigned eagerly: callers key their touched set by id before flush
        organization_id=ctx.organization_id,
        title=title_from_name(entry.path.name),
        status=MediaAssetStatus.ACTIVE,
        asset_kind=ctx.asset_kind,
        # Finals enter the human review gate; footage has no handoff state.
        handoff_status=HandoffStatus.PENDING_REVIEW if ctx.is_final_root else None,
        first_seen_at=ctx.now,
        last_seen_at=ctx.now,
        # Derived from the first ingestion time only; never rewritten by later scans.
        production_period=facets.production_period_for(ctx.now),
    )
    variant = MediaFileVariant(
        asset=asset,
        media_root_id=ctx.root.id,
        kind=ctx.main_kind,
        status=MediaVariantStatus.PRESENT,
        file_size=entry.size,
        modified_at=mtime_from_ns(entry.mtime_ns),
        fingerprint=fingerprint,
        last_seen_at=ctx.now,
        relative_path=entry.relative_path,
        file_name=entry.path.name,
        extension=entry.path.suffix.lower(),
    )
    ctx.session.add(asset)
    probe_ok = probe_into(ctx, variant, entry.path, asset)
    classification_changed = classify(ctx, asset, entry.relative_path)
    return Registration(
        asset=asset,
        variant=variant,
        created=True,
        changed=True,
        probe_ok=probe_ok,
        classification_changed=classification_changed,
    )


def stat_entry(binding: RootBinding, relative_path: str) -> FileEntry:
    """Stat one file under the root for `register_file`: the path may not escape the root,
    be a symlink (checked on the un-resolved path) or anything but a regular file."""
    normalized = normalize_relative_path(relative_path)
    raw = binding.path / normalized
    try:
        info = os.lstat(raw)
    except FileNotFoundError as exc:
        raise IngestFileError("File not found under its media root") from exc
    if stat_module.S_ISLNK(info.st_mode):
        raise IngestFileError("Symbolic links are not ingested")
    if not stat_module.S_ISREG(info.st_mode):
        raise IngestFileError("Only regular files are ingested")
    path = binding.resolve(normalized)  # InvalidMediaPathError on escape
    return FileEntry(
        relative_path=normalized, path=path, size=info.st_size, mtime_ns=info.st_mtime_ns
    )


def register_file(
    ctx: IngestContext,
    relative_path: str,
    *,
    asset_fields: Mapping[str, Any] | None = None,
) -> Registration:
    """Register one completed file at `relative_path` under `ctx.root` without a root scan.

    - New path → new asset (`register_new`) with a pending thumbnail.
    - Known path, same size, mtime and fingerprint → nothing but a re-classification; the
      asset and its thumbnail state are returned untouched (idempotent retry).
    - Known path with different content → the variant is re-fingerprinted and re-probed, the
      asset rediscovered if it was missing, and the thumbnail queued again.

    `asset_fields` are applied to the asset (new or reconciled) and must name real columns.
    Flushes, never commits: the caller owns the transaction.
    """
    entry = stat_entry(ctx.binding, relative_path)
    existing = ctx.session.scalar(
        select(MediaFileVariant)
        .options(selectinload(MediaFileVariant.asset).selectinload(MediaAsset.variants))
        .where(
            MediaFileVariant.media_root_id == ctx.root.id,
            MediaFileVariant.kind == ctx.main_kind,
            MediaFileVariant.relative_path == entry.relative_path,
        )
    )
    if existing is None:
        fingerprint = fingerprint_entry(ctx, entry)
        if fingerprint is None:
            raise IngestFileError("File could not be read for fingerprinting")
        registration = register_new(ctx, entry, fingerprint)
    else:
        registration = _reconcile_existing(ctx, existing, entry)
    _apply_asset_fields(registration.asset, asset_fields)
    if registration.changed:
        queue_thumbnail(registration.asset)
    ctx.session.flush()
    return registration


def _reconcile_existing(
    ctx: IngestContext, variant: MediaFileVariant, entry: FileEntry
) -> Registration:
    asset = variant.asset
    was_missing = variant.status == MediaVariantStatus.MISSING
    variant.last_seen_at = ctx.now
    asset.last_seen_at = ctx.now
    changed = False
    probe_ok = not variant.probe_error
    if was_missing:
        apply_location(variant, entry)
        changed = True
    unchanged_stat = variant.file_size == entry.size and same_mtime(
        variant.modified_at, entry.mtime_ns
    )
    if not unchanged_stat or was_missing:
        fingerprint = fingerprint_entry(ctx, entry)
        if fingerprint is None:
            raise IngestFileError("File could not be read for fingerprinting")
        if fingerprint != variant.fingerprint:
            variant.file_size = entry.size
            variant.modified_at = mtime_from_ns(entry.mtime_ns)
            variant.fingerprint = fingerprint
            variant.content_hash = None
            probe_ok = probe_into(ctx, variant, entry.path, asset)
            asset.duplicate_of_id = None
            changed = True
        else:
            # Same bytes, new stat (copied/touched): keep metadata, record the new stat.
            variant.file_size = entry.size
            variant.modified_at = mtime_from_ns(entry.mtime_ns)
    classification_changed = classify(ctx, asset, entry.relative_path)
    return Registration(
        asset=asset,
        variant=variant,
        created=False,
        changed=changed,
        probe_ok=probe_ok,
        classification_changed=classification_changed,
    )


def _apply_asset_fields(asset: MediaAsset, fields: Mapping[str, Any] | None) -> None:
    if not fields:
        return
    columns = {c.key for c in MediaAsset.__table__.columns}
    unknown = sorted(set(fields) - columns)
    if unknown:
        msg = f"Unknown media asset fields: {', '.join(unknown)}"
        raise ValueError(msg)
    for key, value in fields.items():
        setattr(asset, key, value)


# ---------------------------------------------------------------- duplicate content


@dataclass(slots=True)
class DuplicateMatch:
    asset: MediaAsset
    variant: MediaFileVariant
    content_hash: str  # the confirmed full SHA-256 shared by both files


def _present_variants(session: Session, organization_id: uuid.UUID, kind: MediaVariantKind) -> Any:
    return (
        select(MediaFileVariant)
        .join(MediaAsset, MediaAsset.id == MediaFileVariant.asset_id)
        .options(selectinload(MediaFileVariant.asset), selectinload(MediaFileVariant.root))
        .where(
            MediaAsset.organization_id == organization_id,
            MediaFileVariant.kind == kind,
            MediaFileVariant.status == MediaVariantStatus.PRESENT,
        )
        .order_by(MediaFileVariant.created_at, MediaFileVariant.id)
    )


def candidate_duplicates(
    session: Session,
    organization_id: uuid.UUID,
    *,
    kind: MediaVariantKind,
    size: int,
    fingerprint: str,
) -> list[MediaFileVariant]:
    """Present variants of `kind` in the organization with the same size and sampled
    fingerprint. A hint only (3 MiB sampled): never treat it as proof of identical content;
    confirm with `find_duplicate`."""
    stmt = _present_variants(session, organization_id, kind).where(
        MediaFileVariant.file_size == size, MediaFileVariant.fingerprint == fingerprint
    )
    return list(session.scalars(stmt))


def find_duplicate(
    session: Session,
    organization_id: uuid.UUID,
    registry: MediaRootRegistry,
    *,
    kind: MediaVariantKind,
    size: int,
    fingerprint: str,
    content_hash: str,
) -> DuplicateMatch | None:
    """Existing variant with the same full content as a file whose SHA-256 is `content_hash`.

    Exact `content_hash` matches win; otherwise every size + fingerprint candidate without a
    stored hash is hashed from disk (streamed, the scanner's own confirmation rule) and the
    hash is stored on it for next time. Candidates whose file cannot be read are skipped.
    Returns None when nothing is confirmed; the organization boundary is always applied.
    """
    by_hash = session.scalars(
        _present_variants(session, organization_id, kind).where(
            MediaFileVariant.content_hash == content_hash
        )
    ).first()
    if by_hash is not None:
        return DuplicateMatch(asset=by_hash.asset, variant=by_hash, content_hash=content_hash)
    for candidate in candidate_duplicates(
        session, organization_id, kind=kind, size=size, fingerprint=fingerprint
    ):
        if candidate.content_hash is None:
            binding = registry.get(candidate.root.key)
            if binding is None:
                continue
            try:
                candidate.content_hash = full_hash(binding.resolve(candidate.relative_path))
            except OSError:
                continue
        if candidate.content_hash == content_hash:
            session.flush()
            return DuplicateMatch(
                asset=candidate.asset, variant=candidate, content_hash=content_hash
            )
    session.flush()
    return None


__all__ = [
    "DuplicateMatch",
    "ErrorSink",
    "FileEntry",
    "IngestContext",
    "IngestFileError",
    "Registration",
    "apply_location",
    "apply_probe",
    "candidate_duplicates",
    "classify",
    "find_duplicate",
    "fingerprint_entry",
    "mtime_from_ns",
    "probe_into",
    "queue_thumbnail",
    "register_file",
    "register_new",
    "same_mtime",
    "stat_entry",
    "title_from_name",
]
