"""Editor uploads (upload track U4): session API (RBAC, CSRF, isolation, taxonomy and owner
authorization, chunk protocol, resume, abort), worker finalization (verification, duplicates,
placement, registration with attribution, revisions), crash recovery at every boundary,
expiry cleanup, filesystem safety. Needs TEST_DATABASE_URL."""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from affilio.app import create_app
from affilio.core.config import get_settings
from affilio.core.enums import (
    HandoffStatus,
    IngestSource,
    MediaVariantKind,
    MembershipRole,
    ThumbnailStatus,
    UploadStatus,
)
from affilio.db.session import dispose_engine
from affilio.modules.identity.models import AuditEvent
from affilio.modules.media import upload_storage
from affilio.modules.media.models import MediaAsset, MediaFileVariant, MediaUpload
from affilio.modules.media.probe import ProbeError
from affilio.modules.media.upload_finalize import Finalizer, expire_and_clean
from affilio.modules.media.upload_storage import (
    FinalsPlacer,
    StagingStore,
    folder_name_for,
    sanitize_filename,
    with_suffix,
)
from tests.auth_helpers import create_member, login, sign_in
from tests.media_helpers import FakeProber, build_settings, create_organization
from tests.publishing_helpers import scan_root
from tests.test_facets import categories_of, latest_scan_stats

pytestmark = pytest.mark.database

BASE = "/api/v1/media/uploads"
CHUNK = 8_192  # small chunks keep the tests fast; the protocol is size-agnostic


class ContentProber(FakeProber):
    """Fails on content, not on the filename (staging names are random tokens)."""

    def probe(self, path: Path):
        with path.open("rb") as handle:
            if handle.read(4) == b"BAD!":
                raise ProbeError("unsupported", "Invalid data found when processing input")
        return super().probe(path)


def payload(size: int, seed: int = 1) -> bytes:
    out = bytearray()
    x = seed
    while len(out) < size:
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        out += x.to_bytes(4, "little")
    return bytes(out[:size])


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- fixtures


@pytest.fixture
def incoming(media_dirs) -> Path:
    path = media_dirs.finals / upload_storage.STAGING_DIRNAME
    path.mkdir(mode=0o750)
    return path


@pytest.fixture
def api(
    monkeypatch: pytest.MonkeyPatch, media_dirs, incoming, organization, schema, db_session
) -> Iterator[TestClient]:
    for key, value in media_dirs.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("MEDIA_INCOMING_PATH", str(incoming))
    monkeypatch.setenv("UPLOAD_CHUNK_BYTES", str(CHUNK))
    monkeypatch.setenv("UPLOAD_MAX_ACTIVE_SESSIONS_PER_USER", "3")
    monkeypatch.setenv("DEFAULT_ORGANIZATION_SLUG", organization.slug)
    monkeypatch.delenv("MEDIA_ADMIN_TOKEN", raising=False)
    get_settings.cache_clear()
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        yield client
    dispose_engine()
    get_settings.cache_clear()


def cat(client: TestClient, name: str, parent_id: str, **extra) -> dict:
    response = client.post(
        "/api/v1/content-categories", json={"name": name, "parent_id": parent_id, **extra}
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def world(api: TestClient, db_session: Session, organization) -> dict:
    """Admin signed in; facet roots; owners Hang/Hao, product Men's Fashion (+ archived Old),
    content Review; editor mapped to Hang; viewer; a second editor without mappings."""
    admin = sign_in(api, db_session, organization, role=MembershipRole.ADMIN)
    roots = {
        r["facet"]: r for r in api.post("/api/v1/content-categories/ensure-facets").json()["roots"]
    }
    w = {"admin": admin, "roots": roots}
    w["hang"] = cat(api, "Hang", roots["production_owner"]["id"])
    w["hao"] = cat(api, "Hao", roots["production_owner"]["id"])
    w["mens"] = cat(api, "Men's Fashion", roots["product_category"]["id"], slug="mens-fashion")
    w["review"] = cat(api, "Review", roots["content_type"]["id"])
    old = cat(api, "Old", roots["content_type"]["id"])
    api.patch(f"/api/v1/content-categories/{old['id']}", json={"status": "archived"})
    w["old"] = old
    w["editor"] = create_member(db_session, organization, role=MembershipRole.EDITOR)
    w["editor2"] = create_member(db_session, organization, role=MembershipRole.EDITOR)
    w["viewer"] = create_member(db_session, organization, role=MembershipRole.VIEWER)
    mapped = api.post(
        "/api/v1/media/owner-mappings",
        json={"user_id": str(w["editor"].id), "production_owner_category_id": w["hang"]["id"]},
    )
    assert mapped.status_code == 201, mapped.text
    return w


def create(client: TestClient, w: dict, *, expect: int = 201, **over) -> dict:
    body = {
        "filename": "TEST-001.mp4",
        "size": CHUNK * 3,
        "production_owner_category_id": w["hang"]["id"],
        "product_category_id": w["mens"]["id"],
        "content_type_id": w["review"]["id"],
    }
    body.update(over)
    response = client.post(BASE, json=body)
    assert response.status_code == expect, response.text
    return response.json()


def send_chunk(client: TestClient, upload_id: str, offset: int, data: bytes, **headers):
    return client.patch(
        f"{BASE}/{upload_id}/chunk",
        content=data,
        headers={
            "Upload-Offset": str(offset),
            "X-Upload-Chunk-Sha256": sha(data),
            "Content-Type": "application/offset+octet-stream",
            **headers,
        },
    )


def upload_all(client: TestClient, upload: dict, data: bytes) -> dict:
    offset = 0
    while offset < len(data):
        piece = data[offset : offset + CHUNK]
        response = send_chunk(client, upload["id"], offset, piece)
        assert response.status_code == 200, response.text
        assert response.json()["committed_offset"] == offset + len(piece)
        offset += len(piece)
    response = client.post(f"{BASE}/{upload['id']}/complete")
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "uploaded"
    return response.json()


def finalizer(db_session: Session, media_dirs, **overrides) -> Finalizer:
    settings = build_settings(
        media_dirs, media_finals_writable=True, upload_finalize_lease_seconds=600, **overrides
    )
    return Finalizer.build(
        db_session, media_dirs.registry, settings, prober=ContentProber(), worker_id="test"
    )


def staging_path(incoming: Path, organization, upload: dict) -> Path:
    return incoming / str(organization.id) / f"{row(upload).staging_name}.part"


_session_holder: dict = {}


def row(upload: dict) -> MediaUpload:
    session = _session_holder["session"]
    session.expire_all()
    return session.get(MediaUpload, uuid.UUID(upload["id"]))


@pytest.fixture(autouse=True)
def _hold_session(db_session: Session):
    _session_holder["session"] = db_session
    yield
    _session_holder.clear()


def audit_actions(session: Session, organization) -> list[str]:
    session.expire_all()
    return list(
        session.scalars(
            select(AuditEvent.action)
            .where(
                AuditEvent.organization_id == organization.id, AuditEvent.action.like("upload.%")
            )
            .order_by(AuditEvent.created_at, AuditEvent.id)
        )
    )


# ---------------------------------------------------------------- unit: names


def test_filename_and_folder_sanitising() -> None:
    allowed = {".mp4", ".mov"}
    assert sanitize_filename("../../etc/passwd.mp4", allowed) == "etcpasswd.mp4"
    assert sanitize_filename("  .hidden\x00name .MP4", allowed) == "hiddenname.mp4"
    assert sanitize_filename("a" * 300 + ".mov", allowed).endswith(".mov")
    assert len(sanitize_filename("a" * 300 + ".mov", allowed)) <= 120
    for bad in ("", "...", "noext", "virus.exe", "/", ".mp4"):
        with pytest.raises(ValueError):
            sanitize_filename(bad, allowed)
    assert folder_name_for("Men's Fashion") == "Men's Fashion"
    assert folder_name_for("Try-On/..\\x") == "Try-On..x"
    with pytest.raises(ValueError):
        folder_name_for("...")
    assert with_suffix("clip.mp4", "abcd1234") == "clip-abcd1234.mp4"


# ---------------------------------------------------------------- API: options, RBAC, validation


def test_options_and_role_gates(api: TestClient, world: dict) -> None:
    admin_opts = api.get(f"{BASE}/options").json()
    assert admin_opts["can_submit_for_others"] is True and admin_opts["facets_enabled"]
    assert [o["slug"] for o in admin_opts["owners"]] == ["hang", "hao"]
    assert [c["slug"] for c in admin_opts["content_types"]] == ["review"]  # archived hidden
    assert (
        admin_opts["limits"]["chunk_bytes"] == CHUNK
        and admin_opts["limits"]["default_concurrency"] == 2
    )

    login(api, world["editor"])
    editor_opts = api.get(f"{BASE}/options").json()
    assert editor_opts["can_submit_for_others"] is False
    assert [o["slug"] for o in editor_opts["owners"]] == ["hang"]

    login(api, world["viewer"])
    assert api.get(f"{BASE}/options").status_code == 403
    create(api, world, expect=403)
    assert api.get(BASE).status_code == 403


def test_create_validations_and_authorization(
    api: TestClient, world: dict, db_session: Session
) -> None:
    login(api, world["editor"])
    evil = api.post(BASE, json={}, headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403 and evil.json()["error"]["code"] == "csrf_rejected"

    # owner the editor is not mapped to; owner id that is not an owner; archived content type
    assert (
        create(api, world, expect=403, production_owner_category_id=world["hao"]["id"])["error"][
            "code"
        ]
        == "upload_forbidden"
    )
    assert (
        create(api, world, expect=422, production_owner_category_id=world["mens"]["id"])["error"][
            "code"
        ]
        == "upload_invalid_taxonomy"
    )
    assert (
        create(api, world, expect=422, content_type_id=world["old"]["id"])["error"]["code"]
        == "upload_invalid_taxonomy"
    )
    assert (
        create(api, world, expect=404, content_type_id=str(uuid.uuid4()))["error"]["code"]
        == "not_found"
    )
    # on-behalf is admin only; file rules
    assert (
        create(api, world, expect=403, submitted_for_user_id=str(world["editor2"].id))["error"][
            "code"
        ]
        == "upload_forbidden"
    )
    assert (
        create(api, world, expect=422, filename="notes.txt")["error"]["code"]
        == "upload_invalid_file"
    )
    assert (
        create(api, world, expect=422, size=9 * 1024**3)["error"]["code"] == "upload_invalid_file"
    )
    assert create(api, world, expect=422, size=0)["error"]["code"] == "validation_error"
    assert (
        create(api, world, expect=404, replaces_asset_id=str(uuid.uuid4()))["error"]["code"]
        == "not_found"
    )
    assert db_session.scalar(select(func.count()).select_from(MediaUpload)) == 0

    # quota: three open sessions per user (fixture setting), the fourth is refused
    ids = [create(api, world)["id"] for _ in range(3)]
    assert create(api, world, expect=409)["error"]["code"] == "upload_quota_exceeded"
    assert api.delete(f"{BASE}/{ids[0]}").status_code == 204
    create(api, world)  # slot freed by the abort

    # editor2 (no mappings) cannot touch editor's sessions; other organization sees nothing
    login(api, world["editor2"])
    assert api.get(f"{BASE}/{ids[1]}").status_code == 403
    assert api.get(f"{BASE}/{ids[1]}/manifest").status_code == 403
    assert send_chunk(api, ids[1], 0, b"x").status_code == 403
    assert api.post(f"{BASE}/{ids[1]}/complete").status_code == 403
    assert api.delete(f"{BASE}/{ids[1]}").status_code == 403
    assert api.get(BASE).json() == []
    other = create_organization(db_session)
    db_session.commit()
    stranger = create_member(db_session, other, role=MembershipRole.ADMIN)
    login(api, stranger, organization_slug=other.slug)
    assert api.get(f"{BASE}/{ids[1]}").status_code == 404
    assert api.delete(f"{BASE}/{ids[1]}").status_code == 404
    assert api.get(BASE).json() == []


def test_admin_uploads_on_behalf_of_an_editor(
    api: TestClient, world: dict, db_session: Session, organization
) -> None:
    upload = create(
        api,
        world,
        production_owner_category_id=world["hao"]["id"],  # admin needs no mapping
        submitted_for_user_id=str(world["editor"].id),
        batch_id=str(uuid.uuid4()),
    )
    assert upload["uploaded_by_user_id"] == str(world["admin"].id)
    assert upload["submitted_for_user_id"] == str(world["editor"].id)
    assert upload["production_owner_name"] == "Hao" and upload["production_owner"]["slug"] == "hao"
    db_session.expire_all()
    event = db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.action == "upload.created")
        .order_by(AuditEvent.created_at.desc())
    )
    assert event.data["submitted_for_user_id"] == str(world["editor"].id)
    assert event.actor_user_id == world["admin"].id
    # the editor sees the session in "mine"? No: attribution is separate from ownership of
    # the session, which stays with the authenticated uploader.
    login(api, world["editor"])
    assert api.get(BASE).json() == []


# ---------------------------------------------------------------- API: chunk protocol


def test_chunk_protocol_resume_replay_and_errors(
    api: TestClient, world: dict, incoming: Path, organization
) -> None:
    login(api, world["editor"])
    data = payload(CHUNK * 2 + 100)
    upload = create(api, world, size=len(data))
    path = staging_path(incoming, organization, upload)
    assert (
        path.exists() and path.stat().st_size == 0 and oct(path.stat().st_mode & 0o777) == "0o640"
    )

    first = send_chunk(api, upload["id"], 0, data[:CHUNK])
    assert first.status_code == 200 and first.json() == {
        "id": upload["id"],
        "status": "uploading",
        "committed_offset": CHUNK,
        "expected_size": len(data),
        "replayed": False,
    }
    # replay of the committed chunk: acknowledged, nothing rewritten
    replay = send_chunk(api, upload["id"], 0, data[:CHUNK])
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert replay.json()["committed_offset"] == CHUNK
    # gaps and stale offsets are refused with the server's offset
    ahead = send_chunk(api, upload["id"], CHUNK * 2, data[CHUNK * 2 :])
    assert (
        ahead.status_code == 409 and ahead.json()["error"]["details"]["committed_offset"] == CHUNK
    )
    stale = send_chunk(api, upload["id"], 0, data[: CHUNK - 1])
    assert stale.status_code == 409
    # oversized chunk, wrong digest, short body: nothing committed, file stays at the offset
    big = send_chunk(api, upload["id"], CHUNK, b"x" * (CHUNK + 1))
    assert big.status_code == 413
    wrong = api.patch(
        f"{BASE}/{upload['id']}/chunk",
        content=data[CHUNK : CHUNK * 2],
        headers={"Upload-Offset": str(CHUNK), "X-Upload-Chunk-Sha256": "0" * 64},
    )
    assert (
        wrong.status_code == 422 and wrong.json()["error"]["code"] == "upload_chunk_digest_mismatch"
    )
    short = api.patch(
        f"{BASE}/{upload['id']}/chunk",
        content=data[CHUNK : CHUNK + 10],
        headers={
            "Upload-Offset": str(CHUNK),
            "X-Upload-Chunk-Sha256": sha(data[CHUNK : CHUNK * 2]),
            "Content-Length": str(CHUNK),
        },
    )
    assert short.status_code in (400, 422)
    state = api.get(f"{BASE}/{upload['id']}").json()
    assert state["committed_offset"] == CHUNK
    assert state["last_chunk_offset"] == 0 and state["last_chunk_sha256"] == sha(data[:CHUNK])
    assert path.stat().st_size == CHUNK
    # The manifest holds exactly the committed chunks (rejected and replayed chunks add nothing).
    manifest = api.get(f"{BASE}/{upload['id']}/manifest").json()
    assert manifest["digests"] == [sha(data[:CHUNK])] and manifest["committed_offset"] == CHUNK
    assert manifest["chunk_bytes"] == CHUNK and manifest["expected_size"] == len(data)
    # too early to complete
    early = api.post(f"{BASE}/{upload['id']}/complete")
    assert early.status_code == 409 and early.json()["error"]["code"] == "upload_incomplete"
    # resume from the server offset after an "interruption"
    offset = api.get(f"{BASE}/{upload['id']}").json()["committed_offset"]
    while offset < len(data):
        piece = data[offset : offset + CHUNK]
        assert send_chunk(api, upload["id"], offset, piece).status_code == 200
        offset += len(piece)
    assert path.read_bytes() == data
    digests = api.get(f"{BASE}/{upload['id']}/manifest").json()["digests"]
    assert digests == [sha(data[i : i + CHUNK]) for i in range(0, len(data), CHUNK)]
    beyond = send_chunk(api, upload["id"], len(data), b"z")
    assert beyond.status_code == 413
    done = api.post(f"{BASE}/{upload['id']}/complete")
    assert done.status_code == 202 and done.json()["status"] == "uploaded"
    again = api.post(f"{BASE}/{upload['id']}/complete")
    assert again.status_code == 202 and again.json()["status"] == "uploaded"  # idempotent
    late = send_chunk(api, upload["id"], 0, data[:CHUNK])
    assert late.status_code == 409 and late.json()["error"]["code"] == "upload_invalid_state"
    assert api.get(BASE, params={"status": "uploaded"}).json()[0]["id"] == upload["id"]


def test_abort_discards_staging_and_later_requests_are_gone(
    api: TestClient, world: dict, incoming: Path, organization, db_session: Session
) -> None:
    login(api, world["editor"])
    upload = create(api, world)
    path = staging_path(incoming, organization, upload)
    assert send_chunk(api, upload["id"], 0, payload(CHUNK)).status_code == 200
    assert api.delete(f"{BASE}/{upload['id']}").status_code == 204
    assert not path.exists()
    assert api.delete(f"{BASE}/{upload['id']}").status_code == 204  # idempotent
    assert send_chunk(api, upload["id"], CHUNK, b"x").status_code == 410
    assert api.post(f"{BASE}/{upload['id']}/complete").status_code == 410
    assert api.get(f"{BASE}/{upload['id']}").json()["status"] == "aborted"
    assert audit_actions(db_session, organization) == ["upload.created", "upload.aborted"]


# ---------------------------------------------------------------- worker: finalization


def test_finalize_places_registers_and_attributes(
    api: TestClient, world: dict, db_session: Session, organization, media_dirs, incoming: Path
) -> None:
    login(api, world["editor"])
    data = payload(CHUNK * 3, seed=11)
    upload = create(api, world, filename="My Final (v2).MP4", client_sha256=sha(data))
    upload_all(api, upload, data)
    path = staging_path(incoming, organization, upload)

    outcome = finalizer(db_session, media_dirs).run_next()
    assert outcome is not None and outcome.status == UploadStatus.COMPLETED
    done = row(upload)
    assert done.status == UploadStatus.COMPLETED and done.media_asset_id == outcome.media_asset_id
    assert done.verified_sha256 == sha(data) and done.final_relative_path == (
        "Hang/Men's Fashion/Review/My Final (v2).mp4"
    )
    placed = media_dirs.finals / done.final_relative_path
    assert placed.read_bytes() == data and not path.exists()
    assert oct(placed.parent.stat().st_mode & 0o777) == "0o750"

    asset = db_session.get(MediaAsset, done.media_asset_id)
    assert asset.ingest_source == IngestSource.UPLOAD
    assert asset.uploaded_by_user_id == world["editor"].id and asset.revision_of_asset_id is None
    assert asset.handoff_status == HandoffStatus.PENDING_REVIEW
    assert asset.thumbnail_status == ThumbnailStatus.PENDING
    assert asset.production_period == datetime.now(UTC).strftime("%Y-%m")
    assert categories_of(asset) == {
        "mens-fashion": ("path", True),
        "hang": ("path", False),
        "review": ("path", False),
    }
    variant = asset.main_variant()
    assert variant.content_hash == sha(data) and variant.kind == MediaVariantKind.FINAL
    assert variant.relative_path == done.final_relative_path

    # API view for the uploader: asset id, pending review; the list shows it as completed.
    shown = api.get(f"{BASE}/{upload['id']}").json()
    assert shown["status"] == "completed" and shown["media_asset_id"] == str(asset.id)
    assert shown["review"]["handoff_status"] == "pending_review"
    assert shown["asset_title"] == "My Final (v2)" and shown["thumbnail_url"] is None
    asset.thumbnail_status = ThumbnailStatus.READY
    db_session.commit()
    assert api.get(f"{BASE}/{upload['id']}").json()["thumbnail_url"] == (
        f"/api/v1/media/assets/{asset.id}/thumbnail"
    )
    assert [u["id"] for u in api.get(BASE, params={"status": "completed"}).json()] == [upload["id"]]
    assert audit_actions(db_session, organization) == [
        "upload.created",
        "upload.uploaded",
        "upload.completed",
    ]
    # A later incremental scan sees the file as known and unchanged: no second asset.
    scan_root(db_session, organization.id, media_dirs, "finals")
    stats = latest_scan_stats(db_session, organization)
    assert stats["added"] == 0 and stats["unchanged"] == 1
    assert db_session.scalar(select(func.count()).select_from(MediaAsset)) == 1
    # Nothing left to do for the worker.
    assert finalizer(db_session, media_dirs).run_next() is None


def test_finalize_rejects_hash_mismatch_and_invalid_media(
    api: TestClient, world: dict, db_session: Session, organization, media_dirs, incoming: Path
) -> None:
    login(api, world["editor"])
    data = payload(CHUNK, seed=2)
    wrong_hash = create(api, world, filename="a.mp4", size=len(data), client_sha256="f" * 64)
    upload_all(api, wrong_hash, data)
    bad = create(api, world, filename="b.mp4", size=len(data))
    upload_all(api, bad, b"BAD!" + data[4:])
    for upload, code in ((wrong_hash, "hash_mismatch"), (bad, "invalid_media")):
        outcome = finalizer(db_session, media_dirs).run_next()
        assert outcome.status == UploadStatus.FAILED and outcome.code == code
        assert row(upload).failure_code == code
        assert not staging_path(incoming, organization, upload).exists()
    assert db_session.scalar(select(func.count()).select_from(MediaAsset)) == 0
    assert not any(p.is_file() for p in media_dirs.finals.rglob("*") if ".affilio" not in str(p))
    assert "upload.failed" in audit_actions(db_session, organization)


def test_duplicate_content_is_rejected_and_concurrent_duplicates_converge(
    api: TestClient, world: dict, db_session: Session, organization, media_dirs, incoming: Path
) -> None:
    login(api, world["editor"])
    data = payload(CHUNK * 2, seed=3)
    first = create(api, world, filename="first.mp4", size=len(data))
    upload_all(api, first, data)
    assert finalizer(db_session, media_dirs).run_next().status == UploadStatus.COMPLETED
    asset_id = row(first).media_asset_id

    # Same bytes, different name → duplicate, nothing placed, generic reference only.
    second = create(api, world, filename="renamed copy.mp4", size=len(data))
    upload_all(api, second, data)
    outcome = finalizer(db_session, media_dirs).run_next()
    assert outcome.status == UploadStatus.DUPLICATE and outcome.media_asset_id == asset_id
    shown = api.get(f"{BASE}/{second['id']}").json()
    # Duplicate privacy: the editor learns only that the content exists, never which asset.
    assert shown["status"] == "duplicate" and shown["duplicate_of_asset_id"] is None
    assert shown["media_asset_id"] is None and "first" not in str(shown)
    assert shown["thumbnail_url"] is None and shown["asset_title"] is None
    login(api, world["admin"])
    assert api.get(f"{BASE}/{second['id']}").json()["duplicate_of_asset_id"] == str(asset_id)
    login(api, world["editor"])
    assert not staging_path(incoming, organization, second).exists()
    assert not (media_dirs.finals / "Hang/Men's Fashion/Review/renamed copy.mp4").exists()

    # Same name, different bytes → not a duplicate; collision-safe suffix, never overwrite.
    other = payload(CHUNK * 2, seed=4)
    third = create(api, world, filename="first.mp4", size=len(other))
    upload_all(api, third, other)
    assert finalizer(db_session, media_dirs).run_next().status == UploadStatus.COMPLETED
    assert (
        row(third).final_relative_path
        == f"Hang/Men's Fashion/Review/first-{third['id'].replace('-', '')[:8]}.mp4"
    )
    assert (media_dirs.finals / "Hang/Men's Fashion/Review/first.mp4").read_bytes() == data

    # Concurrent identical uploads: another session already holds the finalizing intent for
    # the same hash, so this one hits the unique index and becomes a duplicate.
    fresh = payload(CHUNK * 2, seed=5)
    a = create(api, world, filename="a.mp4", size=len(fresh))
    b = create(api, world, filename="b.mp4", size=len(fresh))
    upload_all(api, a, fresh)
    upload_all(api, b, fresh)
    rb = row(b)
    rb.status, rb.verified_sha256, rb.final_relative_path = (
        UploadStatus.FINALIZING,
        sha(fresh),
        "Hang/Men's Fashion/Review/b.mp4",
    )
    rb.lease_until = datetime.now(UTC) + timedelta(hours=1)
    db_session.commit()
    outcome = finalizer(db_session, media_dirs).run_next()
    assert outcome.upload_id == uuid.UUID(a["id"]) and outcome.status == UploadStatus.DUPLICATE
    assert row(a).failure_detail.startswith("identical content is being finalized")
    assert db_session.scalar(select(func.count()).select_from(MediaAsset)) == 2


def test_revision_links_to_the_rejected_original(
    api: TestClient, world: dict, db_session: Session, organization, media_dirs
) -> None:
    login(api, world["editor"])
    original_data = payload(CHUNK, seed=6)
    original = create(api, world, filename="take1.mp4", size=len(original_data))
    upload_all(api, original, original_data)
    assert finalizer(db_session, media_dirs).run_next().status == UploadStatus.COMPLETED
    original_asset_id = row(original).media_asset_id

    # Not rejected yet → cannot be replaced; editor2 cannot replace someone else's final.
    assert (
        create(api, world, expect=422, replaces_asset_id=str(original_asset_id))["error"]["code"]
        == "upload_invalid_revision"
    )
    asset = db_session.get(MediaAsset, original_asset_id)
    asset.handoff_status = HandoffStatus.REJECTED
    asset.handoff_note = "wrong colour grade"
    db_session.commit()
    login(api, world["editor2"])
    api.post("/api/v1/auth/logout")
    login(api, world["admin"])
    api.post(
        "/api/v1/media/owner-mappings",
        json={
            "user_id": str(world["editor2"].id),
            "production_owner_category_id": world["hang"]["id"],
        },
    )
    login(api, world["editor2"])
    assert (
        create(api, world, expect=403, replaces_asset_id=str(original_asset_id))["error"]["code"]
        == "upload_forbidden"
    )

    login(api, world["editor"])
    corrected_data = payload(CHUNK, seed=7)
    corrected = create(
        api,
        world,
        filename="take2.mp4",
        size=len(corrected_data),
        replaces_asset_id=str(original_asset_id),
    )
    upload_all(api, corrected, corrected_data)
    assert finalizer(db_session, media_dirs).run_next().status == UploadStatus.COMPLETED
    new_asset = db_session.get(MediaAsset, row(corrected).media_asset_id)
    assert new_asset.revision_of_asset_id == original_asset_id
    assert new_asset.handoff_status == HandoffStatus.PENDING_REVIEW
    db_session.refresh(asset)
    assert (
        asset.handoff_status == HandoffStatus.REJECTED
        and asset.handoff_note == "wrong colour grade"
    )
    assert (media_dirs.finals / "Hang/Men's Fashion/Review/take1.mp4").read_bytes() == original_data
    shown = api.get(f"{BASE}/{original['id']}").json()
    assert (
        shown["review"]["handoff_status"] == "rejected"
        and shown["review"]["handoff_note"] == "wrong colour grade"
    )


# ---------------------------------------------------------------- worker: crash recovery


def _uploaded(
    api: TestClient, world: dict, seed: int, name: str = "clip.mp4"
) -> tuple[dict, bytes]:
    data = payload(CHUNK * 2, seed=seed)
    upload = create(api, world, filename=name, size=len(data))
    upload_all(api, upload, data)
    return upload, data


def _rerun(db_session: Session, media_dirs, upload: dict):
    r = row(upload)
    r.lease_until = datetime.now(UTC) - timedelta(seconds=1)  # the previous worker died
    db_session.commit()
    return finalizer(db_session, media_dirs).run_next()


@pytest.mark.parametrize(
    "crash_point",
    ["before_place", "after_link", "after_place_before_register", "after_commit_before_unlink"],
)
def test_crash_recovery_resumes_from_the_recorded_state(
    api: TestClient,
    world: dict,
    db_session: Session,
    organization,
    media_dirs,
    incoming: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_point: str,
) -> None:
    login(api, world["editor"])
    upload, data = _uploaded(api, world, seed=20)
    path = staging_path(incoming, organization, upload)
    dest = media_dirs.finals / "Hang/Men's Fashion/Review/clip.mp4"

    real_link = FinalsPlacer.link_into
    real_register = Finalizer._register
    real_remove = StagingStore.remove
    if crash_point == "before_place":
        monkeypatch.setattr(
            Finalizer, "_place", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        )
    elif crash_point == "after_link":

        def link_then_die(staging, dir_fd, filename):
            real_link(staging, dir_fd, filename)
            raise RuntimeError("boom after link")

        monkeypatch.setattr(FinalsPlacer, "link_into", staticmethod(link_then_die))
    elif crash_point == "after_place_before_register":
        monkeypatch.setattr(
            Finalizer,
            "_register",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom before register")),
        )
    else:

        def remove_dies(self, org, name):  # the unlink itself fails after the commit
            raise OSError("boom before unlink")

        monkeypatch.setattr(StagingStore, "remove", remove_dies)

    first = finalizer(db_session, media_dirs)
    if crash_point == "after_commit_before_unlink":
        outcome = first.run_next()  # the exception is swallowed as a cleanup warning
        assert outcome.status == UploadStatus.COMPLETED and path.exists()
    else:
        with pytest.raises(RuntimeError):
            first.run_next()
        db_session.rollback()
        r = row(upload)
        assert (
            r.status == UploadStatus.FINALIZING
            and r.final_relative_path == "Hang/Men's Fashion/Review/clip.mp4"
        )
        assert r.verified_sha256 == sha(data) and r.media_asset_id is None
        assert dest.exists() == (crash_point != "before_place")
        assert path.exists()
        assert db_session.scalar(select(func.count()).select_from(MediaAsset)) == 0
    monkeypatch.setattr(FinalsPlacer, "link_into", staticmethod(real_link))
    monkeypatch.setattr(Finalizer, "_register", real_register)
    monkeypatch.setattr(StagingStore, "remove", real_remove)
    monkeypatch.undo()

    if crash_point == "after_commit_before_unlink":
        # Maintenance removes the leftover staging copy of a completed session.
        counts = expire_and_clean(
            db_session, media_dirs.registry, build_settings(media_dirs, media_finals_writable=True)
        )
        assert counts["staging_removed"] == 1 and not path.exists()
    else:
        outcome = _rerun(db_session, media_dirs, upload)
        assert outcome.status == UploadStatus.COMPLETED, outcome
        assert row(upload).finalize_attempts == 2
    done = row(upload)
    assert done.status == UploadStatus.COMPLETED and dest.read_bytes() == data and not path.exists()
    assert db_session.scalar(select(func.count()).select_from(MediaAsset)) == 1
    asset = db_session.get(MediaAsset, done.media_asset_id)
    assert (
        asset.uploaded_by_user_id == world["editor"].id
        and asset.ingest_source == IngestSource.UPLOAD
    )


def test_recovery_adopts_a_scan_registered_file_but_never_a_different_one(
    api: TestClient,
    world: dict,
    db_session: Session,
    organization,
    media_dirs,
    incoming: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    login(api, world["editor"])
    upload, _ = _uploaded(api, world, seed=21, name="adopt.mp4")
    monkeypatch.setattr(
        Finalizer, "_register", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    with pytest.raises(RuntimeError):
        finalizer(db_session, media_dirs).run_next()
    db_session.rollback()
    monkeypatch.undo()
    # A root scan runs before the worker recovers: the placed file is indexed as a scan asset.
    dest = media_dirs.finals / "Hang/Men's Fashion/Review/adopt.mp4"
    old = (datetime.now(UTC) - timedelta(hours=1)).timestamp()
    os.utime(dest, (old, old))
    scan_root(db_session, organization.id, media_dirs, "finals")
    scanned = db_session.scalar(select(MediaAsset))
    assert scanned.ingest_source == IngestSource.SCAN and scanned.uploaded_by_user_id is None
    outcome = _rerun(db_session, media_dirs, upload)
    assert outcome.status == UploadStatus.COMPLETED and outcome.media_asset_id == scanned.id
    db_session.refresh(scanned)
    assert (
        scanned.ingest_source == IngestSource.UPLOAD
        and scanned.uploaded_by_user_id == world["editor"].id
    )
    assert db_session.scalar(select(func.count()).select_from(MediaAsset)) == 1
    event = db_session.scalar(select(AuditEvent).where(AuditEvent.action == "upload.completed"))
    assert event.data["adopted_existing_asset"] is True

    # A different file at the intended path is a conflict: never adopted, never replaced.
    other, _ = _uploaded(api, world, seed=22, name="taken.mp4")
    monkeypatch.setattr(
        Finalizer, "_register", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    with pytest.raises(RuntimeError):
        finalizer(db_session, media_dirs).run_next()
    db_session.rollback()
    monkeypatch.undo()
    taken = media_dirs.finals / "Hang/Men's Fashion/Review/taken.mp4"
    taken.write_bytes(b"someone else's file")
    outcome = _rerun(db_session, media_dirs, other)
    assert outcome.status == UploadStatus.FAILED and outcome.code == "destination_conflict"
    assert taken.read_bytes() == b"someone else's file"
    assert staging_path(incoming, organization, other).exists()  # kept for inspection


def test_missing_staging_symlinks_and_exhausted_attempts(
    api: TestClient, world: dict, db_session: Session, organization, media_dirs, incoming: Path
) -> None:
    login(api, world["editor"])
    gone, _ = _uploaded(api, world, seed=30, name="gone.mp4")
    staging_path(incoming, organization, gone).unlink()
    outcome = finalizer(db_session, media_dirs).run_next()
    assert outcome.status == UploadStatus.FAILED and outcome.code == "staging_missing"

    linked, _ = _uploaded(api, world, seed=31, name="linked.mp4")
    p = staging_path(incoming, organization, linked)
    p.unlink()
    p.symlink_to(media_dirs.finals)  # a symlink where the staged file should be
    outcome = finalizer(db_session, media_dirs).run_next()
    assert outcome.status == UploadStatus.FAILED and outcome.code == "staging_invalid"
    assert p.is_symlink()  # refused, not followed, not removed

    # Attempts are capped: the third claim gives up instead of looping forever.
    tired, _ = _uploaded(api, world, seed=32, name="tired.mp4")
    r = row(tired)
    r.finalize_attempts = 3
    db_session.commit()
    assert finalizer(db_session, media_dirs).run_next() is None
    assert row(tired).status == UploadStatus.FAILED
    assert row(tired).failure_code == "finalize_attempts_exhausted"


def test_destination_symlink_is_refused(
    api: TestClient, world: dict, db_session: Session, organization, media_dirs
) -> None:
    login(api, world["editor"])
    (media_dirs.finals / "Hang").mkdir()
    (media_dirs.finals / "Hang/Men's Fashion").symlink_to(media_dirs.originals)
    _uploaded(api, world, seed=40, name="sym.mp4")
    outcome = finalizer(db_session, media_dirs).run_next()
    assert outcome.status == UploadStatus.FAILED and outcome.code == "destination_unavailable"
    assert list(media_dirs.originals.iterdir()) == []


def test_expiry_and_orphan_cleanup(
    api: TestClient, world: dict, db_session: Session, organization, media_dirs, incoming: Path
) -> None:
    login(api, world["editor"])
    idle = create(api, world, filename="idle.mp4")
    live = create(api, world, filename="live.mp4")
    idle_path = staging_path(incoming, organization, idle)
    live_path = staging_path(incoming, organization, live)
    r = row(idle)
    r.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()
    orphan_old = incoming / str(organization.id) / f"{'a' * 32}.part"
    orphan_old.write_bytes(b"x")
    stale = (datetime.now(UTC) - timedelta(hours=72)).timestamp()
    os.utime(orphan_old, (stale, stale))
    orphan_new = incoming / str(organization.id) / f"{'b' * 32}.part"
    orphan_new.write_bytes(b"x")
    stranger = incoming / str(organization.id) / "notes.txt"
    stranger.write_bytes(b"not ours")

    counts = expire_and_clean(
        db_session, media_dirs.registry, build_settings(media_dirs, media_finals_writable=True)
    )
    assert counts == {"expired": 1, "staging_removed": 1, "orphans_removed": 1}
    assert row(idle).status == UploadStatus.EXPIRED and not idle_path.exists()
    assert row(live).status == UploadStatus.CREATED and live_path.exists()
    assert not orphan_old.exists() and orphan_new.exists() and stranger.exists()
    assert send_chunk(api, idle["id"], 0, b"x").status_code == 410
    assert "upload.expired" in audit_actions(db_session, organization)
    # Nothing happens where finals are not writable (every non-worker process).
    assert expire_and_clean(db_session, media_dirs.registry, build_settings(media_dirs)) == {
        "expired": 0,
        "staging_removed": 0,
        "orphans_removed": 0,
    }


def test_finalizer_is_inert_without_a_writable_finals_root(
    api: TestClient, world: dict, db_session: Session, media_dirs
) -> None:
    login(api, world["editor"])
    _uploaded(api, world, seed=50)
    inert = Finalizer.build(
        db_session, media_dirs.registry, build_settings(media_dirs), prober=ContentProber()
    )
    assert inert.run_next() is None
    assert db_session.scalar(select(MediaUpload.status)) == UploadStatus.UPLOADED


def test_legacy_assets_are_untouched_by_uploads(
    api: TestClient, world: dict, db_session: Session, organization, media_dirs
) -> None:
    legacy = media_dirs.finals / "Hang/T9/legacy.mp4"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(payload(6_000, seed=60))
    old = (datetime.now(UTC) - timedelta(hours=1)).timestamp()
    os.utime(legacy, (old, old))
    scan_root(db_session, organization.id, media_dirs, "finals")
    before = db_session.scalar(select(MediaAsset))
    assert before.ingest_source == IngestSource.SCAN and before.uploaded_by_user_id is None
    snapshot = (legacy.stat().st_size, legacy.stat().st_mtime_ns, before.updated_at)

    login(api, world["editor"])
    _uploaded(api, world, seed=61, name="new.mp4")
    assert finalizer(db_session, media_dirs).run_next().status == UploadStatus.COMPLETED
    db_session.expire_all()
    after = db_session.get(MediaAsset, before.id)
    assert (legacy.stat().st_size, legacy.stat().st_mtime_ns, after.updated_at) == snapshot
    assert after.ingest_source == IngestSource.SCAN and categories_of(after) == {
        "uncategorized": ("folder", True),
        "hang": ("path", False),
    }
    assert db_session.scalar(select(func.count()).select_from(MediaFileVariant)) == 2
