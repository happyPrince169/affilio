"""Identity services: credentials, sessions, memberships."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import available_timezones

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session, selectinload

from affilio.core.enums import MembershipRole, UserStatus
from affilio.core.errors import AppError, ConflictError, NotFoundError
from affilio.core.security import (
    generate_session_token,
    hash_password,
    hash_session_token,
    password_needs_rehash,
    verify_password,
)
from affilio.modules.identity.models import (
    AuditEvent,
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


class InvalidCredentialsError(AppError):
    status_code = 401
    code = "invalid_credentials"


class InvalidTimezoneError(AppError):
    status_code = 422
    code = "invalid_timezone"


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
