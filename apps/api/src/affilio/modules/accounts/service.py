"""Social account registry. Manual records are explicitly unverified; OAuth arrives in M3B."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from affilio.core.enums import Platform, SocialAccountSource, SocialAccountStatus
from affilio.core.errors import ConflictError, NotFoundError
from affilio.modules.accounts.models import ChannelCategoryRule, ChannelProfile, SocialAccount
from affilio.modules.accounts.schemas import (
    ChannelCategoryRuleResponse,
    ChannelProfileResponse,
    ChannelProfileUpdate,
    SocialAccountCreate,
    SocialAccountResponse,
    SocialAccountStatusChange,
    SocialAccountUpdate,
)
from affilio.modules.taxonomy import service as taxonomy_service
from affilio.modules.taxonomy.service import CategoryTree

# Platform capability facts as understood today. Copied onto each registered account so the
# UI and adapters never claim more than this. api_publishing stays False until M3B.
PLATFORM_CAPABILITIES: dict[Platform, dict[str, object]] = {
    Platform.FACEBOOK: {
        "assisted_publishing": True,
        "api_publishing": False,
        "scheduling_via_api": False,
        "clickable_links_in_caption": False,
        "reels": True,
        "max_caption_length": 2200,
        "vertical_video": True,
    },
    Platform.TIKTOK: {"assisted_publishing": True, "api_publishing": False, "vertical_video": True},
    Platform.INSTAGRAM: {
        "assisted_publishing": True,
        "api_publishing": False,
        "vertical_video": True,
    },
    Platform.YOUTUBE: {
        "assisted_publishing": True,
        "api_publishing": False,
        "vertical_video": True,
    },
    Platform.SHOPEE: {"assisted_publishing": True, "api_publishing": False, "vertical_video": True},
}


def to_response(account: SocialAccount) -> SocialAccountResponse:
    return SocialAccountResponse(
        id=account.id,
        platform=account.platform,
        account_type=account.account_type,
        display_name=account.display_name,
        handle=account.handle,
        external_account_id=account.external_account_id,
        profile_url=account.profile_url,
        status=account.status,
        source=account.source,
        is_verified=account.is_verified,
        connection_label=account.connection_label,
        capabilities=dict(account.capabilities or {}),
        notes=account.notes,
        registered_by=account.registered_by,
        created_at=account.created_at,
        updated_at=account.updated_at,
    )


def list_accounts(
    session: Session,
    organization_id: uuid.UUID,
    *,
    platform: Platform | None = None,
    include_disabled: bool = True,
) -> list[SocialAccount]:
    stmt = select(SocialAccount).where(SocialAccount.organization_id == organization_id)
    if platform:
        stmt = stmt.where(SocialAccount.platform == platform)
    if not include_disabled:
        stmt = stmt.where(SocialAccount.status != SocialAccountStatus.DISABLED)
    return list(session.scalars(stmt.order_by(SocialAccount.display_name)))


def get_account(
    session: Session, organization_id: uuid.UUID, account_id: uuid.UUID
) -> SocialAccount:
    account = session.scalar(
        select(SocialAccount).where(
            SocialAccount.organization_id == organization_id, SocialAccount.id == account_id
        )
    )
    if account is None:
        raise NotFoundError("Social account not found")
    return account


def register_manual(
    session: Session, organization_id: uuid.UUID, data: SocialAccountCreate, *, actor: str
) -> SocialAccount:
    if data.external_account_id:
        clash = session.scalar(
            select(SocialAccount).where(
                SocialAccount.organization_id == organization_id,
                SocialAccount.platform == data.platform,
                SocialAccount.external_account_id == data.external_account_id,
            )
        )
        if clash is not None:
            raise ConflictError(
                "An account with this platform id is already registered",
                details={"account_id": str(clash.id)},
            )
    account = SocialAccount(
        id=uuid.uuid4(),
        organization_id=organization_id,
        platform=data.platform,
        account_type=data.account_type,
        display_name=data.display_name,
        handle=data.handle,
        external_account_id=data.external_account_id,
        profile_url=data.profile_url,
        notes=(data.notes or "").strip() or None,
        # Manual records are never "connected": no token, no verification.
        status=SocialAccountStatus.DISCONNECTED,
        source=SocialAccountSource.MANUAL,
        is_verified=False,
        capabilities=dict(PLATFORM_CAPABILITIES.get(data.platform, {"assisted_publishing": True})),
        registered_by=actor,
    )
    session.add(account)
    session.flush()
    return account


def update_account(
    session: Session, organization_id: uuid.UUID, account_id: uuid.UUID, data: SocialAccountUpdate
) -> SocialAccount:
    account = get_account(session, organization_id, account_id)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(account, key, value)
    session.flush()
    return account


def change_status(
    session: Session,
    organization_id: uuid.UUID,
    account_id: uuid.UUID,
    data: SocialAccountStatusChange,
) -> SocialAccount:
    account = get_account(session, organization_id, account_id)
    # Only disable / re-enable (to disconnected) is possible manually; CONNECTED needs OAuth (M3B).
    account.status = SocialAccountStatus(data.status)
    if data.note:
        account.notes = f"{account.notes}\n{data.note}".strip() if account.notes else data.note
    session.flush()
    return account


def publishable_url(account: SocialAccount) -> str | None:
    """Best-effort URL for an operator to open the Page; never fabricated beyond known fields."""
    if account.profile_url:
        return account.profile_url
    if account.platform == Platform.FACEBOOK:
        if account.handle:
            return f"https://www.facebook.com/{account.handle}"
        if account.external_account_id and account.external_account_id.isdigit():
            return f"https://www.facebook.com/{account.external_account_id}"
    return None


# ---------------------------------------------------------------- channel routing (M3A.1)


def list_accounts_with_routing(session: Session, organization_id: uuid.UUID) -> list[SocialAccount]:
    """Accounts with profiles and rules eagerly loaded (used by the routing engine)."""
    return list(
        session.scalars(
            select(SocialAccount)
            .options(
                selectinload(SocialAccount.channel_profile),
                selectinload(SocialAccount.category_rules),
            )
            .where(SocialAccount.organization_id == organization_id)
            .order_by(SocialAccount.display_name)
        )
    )


def profile_response(account: SocialAccount, tree: CategoryTree) -> ChannelProfileResponse:
    profile = account.channel_profile
    rules = sorted(account.category_rules, key=lambda r: (-r.priority, r.created_at or 0))
    return ChannelProfileResponse(
        social_account_id=account.id,
        primary_category=(
            tree.ref(profile.primary_category_id)
            if profile and profile.primary_category_id and profile.primary_category_id in tree.by_id
            else None
        ),
        description=profile.description if profile else None,
        target_audience=profile.target_audience if profile else None,
        language=profile.language if profile else None,
        country=profile.country if profile else None,
        routing_enabled=profile.routing_enabled if profile else True,
        rules=[
            ChannelCategoryRuleResponse(
                category=tree.ref(r.category_id), match_policy=r.match_policy, priority=r.priority
            )
            for r in rules
            if r.category_id in tree.by_id
        ],
        updated_at=profile.updated_at if profile else None,
    )


def get_channel_profile(
    session: Session, organization_id: uuid.UUID, account_id: uuid.UUID
) -> tuple[SocialAccount, CategoryTree]:
    account = session.scalar(
        select(SocialAccount)
        .options(
            selectinload(SocialAccount.channel_profile), selectinload(SocialAccount.category_rules)
        )
        .where(SocialAccount.organization_id == organization_id, SocialAccount.id == account_id)
    )
    if account is None:
        raise NotFoundError("Social account not found")
    return account, taxonomy_service.load_tree(session, organization_id)


def replace_channel_profile(
    session: Session, organization_id: uuid.UUID, account_id: uuid.UUID, data: ChannelProfileUpdate
) -> tuple[SocialAccount, CategoryTree]:
    account, tree = get_channel_profile(session, organization_id, account_id)
    wanted = [r.category_id for r in data.rules]
    if data.primary_category_id is not None:
        wanted.append(data.primary_category_id)
    taxonomy_service.get_categories(session, organization_id, wanted)  # 404 on foreign ids
    profile = account.channel_profile
    if profile is None:
        profile = ChannelProfile(
            id=uuid.uuid4(), organization_id=organization_id, social_account_id=account.id
        )
        account.channel_profile = profile
    profile.primary_category_id = data.primary_category_id
    profile.description = (data.description or "").strip() or None
    profile.target_audience = (data.target_audience or "").strip() or None
    profile.language = (data.language or "").strip() or None
    profile.country = data.country
    profile.routing_enabled = data.routing_enabled
    account.category_rules.clear()
    session.flush()
    for rule in data.rules:
        account.category_rules.append(
            ChannelCategoryRule(
                id=uuid.uuid4(),
                organization_id=organization_id,
                social_account_id=account.id,
                category_id=rule.category_id,
                match_policy=rule.match_policy,
                priority=rule.priority,
            )
        )
    session.flush()
    return account, tree


def evaluate_target(
    session: Session,
    organization_id: uuid.UUID,
    account_id: uuid.UUID,
    *,
    primary_id: uuid.UUID | None,
    secondary_ids: list[uuid.UUID],
):
    """Current routing verdict for one account against a classification (publishing re-checks
    this before approval, scheduling and every publishing step)."""
    from affilio.modules.accounts import routing_engine

    account = session.scalar(
        select(SocialAccount)
        .options(
            selectinload(SocialAccount.channel_profile), selectinload(SocialAccount.category_rules)
        )
        .where(SocialAccount.organization_id == organization_id, SocialAccount.id == account_id)
    )
    if account is None:
        raise NotFoundError("Social account not found")
    tree = taxonomy_service.load_tree(session, organization_id)
    return routing_engine.evaluate_account(
        account, primary_id=primary_id, secondary_ids=secondary_ids, tree=tree
    )
