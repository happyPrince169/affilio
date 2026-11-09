"""Identity services: credentials, sessions, memberships, integration API keys."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import available_timezones

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, selectinload

from affilio.core.enums import MembershipRole, UserStatus
from affilio.core.errors import AppError, ConflictError, NotFoundError
from affilio.core.security import (
    api_key_display_prefix,
    generate_api_key,
    generate_session_token,
    hash_api_key,
    hash_password,
    hash_session_token,
    looks_like_api_key,
    password_needs_rehash,
    tokens_equal,
    verify_password,
)
from affilio.modules.identity.models import (
    AuditEvent,
    IntegrationApiKey,
    Organization,
    OrganizationMembership,
    User,
    UserSession,
)

ROLE_RANK: dict[MembershipRole, int] = {
    MembershipRole.VIEWER: 0,
    MembershipRole.EDITOR: 1,
    MembershipRole.ADMIN: 2,
    MembershipRole.OWNER: 3,
}
SESSION_TOUCH_INTERVAL = timedelta(minutes=15)
API_KEY_TOUCH_INTERVAL = timedelta(minutes=1)

# Scopes an integration key may carry (ADR 0013). Adding one is a code change on purpose.
INTEGRATION_SCOPES: frozenset[str] = frozenset({"products:ingest"})
SCOPE_PRODUCTS_INGEST = "products:ingest"
MAX_API_KEYS_PER_ORGANIZATION = 50


class InvalidCredentialsError(AppError):
    status_code = 401
    code = "invalid_credentials"


class InvalidTimezoneError(AppError):
    status_code = 422
    code = "invalid_timezone"


class UnknownScopeError(AppError):
    status_code = 422
    code = "unknown_scope"


class IntegrationKeyNotActiveError(AppError):
    status_code = 409
    code = "integration_key_not_active"


def role_at_least(role: MembershipRole, minimum: MembershipRole) -> bool:
    return ROLE_RANK[role] >= ROLE_RANK[minimum]


# ---------------------------------------------------------------- credentials


def authenticate(session: Session, email: str, password: str) -> User:
    user = session.scalar(
        select(User)
        .options(selectinload(User.memberships).selectinload(OrganizationMembership.organization))
        .where(User.email == email.strip().lower())
    )
    ok = verify_password(password, user.password_hash if user else None)
    if user is None or not ok or user.status != UserStatus.ACTIVE:
        raise InvalidCredentialsError("Invalid email or password")
    if user.password_hash and password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = datetime.now(UTC)
    return user


def create_user(
    session: Session,
    *,
    email: str,
    password: str | None,
    display_name: str | None = None,
    status: UserStatus = UserStatus.ACTIVE,
) -> User:
    normalized = email.strip().lower()
    if session.scalar(select(User).where(User.email == normalized)) is not None:
        raise ConflictError("A user with this email already exists", details={"email": normalized})
    user = User(
        id=uuid.uuid4(),
        email=normalized,
        password_hash=hash_password(password) if password else None,
        display_name=(display_name or "").strip() or None,
        status=status,
    )
    session.add(user)
    session.flush()
    return user


def set_password(user: User, new_password: str) -> None:
    user.password_hash = hash_password(new_password)


def add_membership(
    session: Session, organization: Organization, user: User, role: MembershipRole
) -> OrganizationMembership:
    existing = session.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization.id,
            OrganizationMembership.user_id == user.id,
        )
    )
    if existing is not None:
        existing.role = role
        return existing
    membership = OrganizationMembership(
        id=uuid.uuid4(), organization_id=organization.id, user_id=user.id, role=role
    )
    session.add(membership)
    session.flush()
    return membership


# ---------------------------------------------------------------- sessions


def create_session(
    session: Session,
    user: User,
    *,
    ttl: timedelta,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> tuple[UserSession, str]:
    raw = generate_session_token()
    now = datetime.now(UTC)
    record = UserSession(
        id=uuid.uuid4(),
        user_id=user.id,
        token_hash=hash_session_token(raw),
        expires_at=now + ttl,
        last_seen_at=now,
        user_agent=(user_agent or "")[:255] or None,
        ip_address=(ip_address or "")[:64] or None,
    )
    session.add(record)
    session.flush()
    return record, raw


def resolve_session(
    session: Session, raw_token: str, *, ttl: timedelta
) -> tuple[UserSession, User] | None:
    """Return the live session + user for a cookie token, sliding the expiry forward."""
    if not raw_token or len(raw_token) > 256:
        return None
    record = session.scalar(
        select(UserSession)
        .options(
            selectinload(UserSession.user)
            .selectinload(User.memberships)
            .selectinload(OrganizationMembership.organization)
        )
        .where(UserSession.token_hash == hash_session_token(raw_token))
    )
    now = datetime.now(UTC)
    if record is None or record.revoked_at is not None or record.expires_at <= now:
        return None
    if record.user.status != UserStatus.ACTIVE:
        return None
    if now - record.last_seen_at > SESSION_TOUCH_INTERVAL:
        record.last_seen_at = now
        record.expires_at = now + ttl
    return record, record.user


def revoke_session(record: UserSession) -> None:
    record.revoked_at = datetime.now(UTC)


def revoke_all_sessions(session: Session, user: User) -> int:
    count = 0
    for record in session.scalars(
        select(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
    ):
        record.revoked_at = datetime.now(UTC)
        count += 1
    return count


def purge_sessions(session: Session, *, retention: timedelta) -> int:
    """Delete sessions that expired or were revoked more than `retention` ago (M3B.1A).

    Live sessions are never touched: a row is removed only when it could not authenticate
    anyway. Audit events keep the user id, so no history is lost.
    """
    cutoff = datetime.now(UTC) - retention
    result = session.execute(
        delete(UserSession).where(
            or_(UserSession.expires_at < cutoff, UserSession.revoked_at < cutoff)
        )
    )
    return int(result.rowcount or 0)


def revoke_sessions_for_user(session: Session, user_id: uuid.UUID) -> int:
    """Admin "sign out everywhere" for a member (M3B.1A). Returns the number revoked."""
    count = 0
    for record in session.scalars(
        select(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
    ):
        record.revoked_at = datetime.now(UTC)
        count += 1
    return count


# ---------------------------------------------------------------- memberships


def memberships_for(user: User) -> list[OrganizationMembership]:
    return sorted(user.memberships, key=lambda m: m.created_at or datetime.min.replace(tzinfo=UTC))


def membership_for(user: User, slug: str | None) -> OrganizationMembership | None:
    memberships = memberships_for(user)
    if not memberships:
        return None
    if slug:
        return next((m for m in memberships if m.organization.slug == slug), None)
    return memberships[0]


def list_members(session: Session, organization_id: uuid.UUID) -> list[OrganizationMembership]:
    return list(
        session.scalars(
            select(OrganizationMembership)
            .options(selectinload(OrganizationMembership.user))
            .where(OrganizationMembership.organization_id == organization_id)
            .order_by(OrganizationMembership.created_at)
        )
    )


def get_member(
    session: Session, organization_id: uuid.UUID, user_id: uuid.UUID
) -> OrganizationMembership:
    membership = session.scalar(
        select(OrganizationMembership)
        .options(selectinload(OrganizationMembership.user))
        .where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id == user_id,
        )
    )
    if membership is None:
        raise NotFoundError("Member not found")
    return membership


def update_organization(
    organization: Organization, *, name: str | None, timezone: str | None
) -> None:
    if name is not None:
        organization.name = name.strip()
    if timezone is not None:
        if timezone not in available_timezones():
            raise InvalidTimezoneError(f"Unknown IANA timezone '{timezone}'")
        organization.timezone = timezone


def list_audit(
    session: Session,
    organization_id: uuid.UUID,
    *,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    limit: int = 50,
) -> list[AuditEvent]:
    stmt = select(AuditEvent).where(AuditEvent.organization_id == organization_id)
    if entity_type:
        stmt = stmt.where(AuditEvent.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditEvent.entity_id == entity_id)
    return list(
        session.scalars(stmt.order_by(AuditEvent.created_at.desc()).limit(min(max(limit, 1), 200)))
    )


# ---------------------------------------------------------------- integration API keys


def validate_scopes(scopes: list[str]) -> list[str]:
    cleaned: list[str] = []
    for scope in scopes:
        value = scope.strip()
        if value not in INTEGRATION_SCOPES:
            raise UnknownScopeError(
                f"Unknown scope '{value[:40]}'", details={"known": sorted(INTEGRATION_SCOPES)}
            )
        if value not in cleaned:
            cleaned.append(value)
    return cleaned


def create_api_key(
    session: Session,
    organization: Organization,
    *,
    name: str,
    scopes: list[str],
    created_by: User | None,
    expires_in_days: int | None = None,
    rotated_from: IntegrationApiKey | None = None,
) -> tuple[IntegrationApiKey, str]:
    """Create a key and return (row, plaintext). The plaintext is never stored or logged."""
    active = session.scalar(
        select(func.count(IntegrationApiKey.id)).where(
            IntegrationApiKey.organization_id == organization.id,
            IntegrationApiKey.revoked_at.is_(None),
        )
    )
    if (active or 0) >= MAX_API_KEYS_PER_ORGANIZATION:
        raise ConflictError(
            f"At most {MAX_API_KEYS_PER_ORGANIZATION} active integration keys per organization"
        )
    raw = generate_api_key()
    now = datetime.now(UTC)
    record = IntegrationApiKey(
        id=uuid.uuid4(),
        organization_id=organization.id,
        name=name.strip()[:120],
        prefix=api_key_display_prefix(raw),
        key_hash=hash_api_key(raw),
        scopes=validate_scopes(scopes),
        created_by_user_id=created_by.id if created_by is not None else None,
        expires_at=now + timedelta(days=expires_in_days) if expires_in_days else None,
        rotated_from_key_id=rotated_from.id if rotated_from is not None else None,
    )
    session.add(record)
    session.flush()
    return record, raw


def list_api_keys(session: Session, organization_id: uuid.UUID) -> list[IntegrationApiKey]:
    return list(
        session.scalars(
            select(IntegrationApiKey)
            .where(IntegrationApiKey.organization_id == organization_id)
            .order_by(IntegrationApiKey.created_at.desc())
        )
    )


def get_api_key(
    session: Session, organization_id: uuid.UUID, key_id: uuid.UUID
) -> IntegrationApiKey:
    record = session.scalar(
        select(IntegrationApiKey).where(
            IntegrationApiKey.organization_id == organization_id, IntegrationApiKey.id == key_id
        )
    )
    if record is None:
        raise NotFoundError("Integration key not found")
    return record


def revoke_api_key(record: IntegrationApiKey, *, revoked_by: User | None) -> None:
    if record.revoked_at is not None:
        raise IntegrationKeyNotActiveError("Integration key is already revoked")
    record.revoked_at = datetime.now(UTC)
    record.revoked_by_user_id = revoked_by.id if revoked_by is not None else None


def rotate_api_key(
    session: Session, record: IntegrationApiKey, *, rotated_by: User | None
) -> tuple[IntegrationApiKey, str]:
    """Issue a replacement with the same name and scopes and revoke the old key at once.

    Both steps happen in the caller's transaction, so there is never a moment with two live
    keys for the same purpose nor one with none committed.
    """
    if not record.is_active(datetime.now(UTC)):
        raise IntegrationKeyNotActiveError("Only an active integration key can be rotated")
    organization = record.organization
    replacement, raw = create_api_key(
        session,
        organization,
        name=record.name,
        scopes=list(record.scopes),
        created_by=rotated_by,
        rotated_from=record,
    )
    revoke_api_key(record, revoked_by=rotated_by)
    return replacement, raw


def resolve_api_key(session: Session, raw: str) -> IntegrationApiKey | None:
    """Return the key row for a bearer secret, in whatever state, or None when unknown.

    The lookup is by SHA-256; the stored digest is then compared in constant time so the
    path taken does not depend on how many digest bytes match. Callers decide what a revoked
    or expired row means (`is_active`) and call `touch_api_key` on success.
    """
    if not looks_like_api_key(raw):
        return None
    digest = hash_api_key(raw)
    record = session.scalar(
        select(IntegrationApiKey)
        .options(selectinload(IntegrationApiKey.organization))
        .where(IntegrationApiKey.key_hash == digest)
    )
    if record is None or not tokens_equal(record.key_hash, digest):
        return None
    return record


def touch_api_key(record: IntegrationApiKey, now: datetime | None = None) -> None:
    """Record use at most once per `API_KEY_TOUCH_INTERVAL` (one write, not one per call)."""
    now = now or datetime.now(UTC)
    if record.last_used_at is None or now - record.last_used_at > API_KEY_TOUCH_INTERVAL:
        record.last_used_at = now


def api_key_state(record: IntegrationApiKey, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    if record.revoked_at is not None:
        return "revoked"
    if record.expires_at is not None and record.expires_at <= now:
        return "expired"
    return "active"
