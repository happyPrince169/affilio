"""Provider adapter contract for Facebook Page connections (M3B.1A).

The accounts service talks to Meta only through this interface. `LiveMetaProvider` implements
the documented Graph API calls (not yet exercised against a real app); `MockMetaProvider` is a
deterministic in-process stand-in for development and tests. Tokens cross this boundary as
plain strings held in memory for the duration of one call and are never logged or returned.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fastapi import status

from affilio.core.errors import AppError

MOCK_TOKEN_PREFIX = "mock:"  # noqa: S105 - marker, not a secret


class ProviderError(AppError):
    """The provider answered with an error. `is_transient` says whether a retry may help."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "provider_error"

    def __init__(
        self,
        message: str,
        *,
        provider_code: int | None = None,
        provider_subcode: int | None = None,
        is_transient: bool = False,
        trace_id: str | None = None,
        details: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            message,
            details={
                "provider_code": provider_code,
                "provider_subcode": provider_subcode,
                "is_transient": is_transient,
                "trace_id": trace_id,
                **(details or {}),
            },
            **kwargs,
        )
        self.provider_code = provider_code
        self.provider_subcode = provider_subcode
        self.is_transient = is_transient
        self.trace_id = trace_id


class ProviderUnavailableError(ProviderError):
    """Network failure, 5xx, or throttling: nothing can be concluded about the token."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "provider_unavailable"


class TokenInvalidError(ProviderError):
    """The provider rejected the stored token (expired, revoked, role removed...)."""

    status_code = status.HTTP_409_CONFLICT
    code = "provider_token_invalid"


class RateLimitedError(ProviderUnavailableError):
    """Provider throttling (codes 4/17/32/613): hold the Page, do not hammer."""

    code = "provider_rate_limited"

    def __init__(self, message: str, *, retry_after_seconds: int | None = None, **kw: Any) -> None:
        super().__init__(message, is_transient=True, **kw)
        self.retry_after_seconds = retry_after_seconds


class LivePublishingDisabledError(AppError):
    """Live Reels publishing is switched off (META_LIVE_PUBLISHING_ENABLED=false)."""

    status_code = status.HTTP_409_CONFLICT
    code = "live_publishing_disabled"


class ProviderNotConfiguredError(AppError):
    """Live mode without the Meta app settings. Names the missing settings, never values."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "meta_not_configured"


class MockCredentialRejectedError(ProviderError):
    """A mock token reached the live provider (or a real one reached the mock). Fail hard."""

    status_code = status.HTTP_409_CONFLICT
    code = "mock_credential_rejected"


@dataclass(frozen=True, slots=True)
class TokenGrant:
    access_token: str
    expires_in: int | None  # seconds; None when the provider reports no expiry


@dataclass(frozen=True, slots=True)
class TokenInfo:
    app_id: str
    user_id: str
    scopes: list[str]
    is_valid: bool
    expires_at: datetime | None
    data_access_expires_at: datetime | None
    user_name: str | None = None


@dataclass(frozen=True, slots=True)
class PageInfo:
    id: str
    name: str
    category: str | None
    tasks: list[str]
    access_token: str

    def public_dict(self) -> dict[str, Any]:
        """Serializable facts without the token (stored on the connection row)."""
        return {"id": self.id, "name": self.name, "category": self.category, "tasks": self.tasks}


# ---------------------------------------------------------------- Reels publishing (M3B.2A)


@dataclass(frozen=True, slots=True)
class ReelUploadSession:
    video_id: str
    upload_url: str


@dataclass(frozen=True, slots=True)
class ReelUploadResult:
    bytes_received: int  # bytes the provider acknowledges so far (resume offset)


@dataclass(frozen=True, slots=True)
class ReelStatus:
    """Normalized `GET /{video_id}?fields=status` (feasibility B4)."""

    video_status: (
        str  # uploading | upload_complete | processing | ready | error | expired | upload_failed
    )
    bytes_transferred: int | None = None
    processing_status: str | None = None  # not_started | in_progress | completed | error
    publish_status: str | None = None  # draft | scheduled | published | error | None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_published(self) -> bool:
        return self.publish_status == "published"

    @property
    def is_error(self) -> bool:
        return self.video_status in {"error", "expired", "upload_failed"} or (
            self.publish_status == "error"
        )


@dataclass(frozen=True, slots=True)
class ReelFinishResult:
    success: bool
    post_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReelIdentity:
    post_id: str | None
    permalink_url: str | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    url: str
    extra: dict[str, Any] = field(default_factory=dict)


class MetaProvider(ABC):
    name: str = "base"
    is_mock: bool = False
    expected_app_id: str = ""

    @abstractmethod
    def authorize_url(
        self, *, state: str, redirect_uri: str, scopes: list[str], mock_scenario: str | None
    ) -> AuthorizationRequest: ...

    @abstractmethod
    def exchange_code(self, *, code: str, redirect_uri: str) -> TokenGrant: ...

    @abstractmethod
    def exchange_long_lived(self, *, token: str) -> TokenGrant: ...

    @abstractmethod
    def debug_token(self, *, token: str) -> TokenInfo: ...

    @abstractmethod
    def list_pages(self, *, user_token: str) -> list[PageInfo]: ...

    @abstractmethod
    def validate_page_token(self, *, page_token: str, page_id: str) -> None:
        """Raise `TokenInvalidError` when the token no longer works; return otherwise."""

    @abstractmethod
    def revoke(self, *, user_token: str) -> None:
        """Revoke the app authorization at the provider. Invalid tokens are not an error."""

    # ---- Reels publishing (M3B.2A). Each call is one network round trip; the engine
    # persists state between calls so a crash never repeats the finish request.
    @abstractmethod
    def start_reel_upload(self, *, page_token: str, page_id: str) -> ReelUploadSession: ...

    @abstractmethod
    def upload_reel_bytes(
        self,
        *,
        page_token: str,
        session: ReelUploadSession,
        chunk: bytes,
        offset: int,
        total: int,
    ) -> ReelUploadResult: ...

    @abstractmethod
    def reel_status(self, *, page_token: str, video_id: str) -> ReelStatus: ...

    @abstractmethod
    def finish_reel(
        self,
        *,
        page_token: str,
        page_id: str,
        video_id: str,
        description: str,
        title: str | None,
    ) -> ReelFinishResult: ...

    @abstractmethod
    def reel_identity(self, *, page_token: str, video_id: str) -> ReelIdentity: ...


def is_mock_token(token: str) -> bool:
    return token.startswith(MOCK_TOKEN_PREFIX)
