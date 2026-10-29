"""Upload finalization, worker side (upload track U4, ADR 0012).

Runs inside the existing worker (`affilio.worker.runner`) and is the only code that writes
into the finals tree. One session at a time, claimed with a lease so a dead worker's work is
re-run by the next one. Every step is re-entrant and driven by the row's recorded state:

    uploaded ──claim──▶ verifying ──intent──▶ finalizing ──place+register──▶ completed
                            │                      │
                            ├─ invalid/mismatch ─▶ failed        (staging removed)
                            └─ same content ─────▶ duplicate     (staging removed)

Recovery is deterministic from (status, final_relative_path, verified_sha256, staging):

- `verifying` with staging present: verify from scratch (idempotent).
- `finalizing`, destination missing, staging present: place again.
- `finalizing`, destination present: prove it is our file (size + SHA-256 equal to the
  intent) and continue with registration; a different file at that name is a conflict,
  never adopted.
- `finalizing`, destination present and registered by a scan meanwhile: the asset is
  adopted only after the same identity proof; attribution is then written once.
- staging missing before placement: `failed(staging_missing)`; after placement it is simply
  the already-removed copy.

The session row is the primary recovery record; a later root scan is never relied upon.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from affilio.core.audit import record_audit
from affilio.core.config import Settings
from affilio.core.enums import IngestSource, MediaVariantKind, UploadStatus
from affilio.core.redaction import Redactor
from affilio.modules.media import ingest
from affilio.modules.media.fingerprint import quick_fingerprint
from affilio.modules.media.models import MediaRoot, MediaUpload
from affilio.modules.media.probe import FFprobe, ProbeError, Prober
from affilio.modules.media.roots import MediaRootRegistry
from affilio.modules.media.upload_storage import (
    FinalsPlacer,
    StagingStore,
    StorageError,
    with_suffix,
)
from affilio.modules.taxonomy import service as taxonomy_service

log = structlog.get_logger(__name__)

HASH_BUFFER = 4 * 1024 * 1024


class FinalizationFailedError(Exception):
    """Terminal failure for this session; `code` is stored on the row."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class RetryableError(Exception):
    """Transient problem: release the lease and let a later attempt retry."""


@dataclass(slots=True)
class Outcome:
    upload_id: uuid.UUID
    status: UploadStatus
    media_asset_id: uuid.UUID | None = None
    code: str | None = None


def sha256_of(path: Path, *, on_progress=None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(HASH_BUFFER)
            if not block:
                break
            digest.update(block)
            if on_progress is not None:
                on_progress(len(block))
    return digest.hexdigest()


# ---------------------------------------------------------------- claiming


def claim_next(
    session: Session, *, now: datetime, lease_seconds: int, max_attempts: int, worker_id: str
) -> MediaUpload | None:
    """Claim one `uploaded` session, or one whose `verifying`/`finalizing` lease expired.
    Sessions that exhausted their attempts are failed here so they stop being retried."""
    stmt = (
        select(MediaUpload)
        .options(
            selectinload(MediaUpload.production_owner),
            selectinload(MediaUpload.product_category),
            selectinload(MediaUpload.content_type),
        )
        .where(
            or_(
                MediaUpload.status == UploadStatus.UPLOADED,
                MediaUpload.status.in_([UploadStatus.VERIFYING, UploadStatus.FINALIZING])
                & (MediaUpload.lease_until < now),
            )
        )
        .order_by(MediaUpload.uploaded_at, MediaUpload.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    upload = session.scalar(stmt)
    if upload is None:
        return None
    if upload.finalize_attempts >= max_attempts:
        upload.status = UploadStatus.FAILED
        upload.failure_code = "finalize_attempts_exhausted"
        upload.failure_detail = f"gave up after {upload.finalize_attempts} attempts"
        upload.lease_until = None
        session.commit()
        return None
    if upload.status == UploadStatus.UPLOADED:
        upload.status = UploadStatus.VERIFYING
    upload.finalize_attempts += 1
    upload.lease_until = now + timedelta(seconds=lease_seconds)
    upload.worker_id = worker_id
    session.commit()
    return upload


# ---------------------------------------------------------------- the finalizer


@dataclass(slots=True)
class Finalizer:
    session: Session
    registry: MediaRootRegistry
    settings: Settings
    prober: Prober
    worker_id: str = "finalizer"

    @classmethod
    def build(
        cls,
        session: Session,
        registry: MediaRootRegistry,
        settings: Settings,
        *,
        prober: Prober | None = None,
        worker_id: str = "finalizer",
    ) -> Finalizer:
        return cls(
            session=session,
            registry=registry,
            settings=settings,
            prober=prober
            or FFprobe(settings.media_ffprobe_path, settings.media_probe_timeout_seconds),
            worker_id=worker_id,
        )

    # -- entry point ---------------------------------------------------------------------
    def run_next(self, *, now: datetime | None = None) -> Outcome | None:
        """Claim and process one session. Returns None when nothing was due."""
        if not self.settings.media_finals_writable:
            return None
        now = now or datetime.now(UTC)
        upload = claim_next(
            self.session,
            now=now,
            lease_seconds=self.settings.upload_finalize_lease_seconds,
            max_attempts=self.settings.upload_finalize_max_attempts,
            worker_id=self.worker_id,
        )
        if upload is None:
            return None
        return self.process(upload, now=now)

    def process(self, upload: MediaUpload, *, now: datetime) -> Outcome:
        log.info(
            "media.upload.finalize.start",
            upload_id=str(upload.id),
            status=upload.status.value,
            attempt=upload.finalize_attempts,
        )
        try:
            outcome = self._process(upload, now)
        except FinalizationFailedError as exc:
            self.session.rollback()
            outcome = self._fail(upload.id, exc.code, exc.detail, now)
        except RetryableError as exc:
            self.session.rollback()
            self._release(upload.id, str(exc))
            outcome = Outcome(upload_id=upload.id, status=UploadStatus.VERIFYING, code="retry")
        log.info(
            "media.upload.finalize.done",
            upload_id=str(outcome.upload_id),
            status=outcome.status.value,
            code=outcome.code,
        )
        return outcome

    # -- pieces --------------------------------------------------------------------------
    def _finals(self, organization_id: uuid.UUID) -> tuple[Path, MediaRoot]:
        binding = self.registry.finals
        if binding is None:
            raise RetryableError("finals root is not configured")
        roots = self.registry.ensure_db_roots(self.session, organization_id)
        return binding.path, roots["finals"]

    def _process(self, upload: MediaUpload, now: datetime) -> Outcome:
        finals_path, root_row = self._finals(upload.organization_id)
        staging = StagingStore.for_worker(finals_path)
        placer = FinalsPlacer(finals_path)
        try:
            staged = staging.stat_regular(upload.organization_id, upload.staging_name)
        except StorageError as exc:
            raise FinalizationFailedError("staging_invalid", str(exc)) from exc
        staging_path = staging.path_for(upload.organization_id, upload.staging_name)

        if upload.status == UploadStatus.FINALIZING and upload.final_relative_path:
            return self._resume_placement(upload, staged, staging_path, placer, root_row, now)

        if staged is None:
            raise FinalizationFailedError("staging_missing", "staged file is gone before placement")
        if (
            staged.st_size != upload.expected_size
            or upload.committed_offset != upload.expected_size
        ):
            raise FinalizationFailedError(
                "size_mismatch",
                f"staged {staged.st_size} bytes, committed {upload.committed_offset}, "
                f"expected {upload.expected_size}",
            )

        # Verification: full hash (lease refreshed as it streams), optional client hash,
        # sampled fingerprint, ffprobe.
        content_hash = sha256_of(staging_path, on_progress=self._lease_refresher(upload))
        if upload.client_sha256 and content_hash != upload.client_sha256:
            raise FinalizationFailedError(
                "hash_mismatch", "content does not match the declared hash"
            )
        fingerprint = quick_fingerprint(staging_path, staged.st_size)
        try:
            self.prober.probe(staging_path)
        except ProbeError as exc:
            raise FinalizationFailedError("invalid_media", f"{exc.kind}: {exc.message}") from exc

        match = ingest.find_duplicate(
            self.session,
            upload.organization_id,
            self.registry,
            kind=MediaVariantKind.FINAL,
            size=staged.st_size,
            fingerprint=fingerprint,
            content_hash=content_hash,
        )
        if match is not None:
            return self._duplicate(upload, match.asset.id, staging, now)

        # Destination from authorized taxonomy ids only; the folder names are category names.
        tree = taxonomy_service.load_tree(self.session, upload.organization_id)
        names = [
            tree.by_id[upload.production_owner_category_id].name,
            tree.by_id[upload.product_category_id].name,
            tree.by_id[upload.content_type_id].name,
        ]
        try:
            dir_fd, used = placer.resolve_directory(names)
        except (StorageError, OSError) as exc:
            raise FinalizationFailedError("destination_unavailable", str(exc)) from exc
        try:
            filename = upload.safe_filename
            if placer.entry_exists(dir_fd, filename):
                filename = with_suffix(filename, upload.id.hex[:8])
            # Intent record: the path we are about to use, before the tree changes. The
            # partial unique index on (organization, verified_sha256) decides concurrent
            # identical uploads here.
            upload.verified_sha256 = content_hash
            upload.fingerprint = fingerprint
            upload.final_relative_path = "/".join([*used, filename])
            upload.status = UploadStatus.FINALIZING
            try:
                self.session.commit()
            except IntegrityError:
                self.session.rollback()
                winner = self._winner(upload.organization_id, content_hash)
                return self._duplicate(
                    self._reload(upload.id), winner, staging, now, concurrent=True
                )
            upload = self._reload(upload.id)
            self._place(upload, staging_path, dir_fd, filename, content_hash)
        finally:
            os.close(dir_fd)
        return self._register(upload, root_row, staging, now)

    def _resume_placement(
        self,
        upload: MediaUpload,
        staged: os.stat_result | None,
        staging_path: Path,
        placer: FinalsPlacer,
        root_row: MediaRoot,
        now: datetime,
    ) -> Outcome:
        """`finalizing` with an intent recorded: find out how far the previous attempt got."""
        assert upload.final_relative_path and upload.verified_sha256  # noqa: S101
        parts = upload.final_relative_path.split("/")
        folders, filename = parts[:-1], parts[-1]
        try:
            dir_fd, _ = placer.resolve_directory(folders)
        except (StorageError, OSError) as exc:
            raise FinalizationFailedError("destination_unavailable", str(exc)) from exc
        try:
            dest = placer.stat_entry(dir_fd, filename)
            if dest is None:
                if staged is None:
                    raise FinalizationFailedError(
                        "staging_missing", "neither the staged nor the placed file exists"
                    )
                self._place(upload, staging_path, dir_fd, filename, upload.verified_sha256)
            else:
                # Prove the destination is our file before touching the library.
                dest_path = Path(placer.finals_root, *folders, filename)
                if (
                    dest.st_size != upload.expected_size
                    or sha256_of(dest_path, on_progress=self._lease_refresher(upload))
                    != upload.verified_sha256
                ):
                    raise FinalizationFailedError(
                        "destination_conflict",
                        "a different file occupies the intended final path",
                    )
        except StorageError as exc:
            raise FinalizationFailedError("destination_conflict", str(exc)) from exc
        finally:
            os.close(dir_fd)
        staging = StagingStore.for_worker(placer.finals_root)
        return self._register(upload, root_row, staging, now)

    def _place(
        self, upload: MediaUpload, staging_path: Path, dir_fd: int, filename: str, content_hash: str
    ) -> None:
        placer = FinalsPlacer(staging_path.parents[2])  # <finals>/.affilio-incoming/<org>/x
        try:
            placer.link_into(staging_path, dir_fd, filename)
        except FileExistsError:
            try:
                existing = placer.stat_entry(dir_fd, filename)
            except StorageError as exc:  # a symlink or special file sits at the name
                raise FinalizationFailedError("destination_conflict", str(exc)) from exc
            dest_path = Path(placer.finals_root, *upload.final_relative_path.split("/"))
            if (
                existing is not None
                and existing.st_size == upload.expected_size
                and sha256_of(dest_path) == content_hash
            ):
                return  # our own earlier link (crash after link)
            raise FinalizationFailedError(
                "destination_conflict", "a different file occupies the intended final path"
            ) from None
        except OSError as exc:
            raise FinalizationFailedError("placement_failed", str(exc)) from exc

    def _register(
        self, upload: MediaUpload, root_row: MediaRoot, staging: StagingStore, now: datetime
    ) -> Outcome:
        assert upload.final_relative_path and upload.verified_sha256  # noqa: S101
        binding = self.registry.finals
        assert binding is not None  # noqa: S101
        errors: list[tuple[str, str, str]] = []
        ctx = ingest.IngestContext.load(
            self.session,
            binding=binding,
            root=root_row,
            organization_id=upload.organization_id,
            now=now,
            prober=self.prober,
            redactor=Redactor.from_settings(self.settings),
            on_error=lambda kind, path, message: errors.append((kind, str(path), message)),
        )
        try:
            registration = ingest.register_file(
                ctx,
                upload.final_relative_path,
                asset_fields={
                    "ingest_source": IngestSource.UPLOAD,
                    "uploaded_by_user_id": upload.uploaded_by_user_id,
                    "revision_of_asset_id": upload.replaces_asset_id,
                },
            )
        except ingest.IngestFileError as exc:
            raise FinalizationFailedError("registration_failed", str(exc)) from exc
        variant = registration.variant
        if variant.file_size != upload.expected_size or (
            upload.fingerprint and variant.fingerprint != upload.fingerprint
        ):
            # The path holds something that is not the verified upload: never adopt it.
            self.session.rollback()
            raise FinalizationFailedError(
                "destination_conflict", "registered file does not match the verified upload"
            )
        if not registration.probe_ok:
            self.session.rollback()
            raise FinalizationFailedError("invalid_media", "ffprobe rejected the placed file")
        variant.content_hash = upload.verified_sha256
        upload.status = UploadStatus.COMPLETED
        upload.media_asset_id = registration.asset.id
        upload.completed_at = now
        upload.lease_until = None
        upload.failure_code = None
        upload.failure_detail = None
        record_audit(
            self.session,
            organization_id=upload.organization_id,
            identity=None,
            action="upload.completed",
            entity_type="media_upload",
            entity_id=upload.id,
            data={
                "media_asset_id": str(registration.asset.id),
                "uploaded_by_user_id": str(upload.uploaded_by_user_id),
                "submitted_for_user_id": (
                    str(upload.submitted_for_user_id) if upload.submitted_for_user_id else None
                ),
                "production_owner_category_id": str(upload.production_owner_category_id),
                "adopted_existing_asset": not registration.created,
                "final_relative_path": upload.final_relative_path,
            },
        )
        self.session.commit()
        # The staging copy is removed only after the registration is durable; a crash here
        # leaves a copy that maintenance removes (completed + staging present).
        try:
            staging.remove(upload.organization_id, upload.staging_name)
        except (StorageError, OSError) as exc:
            log.warning(
                "media.upload.staging_cleanup_failed", upload_id=str(upload.id), error=str(exc)
            )
        return Outcome(
            upload_id=upload.id, status=UploadStatus.COMPLETED, media_asset_id=registration.asset.id
        )

    # -- terminal outcomes ---------------------------------------------------------------
    def _duplicate(
        self,
        upload: MediaUpload,
        existing_asset_id: uuid.UUID | None,
        staging: StagingStore,
        now: datetime,
        *,
        concurrent: bool = False,
    ) -> Outcome:
        upload.status = UploadStatus.DUPLICATE
        upload.duplicate_of_asset_id = existing_asset_id
        upload.failure_code = "duplicate_content"
        upload.failure_detail = (
            "identical content is being finalized by another upload"
            if concurrent
            else "identical content already exists in the library"
        )
        upload.completed_at = now
        upload.lease_until = None
        record_audit(
            self.session,
            organization_id=upload.organization_id,
            identity=None,
            action="upload.duplicate",
            entity_type="media_upload",
            entity_id=upload.id,
            data={
                "duplicate_of_asset_id": str(existing_asset_id) if existing_asset_id else None,
                "uploaded_by_user_id": str(upload.uploaded_by_user_id),
            },
        )
        self.session.commit()
        with contextlib.suppress(StorageError, OSError):
            staging.remove(upload.organization_id, upload.staging_name)
        return Outcome(
            upload_id=upload.id,
            status=UploadStatus.DUPLICATE,
            media_asset_id=existing_asset_id,
            code="duplicate_content",
        )

    def _fail(self, upload_id: uuid.UUID, code: str, detail: str, now: datetime) -> Outcome:
        upload = self._reload(upload_id)
        upload.status = UploadStatus.FAILED
        upload.failure_code = code
        upload.failure_detail = Redactor.from_settings(self.settings).message(detail)
        upload.completed_at = now
        upload.lease_until = None
        record_audit(
            self.session,
            organization_id=upload.organization_id,
            identity=None,
            action="upload.failed",
            entity_type="media_upload",
            entity_id=upload.id,
            data={"code": code, "uploaded_by_user_id": str(upload.uploaded_by_user_id)},
        )
        self.session.commit()
        if code in {"invalid_media", "hash_mismatch", "size_mismatch", "staging_invalid"}:
            binding = self.registry.finals
            if binding is not None:
                with contextlib.suppress(StorageError, OSError):
                    StagingStore.for_worker(binding.path).remove(
                        upload.organization_id, upload.staging_name
                    )
        return Outcome(upload_id=upload.id, status=UploadStatus.FAILED, code=code)

    def _release(self, upload_id: uuid.UUID, reason: str) -> None:
        upload = self._reload(upload_id)
        upload.lease_until = None  # claimable again on the next poll
        upload.failure_detail = reason
        self.session.commit()

    # -- helpers -------------------------------------------------------------------------
    def _reload(self, upload_id: uuid.UUID) -> MediaUpload:
        self.session.expire_all()
        upload = self.session.scalar(
            select(MediaUpload)
            .options(
                selectinload(MediaUpload.production_owner),
                selectinload(MediaUpload.product_category),
                selectinload(MediaUpload.content_type),
            )
            .where(MediaUpload.id == upload_id)
        )
        assert upload is not None  # noqa: S101
        return upload

    def _winner(self, organization_id: uuid.UUID, content_hash: str) -> uuid.UUID | None:
        other = self.session.scalar(
            select(MediaUpload).where(
                MediaUpload.organization_id == organization_id,
                MediaUpload.verified_sha256 == content_hash,
                MediaUpload.status.in_([UploadStatus.FINALIZING, UploadStatus.COMPLETED]),
            )
        )
        return other.media_asset_id if other is not None else None

    def _lease_refresher(self, upload: MediaUpload):
        state = {"since": 0}
        step = 512 * 1024 * 1024

        def refresh(n: int) -> None:
            state["since"] += n
            if state["since"] >= step:
                state["since"] = 0
                upload.lease_until = datetime.now(UTC) + timedelta(
                    seconds=self.settings.upload_finalize_lease_seconds
                )
                self.session.commit()

        return refresh


# ---------------------------------------------------------------- maintenance


def expire_and_clean(
    session: Session,
    registry: MediaRootRegistry,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """Hourly housekeeping: expire idle sessions, remove staging copies of terminal sessions,
    and remove orphan `.part` files older than the TTL. Nothing outside staging is touched."""
    now = now or datetime.now(UTC)
    counts = {"expired": 0, "staging_removed": 0, "orphans_removed": 0}
    binding = registry.finals
    if binding is None or not settings.media_finals_writable:
        return counts
    staging = StagingStore.for_worker(binding.path)
    if not staging.root.is_dir():
        return counts
    idle = session.scalars(
        select(MediaUpload).where(
            MediaUpload.status.in_([UploadStatus.CREATED, UploadStatus.UPLOADING]),
            MediaUpload.expires_at < now,
        )
    ).all()
    for upload in idle:
        upload.status = UploadStatus.EXPIRED
        upload.failure_code = "expired"
        upload.failure_detail = "no data received before the session expired"
        counts["expired"] += 1
        record_audit(
            session,
            organization_id=upload.organization_id,
            identity=None,
            action="upload.expired",
            entity_type="media_upload",
            entity_id=upload.id,
            data={"uploaded_by_user_id": str(upload.uploaded_by_user_id)},
        )
    session.commit()
    terminal = session.scalars(
        select(MediaUpload).where(
            or_(
                MediaUpload.status.in_(
                    [
                        UploadStatus.EXPIRED,
                        UploadStatus.ABORTED,
                        UploadStatus.COMPLETED,
                        UploadStatus.DUPLICATE,
                    ]
                ),
                (MediaUpload.status == UploadStatus.FAILED) & (MediaUpload.expires_at < now),
            )
        )
    ).all()
    known = set()
    for upload in terminal:
        known.add(upload.staging_name)
        try:
            if staging.remove(upload.organization_id, upload.staging_name):
                counts["staging_removed"] += 1
        except (StorageError, OSError):
            continue
    active_names = set(
        session.scalars(
            select(MediaUpload.staging_name).where(
                MediaUpload.status.notin_(
                    [
                        UploadStatus.EXPIRED,
                        UploadStatus.ABORTED,
                        UploadStatus.COMPLETED,
                        UploadStatus.DUPLICATE,
                    ]
                )
            )
        )
    )
    ttl = timedelta(hours=settings.upload_session_ttl_hours)
    for part in staging.orphan_parts():
        if part.stem in active_names:
            continue
        try:
            age = now - datetime.fromtimestamp(part.stat().st_mtime, tz=UTC)
            if age > ttl:
                part.unlink()
                counts["orphans_removed"] += 1
        except OSError:
            continue
    return counts


__all__ = [
    "FinalizationFailedError",
    "Finalizer",
    "Outcome",
    "RetryableError",
    "claim_next",
    "expire_and_clean",
    "sha256_of",
]
