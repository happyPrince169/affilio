"""Editor uploads (ADR 0012, amendment 2: dedicated upload root): session API (RBAC, CSRF,
isolation, taxonomy and owner authorization, chunk protocol, resume, abort), worker
finalization into `<uploads>/ready` by no-overwrite rename (verification, duplicates, UUID
names, registration with attribution and database-only classification, revisions), crash
recovery at every boundary, expiry cleanup, filesystem safety, the unified resolver (preview
stream, thumbnails, publishing checks), legacy read-only guarantees and metadata-only
reclassification. Needs TEST_DATABASE_URL."""

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
from affilio.core.config import Settings, get_settings
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
from affilio.modules.media.errors import InvalidMediaPathError, MediaRootNotConfiguredError
from affilio.modules.media.models import MediaAsset, MediaFileVariant, MediaRoot, MediaUpload
from affilio.modules.media.probe import ProbeError
from affilio.modules.media.roots import MediaRootRegistry
from affilio.modules.media.thumbnails import ThumbnailGenerator
from affilio.modules.media.upload_finalize import Finalizer, expire_and_clean
from affilio.modules.media.upload_storage import (
    ReadyStore,
    StagingStore,
    StorageError,
    physical_name,
    rename_noreplace,
    sanitize_filename,
)
from affilio.modules.publishing.media_checks import check_reel_media
from tests.auth_helpers import create_member, login, sign_in
from tests.media_helpers import (
    FakeProber,
    build_settings,
    can_test_permissions,
    create_organization,
    make_video,
    requires_ffmpeg,
)
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
    """The staging directory under the uploads root (created by the API on first use; made
    here so tests can look at staged bytes before any request)."""
    path = media_dirs.uploads / upload_storage.STAGING_DIRNAME
    path.mkdir(mode=0o750)
    return path


@pytest.fixture
def api(
    monkeypatch: pytest.MonkeyPatch, media_dirs, incoming, organization, schema, db_session
) -> Iterator[TestClient]:
    for key, value in media_dirs.env().items():
        monkeypatch.setenv(key, value)
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


def writable_settings(media_dirs, **overrides) -> Settings:
    return build_settings(
        media_dirs, media_uploads_writable=True, upload_finalize_lease_seconds=600, **overrides
    )


def finalizer(db_session: Session, media_dirs, **overrides) -> Finalizer:
    return Finalizer.build(
        db_session,
        media_dirs.registry,
        writable_settings(media_dirs, **overrides),
        prober=ContentProber(),
        worker_id="test",
    )


def staging_path(incoming: Path, organization, upload: dict) -> Path:
    return incoming / str(organization.id) / f"{row(upload).staging_name}.part"


def ready_path(media_dirs, upload: dict) -> Path:
    rel = row(upload).final_relative_path
    assert rel is not None
    return media_dirs.uploads / rel


def expected_relative_path(organization, upload: dict, when: datetime, ext: str = ".mp4") -> str:
    return f"ready/{organization.id}/{when:%Y}/{when:%m}/{upload['id']}{ext}"


def finals_snapshot(root: Path) -> list[tuple[str, int, int]]:
    """(path, size, mtime_ns) of every entry under the legacy finals tree."""
    return sorted(
        (str(p.relative_to(root)), p.stat().st_size if p.is_file() else -1, p.stat().st_mtime_ns)
        for p in root.rglob("*")
    )


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


def test_filename_sanitising_and_physical_names() -> None:
    allowed = {".mp4", ".mov"}
    assert sanitize_filename("../../etc/passwd.mp4", allowed) == "etcpasswd.mp4"
    assert sanitize_filename("  .hidden\x00name .MP4", allowed) == "hiddenname.mp4"
    assert sanitize_filename("a" * 300 + ".mov", allowed).endswith(".mov")
    assert len(sanitize_filename("a" * 300 + ".mov", allowed)) <= 120
    for bad in ("", "...", "noext", "virus.exe", "/", ".mp4"):
        with pytest.raises(ValueError):
            sanitize_filename(bad, allowed)
    # The physical name is the upload id plus the sanitized extension: nothing of the client's
    # name reaches the filesystem, and two uploads can never collide on a name.
    upload_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
    assert physical_name(upload_id, "My Final (v2).MP4") == f"{upload_id}.mp4"
    assert physical_name(upload_id, "clip.MoV") == f"{upload_id}.mov"
    with pytest.raises(StorageError):
        physical_name(upload_id, "weird.exe/../x")
    when = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    org = uuid.uuid4()
    assert ReadyStore.directory_parts(org, when) == [str(org), "2026", "11"]
    assert ReadyStore.relative_path([str(org), "2026", "11"], "f.mp4") == (
        f"ready/{org}/2026/11/f.mp4"
    )


@pytest.mark.parametrize("use_renameat2", [True, False])
def test_rename_noreplace_never_replaces_an_existing_name(
    tmp_path: Path, use_renameat2: bool
) -> None:
    src = tmp_path / "src.part"
    src.write_bytes(b"new bytes")
    dest_dir = tmp_path / "ready"
    dest_dir.mkdir()
    fd = os.open(dest_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        strategy = rename_noreplace(src, fd, "a.mp4", use_renameat2=use_renameat2)
        assert strategy in {"renameat2", "reserve"}
        if not use_renameat2:
            assert strategy == "reserve"
        assert (dest_dir / "a.mp4").read_bytes() == b"new bytes" and not src.exists()
        # An occupied name is refused and left untouched, whatever the strategy.
        (dest_dir / "taken.mp4").write_bytes(b"someone else")
        src.write_bytes(b"other")
        with pytest.raises(FileExistsError):
            rename_noreplace(src, fd, "taken.mp4", use_renameat2=use_renameat2)
        assert (dest_dir / "taken.mp4").read_bytes() == b"someone else" and src.exists()
        # A missing source fails without leaving a reservation behind.
        with pytest.raises(OSError):
            rename_noreplace(tmp_path / "missing.part", fd, "b.mp4", use_renameat2=use_renameat2)
        assert not (dest_dir / "b.mp4").exists()
    finally:
        os.close(fd)


def test_rename_noreplace_falls_back_when_the_syscall_is_filtered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A seccomp filter answers EPERM, an older kernel ENOSYS/EINVAL: the reservation
    strategy takes over transparently and still never replaces an existing name."""
    import errno as errno_module

    for code in (errno_module.EPERM, errno_module.ENOSYS, errno_module.EINVAL):
        monkeypatch.setattr(upload_storage, "_try_renameat2", lambda *a, _c=code: _c)
        src = tmp_path / f"src-{code}.part"
        src.write_bytes(b"bytes")
        dest_dir = tmp_path / f"ready-{code}"
        dest_dir.mkdir()
        fd = os.open(dest_dir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            assert rename_noreplace(src, fd, "a.mp4") == "reserve"
            assert (dest_dir / "a.mp4").read_bytes() == b"bytes" and not src.exists()
            src.write_bytes(b"other")
            with pytest.raises(FileExistsError):
                rename_noreplace(src, fd, "a.mp4")
            assert (dest_dir / "a.mp4").read_bytes() == b"bytes"
        finally:
            os.close(fd)
    # Any other errno is a real error, not a reason to fall back.
    monkeypatch.setattr(upload_storage, "_try_renameat2", lambda *a: errno_module.EIO)
    src = tmp_path / "io.part"
    src.write_bytes(b"x")
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(OSError) as excinfo:
            rename_noreplace(src, fd, "io.mp4")
        assert excinfo.value.errno == errno_module.EIO and not (tmp_path / "io.mp4").exists()
    finally:
        os.close(fd)


def test_staging_store_stays_unavailable_on_a_read_only_root(tmp_path: Path) -> None:
    """The API's uploads bind may be read-only (nested writable staging bind missing):
    `ensure()` must not raise, and the store reports itself unavailable (routes → 503)."""
    root = tmp_path / "uploads"
    root.mkdir()
    store = StagingStore.for_root(root)
    assert store is not None
    if can_test_permissions():
        root.chmod(0o555)
        try:
            store.ensure()
            assert not store.available()
        finally:
            root.chmod(0o750)
    store.ensure()
    assert store.available() and (root / "staging").is_dir()
    assert StagingStore.for_root(None) is None


def test_ready_store_walks_without_following_symlinks(tmp_path: Path) -> None:
    store = ReadyStore.for_root(tmp_path)
    (tmp_path / "ready").mkdir()
    org = str(uuid.uuid4())
    fd = store.open_directory([org, "2026", "11"], create=True)
    os.close(fd)
    assert (tmp_path / "ready" / org / "2026" / "11").is_dir()
    with pytest.raises(StorageError):
        os.close(store.open_directory([org, "2027", "01"], create=False))
    with pytest.raises(StorageError):
        os.close(store.open_directory(["../etc"], create=True))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "ready" / org / "2026" / "12").symlink_to(elsewhere)
    with pytest.raises(StorageError):
        os.close(store.open_directory([org, "2026", "12"], create=True))
    assert list(elsewhere.iterdir()) == []


def test_unified_resolver_serves_both_storage_roots(media_dirs) -> None:
    registry = media_dirs.registry
    legacy = MediaFileVariant(relative_path="Hang/T9/old.mp4", root=MediaRoot(key="finals"))
    uploaded = MediaFileVariant(
        relative_path="ready/org/2026/11/new.mp4", root=MediaRoot(key="uploads")
    )
    assert registry.resolve_variant(legacy) == media_dirs.finals / "Hang/T9/old.mp4"
    assert registry.resolve_variant(uploaded) == media_dirs.uploads / "ready/org/2026/11/new.mp4"
    with pytest.raises(InvalidMediaPathError):
        registry.resolve_variant(
            MediaFileVariant(relative_path="../finals/x.mp4", root=MediaRoot(key="uploads"))
        )
    partial = MediaRootRegistry.from_paths(finals=media_dirs.finals)
    with pytest.raises(MediaRootNotConfiguredError):
        partial.resolve_variant(uploaded)
    assert registry.get("uploads").is_writable_kind and not registry.get("finals").is_writable_kind


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
    legacy_before = finals_snapshot(media_dirs.finals)

    now = datetime.now(UTC)
    outcome = finalizer(db_session, media_dirs).run_next(now=now)
    assert outcome is not None and outcome.status == UploadStatus.COMPLETED
    done = row(upload)
    assert done.status == UploadStatus.COMPLETED and done.media_asset_id == outcome.media_asset_id
    assert done.verified_sha256 == sha(data)
    assert done.final_relative_path == expected_relative_path(organization, upload, now)
    placed = ready_path(media_dirs, upload)
    assert placed.read_bytes() == data and not path.exists()
    assert oct(placed.parent.stat().st_mode & 0o777) == "0o750"
    assert placed.stat().st_nlink == 1
    # The legacy tree is untouched: no entry added, nothing modified.
    assert finals_snapshot(media_dirs.finals) == legacy_before == []

    asset = db_session.get(MediaAsset, done.media_asset_id)
    assert asset.title == "My Final (v2)"
    assert asset.ingest_source == IngestSource.UPLOAD
    assert asset.uploaded_by_user_id == world["editor"].id and asset.revision_of_asset_id is None
    assert asset.handoff_status == HandoffStatus.PENDING_REVIEW
    assert asset.thumbnail_status == ThumbnailStatus.PENDING
    assert asset.production_period == now.strftime("%Y-%m")
    # Classification comes from the upload session, stored as `upload` rows; nothing is read
    # from folder names (the physical path is a UUID under a date shard).
    assert categories_of(asset) == {
        "mens-fashion": ("upload", True),
        "hang": ("upload", False),
        "review": ("upload", False),
    }
    variant = asset.main_variant()
    assert variant.content_hash == sha(data) and variant.kind == MediaVariantKind.FINAL
    assert variant.relative_path == done.final_relative_path
    assert variant.root.key == "uploads" and variant.file_name == f"{upload['id']}.mp4"

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
    # Library view carries the provenance and the preview link.
    summary = api.get(f"/api/v1/media/assets/{asset.id}").json()
    assert summary["ingest_source"] == "upload" and summary["root_key"] == "uploads"
    assert summary["uploaded_by_user_id"] == str(world["editor"].id)
    assert summary["preview_url"] == f"/api/v1/media/assets/{asset.id}/stream"
    assert summary["relative_path"] == done.final_relative_path
    audit = db_session.scalar(select(AuditEvent).where(AuditEvent.action == "upload.completed"))
    assert audit.data["root_key"] == "uploads" and audit.data["verified_sha256"] == sha(data)
    assert audit_actions(db_session, organization) == [
        "upload.created",
        "upload.uploaded",
        "upload.completed",
    ]
    # A legacy finals scan afterwards finds nothing new: the uploads root is never scanned and
    # the asset count stays at one.
    scan_root(db_session, organization.id, media_dirs, "finals")
    stats = latest_scan_stats(db_session, organization)
    assert stats["added"] == 0
    assert db_session.scalar(select(func.count()).select_from(MediaAsset)) == 1
    # Nothing left to do for the worker.
    assert finalizer(db_session, media_dirs).run_next() is None


def test_finalize_rejects_hash_mismatch_and_invalid_media(
    api: TestClient, world: dict, db_session: Session, organization, media_dirs, incoming: Path
) -> None:
    login(api, world["editor"])
    data = payload(CHUNK, seed=12)
    wrong = create(api, world, filename="wrong.mp4", size=len(data), client_sha256="0" * 64)
    upload_all(api, wrong, data)
    outcome = finalizer(db_session, media_dirs).run_next()
    assert outcome.status == UploadStatus.FAILED and outcome.code == "hash_mismatch"
    assert not staging_path(incoming, organization, wrong).exists()

    bad = b"BAD!" + payload(CHUNK - 4, seed=13)
    broken = create(api, world, filename="broken.mp4", size=len(bad))
    upload_all(api, broken, bad)
    outcome = finalizer(db_session, media_dirs).run_next()
    assert outcome.status == UploadStatus.FAILED and outcome.code == "invalid_media"
    shown = api.get(f"{BASE}/{broken['id']}").json()
    assert shown["status"] == "failed" and shown["failure_code"] == "invalid_media"
    assert db_session.scalar(select(func.count()).select_from(MediaAsset)) == 0
    assert not (media_dirs.uploads / "ready").exists() or not any(
        (media_dirs.uploads / "ready").rglob("*.mp4")
    )


def test_duplicate_content_is_rejected_and_concurrent_duplicates_converge(
    api: TestClient, world: dict, db_session: Session, organization, media_dirs, incoming: Path
) -> None:
    login(api, world["editor"])
    data = payload(CHUNK * 2, seed=3)
    first = create(api, world, filename="first.mp4", size=len(data))
    upload_all(api, first, data)
    assert finalizer(db_session, media_dirs).run_next().status == UploadStatus.COMPLETED
    asset_id = row(first).media_asset_id
    first_path = ready_path(media_dirs, first)

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
    assert row(second).final_relative_path is None
    assert sorted(p.name for p in first_path.parent.iterdir()) == [first_path.name]

    # Same name, different bytes → not a duplicate; a second UUID file, never an overwrite.
    other = payload(CHUNK * 2, seed=4)
    third = create(api, world, filename="first.mp4", size=len(other))
    upload_all(api, third, other)
    assert finalizer(db_session, media_dirs).run_next().status == UploadStatus.COMPLETED
    third_path = ready_path(media_dirs, third)
    assert third_path.name == f"{third['id']}.mp4" and third_path != first_path
    assert third_path.read_bytes() == other and first_path.read_bytes() == data
    titles = sorted(db_session.scalars(select(MediaAsset.title)))
    assert titles == ["first", "first"]

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
        expected_relative_path(organization, b, datetime.now(UTC)),
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
    assert ready_path(media_dirs, original).read_bytes() == original_data
    shown = api.get(f"{BASE}/{original['id']}").json()
    assert (
        shown["review"]["handoff_status"] == "rejected"
        and shown["review"]["handoff_note"] == "wrong colour grade"
    )
    detail = api.get(f"/api/v1/media/assets/{new_asset.id}").json()
    assert detail["revision_of_asset_id"] == str(original_asset_id)


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
    ["before_place", "after_rename", "reservation_left", "after_place_before_register"],
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
    now = datetime.now(UTC)
    dest = media_dirs.uploads / expected_relative_path(organization, upload, now)

    real_place = ReadyStore.place
    if crash_point == "before_place":
        monkeypatch.setattr(
            Finalizer, "_place", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        )
    elif crash_point == "after_rename":

        def rename_then_die(staging, dir_fd, filename):
            real_place(staging, dir_fd, filename)
            raise RuntimeError("boom after rename")

        monkeypatch.setattr(ReadyStore, "place", staticmethod(rename_then_die))
    elif crash_point == "reservation_left":

        def reserve_then_die(staging, dir_fd, filename):
            # The fallback strategy died between its exclusive reservation and the rename.
            os.close(os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640, dir_fd=dir_fd))
            raise RuntimeError("boom after reservation")

        monkeypatch.setattr(ReadyStore, "place", staticmethod(reserve_then_die))
    else:
        monkeypatch.setattr(
            Finalizer,
            "_register",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom before register")),
        )

    with pytest.raises(RuntimeError):
        finalizer(db_session, media_dirs).run_next(now=now)
    db_session.rollback()
    r = row(upload)
    assert r.status == UploadStatus.FINALIZING
    assert r.final_relative_path == expected_relative_path(organization, upload, now)
    assert r.verified_sha256 == sha(data) and r.media_asset_id is None
    assert dest.exists() == (crash_point != "before_place")
    assert path.exists() == (crash_point in {"before_place", "reservation_left"})
    if crash_point == "reservation_left":
        assert dest.stat().st_size == 0
    assert db_session.scalar(select(func.count()).select_from(MediaAsset)) == 0
    monkeypatch.undo()

    outcome = _rerun(db_session, media_dirs, upload)
    assert outcome.status == UploadStatus.COMPLETED, outcome
    done = row(upload)
    assert done.finalize_attempts == 2
    assert done.status == UploadStatus.COMPLETED and dest.read_bytes() == data and not path.exists()
    assert dest.stat().st_nlink == 1
    assert db_session.scalar(select(func.count()).select_from(MediaAsset)) == 1
    asset = db_session.get(MediaAsset, done.media_asset_id)
    assert (
        asset.uploaded_by_user_id == world["editor"].id
        and asset.ingest_source == IngestSource.UPLOAD
    )
    assert categories_of(asset)["mens-fashion"] == ("upload", True)


def test_recovery_never_adopts_a_different_file_at_the_intended_path(
    api: TestClient,
    world: dict,
    db_session: Session,
    organization,
    media_dirs,
    incoming: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    login(api, world["editor"])
    upload, data = _uploaded(api, world, seed=22, name="taken.mp4")
    monkeypatch.setattr(
        Finalizer, "_register", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    now = datetime.now(UTC)
    with pytest.raises(RuntimeError):
        finalizer(db_session, media_dirs).run_next(now=now)
    db_session.rollback()
    monkeypatch.undo()
    dest = media_dirs.uploads / expected_relative_path(organization, upload, now)
    assert dest.read_bytes() == data
    # Something else replaced our bytes at the intended name before the retry.
    dest.write_bytes(b"someone else's file")
    outcome = _rerun(db_session, media_dirs, upload)
    assert outcome.status == UploadStatus.FAILED and outcome.code == "destination_conflict"
    assert dest.read_bytes() == b"someone else's file"
    assert db_session.scalar(select(func.count()).select_from(MediaAsset)) == 0

    # Both names gone (staging renamed away, destination removed by an operator): no silent
    # success, the session fails as staging_missing and keeps its intent for inspection.
    gone, _ = _uploaded(api, world, seed=23, name="gone.mp4")
    monkeypatch.setattr(
        Finalizer, "_register", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    with pytest.raises(RuntimeError):
        finalizer(db_session, media_dirs).run_next(now=now)
    db_session.rollback()
    monkeypatch.undo()
    (media_dirs.uploads / expected_relative_path(organization, gone, now)).unlink()
    outcome = _rerun(db_session, media_dirs, gone)
    assert outcome.status == UploadStatus.FAILED and outcome.code == "staging_missing"


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

    # A staging file with a second name is refused by the chunk writer (defence in depth).
    twice = create(api, world, filename="twice.mp4")
    staged = staging_path(incoming, organization, twice)
    os.link(staged, staged.with_name("alias.part"))
    response = send_chunk(api, twice["id"], 0, payload(CHUNK))
    assert response.status_code == 503 and "singly-linked" in response.json()["error"]["message"]

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
    (media_dirs.uploads / "ready").symlink_to(media_dirs.originals)
    _uploaded(api, world, seed=40, name="sym.mp4")
    outcome = finalizer(db_session, media_dirs).run_next()
    assert outcome.status == UploadStatus.FAILED and outcome.code == "destination_unavailable"
    assert list(media_dirs.originals.iterdir()) == []
    (media_dirs.uploads / "ready").unlink()
    (media_dirs.uploads / "ready").mkdir()
    (media_dirs.uploads / "ready" / str(organization.id)).symlink_to(media_dirs.finals)
    _uploaded(api, world, seed=41, name="sym2.mp4")
    outcome = finalizer(db_session, media_dirs).run_next()
    assert outcome.status == UploadStatus.FAILED and outcome.code == "destination_unavailable"
    assert list(media_dirs.finals.iterdir()) == []


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

    counts = expire_and_clean(db_session, media_dirs.registry, writable_settings(media_dirs))
    assert counts == {"expired": 1, "staging_removed": 1, "orphans_removed": 1}
    assert row(idle).status == UploadStatus.EXPIRED and not idle_path.exists()
    assert row(live).status == UploadStatus.CREATED and live_path.exists()
    assert not orphan_old.exists() and orphan_new.exists() and stranger.exists()
    assert send_chunk(api, idle["id"], 0, b"x").status_code == 410
    assert "upload.expired" in audit_actions(db_session, organization)
    # Nothing happens where the uploads root is not writable (every non-worker process).
    assert expire_and_clean(db_session, media_dirs.registry, build_settings(media_dirs)) == {
        "expired": 0,
        "staging_removed": 0,
        "orphans_removed": 0,
    }


def test_finalizer_is_inert_without_a_writable_uploads_root(
    api: TestClient, world: dict, db_session: Session, media_dirs
) -> None:
    login(api, world["editor"])
    _uploaded(api, world, seed=50)
    inert = Finalizer.build(
        db_session, media_dirs.registry, build_settings(media_dirs), prober=ContentProber()
    )
    assert inert.run_next() is None
    assert db_session.scalar(select(MediaUpload.status)) == UploadStatus.UPLOADED
    # Writable flag without a configured root: released for a later attempt, never failed.
    no_root = MediaRootRegistry.from_paths(finals=media_dirs.finals)
    settings = build_settings(media_dirs, media_uploads_path=None, media_uploads_writable=True)
    outcome = Finalizer.build(db_session, no_root, settings, prober=ContentProber()).run_next()
    assert outcome.code == "retry" and row({"id": str(outcome.upload_id)}).lease_until is None


def test_uploads_are_refused_when_no_uploads_root_is_configured(
    monkeypatch: pytest.MonkeyPatch, media_dirs, organization, schema, db_session
) -> None:
    env = media_dirs.env()
    env.pop("MEDIA_UPLOADS_PATH")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("MEDIA_UPLOADS_PATH", raising=False)
    monkeypatch.setenv("DEFAULT_ORGANIZATION_SLUG", organization.slug)
    monkeypatch.delenv("MEDIA_ADMIN_TOKEN", raising=False)
    get_settings.cache_clear()
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        sign_in(client, db_session, organization, role=MembershipRole.ADMIN)
        roots = {
            r["facet"]: r
            for r in client.post("/api/v1/content-categories/ensure-facets").json()["roots"]
        }
        response = client.post(
            BASE,
            json={
                "filename": "x.mp4",
                "size": 10,
                "production_owner_category_id": roots["production_owner"]["id"],
                "product_category_id": roots["product_category"]["id"],
                "content_type_id": roots["content_type"]["id"],
            },
        )
    dispose_engine()
    get_settings.cache_clear()
    assert response.status_code in (422, 503)  # taxonomy or storage refusal, never a write


# ---------------------------------------------------------------- legacy finals stay read-only


def test_legacy_assets_and_tree_are_untouched_by_uploads(
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
    tree_before = finals_snapshot(media_dirs.finals)

    # The legacy tree is read-only for the finalizer, exactly like production mounts it.
    restore: list[tuple[Path, int]] = []
    if can_test_permissions():
        for directory in [
            media_dirs.finals,
            *[p for p in media_dirs.finals.rglob("*") if p.is_dir()],
        ]:
            restore.append((directory, directory.stat().st_mode & 0o777))
            directory.chmod(0o555)
    try:
        login(api, world["editor"])
        _uploaded(api, world, seed=61, name="new.mp4")
        assert finalizer(db_session, media_dirs).run_next().status == UploadStatus.COMPLETED
    finally:
        for directory, mode in reversed(restore):
            directory.chmod(mode)
    db_session.expire_all()
    after = db_session.get(MediaAsset, before.id)
    assert (legacy.stat().st_size, legacy.stat().st_mtime_ns, after.updated_at) == snapshot
    assert finals_snapshot(media_dirs.finals) == tree_before
    assert after.ingest_source == IngestSource.SCAN and categories_of(after) == {
        "uncategorized": ("folder", True),
        "hang": ("path", False),
    }
    assert db_session.scalar(select(func.count()).select_from(MediaFileVariant)) == 2
    # Both finals are listed together, each resolving through its own root.
    listed = api.get("/api/v1/media/assets", params={"asset_kind": "final"}).json()["items"]
    assert {a["root_key"] for a in listed} == {"finals", "uploads"}
    assert {a["ingest_source"] for a in listed} == {"scan", "upload"}


def test_no_code_path_writes_the_legacy_finals_root() -> None:
    """Static guard: the storage module names only the uploads layout, the finalizer only
    binds `registry.uploads`, and nothing in the media module opens a finals path for
    writing. `FinalsPlacer` (hard-link placement into FINAL) no longer exists."""
    import inspect

    from affilio.modules.media import upload_finalize, upload_storage

    storage_src = inspect.getsource(upload_storage)
    finalize_src = inspect.getsource(upload_finalize)
    assert "FinalsPlacer" not in storage_src and "FinalsPlacer" not in finalize_src
    assert "os.link(" not in storage_src and "os.link(" not in finalize_src
    assert "registry.finals" not in finalize_src and ".finals" not in storage_src
    assert "ensure_layout(binding.path)" in finalize_src  # the uploads binding only
    assert not hasattr(upload_storage, "FinalsPlacer")


# ---------------------------------------------------------------- resolver consumers


def test_stream_preview_resolves_both_roots_and_honours_ranges(
    api: TestClient, world: dict, db_session: Session, organization, media_dirs
) -> None:
    login(api, world["editor"])
    upload, data = _uploaded(api, world, seed=70, name="preview.mp4")
    assert finalizer(db_session, media_dirs).run_next().status == UploadStatus.COMPLETED
    asset_id = str(row(upload).media_asset_id)
    full = api.get(f"/api/v1/media/assets/{asset_id}/stream")
    assert full.status_code == 200 and full.content == data
    assert full.headers["content-type"].startswith("video/mp4")
    assert full.headers["accept-ranges"] == "bytes"
    part = api.get(f"/api/v1/media/assets/{asset_id}/stream", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and part.content == data[:100]
    assert part.headers["content-range"] == f"bytes 0-99/{len(data)}"

    # Legacy final under the read-only tree streams through the same endpoint.
    legacy = media_dirs.finals / "Hang/T9/legacy.mp4"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(payload(5_000, seed=71))
    old = (datetime.now(UTC) - timedelta(hours=1)).timestamp()
    os.utime(legacy, (old, old))
    scan_root(db_session, organization.id, media_dirs, "finals")
    legacy_asset = db_session.scalar(
        select(MediaAsset).where(MediaAsset.ingest_source == IngestSource.SCAN)
    )
    streamed = api.get(f"/api/v1/media/assets/{legacy_asset.id}/stream")
    assert streamed.status_code == 200 and streamed.content == legacy.read_bytes()
    # A file that disappeared answers 404, never a 500 or another file.
    ready_path(media_dirs, upload).unlink()
    assert api.get(f"/api/v1/media/assets/{asset_id}/stream").status_code == 404
    # Viewers may preview; anonymous callers may not.
    login(api, world["viewer"])
    assert api.get(f"/api/v1/media/assets/{legacy_asset.id}/stream").status_code == 200
    api.post("/api/v1/auth/logout")
    assert api.get(f"/api/v1/media/assets/{legacy_asset.id}/stream").status_code == 401


def test_publishing_input_resolves_an_uploaded_final(
    api: TestClient, world: dict, db_session: Session, organization, media_dirs
) -> None:
    login(api, world["editor"])
    upload, _ = _uploaded(api, world, seed=80, name="reel.mp4")
    assert finalizer(db_session, media_dirs).run_next().status == UploadStatus.COMPLETED
    asset = db_session.get(MediaAsset, row(upload).media_asset_id)
    asset.handoff_status = HandoffStatus.APPROVED
    db_session.commit()
    db_session.refresh(asset)
    check = check_reel_media(asset, media_dirs.registry, build_settings(media_dirs))
    assert check.ok, check.errors
    assert check.root_key == "uploads" and check.source == "final"
    assert check.path == ready_path(media_dirs, upload)
    # A worker without the uploads root mounted cannot pick the file up.
    partial = MediaRootRegistry.from_paths(finals=media_dirs.finals, exports=media_dirs.exports)
    missing = check_reel_media(asset, partial, build_settings(media_dirs))
    assert not missing.ok and any("uploads root" in e for e in missing.errors)


@requires_ffmpeg
def test_thumbnail_renders_from_the_uploads_root(
    api: TestClient, world: dict, db_session: Session, organization, media_dirs, schema
) -> None:
    from sqlalchemy.orm import sessionmaker

    clip = make_video(media_dirs.exports / "source.mp4", size="36x64", seconds=2.0)
    data = clip.read_bytes()
    clip.unlink()
    login(api, world["editor"])
    upload = create(api, world, filename="real.mp4", size=len(data))
    upload_all(api, upload, data)
    assert finalizer(db_session, media_dirs).run_next().status == UploadStatus.COMPLETED
    asset_id = row(upload).media_asset_id
    generator = ThumbnailGenerator(writable_settings(media_dirs), media_dirs.registry)
    factory = sessionmaker(bind=schema, expire_on_commit=False)
    assert generator.process_pending(factory, limit=2) == 1
    db_session.expire_all()
    asset = db_session.get(MediaAsset, asset_id)
    assert asset.thumbnail_status == ThumbnailStatus.READY, asset.thumbnail_error
    thumb = asset.variant(MediaVariantKind.THUMBNAIL)
    assert (media_dirs.thumbnails / thumb.relative_path).stat().st_size > 0
    assert not any(media_dirs.uploads.rglob("*.jpg"))
    assert api.get(f"/api/v1/media/assets/{asset_id}/thumbnail").status_code == 200


# ---------------------------------------------------------------- filters and reclassification


def test_library_filters_by_facets_source_and_uploader(
    api: TestClient, world: dict, db_session: Session, organization, media_dirs
) -> None:
    login(api, world["editor"])
    mine, _ = _uploaded(api, world, seed=90, name="mine.mp4")
    assert finalizer(db_session, media_dirs).run_next().status == UploadStatus.COMPLETED
    login(api, world["admin"])
    theirs = create(
        api,
        world,
        filename="theirs.mp4",
        size=CHUNK,
        production_owner_category_id=world["hao"]["id"],
    )
    upload_all(api, theirs, payload(CHUNK, seed=91))
    assert finalizer(db_session, media_dirs).run_next().status == UploadStatus.COMPLETED
    legacy = media_dirs.finals / "Hang/T9/legacy.mp4"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(payload(4_000, seed=92))
    old = (datetime.now(UTC) - timedelta(hours=1)).timestamp()
    os.utime(legacy, (old, old))
    scan_root(db_session, organization.id, media_dirs, "finals")

    def ids(**params) -> set[str]:
        response = api.get("/api/v1/media/assets", params=params)
        assert response.status_code == 200, response.text
        return {a["id"] for a in response.json()["items"]}

    mine_id, theirs_id = str(row(mine).media_asset_id), str(row(theirs).media_asset_id)
    legacy_id = str(
        db_session.scalar(
            select(MediaAsset.id).where(MediaAsset.ingest_source == IngestSource.SCAN)
        )
    )
    assert ids(asset_kind="final") == {mine_id, theirs_id, legacy_id}
    assert ids(ingest_source="upload") == {mine_id, theirs_id}
    assert ids(ingest_source="scan") == {legacy_id}
    assert ids(production_owner_id=world["hang"]["id"]) == {mine_id, legacy_id}
    assert ids(production_owner_id=world["hao"]["id"]) == {theirs_id}
    assert ids(product_category_id=world["mens"]["id"]) == {mine_id, theirs_id}
    assert ids(content_type_id=world["review"]["id"], ingest_source="upload") == {
        mine_id,
        theirs_id,
    }
    assert ids(uploaded_by_user_id=str(world["editor"].id)) == {mine_id}
    assert ids(handoff_status="pending_review", production_owner_id=world["hao"]["id"]) == {
        theirs_id
    }
    assert ids(production_owner_id=str(uuid.uuid4())) == set()


def test_reclassification_is_metadata_only_and_authorized(
    api: TestClient, world: dict, db_session: Session, organization, media_dirs
) -> None:
    login(api, world["editor"])
    upload, _ = _uploaded(api, world, seed=100, name="move-me.mp4")
    assert finalizer(db_session, media_dirs).run_next().status == UploadStatus.COMPLETED
    asset_id = str(row(upload).media_asset_id)
    placed = ready_path(media_dirs, upload)
    stat_before = (placed.stat().st_ino, placed.stat().st_mtime_ns)
    url = f"/api/v1/media/assets/{asset_id}/facets"

    # Editor: own upload, but not mapped to Hao → refused; content type change → allowed.
    refused = api.put(url, json={"production_owner_category_id": world["hao"]["id"]})
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "upload_forbidden"
    assert api.put(url, json={}).status_code == 409
    assert api.put(url, json={"content_type_id": world["mens"]["id"]}).status_code == 422
    assert api.put(url, json={"content_type_id": world["old"]["id"]}).status_code == 422
    ok = api.put(url, json={"content_type_id": world["review"]["id"]})
    assert ok.status_code == 200
    # Another editor and a viewer cannot touch it.
    login(api, world["editor2"])
    assert api.put(url, json={"content_type_id": world["review"]["id"]}).status_code == 403
    login(api, world["viewer"])
    assert api.put(url, json={"content_type_id": world["review"]["id"]}).status_code == 403

    # Admin moves the owner to Hao: database only, the file does not move.
    login(api, world["admin"])
    moved = api.put(url, json={"production_owner_category_id": world["hao"]["id"]})
    assert moved.status_code == 200, moved.text
    facets = {c["facet"]: (c["slug"], c["assignment_source"]) for c in moved.json()["categories"]}
    # Under the uploads root the database record is the classification: rows stay `upload`.
    assert facets["production_owner"] == ("hao", "upload")
    assert facets["product_category"] == ("mens-fashion", "upload")
    assert moved.json()["relative_path"] == row(upload).final_relative_path
    assert (placed.stat().st_ino, placed.stat().st_mtime_ns) == stat_before
    assert not any(p.is_file() for p in media_dirs.finals.rglob("*"))
    db_session.expire_all()
    asset = db_session.get(MediaAsset, uuid.UUID(asset_id))
    assert categories_of(asset) == {
        "mens-fashion": ("upload", True),
        "hao": ("upload", False),
        "review": ("upload", False),
    }
    # A generic categories override keeps the facet rows (they are derived rows).
    kept = api.put(
        f"/api/v1/media/assets/{asset_id}/categories",
        json={"primary_category_id": None, "secondary_category_ids": []},
    ).json()
    assert {c["slug"] for c in kept["categories"]} == {"mens-fashion", "hao", "review"}
    # The submission record keeps its original attribution (reporting, payroll).
    assert row(upload).production_owner_category_id == uuid.UUID(world["hang"]["id"])
    event = db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.action == "media.facets_reclassified")
        .order_by(AuditEvent.created_at.desc())
    )
    assert event.data["before"]["production_owner"] == world["hang"]["id"]
    assert event.data["after"]["production_owner"] == world["hao"]["id"]
    # Library filters follow the new value.
    listed = api.get(
        "/api/v1/media/assets", params={"production_owner_id": world["hao"]["id"]}
    ).json()["items"]
    assert [a["id"] for a in listed] == [asset_id]

    # Legacy final: a manual owner survives the next rescan (folders no longer win).
    legacy = media_dirs.finals / "Hang/T9/legacy.mp4"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(payload(4_000, seed=101))
    old = (datetime.now(UTC) - timedelta(hours=1)).timestamp()
    os.utime(legacy, (old, old))
    scan_root(db_session, organization.id, media_dirs, "finals")
    legacy_asset = db_session.scalar(
        select(MediaAsset).where(MediaAsset.ingest_source == IngestSource.SCAN)
    )
    assert categories_of(legacy_asset) == {
        "uncategorized": ("folder", True),
        "hang": ("path", False),
    }
    done = api.put(
        f"/api/v1/media/assets/{legacy_asset.id}/facets",
        json={"production_owner_category_id": world["hao"]["id"]},
    )
    assert done.status_code == 200, done.text
    scan_root(db_session, organization.id, media_dirs, "finals")
    db_session.expire_all()
    legacy_asset = db_session.get(MediaAsset, legacy_asset.id)
    assert categories_of(legacy_asset) == {
        "uncategorized": ("folder", True),
        "hao": ("override", False),
    }
    # The generic categories override leaves the facet override alone.
    api.put(
        f"/api/v1/media/assets/{legacy_asset.id}/categories",
        json={"primary_category_id": None, "secondary_category_ids": []},
    )
    db_session.expire_all()
    assert categories_of(db_session.get(MediaAsset, legacy_asset.id))["hao"] == (
        "override",
        False,
    )
    assert legacy.read_bytes() == payload(4_000, seed=101)
