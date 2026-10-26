"""Builds recommendation responses from media classification, accounts and publications."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from affilio.core.enums import HandoffStatus, MediaAssetKind, PublicationStatus
from affilio.modules.accounts import routing_engine as engine
from affilio.modules.accounts import service as accounts_service
from affilio.modules.media import classification
from affilio.modules.media import service as media_service
from affilio.modules.publishing.models import PublicationJob
from affilio.modules.routing.schemas import ChannelRecommendation, RecommendationsResponse
from affilio.modules.taxonomy import service as taxonomy_service


def recommend_for_asset(
    session: Session,
    organization_id: uuid.UUID,
    asset_id: uuid.UUID,
    *,
    campaign_id: uuid.UUID | None = None,
) -> RecommendationsResponse:
    asset = media_service.get_asset(session, organization_id, asset_id)
    tree = taxonomy_service.load_tree(session, organization_id)
    klass = classification.classification_of(asset)
    primary_id = klass.primary.category_id if klass.primary else None
    secondary_ids = [a.category_id for a in klass.secondary]
    accounts = accounts_service.list_accounts_with_routing(session, organization_id)
    results = engine.rank(accounts, primary_id=primary_id, secondary_ids=secondary_ids, tree=tree)
    by_account = {a.id: a for a in accounts}

    targeted: set[uuid.UUID] = set()
    if campaign_id is not None:
        targeted = set(
            session.scalars(
                select(PublicationJob.social_account_id).where(
                    PublicationJob.organization_id == organization_id,
                    PublicationJob.campaign_id == campaign_id,
                    PublicationJob.media_asset_id == asset.id,
                    PublicationJob.status != PublicationStatus.CANCELLED,
                )
            )
        )

    def to_item(rec: engine.Recommendation) -> ChannelRecommendation:
        account = by_account[rec.account_id]
        return ChannelRecommendation(
            social_account_id=account.id,
            display_name=account.display_name,
            platform=account.platform,
            connection_label=account.connection_label,
            account_status=account.status.value,
            eligible=rec.eligible,
            recommended=rec.recommended,
            score=rec.score,
            reasons=rec.reasons,
            blocking_reason=rec.blocking_reason,
            matched_categories=[tree.ref(c) for c in rec.matched_category_ids if c in tree.by_id],
            already_targeted=account.id in targeted,
        )

    items = [to_item(r) for r in results]
    main = asset.main_variant()
    return RecommendationsResponse(
        media_asset_id=asset.id,
        media_title=asset.title,
        media_approved=asset.asset_kind == MediaAssetKind.FINAL
        and asset.handoff_status == HandoffStatus.APPROVED,
        folder_path=classification.folder_of(main.relative_path) if main else None,
        primary_category=tree.ref(primary_id) if primary_id and primary_id in tree.by_id else None,
        secondary_categories=[tree.ref(c) for c in secondary_ids if c in tree.by_id],
        engine_version=engine.ENGINE_VERSION,
        recommend_threshold=engine.RECOMMEND_THRESHOLD,
        recommended=[i for i in items if i.eligible and i.recommended],
        low_relevance=[i for i in items if i.eligible and not i.recommended],
        blocked=[i for i in items if not i.eligible],
    )
