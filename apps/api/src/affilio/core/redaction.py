"""Redaction of error text before it is persisted or returned (M3C.1 step 3).

Error strings from the OS, ffprobe/ffmpeg, database drivers and unexpected exceptions are
useful to operators but routinely embed things the API must never reveal: container-internal
or NAS host paths, connection URLs with credentials, tokens. `Redactor.message` keeps the
actionable part (error category, file name, path relative to a known media root) and masks the
rest. It is applied where an error is *stored* (scan job errors, probe and thumbnail errors,
publishing errors), not only when a response is rendered, so the database never holds the raw
text either. Logs keep the same redacted text; nothing here reduces what operators can act on.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from affilio.core.config import Settings, get_settings

REDACTED = "***"
PATH_PLACEHOLDER = "[redacted]"

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_SPACES = re.compile(r"[ \t]{2,}")
# scheme://user:password@host  →  scheme://***:***@host  (also user-only: scheme://user@host)
_URL_CREDENTIALS = re.compile(r"(\w[\w+.\-]*://)([^/\s@:]+)(?::([^@\s/]*))?@")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-+/=]{8,}")
# key=value / key:value / "key": "value" for secret-looking keys. `key: value` with a space and
# an unquoted value is left alone: it is how error messages read ("token_invalid: The …").
_KEY_VALUE_SECRET = re.compile(
    r"""(?ix)
    (?P<key>"?[\w\-]*(?:password|passwd|pwd|secret|token|api[_\-]?key|credential|cookie|
                      authorization|private[_\-]?key)[\w\-]*"?)
    (?P<sep>\s*[=:]\s*)
    (?P<value>"[^"]*"|'[^']*'|[^\s,;}\]]+)
    """
)
# POSIX absolute path tokens that survived root replacement (anything outside the media roots,
# e.g. /volume1/..., /tmp/..., /usr/bin/ffprobe). URL paths are excluded by the lookbehind.
_ABS_PATH = re.compile(r"(?<![\w:/])/(?:[^\s'\"`:,;)\]]+/)*[^\s'\"`:,;)\]]+")

_ROOT_SETTINGS = (
    ("originals", "media_originals_path"),
    ("proxies", "media_proxies_path"),
    ("finals", "media_finals_path"),
    ("thumbnails", "media_thumbnails_path"),
    ("exports", "media_exports_path"),
)


def _mask_key_value(match: re.Match[str]) -> str:
    sep, value = match.group("sep"), match.group("value")
    quoted = value[:1] in {'"', "'"}
    if ":" in sep and sep != ":" and not quoted:
        return match.group(0)  # prose such as "token_invalid: The access token has expired"
    masked = f'"{REDACTED}"' if value.startswith('"') else f"'{REDACTED}'" if quoted else REDACTED
    return f"{match.group('key')}{sep}{masked}"


def _mask_path(match: re.Match[str]) -> str:
    name = match.group(0).rstrip("/").rsplit("/", 1)[-1]
    return f"{PATH_PLACEHOLDER}/{name}" if name else PATH_PLACEHOLDER


@dataclass(frozen=True, slots=True)
class Redactor:
    """Knows the configured media roots so paths under them keep their relative part."""

    roots: tuple[tuple[str, str], ...]  # (absolute path without trailing slash, root key)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> Redactor:
        settings = settings or get_settings()
        pairs: dict[str, str] = {}
        for key, attribute in _ROOT_SETTINGS:
            value: Path | None = getattr(settings, attribute, None)
            if value is None:
                continue
            for candidate in {str(value), os.path.realpath(value)}:
                candidate = candidate.rstrip("/")
                if len(candidate) > 1:
                    pairs.setdefault(candidate, key)
        ordered = sorted(pairs.items(), key=lambda item: len(item[0]), reverse=True)
        return cls(tuple(ordered))

    @classmethod
    def from_paths(cls, roots: dict[str, str | Path]) -> Redactor:
        pairs = {str(path).rstrip("/"): key for key, path in roots.items() if len(str(path)) > 1}
        ordered = sorted(pairs.items(), key=lambda item: len(item[0]), reverse=True)
        return cls(tuple(ordered))

    def message(self, text: object, *, limit: int = 500) -> str:
        """Sanitize one error message. Always returns a bounded, single-line string."""
        value = "" if text is None else str(text)
        value = _ANSI.sub("", value)
        value = _CONTROL.sub(" ", value)
        value = _URL_CREDENTIALS.sub(rf"\1{REDACTED}:{REDACTED}@", value)
        value = _BEARER.sub(f"Bearer {REDACTED}", value)
        value = _KEY_VALUE_SECRET.sub(_mask_key_value, value)
        for root, key in self.roots:
            value = value.replace(root + "/", key + "/")
            value = re.sub(rf"(?<![\w/]){re.escape(root)}(?![\w/])", key, value)
        value = _ABS_PATH.sub(_mask_path, value)
        value = _SPACES.sub(" ", value).strip()
        if limit > 0 and len(value) > limit:
            value = value[: max(limit - 1, 0)] + "…"
        return value


def redact_message(text: object, *, settings: Settings | None = None, limit: int = 500) -> str:
    """Convenience for call sites without a cached `Redactor`."""
    return Redactor.from_settings(settings).message(text, limit=limit)
