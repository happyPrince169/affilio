"""Pure-ASGI middleware that assigns a request ID, binds it to the log context,
and emits one structured access log line per request."""

from __future__ import annotations

import time
import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "x-request-id"
_MAX_REQUEST_ID_LENGTH = 128

log = structlog.get_logger("affilio.request")


def _incoming_request_id(scope: Scope) -> str | None:
    for key, value in scope.get("headers", []):
        if key == REQUEST_ID_HEADER.encode("latin-1"):
            candidate = value.decode("latin-1").strip()
            if 0 < len(candidate) <= _MAX_REQUEST_ID_LENGTH and candidate.isprintable():
                return candidate
    return None


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = message.setdefault("headers", [])
                header_key = REQUEST_ID_HEADER.encode("latin-1")
                if not any(key.lower() == header_key for key, _ in headers):
                    headers.append((header_key, request_id.encode("latin-1")))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            log.info(
                "http.request",
                method=scope.get("method"),
                path=scope.get("path"),
                status=status_code,
                duration_ms=duration_ms,
            )
            structlog.contextvars.clear_contextvars()
