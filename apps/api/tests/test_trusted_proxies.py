"""M3.2-B.2 P4B: X-Forwarded-* is believed only from explicitly trusted proxies.

Covers the settings parser/guard and the identity router's client-address derivation. No DB.
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from affilio.core.config import Settings
from affilio.modules.identity.router import _client_ip


def _request(peer: str | None, forwarded: str | None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/auth/login",
        "headers": headers,
        "client": (peer, 1234) if peer else None,
    }
    return Request(scope)


def test_default_trusts_loopback_only() -> None:
    settings = Settings(app_env="test")
    assert settings.trusted_proxy_ips == "127.0.0.1"
    assert settings.is_trusted_proxy("127.0.0.1")
    assert not settings.is_trusted_proxy("172.18.0.5")
    assert not settings.is_trusted_proxy(None)
    assert not settings.is_trusted_proxy("not-an-ip")


def test_networks_and_hosts_parse_and_match() -> None:
    settings = Settings(app_env="test", trusted_proxy_ips="172.18.0.0/16, 10.0.0.9")
    assert settings.is_trusted_proxy("172.18.3.4")
    assert settings.is_trusted_proxy("10.0.0.9")
    assert not settings.is_trusted_proxy("10.0.0.10")
    assert not settings.is_trusted_proxy("172.19.0.2")  # another compose network


def test_wildcard_is_refused_outside_development() -> None:
    with pytest.raises(ValueError, match="TRUSTED_PROXY_IPS"):
        Settings(app_env="production", trusted_proxy_ips="*", meta_provider_mode="live")
    assert Settings(app_env="test", trusted_proxy_ips="*").is_trusted_proxy("203.0.113.9")


def test_garbage_is_refused_at_startup() -> None:
    with pytest.raises(ValueError, match="comma-separated IPs or CIDR"):
        Settings(app_env="test", trusted_proxy_ips="172.18.0.0/16,web")


def test_forwarded_for_is_used_only_from_a_trusted_peer() -> None:
    settings = Settings(app_env="test", trust_proxy_headers=True, trusted_proxy_ips="172.18.0.0/16")
    # The web proxy (trusted) forwards the browser's address.
    assert _client_ip(_request("172.18.0.3", "203.0.113.7, 172.18.0.3"), settings) == "203.0.113.7"
    # A peer on another network (the MeoScout bridge) cannot choose the recorded address.
    assert _client_ip(_request("172.30.0.4", "203.0.113.7"), settings) == "172.30.0.4"
    # Trust disabled: the header is ignored even from the proxy network.
    off = Settings(app_env="test", trust_proxy_headers=False, trusted_proxy_ips="172.18.0.0/16")
    assert _client_ip(_request("172.18.0.3", "203.0.113.7"), off) == "172.18.0.3"
    assert _client_ip(_request(None, "203.0.113.7"), settings) == "unknown"
