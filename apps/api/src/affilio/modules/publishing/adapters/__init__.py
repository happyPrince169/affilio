"""Publishing adapters. See docs/architecture/distribution.md#adapter-interface."""

from affilio.modules.publishing.adapters.base import (
    AdapterError,
    BasePublishingAdapter,
    PlatformCapabilities,
    PreparedPublication,
    PublishOutcome,
    ReconcileOutcome,
    StatusOutcome,
    ValidationResult,
)
from affilio.modules.publishing.adapters.registry import get_adapter, register_adapter

__all__ = [
    "AdapterError",
    "BasePublishingAdapter",
    "PlatformCapabilities",
    "PreparedPublication",
    "PublishOutcome",
    "ReconcileOutcome",
    "StatusOutcome",
    "ValidationResult",
    "get_adapter",
    "register_adapter",
]
