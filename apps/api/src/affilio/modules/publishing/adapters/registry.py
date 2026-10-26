"""Adapter lookup by platform. Tests register fakes through `register_adapter`."""

from __future__ import annotations

from collections.abc import Callable

from affilio.core.enums import Platform
from affilio.modules.publishing.adapters.assisted import AssistedPublishingAdapter
from affilio.modules.publishing.adapters.base import BasePublishingAdapter
from affilio.modules.publishing.adapters.facebook import FacebookReelsAdapter

AdapterFactory = Callable[[], BasePublishingAdapter]

_REGISTRY: dict[Platform, AdapterFactory] = {Platform.FACEBOOK: FacebookReelsAdapter}


def register_adapter(platform: Platform, factory: AdapterFactory | None) -> None:
    if factory is None:
        _REGISTRY.pop(platform, None)
        if platform == Platform.FACEBOOK:
            _REGISTRY[platform] = FacebookReelsAdapter
    else:
        _REGISTRY[platform] = factory


def get_adapter(platform: Platform) -> BasePublishingAdapter:
    factory = _REGISTRY.get(platform)
    if factory is None:
        return AssistedPublishingAdapter(platform)
    return factory()
