"""Is a publication eligible for API (engine-driven) publishing right now? (M3B.2A)

Shared by the service layer (operator "Start") and the publisher (scheduled dispatch) so both
paths apply the same rules. Returns a reason code instead of raising: assisted publishing
remains the fallback for everything that is not eligible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from affilio.core.config import Settings
from affilio.core.enums import Platform
from affilio.modules.accounts.models import SocialAccount
from affilio.modules.publishing.models import PublicationJob


@dataclass(frozen=True, slots=True)
class Eligibility:
    eligible: bool
    reason: str | None = None  # machine-readable; None when eligible


def account_api_eligibility(account: SocialAccount, settings: Settings) -> Eligibility:
    if account.platform != Platform.FACEBOOK:
        return Eligibility(False, "platform_not_supported")
    if not account.is_connected:
        return Eligibility(False, "account_not_connected")
    if not account.can_publish:
        return Eligibility(False, f"permission_{account.permission_status}")
    if account.is_mock:
        if settings.meta_provider_mode != "mock":
            return Eligibility(False, "mock_account_in_live_mode")
        return Eligibility(True)
    if settings.meta_provider_mode == "mock":
        return Eligibility(False, "real_account_in_mock_mode")
    if not settings.meta_live_publishing_enabled:
        return Eligibility(False, "live_publishing_disabled")
    if not settings.is_meta_configured:
        return Eligibility(False, "meta_not_configured")
    return Eligibility(True)


def job_api_eligibility(job: PublicationJob, settings: Settings) -> Eligibility:
    return account_api_eligibility(job.social_account, settings)


def page_hold(account: SocialAccount, now: datetime | None = None) -> datetime | None:
    """Active per-Page publishing hold (rate limit / quota), or None."""
    now = now or datetime.now(UTC)
    if account.publish_hold_until is not None and account.publish_hold_until > now:
        return account.publish_hold_until
    return None
