"""Upload finalization, worker side (ADR 0012, amendment 2: dedicated upload root).

Runs inside the existing worker (`affilio.worker.runner`) and is the only code that writes
`ready/` under the AFFILIO_UPLOADS root. One session at a time, claimed with a lease so a dead
worker's work is re-run by the next one. Every step is re-entrant and driven by the row's
recorded state:

    uploaded ──claim──▶ verifying ──intent──▶ finalizing ──rename+register──▶ completed
                            │                      │
                            ├─ invalid/mismatch ─▶ failed        (staging removed)
                            └─ same content ─────▶ duplicate     (staging removed)

Placement is one atomic `rename_noreplace` from `staging/<org>/<token>.part` to
`ready/<org>/<YYYY>/<MM>/<upload id>.<ext>` on the same filesystem: the staging name disappears
in the same operation the final name appears, and an existing name is never replaced.

Recovery is deterministic from (status, final_relative_path, verified_sha256, staging):

- `verifying` with staging present: verify from scratch (idempotent).
- `finalizing`, destination missing, staging present: rename again.
- `finalizing`, destination present (the rename happened): prove it is our file (size +
  SHA-256 equal to the intent) and continue with registration; a different file at that name
  is a conflict, never adopted. An empty reservation left by the fallback rename strategy is
  replaced by the staged file.
- staging missing before any placement: `failed(staging_missing)`.

The session row is the primary recovery record; the uploads root is never scanned.
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
from affilio.core.enums import CategoryFacet, IngestSource, MediaVariantKind, UploadStatus
from affilio.core.redaction import Redactor
from affilio.modules.media import ingest
from affilio.modules.media.fingerprint import quick_fingerprint
from affilio.modules.media.models import MediaRoot, MediaUpload
from affilio.modules.media.probe import FFprobe, ProbeError, Prober
from affilio.modules.media.roots import MediaRootRegistry
from affilio.modules.media.upload_storage import (
    ReadyStore,
    StagingStore,
    StorageError,
    ensure_layout,
    physical_name,
)

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
        if not self.settings.media_uploads_writable:
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
    def _uploads(self, organization_id: uuid.UUID) -> tuple[Path, MediaRoot]:
        binding = self.registry.uploads
        if binding is None:
            raise RetryableError("uploads root is not configured")
        if not binding.path.is_dir():
            raise RetryableError("uploads root is not mounted")  # outage: try again later
        try:
            ensure_layout(binding.path)
        except StorageError as exc:  # a symlink where a directory must be: never follow it
            raise FinalizationFailedError("destination_unavailable", str(exc)) from exc
        except OSError as exc:
            raise RetryableError(f"uploads root is not usable: {exc}") from exc
        roots = self.registry.ensure_db_roots(self.session, organization_id)
        return binding.path, roots["uploads"]

    def _process(self, upload: MediaUpload, now: datetime) -> Outcome:
        uploads_path, root_row = self._uploads(upload.organization_id)
        staging = StagingStore.for_root(uploads_path)
        assert staging is not None  # noqa: S101
        ready = ReadyStore.for_root(uploads_path)
        try:
            staged = staging.stat_regular(upload.organization_id, upload.staging_name)
        except StorageError as exc:
            raise FinalizationFailedError("staging_invalid", str(exc)) from exc
        staging_path = staging.path_for(upload.organization_id, upload.staging_name)

        if upload.status == UploadStatus.FINALIZING and upload.final_relative_path:
            return self._resume_placement(upload, staged, staging_path, ready, root_row, now)

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

        # Destination: UUID name under a date shard; classification stays in the database.
        parts = ReadyStore.directory_parts(upload.organization_id, now)
        try:
            filename = physical_name(upload.id, upload.safe_filename)
            dir_fd = ready.open_directory(parts, create=True)
        except (StorageError, OSError) as exc:
            raise FinalizationFailedError("destination_unavailable", str(exc)) from exc
        try:
            # Intent record before the filesystem changes. The partial unique index on
            # (organization, verified_sha256) decides concurrent identical uploads here.
            upload.verified_sha256 = content_hash
            upload.fingerprint = fingerprint
            upload.final_relative_path = ReadyStore.relative_path(parts, filename)
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
            self._place(upload, staging_path, dir_fd, filename, content_hash, uploads_path)
        finally:
            os.close(dir_fd)
        return self._register(upload, root_row, staging, now)

    def _resume_placement(
        self,
        upload: MediaUpload,
        staged: os.stat_result | None,
        staging_path: Path,
        ready: ReadyStore,
        root_row: MediaRoot,
        now: datetime,
    ) -> Outcome:
        """`finalizing` with an intent recorded: find out how far the previous attempt got."""
        assert upload.final_relative_path and upload.verified_sha256  # noqa: S101
        parts = upload.final_relative_path.split("/")
        folders, filename = parts[1:-1], parts[-1]  # drop the leading `ready`
        try:
            dir_fd = ready.open_directory(folders, create=staged is not None)
        except (StorageError, OSError) as exc:
            raise FinalizationFailedError("destination_unavailable", str(exc)) from exc
        uploads_path = ready.root.parent
        try:
            dest = ready.stat_entry(dir_fd, filename)
            if dest is None:
                if staged is None:
                    raise FinalizationFailedError(
                        "staging_missing", "neither the staged nor the placed file exists"
                    )
                self._place(
                    upload, staging_path, dir_fd, filename, upload.verified_sha256, uploads_path
                )
            elif staged is not None and self._is_reservation(dest):
                ReadyStore.replace_reservation(staging_path, dir_fd, filename)
            else:
                # Prove the destination is our file before touching the library.
                dest_path = Path(uploads_path, *upload.final_relative_path.split("/"))
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
        staging = StagingStore.for_root(uploads_path)
        assert staging is not None  # noqa: S101
        return self._register(upload, root_row, staging, now)

    @staticmethod
    def _is_reservation(info: os.stat_result) -> bool:
        return info.st_size == 0 and info.st_nlink == 1 and info.st_uid == os.geteuid()

    def _place(
        self,
        upload: MediaUpload,
        staging_path: Path,
        dir_fd: int,
        filename: str,
        content_hash: str,
        uploads_path: Path,
    ) -> None:
        try:
            strategy = ReadyStore.place(staging_path, dir_fd, filename)
            log.info(
                "media.upload.placed",
                upload_id=str(upload.id),
                strategy=strategy,
                final_relative_path=upload.final_relative_path,
            )
        except FileExistsError:
            try:
                existing = ReadyStore.stat_entry(dir_fd, filename)
            except StorageError as exc:  # a symlink or special file sits at the name
                raise FinalizationFailedError("destination_conflict", str(exc)) from exc
            if existing is not None and self._is_reservation(existing):
                try:
                    ReadyStore.replace_reservation(staging_path, dir_fd, filename)
                except OSError as exc:
                    raise FinalizationFailedError("placement_failed", str(exc)) from exc
                return
            dest_path = Path(uploads_path, *upload.final_relative_path.split("/"))
            if (
                existing is not None
                and existing.st_size == upload.expected_size
                and sha256_of(dest_path) == content_hash
            ):
                return  # the same bytes are already there (nothing else to move)
            raise FinalizationFailedError(
                "destination_conflict", "a different file occupies the intended final path"
            ) from None
        except OSError as exc:
            raise FinalizationFailedError("placement_failed", str(exc)) from exc

    def _register(
        self, upload: MediaUpload, root_row: MediaRoot, staging: StagingStore, now: datetime
    ) -> Outcome:
        assert upload.final_relative_path and upload.verified_sha256  # noqa: S101
        binding = self.registry.uploads
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
                    "title": ingest.title_from_name(upload.safe_filename),
                    "ingest_source": IngestSource.UPLOAD,
                    "uploaded_by_user_id": upload.uploaded_by_user_id,
                    "revision_of_asset_id": upload.replaces_asset_id,
                },
                facet_assignments={
                    CategoryFacet.PRODUCTION_OWNER: upload.production_owner_category_id,
                    CategoryFacet.PRODUCT_CATEGORY: upload.product_category_id,
                    CategoryFacet.CONTENT_TYPE: upload.content_type_id,
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
                "product_category_id": str(upload.product_category_id),
                "content_type_id": str(upload.content_type_id),
                "adopted_existing_asset": not registration.created,
                "root_key": root_row.key,
                "final_relative_path": upload.final_relative_path,
                "verified_sha256": upload.verified_sha256,
            },
        )
        self.session.commit()
        # The rename already removed the staging name; this covers the fallback strategy and
        # older rows. A failure here leaves a copy that maintenance removes.
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
            binding = self.registry.uploads
            staging = StagingStore.for_root(binding.path) if binding is not None else None
            if staging is not None:
                with contextlib.suppress(StorageError, OSError):
                    staging.remove(upload.organization_id, upload.staging_name)
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
    binding = registry.uploads
    if binding is None or not settings.media_uploads_writable:
        return counts
    staging = StagingStore.for_root(binding.path)
    if staging is None or not staging.root.is_dir():
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
    for upload in terminal:
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
