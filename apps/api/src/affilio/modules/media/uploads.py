"""Editor upload sessions, API side (upload track U4, ADR 0012).

The API owns the session rows and the staging file bytes under `<uploads>/staging`; it never
touches `ready/` or the legacy finals tree.
Every operation re-checks the caller: organization from the session cookie, then the row's
uploader (or admin). Progress is server-authoritative: `committed_offset` moves only after
the bytes are durably written and the chunk digest matched. Finalization is the worker's
job (`media/upload_finalize.py`): `complete` only flips the session to `uploaded`.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, selectinload

from affilio.api.deps import Identity
from affilio.core.config import Settings
from affilio.core.enums import (
    CategoryFacet,
    CategoryStatus,
    HandoffStatus,
    MediaAssetKind,
    MembershipRole,
    UploadStatus,
)
from affilio.core.errors import NotFoundError
from affilio.modules.identity import service as identity_service
from affilio.modules.media import owner_mappings
from affilio.modules.media.errors import (
    UploadChunkDigestMismatchError,
    UploadChunkTooLargeError,
    UploadForbiddenError,
    UploadGoneError,
    UploadIncompleteError,
    UploadInvalidFileError,
    UploadInvalidRevisionError,
    UploadInvalidTaxonomyError,
    UploadLockedError,
    UploadOffsetMismatchError,
    UploadQuotaExceededError,
    UploadStateError,
    UploadStorageUnavailableError,
)
from affilio.modules.media.models import TERMINAL_UPLOAD_STATUSES, MediaAsset, MediaUpload
from affilio.modules.media.schemas import UploadCreate
from affilio.modules.media.upload_storage import (
    StagingStore,
    StorageError,
    new_staging_name,
    sanitize_filename,
)
from affilio.modules.taxonomy import service as taxonomy_service
from affilio.modules.taxonomy.models import ContentCategory

READ_BUFFER = 64 * 1024
ACTIVE_STATUSES = (UploadStatus.CREATED, UploadStatus.UPLOADING)
OPEN_STATUSES = (
    UploadStatus.CREATED,
    UploadStatus.UPLOADING,
    UploadStatus.UPLOADED,
    UploadStatus.VERIFYING,
    UploadStatus.FINALIZING,
)


@dataclass(slots=True)
class UploadLimits:
    chunk_bytes: int
    max_file_bytes: int
    max_active_sessions_per_user: int
    session_ttl_hours: int
    staging_max_bytes: int
    free_space_headroom_bytes: int

    @classmethod
    def from_settings(cls, settings: Settings) -> UploadLimits:
        return cls(
            chunk_bytes=settings.upload_chunk_bytes,
            max_file_bytes=settings.upload_max_file_bytes,
            max_active_sessions_per_user=settings.upload_max_active_sessions_per_user,
            session_ttl_hours=settings.upload_session_ttl_hours,
            staging_max_bytes=settings.upload_staging_max_bytes,
            free_space_headroom_bytes=settings.upload_free_space_headroom_bytes,
        )


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- lookups


def _stmt(organization_id: uuid.UUID):
    return (
        select(MediaUpload)
        .options(
            selectinload(MediaUpload.production_owner),
            selectinload(MediaUpload.product_category),
            selectinload(MediaUpload.content_type),
            selectinload(MediaUpload.media_asset),
        )
        .where(MediaUpload.organization_id == organization_id)
    )


def get_upload(session: Session, organization_id: uuid.UUID, upload_id: uuid.UUID) -> MediaUpload:
    upload = session.scalar(_stmt(organization_id).where(MediaUpload.id == upload_id))
    if upload is None:
        raise NotFoundError("Upload not found")
    return upload


def authorize_access(identity: Identity, upload: MediaUpload) -> None:
    """The uploader and admins may act on a session; everyone else is refused."""
    if identity.has_role(MembershipRole.ADMIN):
        return
    if identity.user is None or upload.uploaded_by_user_id != identity.user.id:
        raise UploadForbiddenError("This upload session belongs to another user")


def list_uploads(
    session: Session,
    identity: Identity,
    *,
    status: UploadStatus | None = None,
    user_id: uuid.UUID | None = None,
    batch_id: uuid.UUID | None = None,
    limit: int = 100,
) -> list[MediaUpload]:
    """Editors see their own sessions; admins see every session (optionally one user's)."""
    stmt = _stmt(identity.organization.id)
    if identity.has_role(MembershipRole.ADMIN):
        if user_id is not None:
            stmt = stmt.where(MediaUpload.uploaded_by_user_id == user_id)
    else:
        if identity.user is None:
            return []
        stmt = stmt.where(MediaUpload.uploaded_by_user_id == identity.user.id)
    if status is not None:
        stmt = stmt.where(MediaUpload.status == status)
    if batch_id is not None:
        stmt = stmt.where(MediaUpload.batch_id == batch_id)
    stmt = stmt.order_by(MediaUpload.created_at.desc(), MediaUpload.id).limit(limit)
    return list(session.scalars(stmt))


# ---------------------------------------------------------------- options and validation


@dataclass(slots=True)
class UploadOptions:
    owners: list[ContentCategory]
    product_categories: list[ContentCategory]
    content_types: list[ContentCategory]
    facets_enabled: bool
    can_submit_for_others: bool


def options_for(session: Session, identity: Identity) -> UploadOptions:
    tree = taxonomy_service.load_tree(session, identity.organization.id)
    enabled = len(tree.facet_roots()) == len(CategoryFacet)
    if identity.has_role(MembershipRole.ADMIN):
        owners = sorted(
            tree.facet_values(CategoryFacet.PRODUCTION_OWNER), key=lambda c: c.name.lower()
        )
    elif identity.user is not None:
        owners = owner_mappings.submittable_owners(
            session, identity.organization.id, identity.user.id
        )
    else:
        owners = []
    return UploadOptions(
        owners=owners,
        product_categories=sorted(
            tree.facet_values(CategoryFacet.PRODUCT_CATEGORY), key=lambda c: c.name.lower()
        ),
        content_types=sorted(
            tree.facet_values(CategoryFacet.CONTENT_TYPE), key=lambda c: c.name.lower()
        ),
        facets_enabled=enabled,
        can_submit_for_others=identity.has_role(MembershipRole.ADMIN),
    )


def _facet_value(
    session: Session, organization_id: uuid.UUID, category_id: uuid.UUID, facet: CategoryFacet
) -> ContentCategory:
    """An active value under the organization's root for `facet`; foreign ids are a 404."""
    category = taxonomy_service.get_category(session, organization_id, category_id)
    if category.facet != facet or category.parent_id is None:
        raise UploadInvalidTaxonomyError(
            f"Category is not a {facet.value.replace('_', ' ')} value",
            details={"category_id": str(category_id), "facet": facet.value},
        )
    if category.status != CategoryStatus.ACTIVE:
        raise UploadInvalidTaxonomyError(
            "An archived category cannot be used for new uploads",
            details={"category_id": str(category_id), "status": category.status.value},
        )
    return category


def _authorize_owner(
    session: Session, identity: Identity, owner: ContentCategory, submitted_for: uuid.UUID | None
) -> None:
    if identity.user is None:
        raise UploadForbiddenError("A user session is required to upload")
    if identity.has_role(MembershipRole.ADMIN):
        if submitted_for is not None:
            identity_service.get_member(session, identity.organization.id, submitted_for)
        return
    if submitted_for is not None and submitted_for != identity.user.id:
        raise UploadForbiddenError("Only an admin may submit on behalf of another editor")
    allowed = {
        c.id
        for c in owner_mappings.submittable_owners(
            session, identity.organization.id, identity.user.id
        )
    }
    if owner.id not in allowed:
        raise UploadForbiddenError(
            "You are not mapped to that production owner",
            details={"production_owner_category_id": str(owner.id)},
        )


def _validate_revision(
    session: Session, identity: Identity, replaces_asset_id: uuid.UUID
) -> MediaAsset:
    asset = session.scalar(
        select(MediaAsset).where(
            MediaAsset.organization_id == identity.organization.id,
            MediaAsset.id == replaces_asset_id,
        )
    )
    if asset is None:
        raise NotFoundError("Asset to replace not found")
    if asset.asset_kind != MediaAssetKind.FINAL or asset.handoff_status != HandoffStatus.REJECTED:
        raise UploadInvalidRevisionError(
            "Only a rejected final can be replaced by a corrected submission",
            details={"handoff_status": asset.handoff_status},
        )
    if not identity.has_role(MembershipRole.ADMIN) and (
        identity.user is None or asset.uploaded_by_user_id != identity.user.id
    ):
        raise UploadForbiddenError("You may only correct finals you uploaded")
    return asset


def _check_quotas(
    session: Session,
    identity: Identity,
    staging: StagingStore,
    limits: UploadLimits,
    expected_size: int,
) -> None:
    assert identity.user is not None  # noqa: S101 - checked by _authorize_owner
    active = session.scalar(
        select(func.count()).where(
            MediaUpload.uploaded_by_user_id == identity.user.id,
            MediaUpload.organization_id == identity.organization.id,
            MediaUpload.status.in_(OPEN_STATUSES),
        )
    )
    if (active or 0) >= limits.max_active_sessions_per_user:
        raise UploadQuotaExceededError(
            "Too many open upload sessions; finish or abort some first",
            details={"active": int(active or 0), "limit": limits.max_active_sessions_per_user},
        )
    reserved = session.scalar(
        select(func.coalesce(func.sum(MediaUpload.expected_size), 0)).where(
            MediaUpload.status.in_(OPEN_STATUSES)
        )
    )
    if int(reserved or 0) + expected_size > limits.staging_max_bytes:
        raise UploadQuotaExceededError(
            "Staging capacity is reserved by other uploads; try again later",
            details={"reserved": int(reserved or 0), "limit": limits.staging_max_bytes},
        )
    try:
        free = staging.free_bytes()
    except OSError as exc:
        raise UploadStorageUnavailableError("Staging directory is not available") from exc
    if free < expected_size + limits.free_space_headroom_bytes:
        raise UploadQuotaExceededError(
            "Not enough free space for this file", details={"free_bytes": free}
        )


# ---------------------------------------------------------------- lifecycle


def create_upload(
    session: Session,
    identity: Identity,
    *,
    data: UploadCreate,
    staging: StagingStore | None,
    limits: UploadLimits,
    allowed_extensions: set[str],
    now: datetime | None = None,
) -> MediaUpload:
    if staging is None or not staging.available():
        raise UploadStorageUnavailableError("Uploads are not enabled on this server")
    if identity.user is None:
        raise UploadForbiddenError("A user session is required to upload")
    now = now or _now()
    org_id = identity.organization.id
    owner = _facet_value(
        session, org_id, data.production_owner_category_id, CategoryFacet.PRODUCTION_OWNER
    )
    product = _facet_value(
        session, org_id, data.product_category_id, CategoryFacet.PRODUCT_CATEGORY
    )
    content_type = _facet_value(session, org_id, data.content_type_id, CategoryFacet.CONTENT_TYPE)
    _authorize_owner(session, identity, owner, data.submitted_for_user_id)
    if data.replaces_asset_id is not None:
        _validate_revision(session, identity, data.replaces_asset_id)
    try:
        safe_name = sanitize_filename(data.filename, allowed_extensions)
    except ValueError as exc:
        raise UploadInvalidFileError(str(exc)) from exc
    if data.size <= 0 or data.size > limits.max_file_bytes:
        raise UploadInvalidFileError(
            "File size is out of range", details={"max_bytes": limits.max_file_bytes}
        )
    _check_quotas(session, identity, staging, limits, data.size)

    staging_name = new_staging_name()
    try:
        staging.create(org_id, staging_name)
    except StorageError as exc:
        raise UploadStorageUnavailableError(str(exc)) from exc
    upload = MediaUpload(
        id=uuid.uuid4(),
        organization_id=org_id,
        uploaded_by_user_id=identity.user.id,
        submitted_for_user_id=data.submitted_for_user_id,
        production_owner_category_id=owner.id,
        production_owner_name=owner.name,
        product_category_id=product.id,
        content_type_id=content_type.id,
        batch_id=data.batch_id,
        replaces_asset_id=data.replaces_asset_id,
        original_filename=data.filename[:255],
        safe_filename=safe_name,
        expected_size=data.size,
        client_sha256=data.client_sha256,
        chunk_bytes=limits.chunk_bytes,
        committed_offset=0,
        staging_name=staging_name,
        status=UploadStatus.CREATED,
        expires_at=now + timedelta(hours=limits.session_ttl_hours),
    )
    session.add(upload)
    session.flush()
    return get_upload(session, org_id, upload.id)


def lock_for_chunk(
    session: Session, organization_id: uuid.UUID, upload_id: uuid.UUID
) -> MediaUpload:
    """Row lock for one chunk write; a second writer gets 423 instead of waiting."""
    try:
        upload = session.scalar(
            select(MediaUpload)
            .where(MediaUpload.organization_id == organization_id, MediaUpload.id == upload_id)
            .with_for_update(nowait=True)
        )
    except OperationalError as exc:
        session.rollback()
        raise UploadLockedError("Another chunk of this upload is being written") from exc
    if upload is None:
        raise NotFoundError("Upload not found")
    return upload


@dataclass(slots=True)
class ChunkPlan:
    """Decision for an incoming chunk: write it, or answer a replay without writing."""

    upload: MediaUpload
    replay: bool


def plan_chunk(
    upload: MediaUpload, *, offset: int, length: int, sha256: str, now: datetime
) -> ChunkPlan:
    if upload.status in (UploadStatus.ABORTED, UploadStatus.EXPIRED):
        raise UploadGoneError("Upload session is no longer accepting data")
    if upload.status not in ACTIVE_STATUSES:
        raise UploadStateError(
            "Upload is already complete", details={"status": upload.status.value}
        )
    if now >= upload.expires_at:
        raise UploadGoneError("Upload session has expired")
    if length <= 0 or length > upload.chunk_bytes:
        raise UploadChunkTooLargeError(
            "Chunk length must be between 1 byte and the chunk size",
            details={"chunk_bytes": upload.chunk_bytes},
        )
    committed = upload.committed_offset
    if offset == committed:
        if offset + length > upload.expected_size:
            raise UploadChunkTooLargeError(
                "Chunk would exceed the declared file size",
                details={"expected_size": upload.expected_size, "offset": offset},
            )
        return ChunkPlan(upload=upload, replay=False)
    if (
        upload.last_chunk_offset is not None
        and offset == upload.last_chunk_offset
        and offset + length == committed
        and sha256 == upload.last_chunk_sha256
    ):
        return ChunkPlan(upload=upload, replay=True)  # idempotent retry of the last chunk
    raise UploadOffsetMismatchError(
        "Upload-Offset does not match the committed offset",
        details={"committed_offset": committed},
    )


async def write_chunk(
    upload: MediaUpload,
    staging: StagingStore,
    *,
    offset: int,
    length: int,
    sha256: str,
    body: AsyncIterator[bytes],
    now: datetime,
    ttl_hours: int,
) -> int:
    """Stream the chunk body into the staging file at `offset` while hashing it, with a
    bounded read buffer. On any mismatch the file is truncated back to the committed offset
    and nothing is committed. Returns the new committed offset (caller commits the row)."""
    try:
        fd = staging.open_for_write(upload.organization_id, upload.staging_name)
    except StorageError as exc:
        raise UploadStorageUnavailableError(str(exc)) from exc
    digest = hashlib.sha256()
    written = 0
    try:
        async for piece in body:
            if not piece:
                continue
            if written + len(piece) > length:
                os.ftruncate(fd, offset)
                raise UploadChunkTooLargeError(
                    "Body is longer than Content-Length", details={"length": length}
                )
            view = memoryview(piece)
            while view:
                n = os.pwrite(fd, view, offset + written)
                written += n
                view = view[n:]
            digest.update(piece)
        if written != length:
            os.ftruncate(fd, offset)
            raise UploadChunkDigestMismatchError(
                "Body is shorter than Content-Length",
                details={"received": written, "length": length},
            )
        if digest.hexdigest() != sha256.lower():
            os.ftruncate(fd, offset)
            raise UploadChunkDigestMismatchError(
                "Chunk SHA-256 does not match the body", details={"offset": offset}
            )
        os.fdatasync(fd)
    finally:
        os.close(fd)
    index = offset // upload.chunk_bytes
    manifest = list(upload.chunk_manifest or [])
    if len(manifest) < index:
        manifest.extend([""] * (index - len(manifest)))  # defensive: offsets are sequential
    if len(manifest) == index:
        manifest.append(sha256.lower())
    else:
        manifest[index] = sha256.lower()
    upload.chunk_manifest = manifest[: index + 1]  # new list so the JSONB change is tracked
    upload.committed_offset = offset + length
    upload.last_chunk_offset = offset
    upload.last_chunk_sha256 = sha256.lower()
    upload.last_chunk_at = now
    upload.status = UploadStatus.UPLOADING
    upload.expires_at = now + timedelta(hours=ttl_hours)  # TTL counts from the last chunk
    return upload.committed_offset


def complete_upload(
    session: Session, organization_id: uuid.UUID, upload_id: uuid.UUID, *, now: datetime
) -> tuple[MediaUpload, bool]:
    """Hand the session to the worker. Returns (upload, changed). Idempotent: once the
    session left the uploading states, the current state is simply returned."""
    upload = lock_for_chunk(session, organization_id, upload_id)
    if upload.status in (UploadStatus.ABORTED, UploadStatus.EXPIRED):
        raise UploadGoneError("Upload session is no longer available")
    if upload.status not in ACTIVE_STATUSES:
        return upload, False
    if upload.committed_offset != upload.expected_size:
        raise UploadIncompleteError(
            "Not every byte has been received",
            details={
                "committed_offset": upload.committed_offset,
                "expected_size": upload.expected_size,
            },
        )
    upload.status = UploadStatus.UPLOADED
    upload.uploaded_at = now
    session.flush()
    return upload, True


def abort_upload(
    session: Session,
    organization_id: uuid.UUID,
    upload_id: uuid.UUID,
    staging: StagingStore | None,
) -> tuple[MediaUpload, bool]:
    """Abort before the worker has claimed the session. Returns (upload, changed)."""
    upload = lock_for_chunk(session, organization_id, upload_id)
    if upload.status in TERMINAL_UPLOAD_STATUSES:
        return upload, False
    if upload.status in (UploadStatus.VERIFYING, UploadStatus.FINALIZING):
        raise UploadStateError(
            "The upload is being finalized and can no longer be aborted",
            details={"status": upload.status.value},
        )
    upload.status = UploadStatus.ABORTED
    session.flush()
    if staging is not None:
        with contextlib.suppress(StorageError):  # left for maintenance; the row is terminal
            staging.remove(upload.organization_id, upload.staging_name)
    return upload, True


__all__ = [
    "ACTIVE_STATUSES",
    "OPEN_STATUSES",
    "READ_BUFFER",
    "ChunkPlan",
    "UploadLimits",
    "UploadOptions",
    "abort_upload",
    "authorize_access",
    "complete_upload",
    "create_upload",
    "get_upload",
    "list_uploads",
    "lock_for_chunk",
    "options_for",
    "plan_chunk",
    "write_chunk",
]
