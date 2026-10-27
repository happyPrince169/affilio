"""Credential vault: AES-256-GCM, key ring, AAD binding, fail-closed behaviour (no database)."""

from __future__ import annotations

import base64
import uuid

import pytest

from affilio.core.config import Settings
from affilio.modules.accounts import crypto
from affilio.modules.accounts.crypto import (
    CredentialCipher,
    CredentialUndecryptableError,
    EncryptedSecret,
    EncryptionKeyMissingError,
    UnknownKeyIdError,
    credential_aad,
    generate_key,
    parse_key_ring,
)

ORG = uuid.uuid4()


def _aad(**overrides):
    base = {
        "organization_id": ORG,
        "subject": "conn:page1",
        "kind": "meta_page",
        "key_id": "v1",
        "version": 1,
    }
    base.update(overrides)
    return credential_aad(**base)


def _cipher(key_id: str = "v1", key: str | None = None) -> CredentialCipher:
    return CredentialCipher(parse_key_ring(f"{key_id}:{key or generate_key()}"), key_id)


def test_round_trip_and_random_nonce() -> None:
    cipher = _cipher()
    one = cipher.encrypt("mock:page:1:1", aad=_aad())
    two = cipher.encrypt("mock:page:1:1", aad=_aad())
    assert one.nonce != two.nonce and one.ciphertext != two.ciphertext
    assert cipher.decrypt(one, aad=_aad()) == "mock:page:1:1"
    assert b"mock:page" not in one.ciphertext


def test_wrong_key_same_id_fails_closed() -> None:
    sealed = _cipher().encrypt("secret-token", aad=_aad())
    other = _cipher()  # different random key, same id "v1"
    with pytest.raises(CredentialUndecryptableError):
        other.decrypt(sealed, aad=_aad())


def test_tampered_ciphertext_or_moved_row_is_rejected() -> None:
    cipher = _cipher()
    sealed = cipher.encrypt("secret-token", aad=_aad())
    flipped = bytes([sealed.ciphertext[0] ^ 1]) + sealed.ciphertext[1:]
    with pytest.raises(CredentialUndecryptableError):
        cipher.decrypt(EncryptedSecret("v1", sealed.nonce, flipped), aad=_aad())
    # Same ciphertext bound to another organization / subject / version does not decrypt.
    with pytest.raises(CredentialUndecryptableError):
        cipher.decrypt(sealed, aad=_aad(organization_id=uuid.uuid4()))
    with pytest.raises(CredentialUndecryptableError):
        cipher.decrypt(sealed, aad=_aad(subject="conn:page2"))
    with pytest.raises(CredentialUndecryptableError):
        cipher.decrypt(sealed, aad=_aad(version=2))


def test_unknown_key_id_and_missing_key_fail_closed() -> None:
    sealed = _cipher("v1").encrypt("secret-token", aad=_aad())
    only_v2 = _cipher("v2")
    with pytest.raises(UnknownKeyIdError):
        only_v2.decrypt(sealed, aad=_aad())
    settings = Settings(credential_encryption_keys=None, credential_encryption_active_key_id=None)
    with pytest.raises(EncryptionKeyMissingError):
        CredentialCipher.from_settings(settings)
    with pytest.raises(UnknownKeyIdError):
        CredentialCipher.from_settings(
            Settings(
                credential_encryption_keys=f"v1:{generate_key()},v2:{generate_key()}",
                credential_encryption_active_key_id="v3",
            )
        )
    # Two keys without an explicit active id is ambiguous → refused.
    with pytest.raises(UnknownKeyIdError):
        CredentialCipher.from_settings(
            Settings(credential_encryption_keys=f"v1:{generate_key()},v2:{generate_key()}")
        )


def test_rotation_keeps_old_rows_readable_until_reencrypted() -> None:
    k1, k2 = generate_key(), generate_key()
    old = CredentialCipher(parse_key_ring(f"v1:{k1}"), "v1")
    sealed = old.encrypt("secret-token", aad=_aad())
    ring = CredentialCipher(parse_key_ring(f"v1:{k1},v2:{k2}"), "v2")
    assert ring.decrypt(sealed, aad=_aad()) == "secret-token"
    resealed = ring.encrypt("secret-token", aad=_aad(key_id="v2"))
    assert resealed.key_id == "v2"
    new_only = CredentialCipher(parse_key_ring(f"v2:{k2}"), "v2")
    assert new_only.decrypt(resealed, aad=_aad(key_id="v2")) == "secret-token"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "v1",
        "v1:not-base64!!",
        "v1:" + base64.b64encode(b"short").decode(),
        "bad id:" + generate_key(),
        f"v1:{generate_key()},v1:{generate_key()}",
    ],
)
def test_malformed_key_rings_are_refused(raw: str) -> None:
    with pytest.raises(EncryptionKeyMissingError):
        parse_key_ring(raw)


def test_key_ring_accepts_urlsafe_and_unpadded_keys() -> None:
    raw = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")
    assert parse_key_ring(f"k:{raw}")["k"] == bytes(range(32))


def test_fingerprint_is_one_way_and_stable() -> None:
    assert crypto.fingerprint("a") == crypto.fingerprint("a") != crypto.fingerprint("b")
    assert len(crypto.fingerprint("x")) == 64


def test_mock_mode_is_refused_outside_development() -> None:
    with pytest.raises(ValueError, match="META_PROVIDER_MODE=mock"):
        Settings(app_env="production", meta_provider_mode="mock")
    with pytest.raises(ValueError, match="META_PROVIDER_MODE=mock"):
        Settings(app_env="staging", meta_provider_mode="mock")
    assert Settings(app_env="development", meta_provider_mode="mock").is_meta_configured
    with pytest.raises(ValueError, match="https"):
        Settings(app_env="production", meta_oauth_redirect_uri="http://nas.local/cb")
