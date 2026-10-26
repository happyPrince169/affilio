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


class MediaRootKind(StrEnum):
    """Logical media roots. Paths are bound at runtime from settings, never stored in the DB."""

    ORIGINAL = "original"  # read-only: camera originals / masters
    PROXY = "proxy"  # read-only: existing HEVC proxies produced by the NAS workflow
    FINAL = "final"  # read-only: edited, finished videos uploaded by editors (handoff)
    THUMBNAIL = "thumbnail"  # read-write: AFFILIO-generated thumbnails
    EXPORT = "export"  # read-write: future publish-ready H.264/AAC exports


class MediaRootAccess(StrEnum):
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


class MediaAssetStatus(StrEnum):
    ACTIVE = "active"  # original present and probed
    MISSING = "missing"  # original no longer found at its last known path
    UNSUPPORTED = "unsupported"  # present but ffprobe could not read it as video


class MediaVariantKind(StrEnum):
    ORIGINAL = "original"
    PROXY = "proxy"
    FINAL = "final"
    THUMBNAIL = "thumbnail"
    EXPORT = "export"


class MediaAssetKind(StrEnum):
    FOOTAGE = "footage"  # indexed from the originals root
    FINAL = "final"  # indexed from the finals root (edited, ready for review)


class HandoffStatus(StrEnum):
    """Human review gate for final videos. Only `approved` finals may be published (M3)."""

    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class MediaVariantStatus(StrEnum):
    PRESENT = "present"
    MISSING = "missing"


class ThumbnailStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"
    SKIPPED = "skipped"  # e.g. asset unsupported or thumbnails root not configured


class ScanJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScanTrigger(StrEnum):
    MANUAL = "manual"
    CLI = "cli"
    SCHEDULED = "scheduled"


class ProductStatus(StrEnum):
    """Product research lifecycle. WINNER is only ever set by an explicit human confirmation."""

    DISCOVERED = "discovered"
    SHORTLISTED = "shortlisted"
    TESTING = "testing"
    WINNER = "winner"
    PAUSED = "paused"
    ARCHIVED = "archived"


class ProductSource(StrEnum):
    MANUAL = "manual"
    MEOSCOUT = "meoscout"


class ImportStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"  # same file already imported


class PurchaseRequestStatus(StrEnum):
    REQUESTED = "requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    ORDERED = "ordered"
    CANCELLED = "cancelled"


class PurchaseOrderStatus(StrEnum):
    DRAFT = "draft"
    PLACED = "placed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    PARTIALLY_RECEIVED = "partially_received"
    RECEIVED = "received"
    CANCELLED = "cancelled"


class ProductionStatus(StrEnum):
    PLANNED = "planned"
    SCHEDULED = "scheduled"
    FILMING = "filming"
    FILMED = "filmed"
    CANCELLED = "cancelled"


class EditingStatus(StrEnum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"


class ProductionMediaRole(StrEnum):
    FOOTAGE = "footage"
    FINAL = "final"
    REFERENCE = "reference"
