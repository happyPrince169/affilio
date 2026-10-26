"""Adapter contract.

An adapter encapsulates everything platform-specific about getting a publication out: what the
platform can do, what content/media it accepts, how to prepare a publication, how to publish,
how to read status back, and how to reconcile an uncertain outcome. The service layer owns the
state machine; adapters never change job status themselves.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from affilio.core.enums import Platform, PublishMode


class AdapterError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class PlatformCapabilities:
    """Explicit, honest capability facts. Nothing here implies an authorized integration."""

    platform: Platform
    assisted_publishing: bool = True
    api_publishing: bool = False
    scheduling_via_api: bool = False
    status_lookup_via_api: bool = False
    clickable_links_in_caption: bool = False
    max_caption_length: int | None = None
    max_hashtags: int | None = None
    vertical_video: bool = True
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform.value,
            "assisted_publishing": self.assisted_publishing,
            "api_publishing": self.api_publishing,
            "scheduling_via_api": self.scheduling_via_api,
            "status_lookup_via_api": self.status_lookup_via_api,
            "clickable_links_in_caption": self.clickable_links_in_caption,
            "max_caption_length": self.max_caption_length,
            "max_hashtags": self.max_hashtags,
            "vertical_video": self.vertical_video,
            "notes": list(self.notes),
        }


@dataclass(slots=True)
class ValidationResult:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def error(self, message: str) -> None:
        self.ok = False
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def merge(self, other: ValidationResult) -> ValidationResult:
        self.ok = self.ok and other.ok
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        return self


@dataclass(slots=True)
class PreparedPublication:
    """Everything an operator (or an API call) needs to publish exactly the approved content."""

    mode: PublishMode
    caption_text: str
    links: list[dict[str, Any]]
    checklist: list[dict[str, Any]]
    open_url: str | None = None
    instructions: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PublishOutcome:
    """Result of `publish`. `manual_required` means the operator must finish the job by hand."""

    mode: PublishMode
    status: Literal["manual_required", "succeeded", "failed", "ambiguous"]
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    external_post_id: str | None = None
    external_post_url: str | None = None


@dataclass(slots=True)
class StatusOutcome:
    status: Literal["unknown", "published", "not_found", "processing", "failed"]
    detail: dict[str, Any] = field(default_factory=dict)
    verifiable: bool = False  # True only when the platform API confirmed it


@dataclass(slots=True)
class ReconcileOutcome:
    """What reconciliation determined. `evidence_kind` says how trustworthy the evidence is."""

    status: Literal["published", "not_published", "still_unknown"]
    evidence_kind: Literal["operator_reported", "api_verified"] | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class BasePublishingAdapter(ABC):
    name: str = "base"
    platform: Platform

    @abstractmethod
    def capabilities(self) -> PlatformCapabilities: ...

    @abstractmethod
    def validate_media(self, media: dict[str, Any]) -> ValidationResult:
        """`media` is the snapshot-ready media dict (asset kind, handoff status, dimensions…)."""

    @abstractmethod
    def validate_content(self, content: dict[str, Any]) -> ValidationResult:
        """`content` has title, caption, cta, hashtags, links."""

    def check_capabilities(self, required: set[str]) -> ValidationResult:
        caps = self.capabilities().as_dict()
        result = ValidationResult()
        for name in sorted(required):
            if not caps.get(name):
                result.error(f"{self.platform.value} adapter does not support '{name}'")
        return result

    @abstractmethod
    def prepare_publication(
        self,
        *,
        content: dict[str, Any],
        media: dict[str, Any],
        target: dict[str, Any],
        intent_id: str,
    ) -> PreparedPublication: ...

    @abstractmethod
    def publish(self, prepared: PreparedPublication, *, intent_id: str) -> PublishOutcome: ...

    @abstractmethod
    def get_publication_status(
        self, *, intent_id: str, external_post_id: str | None
    ) -> StatusOutcome: ...

    @abstractmethod
    def reconcile_publication(
        self, *, intent_id: str, operator_evidence: dict[str, Any] | None
    ) -> ReconcileOutcome: ...
