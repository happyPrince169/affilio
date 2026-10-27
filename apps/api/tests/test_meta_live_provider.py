"""Live Meta provider against a fake Graph API (httpx.MockTransport). No network, no DB."""

from __future__ import annotations

import json
import logging
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from affilio.core.config import Settings
from affilio.modules.accounts.providers.base import (
    MockCredentialRejectedError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderUnavailableError,
    TokenInvalidError,
)
from affilio.modules.accounts.providers.live import LiveMetaProvider

SETTINGS = Settings(
    meta_app_id="123456",
    meta_app_secret="shh-app-secret",
    meta_oauth_redirect_uri="https://affilio.test/api/v1/accounts/connect/facebook/callback",
    meta_graph_api_version="v26.0",
)


def _json(payload, status=200):
    return httpx.Response(status, json=payload)


class FakeGraph:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.fail_with: dict | None = None
        self.status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = {k: v[0] for k, v in parse_qs(urlsplit(str(request.url)).query).items()}
        self.calls.append((request.method, request.url.path, params))
        if self.status >= 500:
            return httpx.Response(self.status, text="upstream broke")
        if self.fail_with:
            return _json({"error": self.fail_with}, 400)
        path = request.url.path
        if path.endswith("/oauth/access_token"):
            if params.get("grant_type") == "fb_exchange_token":
                return _json(
                    {"access_token": "EAAlong", "token_type": "bearer", "expires_in": 5183944}
                )
            return _json({"access_token": "EAAshort", "token_type": "bearer", "expires_in": 5000})
        if path.endswith("/debug_token"):
            return _json(
                {
                    "data": {
                        "app_id": "123456",
                        "user_id": "9001",
                        "is_valid": True,
                        "scopes": [
                            "pages_show_list",
                            "pages_manage_posts",
                            "pages_read_engagement",
                        ],
                        "expires_at": 1_900_000_000,
                        "data_access_expires_at": 0,
                    }
                }
            )
        if path.endswith("/me/accounts"):
            return _json(
                {
                    "data": [
                        {
                            "id": "111",
                            "name": "Page One",
                            "category": "Brand",
                            "tasks": ["CREATE_CONTENT", "MANAGE"],
                            "access_token": "EAApage111",
                        }
                    ],
                    "paging": {"cursors": {"after": "x"}},
                }
            )
        if path.endswith("/111"):
            return _json({"id": "111"})
        if path.endswith("/me/permissions"):
            return _json({"success": True})
        return _json({"error": {"message": "unknown path", "code": 803}}, 404)


@pytest.fixture
def graph() -> FakeGraph:
    return FakeGraph()


@pytest.fixture
def provider(graph: FakeGraph) -> LiveMetaProvider:
    return LiveMetaProvider(SETTINGS, transport=httpx.MockTransport(graph))


def test_not_configured_names_missing_settings_only() -> None:
    with pytest.raises(ProviderNotConfiguredError) as exc:
        LiveMetaProvider(Settings(meta_app_id="1"))
    assert exc.value.details == {"missing": ["META_APP_SECRET", "META_OAUTH_REDIRECT_URI"]}
    assert "1" not in exc.value.message


def test_authorize_url_uses_documented_dialog_and_state(provider: LiveMetaProvider) -> None:
    url = provider.authorize_url(
        state="abc",
        redirect_uri=SETTINGS.meta_oauth_redirect_uri or "",
        scopes=["a", "b"],
        mock_scenario=None,
    ).url
    parts = urlsplit(url)
    assert parts.netloc == "www.facebook.com" and parts.path == "/v26.0/dialog/oauth"
    query = parse_qs(parts.query)
    assert (
        query["state"] == ["abc"] and query["scope"] == ["a,b"] and query["client_id"] == ["123456"]
    )
    assert query["response_type"] == ["code"] and "client_secret" not in query


def test_full_exchange_discovery_and_proof(
    provider: LiveMetaProvider, graph: FakeGraph, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="affilio.meta.live")
    short = provider.exchange_code(code="the-code", redirect_uri="https://affilio.test/cb")
    long_lived = provider.exchange_long_lived(token=short.access_token)
    info = provider.debug_token(token=long_lived.access_token)
    pages = provider.list_pages(user_token=long_lived.access_token)
    provider.validate_page_token(page_token=pages[0].access_token, page_id="111")
    provider.revoke(user_token=long_lived.access_token)

    assert short.access_token == "EAAshort" and long_lived.expires_in == 5183944
    assert (
        info.user_id == "9001"
        and info.expires_at is not None
        and info.data_access_expires_at is None
    )
    assert pages[0].id == "111" and pages[0].tasks == ["CREATE_CONTENT", "MANAGE"]
    # appsecret_proof accompanies every token-bearing call; the secret itself never travels.
    for _method, path, params in graph.calls:
        if path.endswith(("/me/accounts", "/111", "/me/permissions")):
            assert "appsecret_proof" in params and "client_secret" not in params
    assert graph.calls[-1][0] == "DELETE"
    # Nothing secret in the log stream.
    logged = " ".join(r.getMessage() for r in caplog.records) + json.dumps(
        [r.__dict__.get("msg") for r in caplog.records], default=str
    )
    for secret in ("EAAshort", "EAAlong", "EAApage111", "shh-app-secret", "the-code"):
        assert secret not in logged


def test_graph_errors_map_to_typed_exceptions(provider: LiveMetaProvider, graph: FakeGraph) -> None:
    graph.fail_with = {
        "message": "Error validating access token",
        "code": 190,
        "error_subcode": 460,
        "fbtrace_id": "T1",
    }
    with pytest.raises(TokenInvalidError) as exc:
        provider.validate_page_token(page_token="EAApage111", page_id="111")
    assert exc.value.provider_code == 190 and exc.value.trace_id == "T1"
    graph.fail_with = {"message": "throttled", "code": 4}
    with pytest.raises(ProviderUnavailableError):
        provider.debug_token(token="EAAlong")
    graph.fail_with = {"message": "(#100) Invalid parameter", "code": 100}
    with pytest.raises(ProviderError) as generic:
        provider.exchange_code(code="bad", redirect_uri="x")
    assert generic.value.provider_code == 100
    graph.fail_with = None
    graph.status = 503
    with pytest.raises(ProviderUnavailableError):
        provider.debug_token(token="EAAlong")


def test_network_failure_is_unavailable_not_invalid() -> None:
    def boom(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    provider = LiveMetaProvider(SETTINGS, transport=httpx.MockTransport(boom))
    with pytest.raises(ProviderUnavailableError):
        provider.validate_page_token(page_token="EAApage111", page_id="111")


def test_live_provider_refuses_mock_credentials(
    provider: LiveMetaProvider, graph: FakeGraph
) -> None:
    for call in (
        lambda: provider.exchange_long_lived(token="mock:user:success:long:1"),
        lambda: provider.debug_token(token="mock:user:success:long:1"),
        lambda: provider.list_pages(user_token="mock:user:success:long:1"),
        lambda: provider.validate_page_token(page_token="mock:page:1:1", page_id="1"),
        lambda: provider.revoke(user_token="mock:user:success:long:1"),
    ):
        with pytest.raises(MockCredentialRejectedError):
            call()
    assert graph.calls == []  # never reached the network
