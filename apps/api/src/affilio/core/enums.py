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


class SocialAccountType(StrEnum):
    PAGE = "page"
    PROFILE = "profile"
    GROUP = "group"
    CHANNEL = "channel"
    SHOP = "shop"


class SocialAccountSource(StrEnum):
    MANUAL = "manual"  # registered by an operator; unverified until OAuth (M3B)
    OAUTH = "oauth"


class ConnectionProvider(StrEnum):
    META = "meta"


class ConnectionStatus(StrEnum):
    """Health of a provider-level grant (one Meta user authorizing the app)."""

    ACTIVE = "active"
    REVOKED = "revoked"  # revoked by an admin (locally and, when possible, at the provider)
    EXPIRED = "expired"  # the provider reports the user grant expired
    INVALID = "invalid"  # the provider rejected the stored token (190 and friends)


class CredentialKind(StrEnum):
    META_USER = "meta_user_long_lived"
    META_PAGE = "meta_page"


class CredentialStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"  # replaced by a newer version (reconnect / rotation)
    REVOKED = "revoked"
    INVALID = "invalid"


class OAuthAttemptStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    DENIED = "denied"  # the person cancelled at the consent screen
    FAILED = "failed"  # provider or validation error
    EXPIRED = "expired"  # swept without ever being consumed


class UploadPhase(StrEnum):
    """Phase of one API publishing attempt (`publication_uploads`). See
    docs/architecture/publishing-engine.md for the machine and the recovery rules."""

    PENDING = "pending"  # created; nothing sent yet
    VALIDATING = "validating"  # media checks running (local I/O only)
    SESSION_STARTING = "session_starting"  # about to ask the provider for an upload session
    UPLOADING = "uploading"  # bytes are being transferred (resumable by offset)
    UPLOAD_COMPLETE = "upload_complete"  # provider acknowledged all bytes
    PROCESSING = "processing"  # provider is transcoding/checking the video
    FINALIZING = "finalizing"  # publish (finish) request persisted BEFORE it is sent
    VERIFYING = "verifying"  # finish acknowledged; polling until the post is live
    RETRY_WAIT = "retry_wait"  # transient failure; next_action_at holds the backoff
    PUBLISHED = "published"  # terminal success
    FAILED = "failed"  # terminal, permanent failure (explicit retry creates a new row)
    NEEDS_RECONCILIATION = "needs_reconciliation"  # finish may have succeeded; human/provider check
    ABANDONED = "abandoned"  # terminal; the job left PUBLISHING by another path


class PublishRequestStatus(StrEnum):
    """Whether the publish (finish) request was sent and what the provider said."""

    NOT_SENT = "not_sent"
    SENT = "sent"  # persisted before the call; response not yet recorded
    ACKNOWLEDGED = "acknowledged"  # provider answered success; post may still be processing
    CONFIRMED = "confirmed"  # provider reports the post as published
    REJECTED = "rejected"  # provider answered with a permanent error
    UNKNOWN = "unknown"  # sent, but the outcome could not be determined


class AffiliateLinkStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class AffiliateLinkSource(StrEnum):
    MANUAL = "manual"
    IMPORT = "import"


class CampaignStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class PublicationStatus(StrEnum):
    DRAFT = "draft"
    READY_FOR_REVIEW = "ready_for_review"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    READY_TO_PUBLISH = "ready_to_publish"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NEEDS_RECONCILIATION = "needs_reconciliation"


class PublicationAttemptStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"


class PublishMode(StrEnum):
    ASSISTED = "assisted"  # operator publishes manually following a checklist
    API = "api"  # reserved for authorized integrations (M3B+)


class EvidenceKind(StrEnum):
    OPERATOR_REPORTED = "operator_reported"
    API_VERIFIED = "api_verified"


class AuditActorKind(StrEnum):
    USER = "user"
    SERVICE = "service"
    SYSTEM = "system"


class CategoryStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class CategoryAssignmentSource(StrEnum):
    FOLDER = "folder"
    MANUAL = "manual"
    IMPORT = "import"
    AI_FUTURE = "ai_future"  # reserved; no AI classification exists yet


class ChannelMatchPolicy(StrEnum):
    ALLOW = "allow"
    PREFER = "prefer"
    BLOCK = "block"
