"""Recommendation schemas. Mirrored in packages/shared-types/src/routing.ts."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict

from affilio.core.enums import Platform
from affilio.modules.taxonomy.schemas import CategoryRef


class RoutingModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class ChannelRecommendation(RoutingModel):
    social_account_id: uuid.UUID
    display_name: str
    platform: Platform
    connection_label: str
    account_status: str
    eligible: bool
    recommended: bool
    score: int
    reasons: list[str]
    blocking_reason: str | None
    matched_categories: list[CategoryRef]
    already_targeted: bool  # a non-cancelled publication already exists in the chosen campaign


class RecommendationsResponse(RoutingModel):
    media_asset_id: uuid.UUID
    media_title: str
    media_approved: bool
    folder_path: str | None
    primary_category: CategoryRef | None
    secondary_categories: list[CategoryRef]
    engine_version: str
    recommend_threshold: int
    recommended: list[ChannelRecommendation]
    low_relevance: list[ChannelRecommendation]
    blocked: list[ChannelRecommendation]
