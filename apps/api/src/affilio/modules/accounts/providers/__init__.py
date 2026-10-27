"""Meta provider adapters. `get_provider(settings)` is the only factory."""

from __future__ import annotations

from affilio.core.config import Settings
from affilio.modules.accounts.providers.base import MetaProvider


def get_provider(settings: Settings) -> MetaProvider:
    """Mock in mock mode (development/test only, enforced by settings); live otherwise.

    The live provider raises `ProviderNotConfiguredError` when the Meta app settings are
    missing, which the connect endpoint reports as 503 `meta_not_configured`.
    """
    if settings.meta_provider_mode == "mock":
        from affilio.modules.accounts.providers.mock import MockMetaProvider

        return MockMetaProvider(settings)
    from affilio.modules.accounts.providers.live import LiveMetaProvider

    return LiveMetaProvider(settings)


__all__ = ["MetaProvider", "get_provider"]
