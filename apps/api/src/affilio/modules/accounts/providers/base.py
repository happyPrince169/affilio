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


def is_mock_token(token: str) -> bool:
    return token.startswith(MOCK_TOKEN_PREFIX)
