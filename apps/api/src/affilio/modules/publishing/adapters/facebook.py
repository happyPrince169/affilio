"""Facebook Reels adapter placeholder (M3A).

Declares what we know about Facebook Reels today and inherits assisted behaviour. It does NOT
call the Graph API. M3B will add OAuth, token storage, upload and status lookup behind the same
contract once Page authorization exists.
"""

from __future__ import annotations

from typing import Any

from affilio.core.enums import Platform
from affilio.modules.publishing.adapters.assisted import AssistedPublishingAdapter
from affilio.modules.publishing.adapters.base import (
    PlatformCapabilities,
    PreparedPublication,
    ValidationResult,
)

FACEBOOK_REELS_MAX_CAPTION = 2200
FACEBOOK_REELS_MAX_HASHTAGS = 30


class FacebookReelsAdapter(AssistedPublishingAdapter):
    name = "facebook_reels"

    def __init__(self) -> None:
        super().__init__(Platform.FACEBOOK)

    def capabilities(self) -> PlatformCapabilities:
        return PlatformCapabilities(
            platform=Platform.FACEBOOK,
            assisted_publishing=True,
            api_publishing=False,  # requires Page OAuth + review (M3B)
            scheduling_via_api=False,
            status_lookup_via_api=False,
            clickable_links_in_caption=False,
            max_caption_length=FACEBOOK_REELS_MAX_CAPTION,
            max_hashtags=FACEBOOK_REELS_MAX_HASHTAGS,
            vertical_video=True,
            notes=(
                "Reels captions do not render links as clickable; put links in the first comment.",
                "API publishing, scheduling and status lookup arrive with Page authorization "
                "(M3B).",
            ),
        )

    def validate_media(self, media: dict[str, Any]) -> ValidationResult:
        result = super().validate_media(media)
        duration = media.get("duration_seconds")
        if duration and duration > 90:
            result.warn("Reels longer than 90 s may be rejected or trimmed by Facebook")
        return result

    def prepare_publication(
        self,
        *,
        content: dict[str, Any],
        media: dict[str, Any],
        target: dict[str, Any],
        intent_id: str,
    ) -> PreparedPublication:
        prepared = super().prepare_publication(
            content=content, media=media, target=target, intent_id=intent_id
        )
        prepared.instructions.insert(
            0,
            "In the Page composer choose Reel, upload the file, paste the caption, "
            "then publish or schedule.",
        )
        prepared.instructions.append(
            "Add the affiliate links as the first comment; captions are not clickable."
        )
        return prepared
