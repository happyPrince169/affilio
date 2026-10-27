"""Live Meta provider: the documented Graph API calls behind the `MetaProvider` contract.

Status (M3B.1A): implemented from the official documentation verified on 2026-10-26
(`docs/integrations/facebook-reels-feasibility.md`, items A2, A3, A3a, A7) and covered by tests
with `httpx.MockTransport`. It has NOT been exercised against a real Meta app yet; the
activation checklist (`docs/integrations/meta-app-activation-checklist.md`) lists what the
first live run must confirm.

Logging policy: only method, path template, status code and `fbtrace_id` are logged. Tokens,
codes and the app secret never appear in log events or error details.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog

from affilio.core.config import Settings
from affilio.modules.accounts.providers.base import (
    AuthorizationRequest,
    MetaProvider,
    MockCredentialRejectedError,
    PageInfo,
    ProviderError,
    ProviderNotConfiguredError,
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

log = structlog.get_logger("affilio.meta.live")

GRAPH_HOST = "https://graph.facebook.com"
DIALOG_HOST = "https://www.facebook.com"
RUPLOAD_HOST = "https://rupload.facebook.com"
# Error codes the Graph API documents for invalid/expired sessions (feasibility A7a).
TOKEN_INVALID_CODES = frozenset({102, 190})
PERMISSION_CODES = frozenset(range(200, 300)) | {10}
THROTTLE_CODES = frozenset({4, 17, 32, 613, 80001})
_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)


class LiveMetaProvider(MetaProvider):
    name = "meta_live"
    is_mock = False

    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None) -> None:
        missing = settings.meta_live_missing_settings
        if missing:
            raise ProviderNotConfiguredError(
                "The Meta app is not configured on this server", details={"missing": missing}
            )
        assert settings.meta_app_id and settings.meta_app_secret  # noqa: S101 - checked above
        self._app_id = settings.meta_app_id
        self._app_secret = settings.meta_app_secret.get_secret_value()
        self._version = settings.meta_graph_api_version
        self._login_config_id = settings.meta_login_config_id
        self.expected_app_id = self._app_id
        self._client = httpx.Client(
            base_url=f"{GRAPH_HOST}/{self._version}", timeout=_TIMEOUT, transport=transport
        )

    # ------------------------------------------------------------------ helpers
    def _proof(self, token: str) -> str:
        return hmac.new(
            self._app_secret.encode("utf-8"), token.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    @staticmethod
    def _refuse_mock(token: str) -> None:
        if is_mock_token(token):
            raise MockCredentialRejectedError(
                "A mock credential reached the live Meta provider; refusing to use it"
            )

    def _request(self, method: str, path: str, *, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.request(method, path, params=params)
        except httpx.HTTPError as exc:
            log.warning("meta.request_failed", method=method, path=path, error=type(exc).__name__)
            raise ProviderUnavailableError("Meta could not be reached", is_transient=True) from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        error = payload.get("error") if isinstance(payload, dict) else None
        trace_id = error.get("fbtrace_id") if isinstance(error, dict) else None
        log.info(
            "meta.request", method=method, path=path, status=response.status_code, trace=trace_id
        )
        if response.status_code >= 500:
            raise ProviderUnavailableError(
                "Meta answered with a server error", is_transient=True, trace_id=trace_id
            )
        if isinstance(payload, dict) and "error" in payload:
            self._raise_graph_error(payload["error"], trace_id)
        if response.status_code >= 400:
            raise ProviderError(f"Meta answered HTTP {response.status_code}", trace_id=trace_id)
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _raise_graph_error(error: dict[str, Any], trace_id: str | None) -> None:
        code = error.get("code")
        subcode = error.get("error_subcode")
        message = str(error.get("error_user_msg") or error.get("message") or "Meta error")[:255]
        transient = bool(error.get("is_transient")) or code in {1, 2}
        if code in TOKEN_INVALID_CODES:
            raise TokenInvalidError(
                message, provider_code=code, provider_subcode=subcode, trace_id=trace_id
            )
        if code in THROTTLE_CODES:
            raise RateLimitedError(
                message, provider_code=code, provider_subcode=subcode, trace_id=trace_id
            )
        if transient:
            raise ProviderUnavailableError(
                message,
                provider_code=code,
                provider_subcode=subcode,
                is_transient=True,
                trace_id=trace_id,
            )
        raise ProviderError(
            message, provider_code=code, provider_subcode=subcode, trace_id=trace_id
        )

    @staticmethod
    def _ts(value: Any) -> datetime | None:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return datetime.fromtimestamp(number, tz=UTC) if number > 0 else None

    # ------------------------------------------------------------------ contract
    def authorize_url(
        self, *, state: str, redirect_uri: str, scopes: list[str], mock_scenario: str | None
    ) -> AuthorizationRequest:
        params: dict[str, str] = {
            "client_id": self._app_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "response_type": "code",
        }
        if self._login_config_id:
            params["config_id"] = self._login_config_id
        else:
            params["scope"] = ",".join(scopes)
        return AuthorizationRequest(
            url=f"{DIALOG_HOST}/{self._version}/dialog/oauth?{urlencode(params)}"
        )

    def exchange_code(self, *, code: str, redirect_uri: str) -> TokenGrant:
        payload = self._request(
            "GET",
            "/oauth/access_token",
            params={
                "client_id": self._app_id,
                "client_secret": self._app_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            },
        )
        return self._grant(payload)

    def exchange_long_lived(self, *, token: str) -> TokenGrant:
        self._refuse_mock(token)
        payload = self._request(
            "GET",
            "/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": self._app_id,
                "client_secret": self._app_secret,
                "fb_exchange_token": token,
            },
        )
        return self._grant(payload)

    @staticmethod
    def _grant(payload: dict[str, Any]) -> TokenGrant:
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise ProviderError("Meta did not return an access token")
        expires = payload.get("expires_in")
        return TokenGrant(access_token=token, expires_in=int(expires) if expires else None)

    def debug_token(self, *, token: str) -> TokenInfo:
        self._refuse_mock(token)
        app_token = f"{self._app_id}|{self._app_secret}"
        payload = self._request(
            "GET", "/debug_token", params={"input_token": token, "access_token": app_token}
        )
        data = payload.get("data") or {}
        return TokenInfo(
            app_id=str(data.get("app_id") or ""),
            user_id=str(data.get("user_id") or ""),
            scopes=[str(s) for s in data.get("scopes") or []],
            is_valid=bool(data.get("is_valid")),
            expires_at=self._ts(data.get("expires_at")),
            data_access_expires_at=self._ts(data.get("data_access_expires_at")),
        )

    def list_pages(self, *, user_token: str) -> list[PageInfo]:
        self._refuse_mock(user_token)
        pages: list[PageInfo] = []
        params: dict[str, Any] = {
            "fields": "id,name,category,tasks,access_token",
            "access_token": user_token,
            "appsecret_proof": self._proof(user_token),
            "limit": 100,
        }
        path = "/me/accounts"
        for _ in range(20):  # bounded pagination
            payload = self._request("GET", path, params=params)
            for item in payload.get("data") or []:
                token = item.get("access_token")
                if not isinstance(token, str) or not item.get("id"):
                    continue
                pages.append(
                    PageInfo(
                        id=str(item["id"]),
                        name=str(item.get("name") or item["id"]),
                        category=item.get("category"),
                        tasks=[str(t) for t in item.get("tasks") or []],
                        access_token=token,
                    )
                )
            after = ((payload.get("paging") or {}).get("cursors") or {}).get("after")
            if not after or not (payload.get("paging") or {}).get("next"):
                break
            params = {**params, "after": after}
        return pages

    def validate_page_token(self, *, page_token: str, page_id: str) -> None:
        self._refuse_mock(page_token)
        payload = self._request(
            "GET",
            f"/{page_id}",
            params={
                "fields": "id",
                "access_token": page_token,
                "appsecret_proof": self._proof(page_token),
            },
        )
        if str(payload.get("id") or "") != page_id:
            raise TokenInvalidError("The Page token does not belong to this Page")

    def revoke(self, *, user_token: str) -> None:
        self._refuse_mock(user_token)
        try:
            self._request(
                "DELETE",
                "/me/permissions",
                params={"access_token": user_token, "appsecret_proof": self._proof(user_token)},
            )
        except TokenInvalidError:
            return

    # ------------------------------------------------------------------ Reels (M3B.2A)
    # Documented three-phase flow (feasibility B1/B2/B4). NOT exercised against Meta yet;
    # the engine refuses to call these unless META_LIVE_PUBLISHING_ENABLED=true.
    def start_reel_upload(self, *, page_token: str, page_id: str) -> ReelUploadSession:
        self._refuse_mock(page_token)
        payload = self._request(
            "POST",
            f"/{page_id}/video_reels",
            params={
                "upload_phase": "start",
                "access_token": page_token,
                "appsecret_proof": self._proof(page_token),
            },
        )
        video_id, upload_url = payload.get("video_id"), payload.get("upload_url")
        if not video_id or not upload_url:
            raise ProviderError("Meta did not return an upload session")
        return ReelUploadSession(video_id=str(video_id), upload_url=str(upload_url))

    def upload_reel_bytes(
        self,
        *,
        page_token: str,
        session: ReelUploadSession,
        chunk: bytes,
        offset: int,
        total: int,
    ) -> ReelUploadResult:
        self._refuse_mock(page_token)
        url = f"{RUPLOAD_HOST}/video-upload/{self._version}/{session.video_id}"
        try:
            response = self._client.post(
                url,
                headers={
                    "Authorization": f"OAuth {page_token}",
                    "offset": str(offset),
                    "file_size": str(total),
                    "Content-Type": "application/octet-stream",
                },
                content=chunk,
                timeout=httpx.Timeout(connect=10.0, read=120.0, write=120.0, pool=10.0),
            )
        except httpx.HTTPError as exc:
            log.warning("meta.upload_failed", video=session.video_id, error=type(exc).__name__)
            raise ProviderUnavailableError("Upload interrupted", is_transient=True) from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code >= 500:
            raise ProviderUnavailableError("Upload host error", is_transient=True)
        if isinstance(payload, dict) and payload.get("error"):
            self._raise_graph_error(payload["error"], None)
        if response.status_code >= 400 or not (
            isinstance(payload, dict) and payload.get("success")
        ):
            raise ProviderError(f"Upload answered HTTP {response.status_code}")
        return ReelUploadResult(bytes_received=offset + len(chunk))

    def reel_status(self, *, page_token: str, video_id: str) -> ReelStatus:
        self._refuse_mock(page_token)
        payload = self._request(
            "GET",
            f"/{video_id}",
            params={
                "fields": "status",
                "access_token": page_token,
                "appsecret_proof": self._proof(page_token),
            },
        )
        status_obj = payload.get("status") or {}
        uploading = status_obj.get("uploading_phase") or {}
        processing = status_obj.get("processing_phase") or {}
        publishing = status_obj.get("publishing_phase") or {}
        error = (
            (publishing.get("error") or {}).get("message")
            or (processing.get("error") or {}).get("message")
            or None
        )
        return ReelStatus(
            video_status=str(status_obj.get("video_status") or "unknown"),
            bytes_transferred=uploading.get("bytes_transfered"),
            processing_status=processing.get("status"),
            publish_status=publishing.get("publish_status"),
            error=error,
            raw=status_obj,
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
        self._refuse_mock(page_token)
        params: dict[str, Any] = {
            "upload_phase": "finish",
            "video_id": video_id,
            "video_state": "PUBLISHED",
            "description": description,
            "access_token": page_token,
            "appsecret_proof": self._proof(page_token),
        }
        if title:
            params["title"] = title
        payload = self._request("POST", f"/{page_id}/video_reels", params=params)
        return ReelFinishResult(
            success=bool(payload.get("success")),
            post_id=str(payload["post_id"]) if payload.get("post_id") else None,
            raw=payload,
        )

    def reel_identity(self, *, page_token: str, video_id: str) -> ReelIdentity:
        self._refuse_mock(page_token)
        payload = self._request(
            "GET",
            f"/{video_id}",
            params={
                "fields": "id,permalink_url,post_id",
                "access_token": page_token,
                "appsecret_proof": self._proof(page_token),
            },
        )
        return ReelIdentity(
            post_id=str(payload["post_id"]) if payload.get("post_id") else None,
            permalink_url=payload.get("permalink_url"),
            raw=payload,
        )
