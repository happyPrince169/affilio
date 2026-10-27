"""Redaction of error text before it is persisted or returned (M3C.1 step 3).

Error strings from the OS, ffprobe/ffmpeg, database drivers and unexpected exceptions are
useful to operators but routinely embed things the API must never reveal: container-internal
or NAS host paths, connection URLs with credentials, tokens. `Redactor.message` keeps the
actionable part (error category, file name, path relative to a known media root) and masks the
rest. It is applied where an error is *stored* (scan job errors, probe and thumbnail errors,
publishing errors), not only when a response is rendered, so the database never holds the raw
text either. Logs keep the same redacted text; nothing here reduces what operators can act on.

Rules, in order (see `docs/architecture/security.md`, "Error redaction"):
1. ANSI escapes and control characters removed; input capped at `INPUT_CAP` characters.
2. Credentials in URLs: `scheme://user:pass@host` → `scheme://***:***@host`.
3. Bearer tokens: `Bearer <token>` → `Bearer ***`.
4. Secret fields. A key is an identifier that *ends* with a secret word (`password`, `token`,
   `api_key`, `client_secret`, `x-api-key`, `authorization`, …); identifiers that merely
   contain one (`token_invalid`, `credential_unavailable`) are error codes and stay readable.
   `key=value` and `key:value` mask one token (shell style; quote values with spaces),
   `key: value with spaces` masks up to the next `,` `;` `|` `}` `]` or the end,
   `"key": "value"` masks the quoted value.
5. Paths under a configured media root → `<root key>/<relative path>` (container and host
   paths, symlink real paths included, spaces and Unicode intact).
6. Any other absolute path, quoted or not, with or without spaces → `[redacted]/<last
   component>`; the directory part never survives. URL paths are not touched.
7. Whitespace collapsed; output bounded (`limit`, `…` marks truncation).

Every quantifier that could backtrack is possessive, so the cost is linear in the (capped)
input length.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from affilio.core.config import Settings, get_settings

REDACTED = "***"
PATH_PLACEHOLDER = "[redacted]"
INPUT_CAP = 20_000  # characters examined; stderr is already capped at its source

_ANSI = re.compile(r"\x1b\[[0-9;?]*+[ -/]*+[@-~]")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]++")
_SPACES = re.compile(r" {2,}+")
# scheme://user:password@host (user or password may be empty) → scheme://***:***@host
_URL_CREDENTIALS = re.compile(r"(?<![\w+.\-])(\w[\w+.\-]*+://)([^/\s@:]*+)(?::([^@\s/]*+))?@")
_BEARER = re.compile(r"(?i)\bbearer[ \t]++[A-Za-z0-9._\-+/=]{8,}+")

_SENSITIVE_KEY_SUFFIXES = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    r"api[_\-]?key",
    "credentials?",
    "cookie",
    "authorization",
    r"private[_\-]?key",
    r"session[_\-]?id",
)
_SECRET_WORDS = "(?:" + "|".join(_SENSITIVE_KEY_SUFFIXES) + ")"
# An identifier that ends with a secret word, optionally quoted (JSON / Python repr keys). The
# boundary lookbehind means every identifier is examined once from its start, so the scan is
# linear; identifiers that only contain a secret word (`token_invalid`) never match.
_SECRET_KEY = rf"""(?P<key>["']?(?<![\w\-])[\w\-]*?{_SECRET_WORDS}["']?)(?![\w\-])"""
# key=value, key:value  (no whitespace after the separator) → one token, or a quoted value.
_KV_TIGHT = re.compile(
    rf"""(?ix)
    {_SECRET_KEY}(?P<sep>[ \t]*+[=:])
    (?P<value>"[^"]*+"|'[^']*+'|[^\s,;}}\]|&]++)
    """
)
# key: value with spaces  (whitespace after the separator) → up to a delimiter / end of text.
_KV_SPACED = re.compile(
    rf"""(?ix)
    {_SECRET_KEY}(?P<sep>[ \t]*+[=:][ \t]++)
    (?:(?P<value>"[^"]*+"|'[^']*+')|(?P<run>[^,;}}\]|]+?)(?=[ \t]*+(?:[,;}}\]|]|$)))
    """
)
# Quoted absolute path, spaces allowed inside the quotes.
_QUOTED_PATH = re.compile(r"""(?P<q>['"])(?P<path>/[^'"]*+)(?P=q)""")
# Unquoted absolute path. A segment may contain single spaces between words; the match is cut
# by quotes, backticks, `:` `,` `;` `|` `)` `]` or a line end. URL paths are excluded by the
# lookbehind (`://host/path` is preceded by `/`, `host/path` by a word character).
_SEG = r"[^\s/'\"`:,;)\]|]++"
# A space continues a segment ("My Media") unless the next word starts with `-` or `/`, which
# marks a command-line argument or another path ("/usr/bin/ffmpeg -i /media/x.mp4").
_ABS_PATH = re.compile(rf"(?<![\w:/\]])/(?:{_SEG}(?: (?![-/]){_SEG})*+/?+)++")
_COMMAND_LINE = re.compile(r" [-/]")

_ROOT_SETTINGS = (
    ("originals", "media_originals_path"),
    ("proxies", "media_proxies_path"),
    ("finals", "media_finals_path"),
    ("thumbnails", "media_thumbnails_path"),
    ("exports", "media_exports_path"),
)


def _mask_value(match: re.Match[str]) -> str:
    groups = match.groupdict()
    value = groups.get("value") or groups.get("run") or ""
    if value.startswith('"'):
        masked = f'"{REDACTED}"'
    elif value.startswith("'"):
        masked = f"'{REDACTED}'"
    else:
        masked = REDACTED
    return f"{match.group('key')}{match.group('sep')}{masked}"


def _mask_quoted_path(match: re.Match[str]) -> str:
    quote, inner = match.group("q"), match.group("path")
    if _COMMAND_LINE.search(inner):  # a quoted command line, not one path: redact per token
        return f"{quote}{_ABS_PATH.sub(_mask_path, inner)}{quote}"
    name = inner.rstrip("/").rsplit("/", 1)[-1].strip()
    inner = f"{PATH_PLACEHOLDER}/{name}" if name else PATH_PLACEHOLDER
    return f"{quote}{inner}{quote}"


def _mask_path(match: re.Match[str]) -> str:
    name = match.group(0).rstrip("/").rsplit("/", 1)[-1].strip()
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
        value = value[:INPUT_CAP]
        value = _ANSI.sub("", value)
        value = _CONTROL.sub(" ", value)
        value = _URL_CREDENTIALS.sub(rf"\1{REDACTED}:{REDACTED}@", value)
        value = _BEARER.sub(f"Bearer {REDACTED}", value)
        value = _KV_TIGHT.sub(_mask_value, value)
        value = _KV_SPACED.sub(_mask_value, value)
        for root, key in self.roots:
            value = value.replace(root + "/", key + "/")
            value = re.sub(rf"(?<![\w/]){re.escape(root)}(?![\w/])", key, value)
        value = _QUOTED_PATH.sub(_mask_quoted_path, value)
        value = _ABS_PATH.sub(_mask_path, value)
        value = _SPACES.sub(" ", value).strip()
        if limit > 0 and len(value) > limit:
            value = value[: max(limit - 1, 0)] + "…"
        return value


def redact_message(text: object, *, settings: Settings | None = None, limit: int = 500) -> str:
    """Convenience for call sites without a cached `Redactor`."""
    return Redactor.from_settings(settings).message(text, limit=limit)
