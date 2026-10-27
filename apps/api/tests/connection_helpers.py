"""Helpers for Facebook connection tests: mock-mode environment and the OAuth round trip."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from affilio.modules.accounts.crypto import generate_key

CALLBACK = "/api/v1/accounts/connect/facebook/callback"


def mock_env(monkeypatch: pytest.MonkeyPatch, *, with_key: bool = True) -> None:
    monkeypatch.setenv("META_PROVIDER_MODE", "mock")
    monkeypatch.delenv("META_APP_ID", raising=False)
    monkeypatch.delenv("META_APP_SECRET", raising=False)
    monkeypatch.delenv("META_OAUTH_REDIRECT_URI", raising=False)
    monkeypatch.delenv("WEB_BASE_URL", raising=False)
    monkeypatch.delenv("CREDENTIAL_ENCRYPTION_ACTIVE_KEY_ID", raising=False)
    if with_key:
        monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEYS", f"v1:{generate_key()}")
    else:
        monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEYS", raising=False)


def live_env(monkeypatch: pytest.MonkeyPatch, *, configured: bool) -> None:
    monkeypatch.setenv("META_PROVIDER_MODE", "live")
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEYS", f"v1:{generate_key()}")
    if configured:
        monkeypatch.setenv("META_APP_ID", "123456")
        monkeypatch.setenv("META_APP_SECRET", "test-app-secret")
        monkeypatch.setenv(
            "META_OAUTH_REDIRECT_URI",
            "https://affilio.test/api/v1/accounts/connect/facebook/callback",
        )
    else:
        for key in ("META_APP_ID", "META_APP_SECRET", "META_OAUTH_REDIRECT_URI"):
            monkeypatch.delenv(key, raising=False)


def start(client: TestClient, scenario: str | None = "success") -> dict:
    body = {"mock_scenario": scenario} if scenario else {}
    response = client.post("/api/v1/accounts/connect/facebook", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def callback(client: TestClient, authorize_url: str):
    """Follow the mock authorize URL (which is the callback) without following redirects."""
    parts = urlsplit(authorize_url)
    assert parts.path == CALLBACK, authorize_url
    return client.get(f"{parts.path}?{parts.query}", follow_redirects=False)


def location_of(response) -> tuple[str, dict[str, list[str]]]:
    assert response.status_code == 303, response.text
    target = urlsplit(response.headers["location"])
    return target.path, parse_qs(target.query)


def connect(client: TestClient, scenario: str = "success") -> str:
    """Start + callback; returns the connection id from the redirect."""
    started = start(client, scenario)
    path, query = location_of(callback(client, started["authorize_url"]))
    assert path.startswith("/accounts/connect/facebook/"), (path, query)
    return path.rsplit("/", 1)[1]


def link(client: TestClient, connection_id: str, page_ids: list[str], expected: int = 200) -> list:
    response = client.post(
        f"/api/v1/accounts/connections/{connection_id}/pages", json={"page_ids": page_ids}
    )
    assert response.status_code == expected, response.text
    return response.json() if expected == 200 else []


def assert_no_secret(payload: object) -> None:
    text = str(payload)
    assert "mock:" not in text, "a mock token leaked into a response"
    assert "ciphertext" not in text and "nonce" not in text
