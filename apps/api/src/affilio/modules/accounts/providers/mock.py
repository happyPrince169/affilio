"""Deterministic mock Meta provider for development and automated tests (M3B.1A).

Enabled only with `META_PROVIDER_MODE=mock`, which settings refuse outside development/test.
Every token it issues starts with `mock:` so the live provider can refuse it, and everything it
creates is flagged `is_mock` so the UI never shows it as a real connection.

Scenarios (chosen when the admin starts a connection; stored on the OAuth attempt):

| scenario              | behaviour                                                        |
| --------------------- | ---------------------------------------------------------------- |
| success               | 3 Pages: Alpha (publishable), Beta (no CREATE_CONTENT), Gamma      |
| missing_permissions   | user grant lacks `pages_manage_posts`; Pages as above               |
| no_pages              | authorization succeeds, no Pages with a role                        |
| denied                | the person cancels at the consent screen (`error=access_denied`)    |
| outage                | the token exchange fails like a Meta outage (503)                   |
| invalid_app           | the token belongs to another app (debug_token.app_id mismatch)      |

Runtime switches (admin-only mock actions): revoke a stored token (validation then reports
190-style invalidity), toggle an outage, reset.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import count
from urllib.parse import urlencode

from affilio.core.config import Settings
from affilio.modules.accounts.crypto import fingerprint
from affilio.modules.accounts.providers.base import (
    MOCK_TOKEN_PREFIX,
    AuthorizationRequest,
    MetaProvider,
    MockCredentialRejectedError,
    PageInfo,
    ProviderError,
    ProviderUnavailableError,
    TokenGrant,
    TokenInfo,
    TokenInvalidError,
    is_mock_token,
)

MOCK_APP_ID = "mock-app"
MOCK_USER_ID = "100000000000000001"
MOCK_USER_NAME = "Mock Meta User"
SCENARIOS: tuple[str, ...] = (
    "success",
    "missing_permissions",
    "no_pages",
    "denied",
    "outage",
    "invalid_app",
)
DEFAULT_SCENARIO = "success"
ALL_TASKS = ["ANALYZE", "ADVERTISE", "MODERATE", "CREATE_CONTENT", "MANAGE"]
MOCK_PAGES: tuple[tuple[str, str, str, list[str]], ...] = (
    ("100000000000001", "Mock Page Alpha", "Shopping & retail", ALL_TASKS),
    ("100000000000002", "Mock Page Beta", "Community", ["ANALYZE", "MODERATE"]),
    ("100000000000003", "Mock Page Gamma", "Brand", ALL_TASKS),
)
FULL_SCOPES = ["public_profile", "pages_show_list", "pages_read_engagement", "pages_manage_posts"]


class MockMetaProvider(MetaProvider):
    name = "meta_mock"
    is_mock = True
    expected_app_id = MOCK_APP_ID

    # Process-wide switches: shared by every instance so admin "mock actions" affect later
    # requests in the same API process (sufficient for development and tests).
    _revoked: set[str] = set()
    _outage: bool = False
    _sequence = count(1)

    def __init__(self, settings: Settings) -> None:
        if settings.meta_provider_mode != "mock" or not settings.is_development:
            raise ProviderError("The mock Meta provider is only available in mock mode")

    # ------------------------------------------------------------------ switches
    @classmethod
    def reset(cls) -> None:
        cls._revoked = set()
        cls._outage = False
        cls._sequence = count(1)

    @classmethod
    def set_outage(cls, active: bool) -> None:
        cls._outage = active

    @classmethod
    def outage(cls) -> bool:
        return cls._outage

    @classmethod
    def revoke_token(cls, token: str) -> None:
        cls._revoked.add(fingerprint(token))

    @classmethod
    def revoked_count(cls) -> int:
        return len(cls._revoked)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _require_mock(token: str) -> None:
        if not is_mock_token(token):
            raise MockCredentialRejectedError(
                "A non-mock credential reached the mock provider; refusing to use it"
            )

    def _check(self, token: str) -> None:
        self._require_mock(token)
        if self._outage:
            raise ProviderUnavailableError("Mock outage: Meta is unreachable", is_transient=True)
        if fingerprint(token) in self._revoked:
            raise TokenInvalidError(
                "Mock: access token has been revoked", provider_code=190, provider_subcode=460
            )

    @staticmethod
    def _scenario_of(token: str) -> str:
        parts = token.split(":")
        return parts[2] if len(parts) > 2 else DEFAULT_SCENARIO

    # ------------------------------------------------------------------ contract
    def authorize_url(
        self, *, state: str, redirect_uri: str, scopes: list[str], mock_scenario: str | None
    ) -> AuthorizationRequest:
        scenario = mock_scenario or DEFAULT_SCENARIO
        if scenario not in SCENARIOS:
            raise ProviderError(f"Unknown mock scenario {scenario!r}")
        if scenario == "denied":
            query = urlencode(
                {"state": state, "error": "access_denied", "error_reason": "user_denied"}
            )
        else:
            query = urlencode({"state": state, "code": f"mock-{scenario}"})
        return AuthorizationRequest(url=f"{redirect_uri}?{query}", extra={"scenario": scenario})

    def exchange_code(self, *, code: str, redirect_uri: str) -> TokenGrant:
        if not code.startswith("mock-"):
            raise MockCredentialRejectedError("The mock provider only accepts mock codes")
        scenario = code.removeprefix("mock-")
        if scenario not in SCENARIOS or scenario == "denied":
            raise ProviderError("Mock: invalid authorization code", provider_code=100)
        if scenario == "outage" or self._outage:
            raise ProviderUnavailableError("Mock outage: token exchange failed", is_transient=True)
        seq = next(self._sequence)
        return TokenGrant(
            access_token=f"{MOCK_TOKEN_PREFIX}user:{scenario}:short:{seq}", expires_in=3600
        )

    def exchange_long_lived(self, *, token: str) -> TokenGrant:
        self._check(token)
        scenario = self._scenario_of(token)
        seq = next(self._sequence)
        return TokenGrant(
            access_token=f"{MOCK_TOKEN_PREFIX}user:{scenario}:long:{seq}",
            expires_in=60 * 24 * 3600,
        )

    def debug_token(self, *, token: str) -> TokenInfo:
        self._check(token)
        scenario = self._scenario_of(token)
        scopes = list(FULL_SCOPES)
        if scenario == "missing_permissions":
            scopes.remove("pages_manage_posts")
        now = datetime.now(UTC)
        return TokenInfo(
            app_id="other-app" if scenario == "invalid_app" else MOCK_APP_ID,
            user_id=MOCK_USER_ID,
            scopes=scopes,
            is_valid=True,
            expires_at=now + timedelta(days=60) if ":long:" in token else now + timedelta(hours=1),
            data_access_expires_at=None,
            user_name=MOCK_USER_NAME,
        )

    def list_pages(self, *, user_token: str) -> list[PageInfo]:
        self._check(user_token)
        if self._scenario_of(user_token) == "no_pages":
            return []
        seq = next(self._sequence)
        return [
            PageInfo(
                id=page_id,
                name=name,
                category=category,
                tasks=list(tasks),
                access_token=f"{MOCK_TOKEN_PREFIX}page:{page_id}:{seq}",
            )
            for page_id, name, category, tasks in MOCK_PAGES
        ]

    def validate_page_token(self, *, page_token: str, page_id: str) -> None:
        self._check(page_token)
        if f":page:{page_id}:" not in page_token:
            raise TokenInvalidError("Mock: token does not belong to this Page", provider_code=190)

    def revoke(self, *, user_token: str) -> None:
        self._require_mock(user_token)
        if self._outage:
            raise ProviderUnavailableError("Mock outage", is_transient=True)
        self._revoked.add(fingerprint(user_token))
