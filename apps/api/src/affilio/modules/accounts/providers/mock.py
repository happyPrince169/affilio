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

from collections import deque
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any
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
    RateLimitedError,
    ReelFinishResult,
    ReelIdentity,
    ReelStatus,
    ReelUploadResult,
    ReelUploadSession,
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

# Scripted faults for Reels publishing, consumed per Page in order by the matching call
# (see `script_fault`). Everything else behaves like a healthy provider.
REEL_FAULTS: tuple[str, ...] = (
    "start_timeout",  # start_reel_upload: network timeout (transient)
    "start_rate_limit",  # start_reel_upload: code 32 throttle (hold the Page)
    "start_permission_denied",  # start_reel_upload: code 200 (permanent)
    "upload_timeout",  # upload_reel_bytes: half the chunk lands, then timeout (resume)
    "upload_reject",  # upload_reel_bytes: code 6000 (permanent, "try another file")
    "status_outage",  # reel_status: transient outage
    "processing_delay",  # reel_status: two extra "processing" polls
    "processing_error",  # reel_status: video_status=error (permanent)
    "finish_timeout_ambiguous",  # finish_reel: applied by the provider, response lost
    "finish_timeout_lost",  # finish_reel: NOT applied, response lost
    "finish_reject",  # finish_reel: code 368 (permanent)
    "identity_missing",  # reel_identity: no post id / permalink yet
)
MOCK_PERMALINK_SCHEME = "mock://facebook/reel/"


class MockMetaProvider(MetaProvider):
    name = "meta_mock"
    is_mock = True
    expected_app_id = MOCK_APP_ID

    # Process-wide switches: shared by every instance so admin "mock actions" affect later
    # requests in the same API process (sufficient for development and tests).
    _revoked: set[str] = set()
    _outage: bool = False
    _sequence = count(1)
    # Reels registry and scripted faults (process-wide, deterministic).
    _videos: dict[str, dict[str, Any]] = {}
    _faults: dict[str, deque[str]] = {}
    _video_sequence = count(1)

    def __init__(self, settings: Settings) -> None:
        if settings.meta_provider_mode != "mock" or not settings.is_development:
            raise ProviderError("The mock Meta provider is only available in mock mode")

    # ------------------------------------------------------------------ switches
    @classmethod
    def reset(cls) -> None:
        cls._revoked = set()
        cls._outage = False
        cls._sequence = count(1)
        cls._videos = {}
        cls._faults = {}
        cls._video_sequence = count(1)

    # ---- Reels scripting / inspection (tests and mock actions)
    @classmethod
    def script_fault(cls, page_id: str, fault: str) -> None:
        if fault not in REEL_FAULTS:
            raise ProviderError(f"Unknown mock fault {fault!r}")
        cls._faults.setdefault(page_id, deque()).append(fault)

    @classmethod
    def clear_faults(cls, page_id: str | None = None) -> None:
        if page_id is None:
            cls._faults = {}
        else:
            cls._faults.pop(page_id, None)

    @classmethod
    def pending_faults(cls, page_id: str) -> list[str]:
        return list(cls._faults.get(page_id, ()))

    @classmethod
    def _take_fault(cls, page_id: str, *candidates: str) -> str | None:
        queue = cls._faults.get(page_id)
        if queue and queue[0] in candidates:
            return queue.popleft()
        return None

    @classmethod
    def video(cls, video_id: str) -> dict[str, Any] | None:
        return cls._videos.get(video_id)

    @classmethod
    def finish_count(cls, video_id: str) -> int:
        return int((cls._videos.get(video_id) or {}).get("finish_count", 0))

    @classmethod
    def published_videos(cls, page_id: str) -> list[str]:
        return [vid for vid, v in cls._videos.items() if v["page_id"] == page_id and v["published"]]

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

    # ------------------------------------------------------------------ Reels publishing
    @staticmethod
    def _page_of_token(page_token: str) -> str:
        parts = page_token.split(":")
        return parts[2] if len(parts) > 3 and parts[1] == "page" else ""

    def _reel_video(self, video_id: str, page_id: str) -> dict[str, Any]:
        video = self._videos.get(video_id)
        if video is None or video["page_id"] != page_id:
            raise ProviderError("Mock: unknown video id", provider_code=100, provider_subcode=33)
        return video

    def start_reel_upload(self, *, page_token: str, page_id: str) -> ReelUploadSession:
        self._check(page_token)
        if self._page_of_token(page_token) != page_id:
            raise TokenInvalidError("Mock: token does not belong to this Page", provider_code=190)
        fault = self._take_fault(
            page_id, "start_timeout", "start_rate_limit", "start_permission_denied"
        )
        if fault == "start_timeout":
            raise ProviderUnavailableError("Mock: start timed out", is_transient=True)
        if fault == "start_rate_limit":
            raise RateLimitedError(
                "Mock: Page API rate limit reached", provider_code=32, retry_after_seconds=120
            )
        if fault == "start_permission_denied":
            raise ProviderError(
                "Mock: (#200) Requires pages_manage_posts permission", provider_code=200
            )
        video_id = f"mockvid{next(self._video_sequence):06d}"
        self._videos[video_id] = {
            "page_id": page_id,
            "total": None,
            "received": 0,
            "upload_complete": False,
            "processing_polls": 1,
            "status_polls": 0,
            "finish_count": 0,
            "published": False,
            "post_id": None,
            "error": None,
            "description": None,
        }
        return ReelUploadSession(
            video_id=video_id, upload_url=f"mock://rupload/video-upload/{video_id}"
        )

    def upload_reel_bytes(
        self,
        *,
        page_token: str,
        session: ReelUploadSession,
        chunk: bytes,
        offset: int,
        total: int,
    ) -> ReelUploadResult:
        self._check(page_token)
        page_id = self._page_of_token(page_token)
        video = self._reel_video(session.video_id, page_id)
        if video["total"] is None:
            video["total"] = total
        if offset != video["received"]:
            raise ProviderError(
                "Mock: OffsetInvalidError",
                provider_code=1363037,
                details={"expected": video["received"]},
            )
        fault = self._take_fault(page_id, "upload_timeout", "upload_reject")
        if fault == "upload_reject":
            raise ProviderError(
                "Mock: There was a problem uploading your video file. Please try again with "
                "another file.",
                provider_code=6000,
            )
        if fault == "upload_timeout":
            video["received"] += len(chunk) // 2
            raise ProviderUnavailableError("Mock: upload connection reset", is_transient=True)
        video["received"] += len(chunk)
        if video["received"] >= total:
            video["upload_complete"] = True
        return ReelUploadResult(bytes_received=video["received"])

    def reel_status(self, *, page_token: str, video_id: str) -> ReelStatus:
        self._check(page_token)
        page_id = self._page_of_token(page_token)
        video = self._reel_video(video_id, page_id)
        fault = self._take_fault(page_id, "status_outage", "processing_delay", "processing_error")
        if fault == "status_outage":
            raise ProviderUnavailableError("Mock: status endpoint unavailable", is_transient=True)
        if fault == "processing_delay":
            video["processing_polls"] += 2
        if fault == "processing_error":
            video["error"] = "Mock: video processing failed"
        video["status_polls"] += 1
        if video["error"]:
            return ReelStatus(
                video_status="error",
                bytes_transferred=video["received"],
                processing_status="error",
                error=video["error"],
                raw={"mock": True},
            )
        if video["published"]:
            return ReelStatus(
                video_status="ready",
                bytes_transferred=video["received"],
                processing_status="completed",
                publish_status="published",
                raw={"mock": True, "publish_time": video.get("publish_time")},
            )
        if not video["upload_complete"]:
            return ReelStatus(
                video_status="uploading",
                bytes_transferred=video["received"],
                processing_status="not_started",
                raw={"mock": True},
            )
        if video["processing_polls"] > 0:
            video["processing_polls"] -= 1
            return ReelStatus(
                video_status="processing",
                bytes_transferred=video["received"],
                processing_status="in_progress",
                raw={"mock": True},
            )
        return ReelStatus(
            video_status="ready",
            bytes_transferred=video["received"],
            processing_status="completed",
            publish_status="draft" if video["finish_count"] else None,
            raw={"mock": True},
        )

    def finish_reel(
        self,
        *,
        page_token: str,
        page_id: str,
        video_id: str,
        description: str,
        title: str | None,
    ) -> ReelFinishResult:
        self._check(page_token)
        if self._page_of_token(page_token) != page_id:
            raise TokenInvalidError("Mock: token does not belong to this Page", provider_code=190)
        video = self._reel_video(video_id, page_id)
        fault = self._take_fault(
            page_id, "finish_timeout_ambiguous", "finish_timeout_lost", "finish_reject"
        )
        if fault == "finish_timeout_lost":
            raise ProviderUnavailableError(
                "Mock: finish timed out (not applied)", is_transient=True
            )
        if fault == "finish_reject":
            raise ProviderError(
                "Mock: Temporarily blocked for policies violations", provider_code=368
            )
        if not video["upload_complete"]:
            raise ProviderError("Mock: upload not complete", provider_code=100)
        video["finish_count"] += 1
        video["published"] = True
        video["description"] = description
        video["publish_time"] = int(datetime.now(UTC).timestamp())
        video["post_id"] = f"{page_id}_{video_id}"
        if fault == "finish_timeout_ambiguous":
            raise ProviderUnavailableError(
                "Mock: finish timed out (applied by the provider)", is_transient=True
            )
        return ReelFinishResult(success=True, post_id=None, raw={"success": True, "mock": True})

    def reel_identity(self, *, page_token: str, video_id: str) -> ReelIdentity:
        self._check(page_token)
        page_id = self._page_of_token(page_token)
        video = self._reel_video(video_id, page_id)
        fault = self._take_fault(page_id, "identity_missing")
        if fault == "identity_missing" or not video["published"]:
            return ReelIdentity(post_id=None, permalink_url=None, raw={"mock": True})
        return ReelIdentity(
            post_id=video["post_id"],
            permalink_url=f"{MOCK_PERMALINK_SCHEME}{video_id}",
            raw={"mock": True},
        )
