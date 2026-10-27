"""Reels publishing engine: the durable, resumable steps the publisher worker executes (M3B.2A).

Design (docs/architecture/publishing-engine.md):

- Every step does at most one kind of provider call and persists its result before returning.
  A crashed worker leaves a lease that expires; the next claim resumes from the stored phase.
- The finish ("publish") request is persisted as FINALIZING/SENT *before* it is sent and is
  never sent twice. An unknown outcome parks the upload in NEEDS_RECONCILIATION; only a
  provider status check (admin action or this module's `reconcile_upload`) can resolve it.
- Transient errors retry with exponential backoff and jitter up to `max_retries`; permanent
  errors fail the attempt (an explicit operator retry creates a new attempt and a new upload).
- Execution is at-least-once. Duplicate *publications* are prevented by the durable video id,
  the single-live-upload index per job, and the never-resend rule; exactly-once cannot be
  promised because Meta has no idempotency key (feasibility B12).
"""

from __future__ import annotations

import random
import socket
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from affilio.core.audit import record_audit
from affilio.core.config import Settings
from affilio.core.enums import (
    CredentialKind,
    EvidenceKind,
    PublicationAttemptStatus,
    PublicationStatus,
    PublishRequestStatus,
    UploadPhase,
)
from affilio.core.errors import AppError
from affilio.modules.accounts import connections as connections_service
from affilio.modules.accounts import vault
from affilio.modules.accounts.crypto import CredentialUndecryptableError, CredentialVaultError
from affilio.modules.accounts.models import SocialAccount
from affilio.modules.accounts.providers import get_provider
from affilio.modules.accounts.providers.base import (
    MetaProvider,
    MockCredentialRejectedError,
    ProviderError,
    ProviderUnavailableError,
    RateLimitedError,
    ReelStatus,
    ReelUploadSession,
    TokenInvalidError,
)
from affilio.modules.media.models import MediaAsset
from affilio.modules.media.roots import MediaRootRegistry
from affilio.modules.publishing import service as publishing_service
from affilio.modules.publishing.adapters.assisted import compose_caption
from affilio.modules.publishing.eligibility import job_api_eligibility, page_hold
from affilio.modules.publishing.media_checks import check_reel_media
from affilio.modules.publishing.models import (
    UPLOAD_ACTIVE_PHASES,
    PublicationJob,
    PublicationUpload,
)
from affilio.modules.taxonomy import service as taxonomy_service

log = structlog.get_logger("affilio.publisher.engine")

WORKER_ID = f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"
STEP_TIME_BUDGET_SECONDS = 20.0
PERMANENT_CODES = {"media_incompatible", "media_changed", "token_invalid", "permission_denied"}


def _now() -> datetime:
    return datetime.now(UTC)


# ============================================================================ outcomes


@dataclass(slots=True)
class StepResult:
    upload_id: uuid.UUID
    phase_before: UploadPhase
    phase_after: UploadPhase
    note: str | None = None


class _TransientError(Exception):
    """Internal: retry later (resume at `phase`)."""

    def __init__(self, code: str, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.retry_after = code, message, retry_after


class _PermanentError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


class _AmbiguousError(Exception):
    """The publish request may have been applied; stop and reconcile."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def classify(exc: Exception) -> _TransientError | _PermanentError:
    """Map provider/vault errors to retry-vs-fail. Finish calls are handled separately."""
    if isinstance(exc, TokenInvalidError):
        return _PermanentError("token_invalid", exc.message)
    if isinstance(exc, MockCredentialRejectedError):
        return _PermanentError("provider_mismatch", exc.message)
    if isinstance(exc, RateLimitedError):
        return _TransientError("rate_limited", exc.message, retry_after=exc.retry_after_seconds)
    if isinstance(exc, ProviderUnavailableError):
        return _TransientError("provider_unavailable", exc.message)
    if isinstance(exc, ProviderError):
        code = exc.provider_code
        if code in {10, 200, 203, 210, 368} or (code is not None and 200 <= code < 300):
            return _PermanentError("permission_denied", exc.message)
        if exc.is_transient:
            return _TransientError("provider_error", exc.message)
        return _PermanentError(f"provider_{code or 'error'}", exc.message)
    if isinstance(exc, CredentialUndecryptableError | CredentialVaultError):
        return _PermanentError("credential_unavailable", exc.message)
    if isinstance(exc, AppError):
        return _PermanentError(exc.code, exc.message)
    return _TransientError("unexpected_error", f"{type(exc).__name__}: {exc}"[:500])


def backoff(settings: Settings, retry_count: int) -> timedelta:
    base = settings.publisher_backoff_base_seconds
    delay = min(base * (2 ** max(retry_count - 1, 0)), settings.publisher_backoff_max_seconds)
    jitter = random.uniform(0, min(base, delay))  # noqa: S311 - not security sensitive
    return timedelta(seconds=delay + jitter)


# ============================================================================ audit


def _audit_phase(
    session: Session,
    upload: PublicationUpload,
    *,
    previous: UploadPhase,
    identity: Any = None,
    extra: dict[str, Any] | None = None,
) -> None:
    record_audit(
        session,
        organization_id=upload.organization_id,
        identity=identity,
        action="publication.upload_phase",
        entity_type="publication_job",
        entity_id=upload.job_id,
        data={
            "upload_id": str(upload.id),
            "from_phase": previous.value,
            "to_phase": upload.phase.value,
            "publish_request_status": upload.publish_request_status.value,
            "retry_count": upload.retry_count,
            "provider_video_id": upload.provider_video_id,
            "mock": upload.is_mock,
            **(extra or {}),
        },
    )


def _set_phase(
    session: Session,
    upload: PublicationUpload,
    phase: UploadPhase,
    *,
    next_action_at: datetime | None = None,
    extra: dict[str, Any] | None = None,
    identity: Any = None,
) -> None:
    previous = upload.phase
    upload.phase = phase
    upload.next_action_at = next_action_at or _now()
    if phase in {UploadPhase.PUBLISHED, UploadPhase.FAILED, UploadPhase.ABANDONED}:
        upload.finished_at = _now()
    if previous != phase or extra:
        _audit_phase(session, upload, previous=previous, extra=extra, identity=identity)


# ============================================================================ scheduling


def release_due_everywhere(session: Session) -> int:
    """Move due SCHEDULED jobs to READY_TO_PUBLISH in every organization (system actor)."""
    org_ids = session.scalars(
        select(PublicationJob.organization_id)
        .where(
            PublicationJob.status == PublicationStatus.SCHEDULED,
            PublicationJob.scheduled_at <= _now(),
        )
        .distinct()
    ).all()
    released = 0
    for org_id in org_ids:
        released += len(publishing_service.release_due(session, org_id, identity=None))
    return released


def reels_published_last_24h(
    session: Session, account_id: uuid.UUID
) -> tuple[int, datetime | None]:
    rows = session.execute(
        select(func.count(), func.min(PublicationUpload.published_at)).where(
            PublicationUpload.social_account_id == account_id,
            PublicationUpload.phase == UploadPhase.PUBLISHED,
            PublicationUpload.published_at >= _now() - timedelta(hours=24),
        )
    ).one()
    return int(rows[0] or 0), rows[1]


def _hold_page(session: Session, account: SocialAccount, *, until: datetime, reason: str) -> None:
    if account.publish_hold_until is None or account.publish_hold_until < until:
        account.publish_hold_until = until
        account.publish_hold_reason = reason[:64]
        record_audit(
            session,
            organization_id=account.organization_id,
            identity=None,
            action="account.publish_hold",
            entity_type="social_account",
            entity_id=account.id,
            data={"until": until.isoformat(), "reason": reason},
        )


def dispatch_ready(session: Session, settings: Settings, *, limit: int = 20) -> list[uuid.UUID]:
    """Start API attempts for READY_TO_PUBLISH jobs whose Page is API-eligible.

    Re-runs every safety check at dispatch time (snapshot current, target eligible, category
    unchanged, account connected, hold/quota). Assisted targets are left for operators.
    """
    candidates = session.scalars(
        select(PublicationJob)
        .where(PublicationJob.status == PublicationStatus.READY_TO_PUBLISH)
        .order_by(PublicationJob.scheduled_at.asc().nulls_last(), PublicationJob.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    started: list[uuid.UUID] = []
    for locked in candidates:
        job = publishing_service.get_job(session, locked.organization_id, locked.id)
        eligibility = job_api_eligibility(job, settings)
        if not eligibility.eligible:
            continue  # assisted path; the operator starts it from the checklist
        snapshot = job.approved_snapshot
        if snapshot is None or snapshot.content_version != job.content_version:
            continue
        tree = taxonomy_service.load_tree(session, job.organization_id)
        reason = publishing_service._blocked_reason(job, tree)
        if reason is not None or publishing_service._classification_stale(job):
            record_audit(
                session,
                organization_id=job.organization_id,
                identity=None,
                action="publication.release_held",
                entity_type="publication_job",
                entity_id=job.id,
                data={
                    "reason": reason or "classification changed after approval",
                    "by": "publisher",
                },
            )
            continue
        if page_hold(job.social_account) is not None:
            continue
        count, oldest = reels_published_last_24h(session, job.social_account_id)
        if count >= settings.meta_reels_daily_quota:
            until = (oldest or _now()) + timedelta(hours=24)
            _hold_page(session, job.social_account, until=until, reason="reels_quota")
            continue
        if any(u.is_live for u in job.uploads):
            continue
        publishing_service.enqueue_api_attempt(
            session,
            job,
            identity=None,
            note="scheduled dispatch",
            started_by_label="publisher",
            settings=settings,
        )
        started.append(job.id)
    session.flush()
    return started


# ============================================================================ claiming


def claim_uploads(
    session: Session, *, worker_id: str, limit: int, lease_seconds: int
) -> list[uuid.UUID]:
    """Atomically lease due uploads. Per-Page serialization: a Page with another leased
    upload is skipped; held Pages are skipped; expired leases are reclaimable."""
    now = _now()
    other = PublicationUpload.__table__.alias("other")
    busy_page = exists(
        select(1).where(
            other.c.social_account_id == PublicationUpload.social_account_id,
            other.c.id != PublicationUpload.id,
            other.c.phase.in_([p.value for p in UPLOAD_ACTIVE_PHASES]),
            other.c.lease_expires_at > now,
        )
    )
    stmt = (
        select(PublicationUpload)
        .join(SocialAccount, SocialAccount.id == PublicationUpload.social_account_id)
        .where(
            PublicationUpload.phase.in_(list(UPLOAD_ACTIVE_PHASES)),
            PublicationUpload.next_action_at <= now,
            (PublicationUpload.lease_expires_at.is_(None))
            | (PublicationUpload.lease_expires_at < now),
            (SocialAccount.publish_hold_until.is_(None))
            | (SocialAccount.publish_hold_until <= now),
            ~busy_page,
        )
        .order_by(PublicationUpload.next_action_at)
        .limit(limit)
        .with_for_update(skip_locked=True, of=PublicationUpload)
    )
    claimed: list[uuid.UUID] = []
    seen_pages: set[uuid.UUID] = set()
    for upload in session.scalars(stmt):
        if upload.social_account_id in seen_pages:
            continue  # one upload per Page per claim batch
        seen_pages.add(upload.social_account_id)
        previous_owner = upload.lease_owner
        upload.lease_owner = worker_id
        upload.lease_expires_at = now + timedelta(seconds=lease_seconds)
        if upload.started_at is None:
            upload.started_at = now
        if previous_owner is not None and previous_owner != worker_id:
            upload.last_status = {**upload.last_status, "recovered_from": previous_owner}
        claimed.append(upload.id)
    session.flush()
    return claimed


def _renew_lease(upload: PublicationUpload, *, lease_seconds: int) -> None:
    upload.lease_expires_at = _now() + timedelta(seconds=lease_seconds)


# ============================================================================ step runner


def run_step(
    session: Session,
    settings: Settings,
    registry: MediaRootRegistry,
    upload_id: uuid.UUID,
    *,
    worker_id: str,
    provider: MetaProvider | None = None,
) -> StepResult:
    """Advance one leased upload by one phase (at most one network call kind). Commits."""
    upload = session.scalar(
        select(PublicationUpload)
        .options(
            selectinload(PublicationUpload.social_account).selectinload(SocialAccount.connection),
            selectinload(PublicationUpload.attempt),
        )
        .where(PublicationUpload.id == upload_id)
        .with_for_update()
    )
    if upload is None:
        raise ValueError("upload not found")
    before = upload.phase
    if upload.lease_owner != worker_id or (
        upload.lease_expires_at is not None and upload.lease_expires_at < _now()
    ):
        session.rollback()
        return StepResult(upload_id, before, before, "lease lost")
    job = publishing_service.get_job(session, upload.organization_id, upload.job_id)
    if job.status != PublicationStatus.PUBLISHING:
        _abandon(session, upload, job, reason=f"job left PUBLISHING ({job.status.value})")
        session.commit()
        return StepResult(upload_id, before, upload.phase, "abandoned")
    try:
        _dispatch_phase(session, settings, registry, upload, job, provider, worker_id)
    except _TransientError as exc:
        _schedule_retry(session, settings, upload, job, exc)
    except _PermanentError as exc:
        _fail(session, upload, job, code=exc.code, message=exc.message)
    except _AmbiguousError as exc:
        _park(session, upload, job, code=exc.code, message=exc.message)
    except Exception as exc:  # defensive: never leave the lease dangling without a plan
        log.exception("publisher.step_crashed", upload_id=str(upload_id))
        _schedule_retry(session, settings, upload, job, classify(exc))
    upload.lease_owner = None
    upload.lease_expires_at = None
    session.commit()
    return StepResult(upload_id, before, upload.phase, upload.last_error_code)


def _dispatch_phase(
    session: Session,
    settings: Settings,
    registry: MediaRootRegistry,
    upload: PublicationUpload,
    job: PublicationJob,
    provider: MetaProvider | None,
    worker_id: str,
) -> None:
    phase = upload.phase
    if phase == UploadPhase.RETRY_WAIT:
        resume = UploadPhase(upload.last_status.get("resume_phase", UploadPhase.PENDING.value))
        _set_phase(session, upload, resume)
        phase = resume
    if phase in {UploadPhase.PENDING, UploadPhase.VALIDATING}:
        _step_validate(session, settings, registry, upload, job)
        return
    # Everything below needs the provider and the Page token.
    eligibility = job_api_eligibility(job, settings)
    if not eligibility.eligible:
        raise _PermanentError(
            eligibility.reason or "not_eligible", "Target is no longer API-eligible"
        )
    provider = provider or get_provider(settings)
    if provider.is_mock != upload.is_mock:
        raise _PermanentError("provider_mismatch", "Upload and provider mock/live modes differ")
    token = _page_token(session, settings, upload)
    try:
        if phase == UploadPhase.SESSION_STARTING:
            _step_start_session(session, upload, provider, token)
        elif phase == UploadPhase.UPLOADING:
            _step_upload(session, settings, registry, upload, provider, token, worker_id)
        elif phase in {UploadPhase.UPLOAD_COMPLETE, UploadPhase.PROCESSING}:
            _step_poll_processing(session, settings, upload, provider, token)
        elif phase == UploadPhase.FINALIZING:
            _step_finalize(session, upload, job, provider, token)
        elif phase == UploadPhase.VERIFYING:
            _step_verify(session, settings, upload, job, provider, token)
        else:
            raise _PermanentError("invalid_phase", f"Upload is in unexpected phase {phase.value}")
    finally:
        del token


# ---------------------------------------------------------------- phase handlers


def _step_validate(
    session: Session,
    settings: Settings,
    registry: MediaRootRegistry,
    upload: PublicationUpload,
    job: PublicationJob,
) -> None:
    _set_phase(session, upload, UploadPhase.VALIDATING)
    asset = session.get(MediaAsset, upload.media_asset_id)
    check = check_reel_media(asset, registry, settings) if asset else None
    if check is None or not check.ok:
        raise _PermanentError("media_incompatible", check.summary() if check else "media missing")
    upload.file_relative_path = check.relative_path
    upload.file_size = check.file_size
    upload.file_mtime = check.file_mtime
    upload.bytes_total = check.file_size
    upload.last_status = {**upload.last_status, "media_warnings": check.warnings}
    _set_phase(session, upload, UploadPhase.SESSION_STARTING)


def _page_token(session: Session, settings: Settings, upload: PublicationUpload) -> str:
    account = upload.social_account
    if account.connection is None or not account.external_account_id:
        raise _PermanentError("account_not_connected", "The Page has no active connection")
    credential = vault.active_credential(
        session,
        connection=account.connection,
        subject=account.external_account_id,
        kind=CredentialKind.META_PAGE,
    )
    if credential is None:
        raise _PermanentError("credential_missing", "No active Page credential; reconnect the Page")
    try:
        cipher = vault.ensure_vault(settings)
        return vault.open_token(cipher, credential)
    except (CredentialVaultError, CredentialUndecryptableError) as exc:
        raise _PermanentError("credential_unavailable", exc.message) from exc


def _call(fn, *args: Any, **kwargs: Any):
    """Run one provider call, translating errors into engine exceptions."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        raise classify(exc) from exc


def _step_start_session(
    session: Session, upload: PublicationUpload, provider: MetaProvider, token: str
) -> None:
    page_id = upload.social_account.external_account_id or ""
    result: ReelUploadSession = _call(provider.start_reel_upload, page_token=token, page_id=page_id)
    upload.provider_session_id = result.upload_url[:2048]
    upload.provider_video_id = result.video_id
    upload.bytes_sent = 0
    upload.attempt.detail = {**upload.attempt.detail, "provider_video_id": result.video_id}
    _set_phase(session, upload, UploadPhase.UPLOADING)


def _step_upload(
    session: Session,
    settings: Settings,
    registry: MediaRootRegistry,
    upload: PublicationUpload,
    provider: MetaProvider,
    token: str,
    worker_id: str,
) -> None:
    assert upload.provider_video_id and upload.provider_session_id  # noqa: S101
    root = registry.finals
    if root is None or not upload.file_relative_path:
        raise _PermanentError("media_incompatible", "Finals root unavailable on this worker")
    path = root.resolve(upload.file_relative_path)
    try:
        stat = path.stat()
    except OSError as exc:
        raise _PermanentError("media_changed", "The final file disappeared before upload") from exc
    if stat.st_size != upload.file_size:
        raise _PermanentError("media_changed", "The final file changed size during publishing")
    reel_session = ReelUploadSession(
        video_id=upload.provider_video_id, upload_url=upload.provider_session_id
    )
    total = int(upload.bytes_total or stat.st_size)
    budget_end = _now() + timedelta(seconds=STEP_TIME_BUDGET_SECONDS)
    chunk_size = settings.publisher_upload_chunk_bytes
    with path.open("rb") as handle:
        while upload.bytes_sent < total and _now() < budget_end:
            handle.seek(upload.bytes_sent)
            chunk = handle.read(chunk_size)
            if not chunk:
                raise _PermanentError("media_changed", "The final file is shorter than expected")
            try:
                result = provider.upload_reel_bytes(
                    page_token=token,
                    session=reel_session,
                    chunk=chunk,
                    offset=upload.bytes_sent,
                    total=total,
                )
            except Exception as exc:
                # Resync the resume offset from the provider when it says how much it has.
                try:
                    status = provider.reel_status(page_token=token, video_id=reel_session.video_id)
                    if status.bytes_transferred is not None:
                        upload.bytes_sent = int(status.bytes_transferred)
                except Exception:  # noqa: S110 - the original error wins
                    pass
                raise classify(exc) from exc
            upload.bytes_sent = int(result.bytes_received)
            upload.lease_owner = worker_id
            _renew_lease(upload, lease_seconds=settings.publisher_lease_seconds)
            session.commit()  # durable progress per chunk
    if upload.bytes_sent >= total:
        _set_phase(session, upload, UploadPhase.UPLOAD_COMPLETE)
    else:
        upload.next_action_at = _now()  # continue on the next claim (budget exhausted)


def _apply_status(upload: PublicationUpload, status: ReelStatus) -> None:
    upload.processing_status = (status.processing_status or status.video_status)[:64]
    upload.last_status = {
        **upload.last_status,
        "video_status": status.video_status,
        "processing_status": status.processing_status,
        "publish_status": status.publish_status,
        "bytes_transferred": status.bytes_transferred,
        "error": status.error,
    }
    upload.last_polled_at = _now()
    upload.poll_count += 1


def _step_poll_processing(
    session: Session,
    settings: Settings,
    upload: PublicationUpload,
    provider: MetaProvider,
    token: str,
) -> None:
    status: ReelStatus = _call(
        provider.reel_status, page_token=token, video_id=upload.provider_video_id
    )
    _apply_status(upload, status)
    if status.is_error:
        raise _PermanentError(
            "processing_failed", status.error or f"provider status {status.video_status}"
        )
    if status.video_status == "uploading" or (
        status.bytes_transferred is not None
        and upload.bytes_total
        and status.bytes_transferred < upload.bytes_total
    ):
        upload.bytes_sent = int(status.bytes_transferred or 0)
        _set_phase(session, upload, UploadPhase.UPLOADING)
        return
    if status.video_status == "ready" or status.processing_status == "completed":
        _set_phase(session, upload, UploadPhase.FINALIZING)
        return
    if _timed_out(settings, upload):
        raise _PermanentError(
            "processing_timeout", "The provider did not finish processing in time"
        )
    _set_phase(
        session,
        upload,
        UploadPhase.PROCESSING,
        next_action_at=_now() + timedelta(seconds=settings.publisher_status_poll_seconds),
    )


def _timed_out(settings: Settings, upload: PublicationUpload) -> bool:
    started = upload.started_at or upload.created_at
    return _now() - started > timedelta(minutes=settings.publisher_publish_timeout_minutes)


def _caption(job: PublicationJob) -> tuple[str, str | None]:
    snapshot = job.approved_snapshot
    content = {
        "caption": snapshot.caption if snapshot else job.caption,
        "cta": snapshot.cta if snapshot else job.cta,
        "hashtags": snapshot.hashtags if snapshot else job.hashtags,
        "links": snapshot.links if snapshot else [],
    }
    # Links in Reel captions are not clickable (capability false): caption, CTA, hashtags only.
    return compose_caption(content, include_links=False), (
        snapshot.title if snapshot else job.title
    )


def _step_finalize(
    session: Session,
    upload: PublicationUpload,
    job: PublicationJob,
    provider: MetaProvider,
    token: str,
) -> None:
    if upload.publish_request_status != PublishRequestStatus.NOT_SENT:
        # A finish was already sent (crash after send, before the response was recorded).
        raise _AmbiguousError(
            "finish_outcome_unknown", "A publish request was already sent; outcome unknown"
        )
    description, title = _caption(job)
    upload.publish_request_status = PublishRequestStatus.SENT
    upload.finish_requested_at = _now()
    _audit_phase(session, upload, previous=upload.phase, extra={"publish_request": "sent"})
    session.commit()  # durable BEFORE the network call: never resend after a crash
    try:
        result = provider.finish_reel(
            page_token=token,
            page_id=upload.social_account.external_account_id or "",
            video_id=upload.provider_video_id or "",
            description=description,
            title=title,
        )
    except TokenInvalidError as exc:
        # Auth rejected: the provider did not apply the request.
        upload.publish_request_status = PublishRequestStatus.REJECTED
        raise _PermanentError("token_invalid", exc.message) from exc
    except ProviderError as exc:
        if isinstance(exc, ProviderUnavailableError) or exc.is_transient:
            upload.publish_request_status = PublishRequestStatus.UNKNOWN
            raise _AmbiguousError("finish_outcome_unknown", exc.message) from exc
        upload.publish_request_status = PublishRequestStatus.REJECTED
        upload.finish_response = {"error": exc.message, "code": exc.provider_code}
        raise _PermanentError(
            f"finish_rejected_{exc.provider_code or 'error'}", exc.message
        ) from exc
    except Exception as exc:
        upload.publish_request_status = PublishRequestStatus.UNKNOWN
        raise _AmbiguousError(
            "finish_outcome_unknown", f"{type(exc).__name__}: {exc}"[:500]
        ) from exc
    upload.publish_request_status = PublishRequestStatus.ACKNOWLEDGED
    upload.finish_response = {**result.raw, "post_id": result.post_id}
    if result.post_id:
        upload.external_post_id = result.post_id
    _set_phase(session, upload, UploadPhase.VERIFYING)


def _step_verify(
    session: Session,
    settings: Settings,
    upload: PublicationUpload,
    job: PublicationJob,
    provider: MetaProvider,
    token: str,
) -> None:
    status: ReelStatus = _call(
        provider.reel_status, page_token=token, video_id=upload.provider_video_id
    )
    _apply_status(upload, status)
    if status.is_published:
        identity = _call(
            provider.reel_identity, page_token=token, video_id=upload.provider_video_id
        )
        _complete(session, upload, job, post_id=identity.post_id, permalink=identity.permalink_url)
        return
    if status.is_error:
        # The provider acknowledged the finish but then failed the post: no post exists.
        upload.publish_request_status = PublishRequestStatus.REJECTED
        raise _PermanentError("publish_failed", status.error or "provider reported an error")
    if _timed_out(settings, upload):
        upload.publish_request_status = PublishRequestStatus.UNKNOWN
        raise _AmbiguousError("publish_unconfirmed", "The provider never confirmed the publication")
    upload.next_action_at = _now() + timedelta(seconds=settings.publisher_status_poll_seconds)


# ---------------------------------------------------------------- terminal transitions


def _complete(
    session: Session,
    upload: PublicationUpload,
    job: PublicationJob,
    *,
    post_id: str | None,
    permalink: str | None,
    identity: Any = None,
) -> None:
    external_id = post_id or f"video:{upload.provider_video_id}"
    upload.external_post_id = external_id
    upload.external_post_url = permalink
    upload.publish_request_status = PublishRequestStatus.CONFIRMED
    upload.published_at = _now()
    job.external_post_id = external_id
    job.external_post_url = permalink
    job.evidence_kind = EvidenceKind.API_VERIFIED
    job.published_at = upload.published_at
    job.confirmed_by_label = "publisher (api_verified)" if identity is None else identity.label
    job.confirmed_at = upload.published_at
    job.last_error = None
    attempt = upload.attempt
    attempt.status = PublicationAttemptStatus.SUCCEEDED
    attempt.finished_at = upload.published_at
    attempt.detail = {
        **attempt.detail,
        "evidence": "api_verified",
        "external_post_id": external_id,
        "mock": upload.is_mock,
    }
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError as exc:
        # Another job already recorded this post for the Page: do not claim it twice.
        session.rollback()
        upload.last_error = "This provider post is already recorded on another publication"
        upload.last_error_code = "duplicate_post_identity"
        _set_phase(session, upload, UploadPhase.NEEDS_RECONCILIATION)
        attempt.status = PublicationAttemptStatus.AMBIGUOUS
        publishing_service._transition(
            session,
            job.organization_id,
            job,
            action="report_uncertain",
            identity=identity,
            note=upload.last_error,
        )
        log.warning(
            "publisher.duplicate_post_identity", upload_id=str(upload.id), error=str(exc)[:120]
        )
        return
    _set_phase(session, upload, UploadPhase.PUBLISHED, identity=identity)
    from_reconciliation = job.status == PublicationStatus.NEEDS_RECONCILIATION
    publishing_service._transition(
        session,
        job.organization_id,
        job,
        action="reconcile" if from_reconciliation else "confirm",
        identity=identity,
        note="confirmed by the provider" if from_reconciliation else "published by the publisher",
        target=PublicationStatus.PUBLISHED,
        extra={
            "evidence_kind": "api_verified",
            "external_post_id": external_id,
            "external_post_url": permalink,
            "mock": upload.is_mock,
            **({"outcome": "published"} if from_reconciliation else {}),
        },
    )


def _record_error(
    upload: PublicationUpload, job: PublicationJob, *, code: str, message: str, transient: bool
) -> None:
    upload.last_error = message[:2000]
    upload.last_error_code = code[:64]
    upload.last_error_transient = transient
    job.last_error = f"{code}: {message}"[:2000]


def _schedule_retry(
    session: Session,
    settings: Settings,
    upload: PublicationUpload,
    job: PublicationJob,
    exc: _TransientError | _PermanentError,
) -> None:
    if isinstance(exc, _PermanentError):
        _fail(session, upload, job, code=exc.code, message=exc.message)
        return
    resume_phase = (
        upload.phase
        if upload.phase != UploadPhase.RETRY_WAIT
        else UploadPhase(upload.last_status.get("resume_phase", UploadPhase.PENDING.value))
    )
    upload.retry_count += 1
    _record_error(upload, job, code=exc.code, message=exc.message, transient=True)
    if upload.retry_count > upload.max_retries:
        _fail(
            session,
            upload,
            job,
            code="retries_exhausted",
            message=f"Gave up after {upload.retry_count - 1} retries: {exc.message}",
        )
        return
    delay = backoff(settings, upload.retry_count)
    if exc.code == "rate_limited":
        hold_for = timedelta(
            seconds=exc.retry_after or settings.publisher_rate_limit_hold_minutes * 60
        )
        _hold_page(session, upload.social_account, until=_now() + hold_for, reason="rate_limited")
        delay = max(delay, hold_for)
    upload.last_status = {**upload.last_status, "resume_phase": resume_phase.value}
    _set_phase(
        session,
        upload,
        UploadPhase.RETRY_WAIT,
        next_action_at=_now() + delay,
        extra={"error_code": exc.code, "retry_in_seconds": int(delay.total_seconds())},
    )


def _fail(
    session: Session,
    upload: PublicationUpload,
    job: PublicationJob,
    *,
    code: str,
    message: str,
    identity: Any = None,
) -> None:
    _record_error(upload, job, code=code, message=message, transient=False)
    attempt = upload.attempt
    attempt.status = PublicationAttemptStatus.FAILED
    attempt.finished_at = _now()
    attempt.error = f"{code}: {message}"[:2000]
    _set_phase(session, upload, UploadPhase.FAILED, extra={"error_code": code}, identity=identity)
    if code == "token_invalid":
        connections_service.mark_token_invalid(session, upload.social_account, identity=identity)
    if job.status == PublicationStatus.PUBLISHING:
        publishing_service._transition(
            session, job.organization_id, job, action="fail", identity=identity, note=attempt.error
        )
    elif job.status == PublicationStatus.NEEDS_RECONCILIATION:
        publishing_service._transition(
            session,
            job.organization_id,
            job,
            action="reconcile",
            identity=identity,
            note=attempt.error,
            target=PublicationStatus.FAILED,
            extra={"outcome": "not_published"},
        )


def _park(
    session: Session, upload: PublicationUpload, job: PublicationJob, *, code: str, message: str
) -> None:
    _record_error(upload, job, code=code, message=message, transient=False)
    attempt = upload.attempt
    attempt.status = PublicationAttemptStatus.AMBIGUOUS
    attempt.error = f"{code}: {message}"[:2000]
    _set_phase(session, upload, UploadPhase.NEEDS_RECONCILIATION, extra={"error_code": code})
    if job.status == PublicationStatus.PUBLISHING:
        publishing_service._transition(
            session,
            job.organization_id,
            job,
            action="report_uncertain",
            identity=None,
            note=attempt.error,
        )


def _abandon(
    session: Session, upload: PublicationUpload, job: PublicationJob, *, reason: str
) -> None:
    upload.last_error = reason[:2000]
    upload.last_error_code = "abandoned"
    attempt = upload.attempt
    if attempt.finished_at is None:
        attempt.status = PublicationAttemptStatus.FAILED
        attempt.finished_at = _now()
        attempt.error = reason[:2000]
    _set_phase(session, upload, UploadPhase.ABANDONED, extra={"reason": reason})


# ============================================================================ reconciliation


def reconcile_upload(
    session: Session, settings: Settings, upload: PublicationUpload, *, identity: Any
) -> PublicationUpload:
    """Ask the provider about a parked upload. Published → PUBLISHED; error/missing → FAILED;
    still unknown → stays parked (never re-sends the publish request)."""
    job = publishing_service.get_job(session, upload.organization_id, upload.job_id)
    if upload.phase != UploadPhase.NEEDS_RECONCILIATION:
        raise AppError(
            "Upload is not waiting for reconciliation", code="invalid_phase", status_code=409
        )
    if not upload.provider_video_id:
        _fail(
            session,
            upload,
            job,
            code="no_provider_video",
            message="No provider video id was recorded; nothing could have been published",
            identity=identity,
        )
        return upload
    provider = get_provider(settings)
    if provider.is_mock != upload.is_mock:
        raise AppError(
            "Provider mode differs from the upload's mode",
            code="provider_mismatch",
            status_code=409,
        )
    try:
        token = _page_token(session, settings, upload)
    except _PermanentError as exc:
        upload.last_error, upload.last_error_code = exc.message, exc.code
        session.flush()
        return upload
    try:
        try:
            status = provider.reel_status(page_token=token, video_id=upload.provider_video_id)
        except ProviderError as exc:
            if exc.provider_code == 100 and exc.provider_subcode == 33:
                # The video no longer exists: the finish was never applied.
                upload.publish_request_status = PublishRequestStatus.REJECTED
                _fail(
                    session,
                    upload,
                    job,
                    code="video_missing",
                    message=exc.message,
                    identity=identity,
                )
                return upload
            upload.last_error, upload.last_error_code = exc.message[:2000], exc.code
            record_audit(
                session,
                organization_id=upload.organization_id,
                identity=identity,
                action="publication.reconcile_provider",
                entity_type="publication_job",
                entity_id=job.id,
                data={"outcome": "still_unknown", "error": exc.code},
            )
            session.flush()
            return upload
        _apply_status(upload, status)
        if status.is_published:
            ident = provider.reel_identity(page_token=token, video_id=upload.provider_video_id)
            _complete(
                session,
                upload,
                job,
                post_id=ident.post_id,
                permalink=ident.permalink_url,
                identity=identity,
            )
        elif status.is_error:
            upload.publish_request_status = PublishRequestStatus.REJECTED
            _fail(
                session,
                upload,
                job,
                code="publish_failed",
                message=status.error or "provider reported an error",
                identity=identity,
            )
        elif status.video_status == "ready" and status.publish_status in (None, "draft"):
            # Upload complete, never published: the lost finish was not applied. Safe to fail;
            # an explicit retry starts a fresh upload. Nothing is re-sent automatically.
            upload.publish_request_status = PublishRequestStatus.REJECTED
            _fail(
                session,
                upload,
                job,
                code="finish_not_applied",
                message=(
                    "The provider shows the video unpublished; the publish request was not applied"
                ),
                identity=identity,
            )
        else:
            record_audit(
                session,
                organization_id=upload.organization_id,
                identity=identity,
                action="publication.reconcile_provider",
                entity_type="publication_job",
                entity_id=job.id,
                data={"outcome": "still_unknown", "video_status": status.video_status},
            )
        session.flush()
    finally:
        del token
    return upload


# ============================================================================ worker helpers


def abandon_orphans(session: Session) -> int:
    """Uploads still live whose job is no longer PUBLISHING (e.g. admin reconciled manually)."""
    count = 0
    stale = session.scalars(
        select(PublicationUpload)
        .join(PublicationJob, PublicationJob.id == PublicationUpload.job_id)
        .where(
            PublicationUpload.phase.in_(list(UPLOAD_ACTIVE_PHASES)),
            PublicationJob.status != PublicationStatus.PUBLISHING,
            (PublicationUpload.lease_expires_at.is_(None))
            | (PublicationUpload.lease_expires_at < _now()),
        )
        .with_for_update(skip_locked=True, of=PublicationUpload)
    ).all()
    for upload in stale:
        job = publishing_service.get_job(session, upload.organization_id, upload.job_id)
        _abandon(session, upload, job, reason=f"job left PUBLISHING ({job.status.value})")
        count += 1
    session.flush()
    return count


__all__ = [
    "WORKER_ID",
    "StepResult",
    "abandon_orphans",
    "claim_uploads",
    "dispatch_ready",
    "reconcile_upload",
    "release_due_everywhere",
    "run_step",
]
