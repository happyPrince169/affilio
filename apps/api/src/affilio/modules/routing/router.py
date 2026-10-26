"""Channel recommendations (M3A.1). Read-only; drafts are created through /publishing."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from affilio.api.deps import CurrentIdentity, DbSession
from affilio.modules.routing import service
from affilio.modules.routing.schemas import RecommendationsResponse

router = APIRouter(prefix="/distribution", tags=["routing"])


@router.get(
    "/recommendations",
    response_model=RecommendationsResponse,
    summary="Rule-based channel recommendations for a final video",
)
def recommendations(
    media_asset_id: uuid.UUID,
    identity: CurrentIdentity,
    session: DbSession,
    campaign_id: uuid.UUID | None = None,
) -> RecommendationsResponse:
    return service.recommend_for_asset(
        session, identity.organization.id, media_asset_id, campaign_id=campaign_id
    )
