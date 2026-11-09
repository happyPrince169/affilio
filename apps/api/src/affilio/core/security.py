"""Password hashing, opaque session tokens and integration API keys.

- Passwords: argon2id via argon2-cffi (memory-hard, salted). Never log or return hashes.
- Sessions: 32 random bytes, URL-safe, handed to the browser in an HttpOnly cookie; only the
  SHA-256 of the token is stored, so a database leak does not yield usable sessions.
- Integration API keys (ADR 0013): `afk_` + 30 random bytes URL-safe (240 bits of entropy).
  The prefix makes a leaked key recognisable in logs and lets the redactor mask it; only the
  SHA-256 is stored and the plaintext is shown exactly once, at creation.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=2)
# Used when the user does not exist so timing does not reveal valid emails.
_DUMMY_HASH = _hasher.hash("affilio-dummy-password-for-constant-time")

MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


API_KEY_PREFIX = "afk_"
# `afk_` + token_urlsafe(30) → 4 + 40 characters. Anything else is not an AFFILIO key.
API_KEY_LENGTH = 44
API_KEY_DISPLAY_PREFIX_LENGTH = 12  # "afk_" + 8 characters: enough to tell keys apart


def generate_api_key() -> str:
    return API_KEY_PREFIX + secrets.token_urlsafe(30)


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def api_key_display_prefix(raw: str) -> str:
    return raw[:API_KEY_DISPLAY_PREFIX_LENGTH]


def looks_like_api_key(raw: str) -> bool:
    """Cheap shape check before any database lookup (never a validity check)."""
    return (
        raw.startswith(API_KEY_PREFIX)
        and len(raw) == API_KEY_LENGTH
        and raw[len(API_KEY_PREFIX) :].replace("-", "").replace("_", "").isalnum()
    )
