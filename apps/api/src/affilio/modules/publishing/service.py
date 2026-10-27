"""Publishing services: campaigns, publication jobs, snapshots, evidence, reconciliation.

Rules enforced here (not in routers):
- only approved FINAL media and non-disabled accounts become publication targets;
- one job per (campaign, media, account); idempotency keys return the existing job;
- every mutation checks the optimistic `version` and locks the row;
- approval freezes an immutable snapshot; later content edits drop back to DRAFT;
- PUBLISHED requires evidence; duplicate evidence is rejected; nothing auto-retries.
"""

from __future__ import annotations

import math
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, object_session, selectinload

from affilio.core.audit import record_audit
from affilio.core.config import Settings, get_settings
from affilio.core.enums import (
    CampaignStatus,
    EvidenceKind,
    HandoffStatus,
    MediaAssetKind,
    MediaAssetStatus,
    Platform,
    PublicationAttemptStatus,
    PublicationStatus,
    PublishMode,
    PublishRequestStatus,
    SocialAccountSource,
    SocialAccountStatus,
    ThumbnailStatus,
    UploadPhase,
)
from affilio.core.errors import NotFoundError
from affilio.modules.accounts import routing_engine
from affilio.modules.accounts import service as accounts_service
from affilio.modules.accounts.models import SocialAccount
from affilio.modules.content import service as links_service
from affilio.modules.content.models import AffiliateLink
from affilio.modules.media import classification
from affilio.modules.media import service as media_service
from affilio.modules.media.models import MediaAsset, MediaContentCategory
from affilio.modules.products.urls import normalize_url
from affilio.modules.publishing import lifecycle
from affilio.modules.publishing.adapters import get_adapter
from affilio.modules.publishing.adapters.base import ValidationResult
from affilio.modules.publishing.eligibility import (
    account_api_eligibility,
    job_api_eligibility,
    page_hold,
)
from affilio.modules.publishing.errors import (
    CampaignClosedError,
    ContentInvalidError,
    DuplicateEvidenceError,
    DuplicatePublicationError,
    EvidenceInvalidError,
    InvalidPublicationTransitionError,
    MediaNotApprovedError,
    RevalidationRequiredError,
    ScheduleConflictError,
    ScheduleInvalidError,
    TargetBlockedError,
    TargetUnavailableError,
    VersionConflictError,
)
from affilio.modules.publishing.models import (
    UPLOAD_ACTIVE_PHASES,
    Campaign,
    CampaignMedia,
    CampaignTarget,
    PublicationAttempt,
    PublicationJob,
    PublicationLink,
    PublicationSnapshot,
    PublicationUpload,
)
from affilio.modules.publishing.schemas import (
    CalendarResponse,
    CampaignCreate,
    CampaignDetail,
    CampaignMediaResponse,
    CampaignSummary,
    CampaignTargetResponse,
    CampaignUpdate,
    EngineStatus,
    EvidenceRequest,
    FailureRequest,
    PagePublishingStatus,
    PaginatedCampaigns,
    PaginatedPublications,
    PreparedPublicationResponse,
    PublicationAttemptResponse,
    PublicationBatchCreate,
    PublicationContentUpdate,
    PublicationCreate,
    PublicationDetail,
    PublicationLinkResponse,
    PublicationSnapshotResponse,
    PublicationSummary,
    PublishingStats,
    ReconcileRequest,
    ScheduleRequest,
    UploadSummary,
    ValidationResponse,
    WorkerStatus,
)
from affilio.modules.taxonomy import service as taxonomy_service

if TYPE_CHECKING:
    from affilio.api.deps import Identity

MAX_PAGE_SIZE = 100
SCHEDULE_PAST_TOLERANCE = timedelta(minutes=2)
_FB_POST_ID_PATTERNS = (
    re.compile(r"/(?:reel|reels|videos|posts)/(\d{5,})", re.IGNORECASE),
    re.compile(r"[?&](?:v|story_fbid|fbid)=(\d{5,})", re.IGNORECASE),
)
PLATFORM_HOSTS: dict[Platform, tuple[str, ...]] = {
    Platform.FACEBOOK: ("facebook.com", "fb.com", "fb.watch"),
    Platform.INSTAGRAM: ("instagram.com",),
    Platform.TIKTOK: ("tiktok.com",),
    Platform.YOUTUBE: ("youtube.com", "youtu.be"),
    Platform.SHOPEE: ("shopee.vn", "shopee.com", "shp.ee", "shope.ee"),
}


def _now() -> datetime:
    return datetime.now(UTC)


# ====================================================================== mapping helpers


def _thumbnail_url(asset: MediaAsset, prefix: str) -> str | None:
    return (
        f"{prefix}/{asset.id}/thumbnail"
        if asset.thumbnail_status == ThumbnailStatus.READY
        else None
    )


def _media_dict(asset: MediaAsset) -> dict[str, Any]:
    main = asset.main_variant()
    return {
        "asset_id": str(asset.id),
        "title": asset.title,
        "asset_kind": asset.asset_kind.value,
        "handoff_status": asset.handoff_status.value if asset.handoff_status else None,
        "status": asset.status.value,
        "file_name": main.file_name if main else None,
        "relative_path": main.relative_path if main else None,
        "extension": main.extension if main else None,
        "content_hash": main.content_hash if main else None,
        "file_size": main.file_size if main else None,
        "duration_seconds": asset.duration_seconds,
        "width": asset.width,
        "height": asset.height,
    }


def _target_dict(account: SocialAccount) -> dict[str, Any]:
    return {
        "social_account_id": str(account.id),
        "platform": account.platform.value,
        "display_name": account.display_name,
        "handle": account.handle,
        "external_account_id": account.external_account_id,
        "status": account.status.value,
        "is_verified": account.is_verified,
        "open_url": accounts_service.publishable_url(account),
    }


def _links_dicts(job: PublicationJob) -> list[dict[str, Any]]:
    return [
        {
            "affiliate_link_id": str(pl.affiliate_link_id),
            "label": pl.affiliate_link.label,
            "url": pl.affiliate_link.url,
            "platform": pl.affiliate_link.platform.value,
            "position": pl.position,
        }
        for pl in sorted(job.links, key=lambda x: x.position)
    ]


def _content_dict(job: PublicationJob) -> dict[str, Any]:
    return {
        "title": job.title,
        "caption": job.caption,
        "cta": job.cta,
        "hashtags": list(job.hashtags or []),
        "links": _links_dicts(job),
    }


def validate_job(job: PublicationJob) -> ValidationResult:
    adapter = get_adapter(job.platform)
    result = ValidationResult()
    result.merge(adapter.validate_media(_media_dict(job.media_asset)))
    result.merge(adapter.validate_content(_content_dict(job)))
    if job.social_account.status == SocialAccountStatus.DISABLED:
        result.error("The target account is disabled")
    inactive = [pl.affiliate_link.label for pl in job.links if pl.affiliate_link.status != "active"]
    if inactive:
        result.error(f"Archived links attached: {', '.join(inactive)}")
    return result


def _snapshot_response(s: PublicationSnapshot) -> PublicationSnapshotResponse:
    return PublicationSnapshotResponse.model_validate(s)


def _attempt_response(a: PublicationAttempt) -> PublicationAttemptResponse:
    return PublicationAttemptResponse.model_validate(a)


# ---------------------------------------------------------------- routing safety (M3A.1)


def _classification_stale(job: PublicationJob) -> bool:
    snapshot = job.approved_snapshot
    changed = job.media_asset.classification_changed_at
    if snapshot is None or changed is None:
        return False
    baseline = snapshot.approved_at
    if job.revalidated_at is not None and job.revalidated_at > baseline:
        baseline = job.revalidated_at
    return changed > baseline


def _tree_for(job: PublicationJob):
    """Category tree for the job's organization, cached per session (request-scoped)."""
    session = object_session(job)
    cache = session.info.setdefault("category_trees", {}) if session is not None else {}
    tree = cache.get(job.organization_id)
    if tree is None:
        tree = taxonomy_service.load_tree(session, job.organization_id)
        cache[job.organization_id] = tree
    return tree


def target_verdict(job: PublicationJob, tree) -> routing_engine.Recommendation:
    """Re-evaluate the target against the video's *current* classification."""
    klass = classification.classification_of(job.media_asset)
    return routing_engine.evaluate_account(
        job.social_account,
        primary_id=klass.primary.category_id if klass.primary else None,
        secondary_ids=[a.category_id for a in klass.secondary],
        tree=tree,
    )


def _blocked_reason(job: PublicationJob, tree) -> str | None:
    verdict = target_verdict(job, tree)
    return None if verdict.eligible else verdict.blocking_reason


def assert_publishable_target(session: Session, job: PublicationJob, *, step: str) -> None:
    """Refuse approval/scheduling/publishing steps for targets that are no longer eligible or
    for publications whose video was reclassified after approval (explicit decision needed)."""
    tree = taxonomy_service.load_tree(session, job.organization_id)
    reason = _blocked_reason(job, tree)
    if reason is not None:
        raise TargetBlockedError(
            f"Cannot {step}: {reason}. Review the channel rules or cancel this publication.",
            details={"blocking_reason": reason, "social_account_id": str(job.social_account_id)},
        )
    if step not in {"approve", "submit"} and _classification_stale(job):
        raise RevalidationRequiredError(
            f"Cannot {step}: the video's category changed after approval. "
            "An admin must revalidate the approval or revoke it.",
            details={
                "classification_changed_at": job.media_asset.classification_changed_at.isoformat()
                if job.media_asset.classification_changed_at
                else None,
                "approved_at": job.approved_snapshot.approved_at.isoformat()
                if job.approved_snapshot
                else None,
            },
        )


def _summary_fields(job: PublicationJob, *, thumbnail_url_prefix: str) -> dict[str, Any]:
    main = job.media_asset.main_variant()
    eligibility = job_api_eligibility(job, get_settings())
    tree = _tree_for(job)
    blocked_reason = _blocked_reason(job, tree)
    stale = _classification_stale(job)
    caption = (job.caption or "").strip()
    return {
        "id": job.id,
        "intent_id": job.intent_id,
        "campaign_id": job.campaign_id,
        "campaign_name": job.campaign.name,
        "media_asset_id": job.media_asset_id,
        "media_title": job.media_asset.title,
        "media_file_name": main.file_name if main else None,
        "thumbnail_url": _thumbnail_url(job.media_asset, thumbnail_url_prefix),
        "social_account_id": job.social_account_id,
        "account_display_name": job.social_account.display_name,
        "account_connection_label": job.social_account.connection_label,
        "platform": job.platform,
        "status": job.status,
        "version": job.version,
        "content_version": job.content_version,
        "title": job.title,
        "caption_preview": (caption[:140] + "…") if len(caption) > 140 else (caption or None),
        "scheduled_at": job.scheduled_at,
        "published_at": job.published_at,
        "external_post_url": job.external_post_url,
        "evidence_kind": job.evidence_kind,
        "has_approved_snapshot": job.approved_snapshot_id is not None,
        "approval_stale": bool(
            job.approved_snapshot is not None
            and job.approved_snapshot.content_version != job.content_version
        ),
        # The video's folder/category changed after approval (M3A.1): review before publishing.
        "classification_stale": stale,
        "target_blocked_reason": blocked_reason,
        "revalidation_required": stale or blocked_reason is not None,
        "revalidated_at": job.revalidated_at,
        "revalidated_by_label": job.revalidated_by_label,
        "link_count": len(job.links),
        "attempt_count": len(job.attempts),
        "last_error": job.last_error,
        "status_changed_at": job.status_changed_at,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        # --- publishing engine (M3B.2A) ---
        "publish_mode": "api" if eligibility.eligible else "assisted",
        "api_eligibility_reason": eligibility.reason,
        "target_is_mock": job.social_account.is_mock,
        "target_hold_until": page_hold(job.social_account),
        "target_hold_reason": job.social_account.publish_hold_reason
        if page_hold(job.social_account)
        else None,
        "upload": upload_summary(_live_upload(job)),
    }


def _live_upload(job: PublicationJob) -> PublicationUpload | None:
    """The current (non-terminal) upload, else the most recent one."""
    live = [u for u in job.uploads if u.is_live]
    if live:
        return live[-1]
    return job.uploads[-1] if job.uploads else None


def upload_summary(upload: PublicationUpload | None) -> UploadSummary | None:
    if upload is None:
        return None
    return UploadSummary(
        id=upload.id,
        attempt_id=upload.attempt_id,
        phase=upload.phase,
        publish_request_status=upload.publish_request_status,
        is_mock=upload.is_mock,
        progress_percent=upload.progress_percent,
        bytes_sent=upload.bytes_sent,
        bytes_total=upload.bytes_total,
        processing_status=upload.processing_status,
        provider_video_id=upload.provider_video_id,
        external_post_id=upload.external_post_id,
        external_post_url=upload.external_post_url,
        retry_count=upload.retry_count,
        max_retries=upload.max_retries,
        next_action_at=upload.next_action_at,
        lease_owner=upload.lease_owner,
        lease_expires_at=upload.lease_expires_at,
        last_error=upload.last_error,
        last_error_code=upload.last_error_code,
        last_error_transient=upload.last_error_transient,
        finish_requested_at=upload.finish_requested_at,
        published_at=upload.published_at,
        started_at=upload.started_at,
        finished_at=upload.finished_at,
        updated_at=upload.updated_at,
    )


def job_summary(job: PublicationJob, *, thumbnail_url_prefix: str) -> PublicationSummary:
    return PublicationSummary(**_summary_fields(job, thumbnail_url_prefix=thumbnail_url_prefix))


def job_detail(job: PublicationJob, *, thumbnail_url_prefix: str, role) -> PublicationDetail:
    validation = validate_job(job)
    return PublicationDetail(
        uploads=[u for u in (upload_summary(x) for x in job.uploads) if u is not None],
        **_summary_fields(job, thumbnail_url_prefix=thumbnail_url_prefix),
        caption=job.caption,
        cta=job.cta,
        hashtags=list(job.hashtags or []),
        content_notes=job.content_notes,
        idempotency_key=job.idempotency_key,
        links=[
            PublicationLinkResponse(
                affiliate_link_id=pl.affiliate_link_id,
                position=pl.position,
                label=pl.affiliate_link.label,
                url=pl.affiliate_link.url,
                platform=pl.affiliate_link.platform,
                status=pl.affiliate_link.status.value,
            )
            for pl in sorted(job.links, key=lambda x: x.position)
        ],
        approved_snapshot=_snapshot_response(job.approved_snapshot)
        if job.approved_snapshot
        else None,
        snapshots=[_snapshot_response(s) for s in job.snapshots],
        attempts=[_attempt_response(a) for a in job.attempts],
        external_post_id=job.external_post_id,
        confirmed_by_label=job.confirmed_by_label,
        confirmed_at=job.confirmed_at,
        confirmation_note=job.confirmation_note,
        validation=ValidationResponse(
            ok=validation.ok, errors=validation.errors, warnings=validation.warnings
        ),
        allowed_actions=lifecycle.allowed_actions(job.status, role),
        capabilities=get_adapter(job.platform).capabilities().as_dict(),
    )


# ====================================================================== campaigns


def _campaign_query() -> Select:
    return select(Campaign).options(
        selectinload(Campaign.media)
        .selectinload(CampaignMedia.media_asset)
        .selectinload(MediaAsset.variants),
        selectinload(Campaign.targets).selectinload(CampaignTarget.social_account),
    )


def _job_query() -> Select:
    return select(PublicationJob).options(
        selectinload(PublicationJob.campaign),
        selectinload(PublicationJob.media_asset).selectinload(MediaAsset.variants),
        selectinload(PublicationJob.media_asset)
        .selectinload(MediaAsset.category_assignments)
        .selectinload(MediaContentCategory.category),
        selectinload(PublicationJob.social_account).selectinload(SocialAccount.channel_profile),
        selectinload(PublicationJob.social_account).selectinload(SocialAccount.category_rules),
        selectinload(PublicationJob.social_account).selectinload(SocialAccount.connection),
        selectinload(PublicationJob.links).selectinload(PublicationLink.affiliate_link),
        selectinload(PublicationJob.snapshots),
        selectinload(PublicationJob.approved_snapshot),
        selectinload(PublicationJob.attempts),
        selectinload(PublicationJob.uploads),
    )


def get_campaign(session: Session, organization_id: uuid.UUID, campaign_id: uuid.UUID) -> Campaign:
    campaign = session.scalar(
        _campaign_query().where(
            Campaign.organization_id == organization_id, Campaign.id == campaign_id
        )
    )
    if campaign is None:
        raise NotFoundError("Campaign not found")
    return campaign


def _publication_counts(
    session: Session, campaign_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, int]]:
    counts: dict[uuid.UUID, dict[str, int]] = {cid: {} for cid in campaign_ids}
    if not campaign_ids:
        return counts
    rows = session.execute(
        select(PublicationJob.campaign_id, PublicationJob.status, func.count())
        .where(PublicationJob.campaign_id.in_(campaign_ids))
        .group_by(PublicationJob.campaign_id, PublicationJob.status)
    )
    for cid, status, count in rows:
        counts[cid][status.value] = int(count)
    return counts


def campaign_summary(campaign: Campaign, counts: dict[str, int]) -> CampaignSummary:
    return CampaignSummary(
        id=campaign.id,
        name=campaign.name,
        description=campaign.description,
        status=campaign.status,
        starts_on=campaign.starts_on,
        ends_on=campaign.ends_on,
        media_count=len(campaign.media),
        target_count=len(campaign.targets),
        publication_counts=counts,
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
    )


def campaign_detail(
    session: Session, campaign: Campaign, *, thumbnail_url_prefix: str
) -> CampaignDetail:
    counts = _publication_counts(session, [campaign.id])[campaign.id]
    jobs = list(
        session.scalars(
            _job_query()
            .where(PublicationJob.campaign_id == campaign.id)
            .order_by(PublicationJob.created_at)
        )
    )
    summary = campaign_summary(campaign, counts)
    return CampaignDetail(
        **summary.model_dump(),
        notes=campaign.notes,
        media=[
            CampaignMediaResponse(
                media_asset_id=cm.media_asset_id,
                title=cm.media_asset.title,
                file_name=(
                    cm.media_asset.main_variant().file_name
                    if cm.media_asset.main_variant()
                    else None
                ),
                handoff_status=cm.media_asset.handoff_status.value
                if cm.media_asset.handoff_status
                else None,
                asset_kind=cm.media_asset.asset_kind.value,
                duration_seconds=cm.media_asset.duration_seconds,
                width=cm.media_asset.width,
                height=cm.media_asset.height,
                thumbnail_url=_thumbnail_url(cm.media_asset, thumbnail_url_prefix),
                added_at=cm.created_at,
            )
            for cm in sorted(campaign.media, key=lambda x: x.created_at)
        ],
        targets=[
            CampaignTargetResponse(
                social_account_id=ct.social_account_id,
                platform=ct.social_account.platform,
                display_name=ct.social_account.display_name,
                handle=ct.social_account.handle,
                status=ct.social_account.status.value,
                is_verified=ct.social_account.is_verified,
                connection_label=ct.social_account.connection_label,
            )
            for ct in sorted(campaign.targets, key=lambda x: x.social_account.display_name)
        ],
        publications=[job_summary(j, thumbnail_url_prefix=thumbnail_url_prefix) for j in jobs],
    )


def list_campaigns(
    session: Session,
    organization_id: uuid.UUID,
    *,
    status: CampaignStatus | None,
    q: str | None,
    page: int,
    page_size: int,
) -> PaginatedCampaigns:
    page = max(1, page)
    page_size = min(max(1, page_size), MAX_PAGE_SIZE)
    base = _campaign_query().where(Campaign.organization_id == organization_id)
    if status:
        base = base.where(Campaign.status == status)
    if q:
        base = base.where(Campaign.name.ilike(f"%{q.strip()}%"))
    total = int(
        session.scalar(select(func.count()).select_from(base.order_by(None).subquery())) or 0
    )
    campaigns = list(
        session.scalars(
            base.order_by(Campaign.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    counts = _publication_counts(session, [c.id for c in campaigns])
    return PaginatedCampaigns(
        items=[campaign_summary(c, counts[c.id]) for c in campaigns],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=max(1, math.ceil(total / page_size)) if total else 0,
    )


def _require_approved_final(
    session: Session, organization_id: uuid.UUID, asset_id: uuid.UUID
) -> MediaAsset:
    asset = media_service.get_asset(session, organization_id, asset_id)
    if asset.asset_kind != MediaAssetKind.FINAL:
        raise MediaNotApprovedError(
            "Only final videos can be distributed",
            details={"asset_id": str(asset_id), "asset_kind": asset.asset_kind.value},
        )
    if asset.handoff_status != HandoffStatus.APPROVED:
        raise MediaNotApprovedError(
            "The final video has not been approved for distribution",
            details={
                "asset_id": str(asset_id),
                "handoff_status": asset.handoff_status.value if asset.handoff_status else None,
            },
        )
    if asset.status != MediaAssetStatus.ACTIVE:
        raise MediaNotApprovedError(
            "The media file is missing or unsupported",
            details={"asset_id": str(asset_id), "status": asset.status.value},
        )
    return asset


def _require_usable_account(
    session: Session, organization_id: uuid.UUID, account_id: uuid.UUID
) -> SocialAccount:
    account = accounts_service.get_account(session, organization_id, account_id)
    if account.status == SocialAccountStatus.DISABLED:
        raise TargetUnavailableError(
            "The target account is disabled", details={"account_id": str(account_id)}
        )
    return account


def add_campaign_media(
    session: Session, organization_id: uuid.UUID, campaign: Campaign, asset_ids: list[uuid.UUID]
) -> list[CampaignMedia]:
    existing = {cm.media_asset_id for cm in campaign.media}
    added: list[CampaignMedia] = []
    for asset_id in asset_ids:
        if asset_id in existing:
            continue
        asset = _require_approved_final(session, organization_id, asset_id)
        link = CampaignMedia(id=uuid.uuid4(), campaign_id=campaign.id, media_asset_id=asset.id)
        link.media_asset = asset
        try:
            with session.begin_nested():
                campaign.media.append(link)
                session.flush()
        except IntegrityError:
            # Concurrent request added the same video; treat as already present.
            if link in campaign.media:
                campaign.media.remove(link)
            session.expire(campaign, ["media"])
        else:
            added.append(link)
        existing.add(asset_id)
    session.flush()
    return added


def add_campaign_targets(
    session: Session, organization_id: uuid.UUID, campaign: Campaign, account_ids: list[uuid.UUID]
) -> list[CampaignTarget]:
    existing = {ct.social_account_id for ct in campaign.targets}
    added: list[CampaignTarget] = []
    for account_id in account_ids:
        if account_id in existing:
            continue
        account = _require_usable_account(session, organization_id, account_id)
        target = CampaignTarget(
            id=uuid.uuid4(), campaign_id=campaign.id, social_account_id=account.id
        )
        target.social_account = account
        try:
            with session.begin_nested():
                campaign.targets.append(target)
                session.flush()
        except IntegrityError:
            if target in campaign.targets:
                campaign.targets.remove(target)
            session.expire(campaign, ["targets"])
        else:
            added.append(target)
        existing.add(account_id)
    session.flush()
    return added


def create_campaign(
    session: Session, organization_id: uuid.UUID, data: CampaignCreate, *, identity: Identity
) -> Campaign:
    campaign = Campaign(
        id=uuid.uuid4(),
        organization_id=organization_id,
        name=data.name,
        description=(data.description or "").strip() or None,
        status=CampaignStatus.DRAFT,
        starts_on=data.starts_on,
        ends_on=data.ends_on,
        notes=(data.notes or "").strip() or None,
        created_by_user_id=identity.user_id,
    )
    session.add(campaign)
    session.flush()
    add_campaign_media(session, organization_id, campaign, data.media_asset_ids)
    add_campaign_targets(session, organization_id, campaign, data.social_account_ids)
    return get_campaign(session, organization_id, campaign.id)


def update_campaign(campaign: Campaign, data: CampaignUpdate) -> Campaign:
    changes = data.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(campaign, key, value)
    if campaign.starts_on and campaign.ends_on and campaign.ends_on < campaign.starts_on:
        raise ScheduleInvalidError("ends_on must not be before starts_on")
    return campaign


def remove_campaign_media(session: Session, campaign: Campaign, asset_id: uuid.UUID) -> None:
    in_use = session.scalar(
        select(func.count()).where(
            PublicationJob.campaign_id == campaign.id,
            PublicationJob.media_asset_id == asset_id,
            PublicationJob.status.not_in([PublicationStatus.CANCELLED]),
        )
    )
    if in_use:
        raise DuplicatePublicationError(
            "This video has publications in the campaign; cancel them first"
        )
    for cm in list(campaign.media):
        if cm.media_asset_id == asset_id:
            campaign.media.remove(cm)
            session.flush()
            return
    raise NotFoundError("Media is not part of this campaign")


def remove_campaign_target(session: Session, campaign: Campaign, account_id: uuid.UUID) -> None:
    in_use = session.scalar(
        select(func.count()).where(
            PublicationJob.campaign_id == campaign.id,
            PublicationJob.social_account_id == account_id,
            PublicationJob.status.not_in([PublicationStatus.CANCELLED]),
        )
    )
    if in_use:
        raise DuplicatePublicationError(
            "This account has publications in the campaign; cancel them first"
        )
    for ct in list(campaign.targets):
        if ct.social_account_id == account_id:
            campaign.targets.remove(ct)
            session.flush()
            return
    raise NotFoundError("Account is not a target of this campaign")


# ====================================================================== publications: read


def get_job(session: Session, organization_id: uuid.UUID, job_id: uuid.UUID) -> PublicationJob:
    job = session.scalar(
        _job_query().where(
            PublicationJob.organization_id == organization_id, PublicationJob.id == job_id
        )
    )
    if job is None:
        raise NotFoundError("Publication not found")
    return job


def _lock_job(
    session: Session, organization_id: uuid.UUID, job_id: uuid.UUID, *, expected_version: int
) -> PublicationJob:
    """Row-lock the job and verify the client's version (optimistic concurrency)."""
    locked = session.scalar(
        select(PublicationJob)
        .where(PublicationJob.organization_id == organization_id, PublicationJob.id == job_id)
        .with_for_update()
    )
    if locked is None:
        raise NotFoundError("Publication not found")
    session.refresh(locked)
    if locked.version != expected_version:
        raise VersionConflictError(
            "The publication changed since you loaded it; reload and try again",
            details={"expected_version": expected_version, "current_version": locked.version},
        )
    return get_job(session, organization_id, job_id)


def _bump(job: PublicationJob) -> None:
    job.version += 1


def _set_status(job: PublicationJob, status: PublicationStatus) -> None:
    if job.status != status:
        job.status = status
        job.status_changed_at = _now()


def list_jobs(
    session: Session,
    organization_id: uuid.UUID,
    *,
    thumbnail_url_prefix: str,
    status: PublicationStatus | None = None,
    statuses: list[PublicationStatus] | None = None,
    campaign_id: uuid.UUID | None = None,
    social_account_id: uuid.UUID | None = None,
    media_asset_id: uuid.UUID | None = None,
    platform: Platform | None = None,
    q: str | None = None,
    scheduled_from: datetime | None = None,
    scheduled_to: datetime | None = None,
    sort: str = "updated_desc",
    page: int = 1,
    page_size: int = 25,
) -> PaginatedPublications:
    page = max(1, page)
    page_size = min(max(1, page_size), MAX_PAGE_SIZE)
    base = _job_query().where(PublicationJob.organization_id == organization_id)
    if status:
        base = base.where(PublicationJob.status == status)
    if statuses:
        base = base.where(PublicationJob.status.in_(statuses))
    if campaign_id:
        base = base.where(PublicationJob.campaign_id == campaign_id)
    if social_account_id:
        base = base.where(PublicationJob.social_account_id == social_account_id)
    if media_asset_id:
        base = base.where(PublicationJob.media_asset_id == media_asset_id)
    if platform:
        base = base.where(PublicationJob.platform == platform)
    if q:
        pattern = f"%{q.strip()}%"
        base = base.where(
            PublicationJob.title.ilike(pattern) | PublicationJob.caption.ilike(pattern)
        )
    if scheduled_from:
        base = base.where(PublicationJob.scheduled_at >= scheduled_from)
    if scheduled_to:
        base = base.where(PublicationJob.scheduled_at < scheduled_to)
    total = int(
        session.scalar(select(func.count()).select_from(base.order_by(None).subquery())) or 0
    )
    order = {
        "updated_desc": PublicationJob.updated_at.desc(),
        "scheduled_asc": PublicationJob.scheduled_at.asc().nulls_last(),
        "scheduled_desc": PublicationJob.scheduled_at.desc().nulls_last(),
        "created_desc": PublicationJob.created_at.desc(),
    }.get(sort, PublicationJob.updated_at.desc())
    jobs = list(
        session.scalars(
            base.order_by(order, PublicationJob.id).offset((page - 1) * page_size).limit(page_size)
        )
    )
    return PaginatedPublications(
        items=[job_summary(j, thumbnail_url_prefix=thumbnail_url_prefix) for j in jobs],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=max(1, math.ceil(total / page_size)) if total else 0,
    )


def queue(
    session: Session, organization_id: uuid.UUID, *, thumbnail_url_prefix: str
) -> list[PublicationSummary]:
    jobs = list(
        session.scalars(
            _job_query()
            .where(
                PublicationJob.organization_id == organization_id,
                PublicationJob.status.in_(list(lifecycle.QUEUE_STATES)),
            )
            .order_by(
                PublicationJob.scheduled_at.asc().nulls_last(), PublicationJob.updated_at.desc()
            )
        )
    )
    rank = {s: i for i, s in enumerate(lifecycle.QUEUE_STATES)}
    jobs.sort(key=lambda j: (rank[j.status], j.scheduled_at or datetime.max.replace(tzinfo=UTC)))
    return [job_summary(j, thumbnail_url_prefix=thumbnail_url_prefix) for j in jobs]


def calendar(
    session: Session,
    organization_id: uuid.UUID,
    *,
    timezone: str,
    start: datetime,
    end: datetime,
    thumbnail_url_prefix: str,
) -> CalendarResponse:
    if end <= start or end - start > timedelta(days=62):
        raise ScheduleInvalidError("Calendar range must be positive and at most 62 days")
    stmt = (
        _job_query()
        .where(
            PublicationJob.organization_id == organization_id,
            PublicationJob.status != PublicationStatus.CANCELLED,
            (
                (PublicationJob.scheduled_at >= start) & (PublicationJob.scheduled_at < end)
                | (PublicationJob.published_at >= start) & (PublicationJob.published_at < end)
            ),
        )
        .order_by(PublicationJob.scheduled_at.asc().nulls_last(), PublicationJob.published_at.asc())
    )
    jobs = list(session.scalars(stmt))
    return CalendarResponse(
        timezone=timezone,
        range_start=start,
        range_end=end,
        items=[job_summary(j, thumbnail_url_prefix=thumbnail_url_prefix) for j in jobs],
    )


def stats(session: Session, organization_id: uuid.UUID) -> PublishingStats:
    rows = session.execute(
        select(PublicationJob.status, func.count())
        .where(PublicationJob.organization_id == organization_id)
        .group_by(PublicationJob.status)
    )
    by_status = {s.value: 0 for s in PublicationStatus}
    for status, count in rows:
        by_status[status.value] = int(count)
    due_now = int(
        session.scalar(
            select(func.count()).where(
                PublicationJob.organization_id == organization_id,
                PublicationJob.status.in_(
                    [PublicationStatus.SCHEDULED, PublicationStatus.READY_TO_PUBLISH]
                ),
                PublicationJob.scheduled_at <= _now(),
            )
        )
        or 0
    )
    campaigns_active = int(
        session.scalar(
            select(func.count()).where(
                Campaign.organization_id == organization_id,
                Campaign.status == CampaignStatus.ACTIVE,
            )
        )
        or 0
    )
    accounts_registered = int(
        session.scalar(
            select(func.count()).where(
                SocialAccount.organization_id == organization_id,
                SocialAccount.status != SocialAccountStatus.DISABLED,
            )
        )
        or 0
    )
    links_active = int(
        session.scalar(
            select(func.count()).where(
                AffiliateLink.organization_id == organization_id, AffiliateLink.status == "active"
            )
        )
        or 0
    )
    return PublishingStats(
        by_status=by_status,
        due_now=due_now,
        needs_attention=sum(by_status[s.value] for s in lifecycle.ATTENTION_STATES),
        campaigns_active=campaigns_active,
        accounts_registered=accounts_registered,
        links_active=links_active,
    )


# ====================================================================== publications: create


def _set_links(
    session: Session, organization_id: uuid.UUID, job: PublicationJob, link_ids: list[uuid.UUID]
) -> None:
    links = links_service.get_links(session, organization_id, link_ids) if link_ids else []
    job.links.clear()
    session.flush()
    for position, link in enumerate(links):
        pl = PublicationLink(
            id=uuid.uuid4(), job_id=job.id, affiliate_link_id=link.id, position=position
        )
        pl.affiliate_link = link
        job.links.append(pl)
    session.flush()


def _find_by_idempotency(
    session: Session, organization_id: uuid.UUID, key: str | None
) -> PublicationJob | None:
    if not key:
        return None
    return session.scalar(
        _job_query().where(
            PublicationJob.organization_id == organization_id, PublicationJob.idempotency_key == key
        )
    )


def create_job(
    session: Session, organization_id: uuid.UUID, data: PublicationCreate, *, identity: Identity
) -> tuple[PublicationJob, bool]:
    """Returns (job, created). With an idempotency key, a repeat returns the original job."""
    existing = _find_by_idempotency(session, organization_id, data.idempotency_key)
    if existing is not None:
        return existing, False
    campaign = get_campaign(session, organization_id, data.campaign_id)
    if campaign.status in {CampaignStatus.COMPLETED, CampaignStatus.ARCHIVED}:
        raise CampaignClosedError("Campaign is closed; reopen it to add publications")
    asset = _require_approved_final(session, organization_id, data.media_asset_id)
    account = _require_usable_account(session, organization_id, data.social_account_id)
    add_campaign_media(session, organization_id, campaign, [asset.id])
    add_campaign_targets(session, organization_id, campaign, [account.id])
    if data.scheduled_at is not None:
        _assert_schedulable(data.scheduled_at)

    job = PublicationJob(
        id=uuid.uuid4(),
        organization_id=organization_id,
        campaign_id=campaign.id,
        media_asset_id=asset.id,
        social_account_id=account.id,
        platform=account.platform,
        intent_id=uuid.uuid4(),
        idempotency_key=data.idempotency_key,
        status=PublicationStatus.DRAFT,
        title=(data.title or "").strip() or None,
        caption=(data.caption or "").strip() or None,
        cta=(data.cta or "").strip() or None,
        hashtags=list(data.hashtags),
        content_notes=(data.content_notes or "").strip() or None,
        scheduled_at=data.scheduled_at,
        created_by_user_id=identity.user_id,
        status_changed_at=_now(),
    )
    clash = session.scalar(
        select(PublicationJob.id).where(
            PublicationJob.campaign_id == campaign.id,
            PublicationJob.media_asset_id == asset.id,
            PublicationJob.social_account_id == account.id,
        )
    )
    if clash is not None:
        raise DuplicatePublicationError(
            "A publication for this video and account already exists in the campaign",
            details={
                "publication_id": str(clash),
                "campaign_id": str(campaign.id),
                "media_asset_id": str(asset.id),
                "social_account_id": str(account.id),
            },
        )
    session.add(job)
    try:
        with session.begin_nested():
            session.flush()
            _set_links(session, organization_id, job, data.link_ids)
    except IntegrityError as exc:
        # Lost a race with a concurrent identical request; the client should re-read.
        raise DuplicatePublicationError(
            "A publication for this video and account was created concurrently; reload",
            details={"campaign_id": str(campaign.id), "media_asset_id": str(asset.id)},
        ) from exc
    record_audit(
        session,
        organization_id=organization_id,
        identity=identity,
        action="publication.created",
        entity_type="publication_job",
        entity_id=job.id,
        data={
            "campaign_id": str(campaign.id),
            "media_asset_id": str(asset.id),
            "social_account_id": str(account.id),
        },
    )
    return get_job(session, organization_id, job.id), True


def create_jobs_batch(
    session: Session,
    organization_id: uuid.UUID,
    data: PublicationBatchCreate,
    *,
    identity: Identity,
) -> tuple[list[PublicationJob], list[PublicationJob]]:
    created: list[PublicationJob] = []
    reused: list[PublicationJob] = []
    for item in data.items:
        payload = PublicationCreate(
            campaign_id=data.campaign_id,
            media_asset_id=data.media_asset_id,
            **item.model_dump(),
        )
        job, was_created = create_job(session, organization_id, payload, identity=identity)
        (created if was_created else reused).append(job)
    return created, reused


# ====================================================================== publications: edit


def update_content(
    session: Session,
    organization_id: uuid.UUID,
    job_id: uuid.UUID,
    data: PublicationContentUpdate,
    *,
    identity: Identity,
) -> PublicationJob:
    job = _lock_job(session, organization_id, job_id, expected_version=data.version)
    if job.status not in lifecycle.EDITABLE:
        raise InvalidPublicationTransitionError(
            f"Content cannot be edited in status '{job.status.value}'",
            details={"status": job.status.value},
        )
    changes = data.model_dump(exclude_unset=True, exclude={"version"})
    link_ids = changes.pop("link_ids", None)
    for key, value in changes.items():
        setattr(job, key, (value.strip() or None) if isinstance(value, str) else value)
    if link_ids is not None:
        _set_links(session, organization_id, job, link_ids)
    job.content_version += 1
    invalidated = (
        job.status in lifecycle.APPROVAL_STATES or job.status == PublicationStatus.READY_FOR_REVIEW
    )
    previous = job.status
    if invalidated:
        _set_status(job, PublicationStatus.DRAFT)
        job.approved_snapshot_id = None
        job.approved_snapshot = None
    _bump(job)
    record_audit(
        session,
        organization_id=organization_id,
        identity=identity,
        action="publication.content_edited",
        entity_type="publication_job",
        entity_id=job.id,
        data={
            "fields": sorted(changes) + (["link_ids"] if link_ids is not None else []),
            "content_version": job.content_version,
            "approval_invalidated": invalidated and previous in lifecycle.APPROVAL_STATES,
            "from_status": previous.value,
            "to_status": job.status.value,
        },
    )
    session.flush()
    return get_job(session, organization_id, job.id)


# ====================================================================== transitions


def _transition(
    session: Session,
    organization_id: uuid.UUID,
    job: PublicationJob,
    *,
    action: str,
    identity: Identity,
    note: str | None = None,
    extra: dict[str, Any] | None = None,
    target: PublicationStatus | None = None,
) -> PublicationJob:
    computed = lifecycle.assert_transition(action, job.status)
    previous = job.status
    _set_status(job, target or computed)
    _bump(job)
    record_audit(
        session,
        organization_id=organization_id,
        identity=identity,
        action=f"publication.{action}",
        entity_type="publication_job",
        entity_id=job.id,
        data={
            "from_status": previous.value,
            "to_status": job.status.value,
            "note": note,
            **(extra or {}),
        },
    )
    session.flush()
    return job


def submit(
    session: Session,
    organization_id: uuid.UUID,
    job_id: uuid.UUID,
    *,
    version: int,
    note: str | None,
    identity: Identity,
) -> PublicationJob:
    job = _lock_job(session, organization_id, job_id, expected_version=version)
    lifecycle.assert_transition("submit", job.status)
    validation = validate_job(job)
    if not validation.ok:
        raise ContentInvalidError(
            "Fix the content before submitting", details={"errors": validation.errors}
        )
    return _transition(session, organization_id, job, action="submit", identity=identity, note=note)


def return_to_draft(
    session: Session,
    organization_id: uuid.UUID,
    job_id: uuid.UUID,
    *,
    version: int,
    note: str | None,
    identity: Identity,
) -> PublicationJob:
    job = _lock_job(session, organization_id, job_id, expected_version=version)
    return _transition(
        session, organization_id, job, action="return_to_draft", identity=identity, note=note
    )


def approve(
    session: Session,
    organization_id: uuid.UUID,
    job_id: uuid.UUID,
    *,
    version: int,
    note: str | None,
    identity: Identity,
) -> PublicationJob:
    job = _lock_job(session, organization_id, job_id, expected_version=version)
    lifecycle.assert_transition("approve", job.status)
    validation = validate_job(job)
    if not validation.ok:
        raise ContentInvalidError(
            "Content is not valid for this platform", details={"errors": validation.errors}
        )
    assert_publishable_target(session, job, step="approve")
    job.revalidated_at = None
    job.revalidated_by_label = None
    snapshot = PublicationSnapshot(
        id=uuid.uuid4(),
        job_id=job.id,
        content_version=job.content_version,
        title=job.title,
        caption=job.caption,
        cta=job.cta,
        hashtags=list(job.hashtags or []),
        links=_links_dicts(job),
        media=_media_dict(job.media_asset),
        target=_target_dict(job.social_account),
        approved_by_user_id=identity.user_id,
        approved_by_label=identity.label,
        approved_at=_now(),
    )
    session.add(snapshot)
    session.flush()
    job.approved_snapshot_id = snapshot.id
    job.approved_snapshot = snapshot
    job.snapshots.insert(0, snapshot)
    return _transition(
        session,
        organization_id,
        job,
        action="approve",
        identity=identity,
        note=note,
        extra={"snapshot_id": str(snapshot.id), "content_version": job.content_version},
    )


def revoke_approval(
    session: Session,
    organization_id: uuid.UUID,
    job_id: uuid.UUID,
    *,
    version: int,
    note: str | None,
    identity: Identity,
) -> PublicationJob:
    job = _lock_job(session, organization_id, job_id, expected_version=version)
    lifecycle.assert_transition("revoke_approval", job.status)
    job.approved_snapshot_id = None
    job.approved_snapshot = None
    job.scheduled_at = None
    return _transition(
        session, organization_id, job, action="revoke_approval", identity=identity, note=note
    )


def _assert_schedulable(when: datetime) -> None:
    if when < _now() - SCHEDULE_PAST_TOLERANCE:
        raise ScheduleInvalidError(
            "scheduled_at must be in the future", details={"scheduled_at": when.isoformat()}
        )


def find_conflicts(
    session: Session,
    organization_id: uuid.UUID,
    *,
    account_id: uuid.UUID,
    when: datetime,
    gap: timedelta,
    exclude_id: uuid.UUID | None,
) -> list[PublicationJob]:
    stmt = _job_query().where(
        PublicationJob.organization_id == organization_id,
        PublicationJob.social_account_id == account_id,
        PublicationJob.scheduled_at.is_not(None),
        PublicationJob.scheduled_at > when - gap,
        PublicationJob.scheduled_at < when + gap,
        PublicationJob.status.in_(
            [
                PublicationStatus.SCHEDULED,
                PublicationStatus.READY_TO_PUBLISH,
                PublicationStatus.PUBLISHING,
                PublicationStatus.APPROVED,
            ]
        ),
    )
    if exclude_id:
        stmt = stmt.where(PublicationJob.id != exclude_id)
    return list(session.scalars(stmt))


def schedule(
    session: Session,
    organization_id: uuid.UUID,
    job_id: uuid.UUID,
    data: ScheduleRequest,
    *,
    identity: Identity,
    min_gap: timedelta,
) -> PublicationJob:
    job = _lock_job(session, organization_id, job_id, expected_version=data.version)
    lifecycle.assert_transition("schedule", job.status)
    if (
        job.approved_snapshot is None
        or job.approved_snapshot.content_version != job.content_version
    ):
        raise InvalidPublicationTransitionError("Scheduling requires a current approved snapshot")
    assert_publishable_target(session, job, step="schedule")
    _assert_schedulable(data.scheduled_at)
    conflicts = find_conflicts(
        session,
        organization_id,
        account_id=job.social_account_id,
        when=data.scheduled_at,
        gap=min_gap,
        exclude_id=job.id,
    )
    if conflicts and not data.allow_conflict:
        raise ScheduleConflictError(
            "Another publication for this account is within "
            f"{int(min_gap.total_seconds() // 60)} minutes",
            details={
                "conflicts": [
                    {
                        "id": str(c.id),
                        "scheduled_at": c.scheduled_at.isoformat() if c.scheduled_at else None,
                        "status": c.status.value,
                    }
                    for c in conflicts
                ],
                "min_gap_minutes": int(min_gap.total_seconds() // 60),
            },
        )
    job.scheduled_at = data.scheduled_at
    return _transition(
        session,
        organization_id,
        job,
        action="schedule",
        identity=identity,
        note=data.note,
        extra={
            "scheduled_at": data.scheduled_at.isoformat(),
            "conflicts_overridden": [str(c.id) for c in conflicts],
        },
    )


def unschedule(
    session: Session,
    organization_id: uuid.UUID,
    job_id: uuid.UUID,
    *,
    version: int,
    note: str | None,
    identity: Identity,
) -> PublicationJob:
    job = _lock_job(session, organization_id, job_id, expected_version=version)
    lifecycle.assert_transition("unschedule", job.status)
    job.scheduled_at = None
    return _transition(
        session, organization_id, job, action="unschedule", identity=identity, note=note
    )


def release_due(
    session: Session, organization_id: uuid.UUID, *, identity: Identity | None
) -> list[uuid.UUID]:
    """Move SCHEDULED jobs whose time has come to READY_TO_PUBLISH. No publishing happens."""
    due = list(
        session.scalars(
            select(PublicationJob)
            .where(
                PublicationJob.organization_id == organization_id,
                PublicationJob.status == PublicationStatus.SCHEDULED,
                PublicationJob.scheduled_at <= _now(),
            )
            .with_for_update(skip_locked=True)
        )
    )
    released: list[uuid.UUID] = []
    tree = taxonomy_service.load_tree(session, organization_id)
    for job in due:
        # Re-check routing eligibility and classification before any automated step.
        full = get_job(session, organization_id, job.id)
        reason = _blocked_reason(full, tree)
        if reason is not None or _classification_stale(full):
            record_audit(
                session,
                organization_id=organization_id,
                identity=identity,
                action="publication.release_held",
                entity_type="publication_job",
                entity_id=job.id,
                data={
                    "reason": reason or "classification changed after approval",
                    "status": job.status.value,
                },
            )
            continue
        _set_status(job, PublicationStatus.READY_TO_PUBLISH)
        _bump(job)
        record_audit(
            session,
            organization_id=organization_id,
            identity=identity,
            action="publication.release",
            entity_type="publication_job",
            entity_id=job.id,
            data={"from_status": "scheduled", "to_status": "ready_to_publish"},
        )
        released.append(job.id)
    session.flush()
    return released


def start(
    session: Session,
    organization_id: uuid.UUID,
    job_id: uuid.UUID,
    *,
    version: int,
    note: str | None,
    identity: Identity,
) -> PublicationJob:
    """Begin an attempt: API-eligible targets are queued for the publisher worker (M3B.2A);
    everything else starts an assisted attempt (the adapter never publishes over the network)."""
    job = _lock_job(session, organization_id, job_id, expected_version=version)
    lifecycle.assert_transition("start", job.status)
    snapshot = job.approved_snapshot
    if snapshot is None or snapshot.content_version != job.content_version:
        raise InvalidPublicationTransitionError("Publishing requires a current approved snapshot")
    if job.social_account.status == SocialAccountStatus.DISABLED:
        raise TargetUnavailableError("The target account is disabled")
    assert_publishable_target(session, job, step="start publishing")
    settings = get_settings()
    if job_api_eligibility(job, settings).eligible:
        hold = page_hold(job.social_account)
        if hold is not None:
            raise TargetUnavailableError(
                "The target Page is on a publishing hold "
                f"({job.social_account.publish_hold_reason or 'hold'}) until {hold.isoformat()}",
                details={"hold_until": hold.isoformat()},
            )
        enqueue_api_attempt(
            session,
            job,
            identity=identity,
            note=note,
            started_by_label=identity.label,
            settings=settings,
        )
        return job
    adapter = get_adapter(job.platform)
    prepared = adapter.prepare_publication(
        content={
            "title": snapshot.title,
            "caption": snapshot.caption,
            "cta": snapshot.cta,
            "hashtags": snapshot.hashtags,
            "links": snapshot.links,
        },
        media=snapshot.media,
        target=snapshot.target,
        intent_id=str(job.intent_id),
    )
    outcome = adapter.publish(prepared, intent_id=str(job.intent_id))
    attempt = PublicationAttempt(
        id=uuid.uuid4(),
        job_id=job.id,
        attempt_no=len(job.attempts) + 1,
        mode=outcome.mode,
        adapter=adapter.name,
        status=PublicationAttemptStatus.STARTED,
        snapshot_id=snapshot.id,
        started_by_label=identity.label,
        started_at=_now(),
        detail=dict(outcome.detail),
    )
    session.add(attempt)
    job.attempts.append(attempt)
    job.last_error = None
    if outcome.status == "manual_required":
        return _transition(
            session,
            organization_id,
            job,
            action="start",
            identity=identity,
            note=note,
            extra={"attempt_no": attempt.attempt_no, "adapter": adapter.name},
        )
    # Non-assisted outcomes are reserved for M3B adapters; handle defensively without auto-retry.
    attempt.finished_at = _now()
    if outcome.status == "succeeded" and (outcome.external_post_id or outcome.external_post_url):
        attempt.status = PublicationAttemptStatus.SUCCEEDED
        job.external_post_id, job.external_post_url = (
            outcome.external_post_id,
            outcome.external_post_url,
        )
        job.evidence_kind, job.published_at = EvidenceKind.API_VERIFIED, _now()
        _transition(
            session,
            organization_id,
            job,
            action="start",
            identity=identity,
            target=PublicationStatus.PUBLISHING,
        )
        return _transition(
            session,
            organization_id,
            job,
            action="confirm",
            identity=identity,
            note=note,
            extra={"attempt_no": attempt.attempt_no},
        )
    if outcome.status == "failed":
        attempt.status, attempt.error, job.last_error = (
            PublicationAttemptStatus.FAILED,
            outcome.error,
            outcome.error,
        )
        _transition(
            session,
            organization_id,
            job,
            action="start",
            identity=identity,
            target=PublicationStatus.PUBLISHING,
        )
        return _transition(
            session, organization_id, job, action="fail", identity=identity, note=outcome.error
        )
    attempt.status = PublicationAttemptStatus.AMBIGUOUS
    _transition(
        session,
        organization_id,
        job,
        action="start",
        identity=identity,
        target=PublicationStatus.PUBLISHING,
    )
    return _transition(
        session,
        organization_id,
        job,
        action="report_uncertain",
        identity=identity,
        note=outcome.error,
    )


def prepare(
    session: Session, organization_id: uuid.UUID, job_id: uuid.UUID
) -> PreparedPublicationResponse:
    job = get_job(session, organization_id, job_id)
    snapshot = job.approved_snapshot
    if snapshot is None:
        raise InvalidPublicationTransitionError("This publication has no approved snapshot yet")
    adapter = get_adapter(job.platform)
    prepared = adapter.prepare_publication(
        content={
            "title": snapshot.title,
            "caption": snapshot.caption,
            "cta": snapshot.cta,
            "hashtags": snapshot.hashtags,
            "links": snapshot.links,
        },
        media=snapshot.media,
        target=snapshot.target,
        intent_id=str(job.intent_id),
    )
    return PreparedPublicationResponse(
        mode=prepared.mode,
        caption_text=prepared.caption_text,
        links=prepared.links,
        checklist=prepared.checklist,
        open_url=prepared.open_url,
        instructions=prepared.instructions,
        intent_id=job.intent_id,
        snapshot_id=snapshot.id,
        media_file_name=snapshot.media.get("file_name"),
        media_relative_path=snapshot.media.get("relative_path"),
    )


# ====================================================================== evidence


def _derive_post_id(platform: Platform, url: str) -> str | None:
    if platform != Platform.FACEBOOK:
        return None
    for pattern in _FB_POST_ID_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    return None


def _check_evidence(
    session: Session,
    organization_id: uuid.UUID,
    job: PublicationJob,
    *,
    url: str,
    post_id: str | None,
) -> str | None:
    from urllib.parse import urlsplit

    host = (urlsplit(url).hostname or "").lower()
    allowed = PLATFORM_HOSTS.get(job.platform, ())
    if allowed and not any(host == h or host.endswith("." + h) for h in allowed):
        raise EvidenceInvalidError(
            f"The post URL host '{host}' does not belong to {job.platform.value}",
            details={"allowed_hosts": list(allowed)},
        )
    effective_id = post_id or _derive_post_id(job.platform, url)
    normalized = normalize_url(url)
    dup_stmt = select(PublicationJob).where(
        PublicationJob.organization_id == organization_id,
        PublicationJob.id != job.id,
        PublicationJob.external_post_url.is_not(None),
    )
    for other in session.scalars(dup_stmt):
        # Platform post ids are globally unique: one post can confirm exactly one job.
        same_id = bool(effective_id) and other.external_post_id == effective_id
        same_url = other.external_post_url and normalize_url(other.external_post_url) == normalized
        if same_id or same_url:
            raise DuplicateEvidenceError(
                "This post has already been recorded as evidence for another publication",
                details={"publication_id": str(other.id)},
            )
    return effective_id


def _apply_evidence(
    job: PublicationJob,
    *,
    url: str,
    post_id: str | None,
    published_at: datetime | None,
    note: str | None,
    identity: Identity,
) -> None:
    job.external_post_url = url
    job.external_post_id = post_id
    job.evidence_kind = EvidenceKind.OPERATOR_REPORTED
    job.published_at = published_at or _now()
    job.confirmed_by_user_id = identity.user_id
    job.confirmed_by_label = identity.label
    job.confirmed_at = _now()
    job.confirmation_note = (note or "").strip() or None
    job.last_error = None


def _finish_attempt(
    job: PublicationJob,
    status: PublicationAttemptStatus,
    *,
    error: str | None = None,
    detail: dict[str, Any] | None = None,
    include_finished: bool = False,
) -> None:
    candidates = [a for a in job.attempts if include_finished or a.finished_at is None]
    if not candidates:
        return
    attempt = candidates[-1]
    attempt.status = status
    attempt.finished_at = _now()
    attempt.error = error
    if detail:
        attempt.detail = {**(attempt.detail or {}), **detail}


def confirm(
    session: Session,
    organization_id: uuid.UUID,
    job_id: uuid.UUID,
    data: EvidenceRequest,
    *,
    identity: Identity,
) -> PublicationJob:
    job = _lock_job(session, organization_id, job_id, expected_version=data.version)
    lifecycle.assert_transition("confirm", job.status)
    post_id = _check_evidence(
        session, organization_id, job, url=data.external_post_url, post_id=data.external_post_id
    )
    _apply_evidence(
        job,
        url=data.external_post_url,
        post_id=post_id,
        published_at=data.published_at,
        note=data.note,
        identity=identity,
    )
    _finish_attempt(
        job,
        PublicationAttemptStatus.SUCCEEDED,
        detail={"evidence": "operator_reported", "external_post_url": data.external_post_url},
    )
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError as exc:
        raise DuplicateEvidenceError("This post id is already recorded for this account") from exc
    return _transition(
        session,
        organization_id,
        job,
        action="confirm",
        identity=identity,
        note=data.note,
        extra={
            "evidence_kind": "operator_reported",
            "external_post_url": data.external_post_url,
            "external_post_id": post_id,
        },
    )


def fail(
    session: Session,
    organization_id: uuid.UUID,
    job_id: uuid.UUID,
    data: FailureRequest,
    *,
    identity: Identity,
) -> PublicationJob:
    job = _lock_job(session, organization_id, job_id, expected_version=data.version)
    if data.outcome == "ambiguous":
        lifecycle.assert_transition("report_uncertain", job.status)
        _finish_attempt(job, PublicationAttemptStatus.AMBIGUOUS, error=data.reason)
        job.last_error = data.reason
        return _transition(
            session,
            organization_id,
            job,
            action="report_uncertain",
            identity=identity,
            note=data.note,
            extra={"reason": data.reason},
        )
    lifecycle.assert_transition("fail", job.status)
    _finish_attempt(job, PublicationAttemptStatus.FAILED, error=data.reason)
    job.last_error = data.reason
    return _transition(
        session,
        organization_id,
        job,
        action="fail",
        identity=identity,
        note=data.note,
        extra={"reason": data.reason},
    )


def retry(
    session: Session,
    organization_id: uuid.UUID,
    job_id: uuid.UUID,
    *,
    version: int,
    note: str | None,
    identity: Identity,
) -> PublicationJob:
    """Explicit operator retry after a FAILED attempt (never automatic)."""
    job = _lock_job(session, organization_id, job_id, expected_version=version)
    lifecycle.assert_transition("retry", job.status)
    if (
        job.approved_snapshot is None
        or job.approved_snapshot.content_version != job.content_version
    ):
        raise InvalidPublicationTransitionError(
            "Retry requires a current approved snapshot; revise instead"
        )
    assert_publishable_target(session, job, step="retry")
    return _transition(session, organization_id, job, action="retry", identity=identity, note=note)


def revalidate(
    session: Session,
    organization_id: uuid.UUID,
    job_id: uuid.UUID,
    *,
    version: int,
    note: str | None,
    identity: Identity,
) -> PublicationJob:
    """Admin confirms the approved snapshot still applies after the video was reclassified.
    The snapshot is untouched; only the revalidation marker moves. Blocked targets cannot be
    revalidated (change the channel rules or cancel instead)."""
    job = _lock_job(session, organization_id, job_id, expected_version=version)
    lifecycle.assert_transition("revalidate", job.status)
    if job.approved_snapshot is None:
        raise InvalidPublicationTransitionError("Nothing to revalidate: no approved snapshot")
    tree = taxonomy_service.load_tree(session, organization_id)
    reason = _blocked_reason(job, tree)
    if reason is not None:
        raise TargetBlockedError(
            f"Cannot revalidate: {reason}", details={"blocking_reason": reason}
        )
    klass = classification.classification_of(job.media_asset)
    job.revalidated_at = _now()
    job.revalidated_by_label = identity.label
    return _transition(
        session,
        organization_id,
        job,
        action="revalidate",
        identity=identity,
        note=note,
        target=job.status,
        extra={
            "snapshot_id": str(job.approved_snapshot_id),
            "current_primary_category_id": str(klass.primary.category_id)
            if klass.primary
            else None,
        },
    )


def revise(
    session: Session,
    organization_id: uuid.UUID,
    job_id: uuid.UUID,
    *,
    version: int,
    note: str | None,
    identity: Identity,
) -> PublicationJob:
    job = _lock_job(session, organization_id, job_id, expected_version=version)
    lifecycle.assert_transition("revise", job.status)
    job.approved_snapshot_id = None
    job.approved_snapshot = None
    job.scheduled_at = None
    return _transition(session, organization_id, job, action="revise", identity=identity, note=note)


def reconcile(
    session: Session,
    organization_id: uuid.UUID,
    job_id: uuid.UUID,
    data: ReconcileRequest,
    *,
    identity: Identity,
) -> PublicationJob:
    job = _lock_job(session, organization_id, job_id, expected_version=data.version)
    lifecycle.assert_transition("reconcile", job.status)
    adapter = get_adapter(job.platform)
    evidence = {
        "external_post_url": data.external_post_url,
        "external_post_id": data.external_post_id,
        "not_published": data.outcome == "not_published",
        "note": data.note,
    }
    outcome = adapter.reconcile_publication(
        intent_id=str(job.intent_id), operator_evidence=evidence
    )
    if data.outcome == "published":
        if outcome.status != "published" or not data.external_post_url:
            raise EvidenceInvalidError("Evidence is required to reconcile as published")
        post_id = _check_evidence(
            session, organization_id, job, url=data.external_post_url, post_id=data.external_post_id
        )
        _apply_evidence(
            job,
            url=data.external_post_url,
            post_id=post_id,
            published_at=data.published_at,
            note=data.note,
            identity=identity,
        )
        _finish_attempt(
            job,
            PublicationAttemptStatus.SUCCEEDED,
            detail={"reconciled": True, "evidence": "operator_reported"},
            include_finished=True,
        )
        try:
            with session.begin_nested():
                session.flush()
        except IntegrityError as exc:
            raise DuplicateEvidenceError(
                "This post id is already recorded for this account"
            ) from exc
        return _transition(
            session,
            organization_id,
            job,
            action="reconcile",
            identity=identity,
            note=data.note,
            target=PublicationStatus.PUBLISHED,
            extra={"outcome": "published", "external_post_url": data.external_post_url},
        )
    if data.outcome == "not_published":
        _finish_attempt(
            job, PublicationAttemptStatus.FAILED, error=data.note, include_finished=True
        )
        job.last_error = data.note
        return _transition(
            session,
            organization_id,
            job,
            action="reconcile",
            identity=identity,
            note=data.note,
            target=PublicationStatus.FAILED,
            extra={"outcome": "not_published"},
        )
    # still_unknown: record the check, stay put, bump the version so the UI refreshes.
    _bump(job)
    record_audit(
        session,
        organization_id=organization_id,
        identity=identity,
        action="publication.reconcile",
        entity_type="publication_job",
        entity_id=job.id,
        data={
            "outcome": "still_unknown",
            "note": data.note,
            "from_status": job.status.value,
            "to_status": job.status.value,
        },
    )
    session.flush()
    return job


def cancel(
    session: Session,
    organization_id: uuid.UUID,
    job_id: uuid.UUID,
    *,
    version: int,
    note: str | None,
    identity: Identity,
) -> PublicationJob:
    job = _lock_job(session, organization_id, job_id, expected_version=version)
    lifecycle.assert_transition("cancel", job.status)
    job.scheduled_at = None
    return _transition(session, organization_id, job, action="cancel", identity=identity, note=note)


# ====================================================================== publishing engine (M3B.2A)


def enqueue_api_attempt(
    session: Session,
    job: PublicationJob,
    *,
    identity: Identity | None,
    note: str | None,
    started_by_label: str,
    settings: Settings,
) -> PublicationUpload:
    """Create the attempt + durable upload row and move the job to PUBLISHING.

    Callers hold the row lock and have verified: transition allowed, snapshot current, target
    eligible. No network I/O happens here; the publisher worker drives the upload.
    """
    snapshot = job.approved_snapshot
    assert snapshot is not None  # noqa: S101 - caller verified
    live = [u for u in job.uploads if u.is_live]
    if live:
        raise InvalidPublicationTransitionError(
            "An upload is already in progress for this publication",
            details={"upload_id": str(live[0].id)},
        )
    attempt = PublicationAttempt(
        id=uuid.uuid4(),
        job_id=job.id,
        attempt_no=len(job.attempts) + 1,
        mode=PublishMode.API,
        adapter="facebook_reels",
        status=PublicationAttemptStatus.STARTED,
        snapshot_id=snapshot.id,
        started_by_label=started_by_label[:255],
        started_at=_now(),
        detail={"engine": "publisher", "mock": job.social_account.is_mock},
    )
    session.add(attempt)
    job.attempts.append(attempt)
    session.flush()
    upload = PublicationUpload(
        id=uuid.uuid4(),
        organization_id=job.organization_id,
        job_id=job.id,
        attempt_id=attempt.id,
        social_account_id=job.social_account_id,
        media_asset_id=job.media_asset_id,
        snapshot_id=snapshot.id,
        provider="meta",
        is_mock=job.social_account.is_mock,
        phase=UploadPhase.PENDING,
        publish_request_status=PublishRequestStatus.NOT_SENT,
        max_retries=settings.publisher_max_retries,
        next_action_at=_now(),
    )
    session.add(upload)
    job.uploads.append(upload)
    job.last_error = None
    attempt.detail = {**attempt.detail, "upload_id": str(upload.id)}
    _transition(
        session,
        job.organization_id,
        job,
        action="start",
        identity=identity,
        note=note,
        extra={"attempt_no": attempt.attempt_no, "adapter": "facebook_reels", "mode": "api"},
    )
    return upload


def reconcile_with_provider(
    session: Session,
    organization_id: uuid.UUID,
    job_id: uuid.UUID,
    *,
    version: int,
    identity: Identity,
    settings: Settings,
) -> PublicationJob:
    """Admin: ask the provider what happened to a parked (NEEDS_RECONCILIATION) upload."""
    from affilio.modules.publishing import engine

    job = _lock_job(session, organization_id, job_id, expected_version=version)
    lifecycle.assert_transition("reconcile", job.status)
    upload = next((u for u in job.uploads if u.phase == UploadPhase.NEEDS_RECONCILIATION), None)
    if upload is None:
        raise InvalidPublicationTransitionError(
            "No API attempt is waiting for provider reconciliation; use manual reconcile"
        )
    engine.reconcile_upload(session, settings, upload, identity=identity)
    _bump(job)
    session.flush()
    return get_job(session, organization_id, job_id)


def engine_status(session: Session, organization_id: uuid.UUID, settings: Settings) -> EngineStatus:
    from affilio.modules.publishing.models import PublisherWorker

    now = _now()
    fresh_cutoff = now - timedelta(seconds=120)
    workers = [
        WorkerStatus(
            worker_id=w.worker_id,
            hostname=w.hostname,
            pid=w.pid,
            version=w.version,
            max_concurrency=w.max_concurrency,
            in_flight=w.in_flight,
            started_at=w.started_at,
            last_seen_at=w.last_seen_at,
            healthy=w.stopped_at is None and w.last_seen_at >= fresh_cutoff,
        )
        for w in session.scalars(
            select(PublisherWorker)
            .where(PublisherWorker.stopped_at.is_(None))
            .order_by(PublisherWorker.last_seen_at.desc())
        )
    ]
    counts: dict[str, int] = {phase.value: 0 for phase in UploadPhase}
    for phase, count in session.execute(
        select(PublicationUpload.phase, func.count())
        .where(PublicationUpload.organization_id == organization_id)
        .group_by(PublicationUpload.phase)
    ):
        counts[phase.value if hasattr(phase, "value") else str(phase)] = int(count)
    pages: list[PagePublishingStatus] = []
    accounts = session.scalars(
        select(SocialAccount).where(
            SocialAccount.organization_id == organization_id,
            SocialAccount.platform == Platform.FACEBOOK,
        )
    )
    for account in accounts:
        if not (account.source == SocialAccountSource.OAUTH or account.is_connected):
            continue
        live = session.scalar(
            select(func.count())
            .select_from(PublicationUpload)
            .where(
                PublicationUpload.social_account_id == account.id,
                PublicationUpload.phase.in_(list(UPLOAD_ACTIVE_PHASES)),
            )
        )
        parked = session.scalar(
            select(func.count())
            .select_from(PublicationUpload)
            .where(
                PublicationUpload.social_account_id == account.id,
                PublicationUpload.phase == UploadPhase.NEEDS_RECONCILIATION,
            )
        )
        published_24h = session.scalar(
            select(func.count())
            .select_from(PublicationUpload)
            .where(
                PublicationUpload.social_account_id == account.id,
                PublicationUpload.phase == UploadPhase.PUBLISHED,
                PublicationUpload.published_at >= now - timedelta(hours=24),
            )
        )
        eligibility = account_api_eligibility(account, settings)
        pages.append(
            PagePublishingStatus(
                social_account_id=account.id,
                display_name=account.display_name,
                is_mock=account.is_mock,
                connected=account.is_connected,
                api_eligible=eligibility.eligible,
                api_eligibility_reason=eligibility.reason,
                live_uploads=int(live or 0),
                needs_reconciliation=int(parked or 0),
                published_last_24h=int(published_24h or 0),
                daily_quota=settings.meta_reels_daily_quota,
                hold_until=page_hold(account, now),
                hold_reason=account.publish_hold_reason if page_hold(account, now) else None,
            )
        )
    return EngineStatus(
        mode=settings.meta_provider_mode,
        live_publishing_enabled=settings.meta_live_publishing_enabled,
        workers=workers,
        healthy_workers=sum(1 for w in workers if w.healthy),
        phase_counts=counts,
        pages=pages,
        generated_at=now,
    )
