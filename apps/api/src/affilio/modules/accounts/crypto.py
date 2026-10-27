"""Credential vault primitives: AES-256-GCM with a versioned key ring (M3B.1A).

- Keys come from `CREDENTIAL_ENCRYPTION_KEYS` (`key_id:base64(32 bytes)`, comma-separated) and
  never from the database. Encryption uses the active key; decryption picks the key named by
  the stored `key_id`, so rotation is "add a key, switch the active id, re-encrypt, drop the
  old key".
- Every ciphertext is bound to its row through additional authenticated data (organization,
  subject, credential kind, key id): a ciphertext copied onto another row does not decrypt.
- Fail closed: without a usable key ring the vault refuses to encrypt *and* to decrypt; the
  API surfaces that as 503 `credential_vault_unavailable` and never stores plaintext instead.

Plaintext tokens only ever exist in memory inside the accounts service; they are never
returned by a router, logged, or placed in audit data.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import uuid
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import status

from affilio.core.config import Settings
from affilio.core.errors import AppError

KEY_BYTES = 32
NONCE_BYTES = 12
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")


class CredentialVaultError(AppError):
    """The vault cannot operate (no key, unknown key id). Never falls back to plaintext."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "credential_vault_unavailable"


class EncryptionKeyMissingError(CredentialVaultError):
    pass


class UnknownKeyIdError(CredentialVaultError):
    pass


class CredentialUndecryptableError(AppError):
    """Wrong key, tampered ciphertext, or ciphertext moved between rows."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "credential_undecryptable"


@dataclass(frozen=True, slots=True)
class EncryptedSecret:
    key_id: str
    nonce: bytes
    ciphertext: bytes


def parse_key_ring(raw: str) -> dict[str, bytes]:
    """Parse `id:base64,...` into `{id: 32 raw bytes}`. Strict: malformed rings are refused."""
    keys: dict[str, bytes] = {}
    for entry in (part.strip() for part in raw.split(",")):
        if not entry:
            continue
        key_id, sep, encoded = entry.partition(":")
        key_id = key_id.strip()
        if not sep or not _KEY_ID_RE.match(key_id):
            msg = "CREDENTIAL_ENCRYPTION_KEYS entries must look like key_id:base64"
            raise EncryptionKeyMissingError(msg)
        if key_id in keys:
            msg = f"CREDENTIAL_ENCRYPTION_KEYS lists key id {key_id!r} twice"
            raise EncryptionKeyMissingError(msg)
        try:
            material = base64.b64decode(_normalize_b64(encoded.strip()), validate=True)
        except (binascii.Error, ValueError) as exc:
            msg = f"CREDENTIAL_ENCRYPTION_KEYS key {key_id!r} is not valid base64"
            raise EncryptionKeyMissingError(msg) from exc
        if len(material) != KEY_BYTES:
            msg = f"CREDENTIAL_ENCRYPTION_KEYS key {key_id!r} must decode to {KEY_BYTES} bytes"
            raise EncryptionKeyMissingError(msg)
        keys[key_id] = material
    if not keys:
        msg = "CREDENTIAL_ENCRYPTION_KEYS is empty"
        raise EncryptionKeyMissingError(msg)
    return keys


def _normalize_b64(value: str) -> str:
    # Accept URL-safe alphabet and missing padding; `openssl rand -base64 32` output is standard.
    value = value.replace("-", "+").replace("_", "/")
    return value + "=" * (-len(value) % 4)


def generate_key() -> str:
    """Base64 of 32 random bytes — for operators' `.env` files, never stored by the app."""
    return base64.b64encode(os.urandom(KEY_BYTES)).decode("ascii")


def fingerprint(plaintext: str) -> str:
    """SHA-256 hex of a secret: equality checks without decrypting; not reversible."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def credential_aad(
    *, organization_id: uuid.UUID, subject: str, kind: str, key_id: str, version: int
) -> bytes:
    """Row binding for the AEAD tag. Changing any component makes the ciphertext undecryptable."""
    return f"affilio.credential|{organization_id}|{subject}|{kind}|{key_id}|{version}".encode()


class CredentialCipher:
    def __init__(self, keys: dict[str, bytes], active_key_id: str) -> None:
        if active_key_id not in keys:
            msg = (
                f"CREDENTIAL_ENCRYPTION_ACTIVE_KEY_ID {active_key_id!r} is not in "
                "CREDENTIAL_ENCRYPTION_KEYS"
            )
            raise UnknownKeyIdError(msg)
        self._keys = dict(keys)
        self.active_key_id = active_key_id

    @classmethod
    def from_settings(cls, settings: Settings) -> CredentialCipher:
        if settings.credential_encryption_keys is None:
            msg = (
                "The credential vault is not configured: set CREDENTIAL_ENCRYPTION_KEYS "
                "(and CREDENTIAL_ENCRYPTION_ACTIVE_KEY_ID when more than one key is listed)"
            )
            raise EncryptionKeyMissingError(msg)
        keys = parse_key_ring(settings.credential_encryption_keys.get_secret_value())
        active = settings.credential_encryption_active_key_id
        if active is None:
            if len(keys) != 1:
                msg = (
                    "CREDENTIAL_ENCRYPTION_ACTIVE_KEY_ID is required when "
                    "CREDENTIAL_ENCRYPTION_KEYS lists more than one key"
                )
                raise UnknownKeyIdError(msg)
            active = next(iter(keys))
        return cls(keys, active)

    @property
    def key_ids(self) -> list[str]:
        return list(self._keys)

    def encrypt(self, plaintext: str, *, aad: bytes) -> EncryptedSecret:
        nonce = os.urandom(NONCE_BYTES)
        ciphertext = AESGCM(self._keys[self.active_key_id]).encrypt(
            nonce, plaintext.encode("utf-8"), aad
        )
        return EncryptedSecret(key_id=self.active_key_id, nonce=nonce, ciphertext=ciphertext)

    def decrypt(self, secret: EncryptedSecret, *, aad: bytes) -> str:
        key = self._keys.get(secret.key_id)
        if key is None:
            msg = f"Stored credential uses unknown key id {secret.key_id!r}"
            raise UnknownKeyIdError(msg)
        if len(secret.nonce) != NONCE_BYTES:
            msg = "Stored credential has a malformed nonce"
            raise CredentialUndecryptableError(msg)
        try:
            return AESGCM(key).decrypt(secret.nonce, secret.ciphertext, aad).decode("utf-8")
        except InvalidTag as exc:
            msg = "Stored credential could not be decrypted (wrong key or tampered data)"
            raise CredentialUndecryptableError(msg) from exc


def get_cipher(settings: Settings) -> CredentialCipher:
    """Build the cipher for a request/worker tick. Raises `CredentialVaultError` (fail closed)."""
    return CredentialCipher.from_settings(settings)
