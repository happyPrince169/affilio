"""Domain enumerations shared across modules.

Keep values in sync with `packages/shared-types/src/domain.ts`.
Stored in PostgreSQL as VARCHAR with a CHECK constraint (not native enums) so that adding a
value is a simple, reversible migration.
"""

from enum import StrEnum


class Platform(StrEnum):
    """Platforms the architecture is designed for. Only `SHOPEE` is in MVP scope."""

    SHOPEE = "shopee"
    FACEBOOK = "facebook"
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"
    YOUTUBE = "youtube"


class SocialAccountStatus(StrEnum):
    PENDING = "pending"  # record exists, no credentials/authorization yet
    CONNECTED = "connected"  # authorized through an official platform flow
    DISCONNECTED = "disconnected"  # authorization revoked or expired
    DISABLED = "disabled"  # intentionally paused by the organization


class UserStatus(StrEnum):
    INVITED = "invited"
    ACTIVE = "active"
    SUSPENDED = "suspended"


class MembershipRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"
