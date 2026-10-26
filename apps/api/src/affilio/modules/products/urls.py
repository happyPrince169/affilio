"""URL validation and normalization for product links.

Only absolute http(s) URLs are accepted; `javascript:`, `data:`, `file:` and friends are
rejected, as are control characters and whitespace. Normalization is used for duplicate
detection only and never changes what is stored or shown.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from affilio.modules.products.errors import InvalidProductUrlError

ALLOWED_SCHEMES = {"http", "https"}
MAX_URL_LENGTH = 2048


def validate_http_url(value: str, *, field: str = "url") -> str:
    candidate = value.strip()
    if not candidate or len(candidate) > MAX_URL_LENGTH:
        raise InvalidProductUrlError(f"{field} is empty or longer than {MAX_URL_LENGTH} characters")
    if any(ch.isspace() or ord(ch) < 32 or ch == "\x7f" for ch in candidate):
        raise InvalidProductUrlError(f"{field} contains whitespace or control characters")
    try:
        parts = urlsplit(candidate)
    except ValueError as exc:
        raise InvalidProductUrlError(f"{field} is not a valid URL") from exc
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise InvalidProductUrlError(f"{field} must use http or https")
    if not parts.netloc or not parts.hostname:
        raise InvalidProductUrlError(f"{field} must include a host")
    if parts.username or parts.password:
        raise InvalidProductUrlError(f"{field} must not embed credentials")
    return candidate


def normalize_url(value: str) -> str:
    """Lower-case scheme and host, drop the fragment and a trailing slash. Keeps query strings."""
    parts = urlsplit(value.strip())
    host = (parts.hostname or "").lower()
    if parts.port:
        host = f"{host}:{parts.port}"
    path = parts.path.rstrip("/") or ""
    return urlunsplit((parts.scheme.lower(), host, path, parts.query, ""))
