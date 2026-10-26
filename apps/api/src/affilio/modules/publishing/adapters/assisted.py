"""Platform-neutral assisted adapter: validates, builds a checklist, and leaves the actual
publishing to the operator. It never performs network I/O."""

from __future__ import annotations

from typing import Any

from affilio.core.enums import Platform, PublishMode
from affilio.modules.publishing.adapters.base import (
    BasePublishingAdapter,
    PlatformCapabilities,
    PreparedPublication,
    PublishOutcome,
    ReconcileOutcome,
    StatusOutcome,
    ValidationResult,
)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}


def compose_caption(content: dict[str, Any], *, include_links: bool) -> str:
    parts: list[str] = []
    if content.get("caption"):
        parts.append(str(content["caption"]).strip())
    if content.get("cta"):
        parts.append(str(content["cta"]).strip())
    if include_links:
        for link in content.get("links") or []:
            label = link.get("label")
            url = link.get("url")
            if url:
                parts.append(f"{label}: {url}" if label else url)
    tags = [t for t in (content.get("hashtags") or []) if t]
    if tags:
        parts.append(" ".join(f"#{t.lstrip('#')}" for t in tags))
    return "\n\n".join(p for p in parts if p)


class AssistedPublishingAdapter(BasePublishingAdapter):
    name = "assisted"

    def __init__(self, platform: Platform) -> None:
        self.platform = platform

    def capabilities(self) -> PlatformCapabilities:
        return PlatformCapabilities(
            platform=self.platform,
            assisted_publishing=True,
            api_publishing=False,
            notes=("Operator publishes manually following the checklist.",),
        )

    # ------------------------------------------------------------- validation
    def validate_media(self, media: dict[str, Any]) -> ValidationResult:
        result = ValidationResult()
        if media.get("asset_kind") != "final":
            result.error("Only final videos can be published")
        if media.get("handoff_status") != "approved":
            result.error("The final video has not been approved in the handoff review")
        if media.get("status") != "active":
            result.error("The media file is missing or unsupported")
        ext = str(media.get("extension") or "").lower()
        if ext and ext not in VIDEO_EXTENSIONS:
            result.warn(f"Container {ext} may need re-export before upload")
        width, height = media.get("width"), media.get("height")
        if width and height and width > height:
            result.warn("Video is horizontal; short-form placements favour vertical 9:16")
        return result

    def validate_content(self, content: dict[str, Any]) -> ValidationResult:
        result = ValidationResult()
        caps = self.capabilities()
        caption = (content.get("caption") or "").strip()
        if not caption:
            result.error("A caption is required")
        if (
            caps.max_caption_length
            and len(compose_caption(content, include_links=True)) > caps.max_caption_length
        ):
            result.error(f"Composed caption exceeds {caps.max_caption_length} characters")
        tags = content.get("hashtags") or []
        if caps.max_hashtags and len(tags) > caps.max_hashtags:
            result.error(f"More than {caps.max_hashtags} hashtags")
        for link in content.get("links") or []:
            url = str(link.get("url") or "")
            if not url.lower().startswith(("http://", "https://")):
                result.error(f"Link '{link.get('label')}' is not an http(s) URL")
        if content.get("links") and not caps.clickable_links_in_caption:
            result.warn(
                "Links in captions are not clickable on this platform; "
                "add them to the first comment or bio as well"
            )
        return result

    # ------------------------------------------------------------- operations
    def prepare_publication(
        self,
        *,
        content: dict[str, Any],
        media: dict[str, Any],
        target: dict[str, Any],
        intent_id: str,
    ) -> PreparedPublication:
        caption_text = compose_caption(content, include_links=True)
        checklist = [
            {
                "key": "open_target",
                "label": f"Open {target.get('display_name') or 'the target account'}",
                "done": False,
            },
            {
                "key": "upload_media",
                "label": f"Upload {media.get('file_name') or 'the approved final video'}",
                "done": False,
            },
            {
                "key": "paste_caption",
                "label": "Paste the approved caption exactly as shown",
                "done": False,
            },
            {
                "key": "add_links",
                "label": "Add the affiliate links where the platform allows them",
                "done": False,
            },
            {"key": "publish", "label": "Publish / schedule in the platform UI", "done": False},
            {
                "key": "record_evidence",
                "label": "Copy the post URL back into AFFILIO and confirm",
                "done": False,
            },
        ]
        return PreparedPublication(
            mode=PublishMode.ASSISTED,
            caption_text=caption_text,
            links=list(content.get("links") or []),
            checklist=checklist,
            open_url=target.get("open_url"),
            instructions=[
                "Publish only the content shown here; it is the approved snapshot.",
                "Do not retry a publish you are unsure about—report it as uncertain instead.",
            ],
            extra={"intent_id": intent_id},
        )

    def publish(self, prepared: PreparedPublication, *, intent_id: str) -> PublishOutcome:
        # No network: the operator must complete publishing manually.
        return PublishOutcome(
            mode=PublishMode.ASSISTED,
            status="manual_required",
            detail={"intent_id": intent_id, "checklist": prepared.checklist},
        )

    def get_publication_status(
        self, *, intent_id: str, external_post_id: str | None
    ) -> StatusOutcome:
        return StatusOutcome(
            status="unknown", detail={"reason": "assisted mode has no API status"}, verifiable=False
        )

    def reconcile_publication(
        self, *, intent_id: str, operator_evidence: dict[str, Any] | None
    ) -> ReconcileOutcome:
        if operator_evidence and (
            operator_evidence.get("external_post_url") or operator_evidence.get("external_post_id")
        ):
            return ReconcileOutcome(
                status="published", evidence_kind="operator_reported", detail=operator_evidence
            )
        if operator_evidence and operator_evidence.get("not_published"):
            return ReconcileOutcome(
                status="not_published", evidence_kind="operator_reported", detail=operator_evidence
            )
        return ReconcileOutcome(status="still_unknown")
