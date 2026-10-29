"""Shared ingestion service (upload track U3): single-file registration without a root scan,
reconciliation, idempotency, facet classification and primacy, handoff and period
initialisation, thumbnail scheduling, duplicate lookup, transaction boundary, and parity
with the scanner. Needs TEST_DATABASE_URL."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from affilio.core.enums import (
    CategoryFacet,
    HandoffStatus,
    MediaAssetKind,
    MediaAssetStatus,
    MediaVariantKind,
    MediaVariantStatus,
    ThumbnailStatus,
)
from affilio.core.redaction import Redactor
from affilio.modules.media import ingest
from affilio.modules.media.errors import InvalidMediaPathError
from affilio.modules.media.fingerprint import full_hash, quick_fingerprint
from affilio.modules.media.models import MediaAsset, MediaFileVariant
from affilio.modules.taxonomy import service as taxonomy_service
from affilio.modules.taxonomy.schemas import CategoryCreate
from tests.media_helpers import FakeProber, build_settings, create_organization, write_bytes
from tests.publishing_helpers import scan_root
from tests.test_facets import categories_of, latest_scan_stats

pytestmark = pytest.mark.database


def make_context(db_session: Session, organization, media_dirs, errors: list, prober=None):
    settings = build_settings(media_dirs)
    registry = media_dirs.registry
    roots = registry.ensure_db_roots(db_session, organization.id)
    binding = registry.get("finals")
    assert binding is not None
    return ingest.IngestContext.load(
        db_session,
        binding=binding,
        root=roots["finals"],
        organization_id=organization.id,
        now=datetime.now(UTC),
        prober=prober or FakeProber(),
        redactor=Redactor.from_settings(settings),
        on_error=lambda kind, path, message: errors.append((kind, str(path), message)),
    )


def enable_facets_with_values(session: Session, organization) -> None:
    roots = taxonomy_service.ensure_facet_roots(session, organization.id)
    for facet, name, slug in (
        (CategoryFacet.PRODUCTION_OWNER, "Hang", "hang"),
        (CategoryFacet.PRODUCT_CATEGORY, "Men's Fashion", "mens-fashion"),
        (CategoryFacet.CONTENT_TYPE, "Review", "review"),
    ):
        taxonomy_service.create_category(
            session,
            organization.id,
            CategoryCreate(name=name, slug=slug, parent_id=roots[facet].id),
        )
    session.commit()


def drop(media_dirs, relative: str, size: int = 6_000, seed: int = 1) -> Path:
    path = media_dirs.finals / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    write_bytes(path, size, seed=seed)
    return path


def asset_count(session: Session, organization) -> int:
    return (
        session.scalar(select(func.count()).where(MediaAsset.organization_id == organization.id))
        or 0
    )


# ---------------------------------------------------------------- registration


def test_register_file_creates_a_classified_final_without_a_scan(
    db_session: Session, organization, media_dirs
) -> None:
    enable_facets_with_values(db_session, organization)
    errors: list = []
    ctx = make_context(db_session, organization, media_dirs, errors)
    path = drop(media_dirs, "Hang/Mens-Fashion/Review/TEST-001.mp4")

    registration = ingest.register_file(
        ctx, "Hang/Mens-Fashion/Review/TEST-001.mp4", asset_fields={"title": "Custom title"}
    )
    db_session.commit()

    assert registration.created and registration.changed and registration.probe_ok
    assert registration.classification_changed is False  # first classification, no change
    asset, variant = registration.asset, registration.variant
    assert asset.asset_kind == MediaAssetKind.FINAL and asset.status == MediaAssetStatus.ACTIVE
    assert asset.handoff_status == HandoffStatus.PENDING_REVIEW
    assert asset.production_period == ctx.now.strftime("%Y-%m")
    assert asset.first_seen_at == ctx.now and asset.last_seen_at == ctx.now
    assert asset.thumbnail_status == ThumbnailStatus.PENDING
    assert asset.title == "Custom title"
    assert asset.width == 1080 and asset.height == 1920 and asset.video_codec == "h264"
    assert variant.kind == MediaVariantKind.FINAL and variant.status == MediaVariantStatus.PRESENT
    assert variant.relative_path == "Hang/Mens-Fashion/Review/TEST-001.mp4"
    assert variant.file_name == "TEST-001.mp4" and variant.extension == ".mp4"
    assert variant.file_size == 6_000 and variant.fingerprint == quick_fingerprint(path, 6_000)
    assert variant.probed_at == ctx.now and variant.probe["format"]["format_name"] == "mov"
    assert categories_of(asset) == {
        "mens-fashion": ("path", True),
        "hang": ("path", False),
        "review": ("path", False),
    }
    assert errors == []

    # A following incremental scan sees exactly what it would have produced itself.
    scan_root(db_session, organization.id, media_dirs, "finals")
    stats = latest_scan_stats(db_session, organization)
    assert stats["files_seen"] == 1 and stats["unchanged"] == 1 and stats["added"] == 0
    assert stats["reclassified"] == 0 and stats["suggestions"] == 0
    db_session.expire_all()
    assert asset_count(db_session, organization) == 1
    assert db_session.get(MediaAsset, asset.id).title == "Custom title"


def test_register_file_is_idempotent_and_keeps_a_ready_thumbnail(
    db_session: Session, organization, media_dirs
) -> None:
    ctx = make_context(db_session, organization, media_dirs, [])
    drop(media_dirs, "Hang/T9/legacy.mp4")
    first = ingest.register_file(ctx, "Hang/T9/legacy.mp4")
    first.asset.thumbnail_status = ThumbnailStatus.READY
    first.asset.thumbnail_attempts = 1
    db_session.commit()

    prober = FakeProber()
    ctx = make_context(db_session, organization, media_dirs, [], prober=prober)
    again = ingest.register_file(ctx, "Hang/T9/legacy.mp4")
    db_session.commit()
    assert again.created is False and again.changed is False
    assert again.asset.id == first.asset.id and again.variant.id == first.variant.id
    assert again.asset.thumbnail_status == ThumbnailStatus.READY
    assert again.asset.thumbnail_attempts == 1
    assert prober.calls == []  # unchanged file: no probe, no fingerprint read needed
    assert asset_count(db_session, organization) == 1
    assert db_session.scalar(select(func.count()).select_from(MediaFileVariant)) == 1
    assert categories_of(again.asset) == {"uncategorized": ("folder", True)}


def test_register_file_reconciles_changed_content(
    db_session: Session, organization, media_dirs
) -> None:
    ctx = make_context(db_session, organization, media_dirs, [])
    path = drop(media_dirs, "Hang/T9/edit.mp4", seed=1)
    first = ingest.register_file(ctx, "Hang/T9/edit.mp4")
    first.asset.thumbnail_status = ThumbnailStatus.READY
    first.asset.duplicate_of_id = None
    db_session.commit()
    old_fingerprint = first.variant.fingerprint

    write_bytes(path, 7_000, seed=2)
    later = path.stat().st_mtime + 5
    os.utime(path, (later, later))
    prober = FakeProber()
    ctx = make_context(db_session, organization, media_dirs, [], prober=prober)
    second = ingest.register_file(ctx, "Hang/T9/edit.mp4")
    db_session.commit()

    assert second.created is False and second.changed is True and second.probe_ok
    assert second.asset.id == first.asset.id
    assert second.variant.file_size == 7_000
    assert second.variant.fingerprint != old_fingerprint
    assert second.variant.content_hash is None
    assert second.asset.thumbnail_status == ThumbnailStatus.PENDING
    assert prober.calls == [path]


def test_register_file_rediscovers_a_missing_asset(
    db_session: Session, organization, media_dirs
) -> None:
    ctx = make_context(db_session, organization, media_dirs, [])
    drop(media_dirs, "Hang/T9/back.mp4")
    first = ingest.register_file(ctx, "Hang/T9/back.mp4")
    first.variant.status = MediaVariantStatus.MISSING
    first.variant.missing_since = ctx.now
    first.asset.status = MediaAssetStatus.MISSING
    first.asset.missing_since = ctx.now
    first.asset.thumbnail_status = ThumbnailStatus.READY
    db_session.commit()

    ctx = make_context(db_session, organization, media_dirs, [])
    again = ingest.register_file(ctx, "Hang/T9/back.mp4")
    db_session.commit()
    assert again.created is False and again.changed is True
    assert (
        again.variant.status == MediaVariantStatus.PRESENT and again.variant.missing_since is None
    )
    assert again.asset.status == MediaAssetStatus.ACTIVE and again.asset.missing_since is None
    assert again.asset.thumbnail_status == ThumbnailStatus.PENDING


def test_register_file_records_unsupported_media_and_legacy_classification(
    db_session: Session, organization, media_dirs
) -> None:
    enable_facets_with_values(db_session, organization)
    errors: list = []
    ctx = make_context(db_session, organization, media_dirs, errors)
    drop(media_dirs, "Hang/T9/broken.mp4")
    registration = ingest.register_file(ctx, "Hang/T9/broken.mp4")
    db_session.commit()
    assert registration.created and registration.probe_ok is False
    assert registration.asset.status == MediaAssetStatus.UNSUPPORTED
    assert registration.asset.thumbnail_status == ThumbnailStatus.SKIPPED
    assert registration.asset.probe_error.startswith("unsupported:")
    assert errors == [
        ("probe_unsupported", "Hang/T9/broken.mp4", "Invalid data found when processing input")
    ]
    # Legacy path: Uncategorized primary plus the known owner, exactly like a scan.
    assert categories_of(registration.asset) == {
        "uncategorized": ("folder", True),
        "hang": ("path", False),
    }


def test_register_file_refuses_symlinks_missing_files_escapes_and_unknown_fields(
    db_session: Session, organization, media_dirs
) -> None:
    ctx = make_context(db_session, organization, media_dirs, [])
    real = drop(media_dirs, "Hang/T9/real.mp4")
    link = media_dirs.finals / "Hang/T9/link.mp4"
    link.symlink_to(real)
    with pytest.raises(ingest.IngestFileError, match="Symbolic links"):
        ingest.register_file(ctx, "Hang/T9/link.mp4")
    with pytest.raises(ingest.IngestFileError, match="not found"):
        ingest.register_file(ctx, "Hang/T9/absent.mp4")
    with pytest.raises(ingest.IngestFileError, match="regular files"):
        ingest.register_file(ctx, "Hang/T9")
    with pytest.raises(InvalidMediaPathError):
        ingest.register_file(ctx, "../outside.mp4")
    with pytest.raises(ValueError, match="Unknown media asset fields: uploaded_by"):
        ingest.register_file(ctx, "Hang/T9/real.mp4", asset_fields={"uploaded_by": "x"})
    db_session.rollback()
    assert asset_count(db_session, organization) == 0


def test_register_file_never_commits(db_session: Session, organization, media_dirs) -> None:
    ctx = make_context(db_session, organization, media_dirs, [])
    drop(media_dirs, "Hang/T9/uncommitted.mp4")
    registration = ingest.register_file(ctx, "Hang/T9/uncommitted.mp4")
    assert registration.created
    db_session.rollback()  # the caller owns the transaction: nothing reached the database
    assert asset_count(db_session, organization) == 0


# ---------------------------------------------------------------- duplicate content


def test_duplicate_lookup_confirms_by_full_hash_only(
    db_session: Session, organization, media_dirs
) -> None:
    ctx = make_context(db_session, organization, media_dirs, [])
    original = drop(media_dirs, "Hang/T9/original.mp4", size=4 * 1024 * 1024, seed=7)
    registered = ingest.register_file(ctx, "Hang/T9/original.mp4")
    db_session.commit()
    assert registered.variant.content_hash is None

    # Same bytes under another name: a candidate by size + fingerprint, confirmed by hash,
    # and the stored hash is filled in on the existing variant for next time.
    copy = media_dirs.exports / "copy.mp4"
    copy.write_bytes(original.read_bytes())
    size = copy.stat().st_size
    assert ingest.candidate_duplicates(
        db_session,
        organization.id,
        kind=MediaVariantKind.FINAL,
        size=size,
        fingerprint=quick_fingerprint(copy, size),
    ) == [registered.variant]
    match = ingest.find_duplicate(
        db_session,
        organization.id,
        media_dirs.registry,
        kind=MediaVariantKind.FINAL,
        size=size,
        fingerprint=quick_fingerprint(copy, size),
        content_hash=full_hash(copy),
    )
    assert match is not None and match.asset.id == registered.asset.id
    assert match.content_hash == full_hash(original) == registered.variant.content_hash

    # Same size and sampled fingerprint but different bytes in an unsampled region: still a
    # candidate (weak), never a confirmed duplicate.
    near = media_dirs.exports / "near.mp4"
    data = bytearray(original.read_bytes())
    offset = 1_200_000  # between the first and the middle sample
    data[offset] ^= 0xFF
    near.write_bytes(bytes(data))
    assert quick_fingerprint(near, size) == registered.variant.fingerprint
    assert full_hash(near) != registered.variant.content_hash
    assert (
        len(
            ingest.candidate_duplicates(
                db_session,
                organization.id,
                kind=MediaVariantKind.FINAL,
                size=size,
                fingerprint=quick_fingerprint(near, size),
            )
        )
        == 1
    )
    assert (
        ingest.find_duplicate(
            db_session,
            organization.id,
            media_dirs.registry,
            kind=MediaVariantKind.FINAL,
            size=size,
            fingerprint=quick_fingerprint(near, size),
            content_hash=full_hash(near),
        )
        is None
    )

    # Different content of the same size: not even a candidate.
    other = drop(media_dirs, "Hang/T9/other.mp4", size=4 * 1024 * 1024, seed=8)
    assert (
        ingest.candidate_duplicates(
            db_session,
            organization.id,
            kind=MediaVariantKind.FINAL,
            size=size,
            fingerprint=quick_fingerprint(other, size),
        )
        == []
    )

    # Exact stored-hash matches win without touching the disk, and the organization boundary
    # always applies.
    assert (
        ingest.find_duplicate(
            db_session,
            organization.id,
            media_dirs.registry,
            kind=MediaVariantKind.FINAL,
            size=size,
            fingerprint="not-the-fingerprint",
            content_hash=full_hash(original),
        )
        is not None
    )
    stranger = create_organization(db_session)
    db_session.commit()
    assert (
        ingest.find_duplicate(
            db_session,
            stranger.id,
            media_dirs.registry,
            kind=MediaVariantKind.FINAL,
            size=size,
            fingerprint=quick_fingerprint(copy, size),
            content_hash=full_hash(copy),
        )
        is None
    )
    db_session.delete(stranger)
    db_session.commit()


def test_duplicate_lookup_ignores_missing_variants(
    db_session: Session, organization, media_dirs
) -> None:
    ctx = make_context(db_session, organization, media_dirs, [])
    path = drop(media_dirs, "Hang/T9/gone.mp4", seed=3)
    registered = ingest.register_file(ctx, "Hang/T9/gone.mp4")
    registered.variant.status = MediaVariantStatus.MISSING
    db_session.commit()
    size = path.stat().st_size
    assert (
        ingest.find_duplicate(
            db_session,
            organization.id,
            media_dirs.registry,
            kind=MediaVariantKind.FINAL,
            size=size,
            fingerprint=quick_fingerprint(path, size),
            content_hash=full_hash(path),
        )
        is None
    )
