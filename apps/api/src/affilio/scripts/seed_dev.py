"""DEVELOPMENT-ONLY seed.

Creates one demo organization, one owner user that can sign in with the development password
from `SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD`, plus one legacy placeholder user, and ONE
placeholder Shopee social account with status `pending`. Nothing is connected to a real platform
and no platform credentials are stored. The script refuses to run outside `development`/`test`
environments and is idempotent (safe to re-run).

    uv run python -m affilio.scripts.seed_dev
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from affilio.core.config import Settings, get_settings
from affilio.core.enums import MembershipRole, Platform, SocialAccountStatus, UserStatus
from affilio.core.security import hash_password
from affilio.db.session import get_session_factory
from affilio.modules.accounts.models import SocialAccount
from affilio.modules.identity.models import Organization, OrganizationMembership, User

DEMO_ORG_SLUG = "demo-org"
DEMO_ORG_NAME = "Demo Organization (DEV ONLY)"
DEMO_USER_EMAIL = "dev@affilio.local"
DEMO_SHOPEE_ACCOUNT_NAME = "Placeholder Shopee Affiliate (DEV — not connected)"

ALLOWED_ENVIRONMENTS = {"development", "test"}


@dataclass(slots=True)
class SeedResult:
    organization_created: bool
    user_created: bool
    membership_created: bool
    social_account_created: bool
    owner_created: bool = False
    owner_email: str = ""

    @property
    def created_anything(self) -> bool:
        return any(
            (
                self.organization_created,
                self.user_created,
                self.membership_created,
                self.social_account_created,
                self.owner_created,
            )
        )


def _ensure_owner(session: Session, organization: Organization, settings: Settings) -> bool:
    """Owner that can sign in locally. Password comes from settings, never from code."""
    email = settings.seed_admin_email.strip().lower()
    owner = session.scalar(select(User).where(User.email == email))
    created = owner is None
    if owner is None:
        owner = User(
            email=email,
            password_hash=hash_password(settings.seed_admin_password.get_secret_value()),
            display_name="Local owner (DEV)",
            status=UserStatus.ACTIVE,
        )
        session.add(owner)
        session.flush()
    elif owner.password_hash is None:
        owner.password_hash = hash_password(settings.seed_admin_password.get_secret_value())
    membership = session.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization.id,
            OrganizationMembership.user_id == owner.id,
        )
    )
    if membership is None:
        session.add(
            OrganizationMembership(
                organization_id=organization.id, user_id=owner.id, role=MembershipRole.OWNER
            )
        )
    return created


def seed(session: Session, settings: Settings | None = None) -> SeedResult:
    """Insert the demo records if they do not exist. Caller owns the transaction."""
    settings = settings or get_settings()
    organization = session.scalar(select(Organization).where(Organization.slug == DEMO_ORG_SLUG))
    organization_created = organization is None
    if organization is None:
        organization = Organization(name=DEMO_ORG_NAME, slug=DEMO_ORG_SLUG)
        session.add(organization)

    user = session.scalar(select(User).where(User.email == DEMO_USER_EMAIL))
    user_created = user is None
    if user is None:
        user = User(email=DEMO_USER_EMAIL, password_hash=None, status=UserStatus.ACTIVE)
        session.add(user)

    session.flush()  # assign ids before creating links

    membership = session.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization.id,
            OrganizationMembership.user_id == user.id,
        )
    )
    membership_created = membership is None
    if membership is None:
        session.add(
            OrganizationMembership(
                organization_id=organization.id, user_id=user.id, role=MembershipRole.OWNER
            )
        )

    account = session.scalar(
        select(SocialAccount).where(
            SocialAccount.organization_id == organization.id,
            SocialAccount.platform == Platform.SHOPEE,
            SocialAccount.display_name == DEMO_SHOPEE_ACCOUNT_NAME,
        )
    )
    social_account_created = account is None
    if account is None:
        session.add(
            SocialAccount(
                organization_id=organization.id,
                platform=Platform.SHOPEE,
                display_name=DEMO_SHOPEE_ACCOUNT_NAME,
                external_account_id=None,
                status=SocialAccountStatus.PENDING,
            )
        )

    owner_created = _ensure_owner(session, organization, settings)

    return SeedResult(
        organization_created=organization_created,
        user_created=user_created,
        membership_created=membership_created,
        social_account_created=social_account_created,
        owner_created=owner_created,
        owner_email=settings.seed_admin_email,
    )


def main(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    if settings.app_env not in ALLOWED_ENVIRONMENTS:
        print(
            f"Refusing to seed: APP_ENV={settings.app_env!r} is not one of "
            f"{sorted(ALLOWED_ENVIRONMENTS)}. This seed is development-only.",
            file=sys.stderr,
        )
        return 2

    session_factory = get_session_factory()
    with session_factory() as session, session.begin():
        result = seed(session, settings)

    def outcome(created: bool) -> str:
        return "created" if created else "exists"

    print("AFFILIO development seed (DEV ONLY — placeholder data, nothing is connected)")
    print(f"  organization  {DEMO_ORG_SLUG:<40} {outcome(result.organization_created)}")
    print(f"  user          {DEMO_USER_EMAIL:<40} {outcome(result.user_created)}")
    print(f"  membership    {'owner':<40} {outcome(result.membership_created)}")
    print(f"  social acct   {'shopee / pending':<40} {outcome(result.social_account_created)}")
    print(f"  owner login   {result.owner_email:<40} {outcome(result.owner_created)}")
    print("  password      from SEED_ADMIN_PASSWORD (see .env.example); change it after login")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
