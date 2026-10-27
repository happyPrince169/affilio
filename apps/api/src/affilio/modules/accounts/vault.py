"""Credential store: encrypted token rows and the only code path that sees plaintext.

Routers never call this module directly; the connections service does, and it passes the
decrypted value straight into a provider call. Responses carry `CredentialSummary` only.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from affilio.core.config import Settings
from affilio.core.enums import CredentialKind, CredentialStatus
from affilio.modules.accounts.crypto import (
    CredentialCipher,
    EncryptedSecret,
    credential_aad,
    fingerprint,
    get_cipher,
)
from affilio.modules.accounts.models import AccountCredential, PlatformConnection


def _now() -> datetime:
    return datetime.now(UTC)


def ensure_vault(settings: Settings) -> CredentialCipher:
    """Raise `CredentialVaultError` (503) when no usable key is configured. Fail closed."""
    return get_cipher(settings)


def active_credential(
    session: Session,
    *,
    connection: PlatformConnection,
    subject: str,
    kind: CredentialKind,
) -> AccountCredential | None:
    return session.scalar(
        select(AccountCredential).where(
            AccountCredential.connection_id == connection.id,
            AccountCredential.external_subject_id == subject,
            AccountCredential.kind == kind,
            AccountCredential.status == CredentialStatus.ACTIVE,
        )
    )


def store_token(
    session: Session,
    cipher: CredentialCipher,
    *,
    connection: PlatformConnection,
    subject: str,
    kind: CredentialKind,
    plaintext: str,
    scopes: list[str],
    page_tasks: list[str] | None,
    expires_at: datetime | None,
    data_access_expires_at: datetime | None,
    social_account_id: uuid.UUID | None,
    actor: str,
    is_mock: bool,
) -> AccountCredential:
    """Encrypt and store a token as a new version; the previous active row is superseded.

    The ciphertext is bound (AAD) to organization, connection, subject, kind, key id and
    version, so a row copied elsewhere cannot be decrypted.
    """
    previous = active_credential(session, connection=connection, subject=subject, kind=kind)
    version = 1
    if previous is not None:
        previous.status = CredentialStatus.SUPERSEDED
        previous.invalidated_reason = "superseded"
        version = previous.version + 1
        if social_account_id is None:
            social_account_id = previous.social_account_id
        session.flush()
    sealed = cipher.encrypt(
        plaintext,
        aad=credential_aad(
            organization_id=connection.organization_id,
            subject=f"{connection.id}:{subject}",
            kind=kind.value,
            key_id=cipher.active_key_id,
            version=version,
        ),
    )
    row = AccountCredential(
        id=uuid.uuid4(),
        organization_id=connection.organization_id,
        connection_id=connection.id,
        social_account_id=social_account_id,
        external_subject_id=subject,
        kind=kind,
        version=version,
        status=CredentialStatus.ACTIVE,
        ciphertext=sealed.ciphertext,
        nonce=sealed.nonce,
        key_id=sealed.key_id,
        token_fingerprint=fingerprint(plaintext),
        scopes=list(scopes),
        page_tasks=list(page_tasks) if page_tasks is not None else None,
        issued_at=_now(),
        expires_at=expires_at,
        data_access_expires_at=data_access_expires_at,
        is_mock=is_mock,
        created_by_label=actor[:255],
    )
    session.add(row)
    session.flush()
    return row


def open_token(cipher: CredentialCipher, credential: AccountCredential) -> str:
    """Decrypt for immediate use by a provider call. Raises `CredentialUndecryptableError`."""
    plaintext = cipher.decrypt(
        EncryptedSecret(
            key_id=credential.key_id, nonce=credential.nonce, ciphertext=credential.ciphertext
        ),
        aad=credential_aad(
            organization_id=credential.organization_id,
            subject=f"{credential.connection_id}:{credential.external_subject_id}",
            kind=credential.kind.value,
            key_id=credential.key_id,
            version=credential.version,
        ),
    )
    credential.last_used_at = _now()
    return plaintext


def invalidate(
    credential: AccountCredential, *, status: CredentialStatus, reason: str
) -> AccountCredential:
    credential.status = status
    credential.invalidated_reason = reason[:64]
    return credential


def rotate_all(
    session: Session, cipher: CredentialCipher, organization_id: uuid.UUID | None
) -> int:
    """Re-encrypt every active credential under the active key. Used by the rotation script."""
    stmt = select(AccountCredential).where(AccountCredential.status == CredentialStatus.ACTIVE)
    if organization_id is not None:
        stmt = stmt.where(AccountCredential.organization_id == organization_id)
    count = 0
    for credential in session.scalars(stmt):
        if credential.key_id == cipher.active_key_id:
            continue
        plaintext = open_token(cipher, credential)
        sealed = cipher.encrypt(
            plaintext,
            aad=credential_aad(
                organization_id=credential.organization_id,
                subject=f"{credential.connection_id}:{credential.external_subject_id}",
                kind=credential.kind.value,
                key_id=cipher.active_key_id,
                version=credential.version,
            ),
        )
        credential.ciphertext, credential.nonce, credential.key_id = (
            sealed.ciphertext,
            sealed.nonce,
            sealed.key_id,
        )
        count += 1
    session.flush()
    return count
